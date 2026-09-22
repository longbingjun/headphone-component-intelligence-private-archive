#!/usr/bin/env python3
"""Extract semantic BOM parameters with optional text-LLM assistance."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.extract.bom_parameters import (  # noqa: E402
    bom_row_key,
    deterministic_candidates,
    parameter_source_hash,
    validate_bom_parameters,
)
from core.paths import bom_parameters_enrich_dir, products_dir  # noqa: E402


SYSTEM_PROMPT = """你是消费电子拆解报告的 BOM 参数编辑。请把每个器件的原文转换为有明确含义的参数名和值。
规则：
1. 只处理输入 items 中的当前器件，不得跨器件引用。
2. label 必须说明值的含义，例如“最高主频”“片上闪存”“额定容量”“主要用途”，禁止使用“参数”“规格”等空泛名称。
3. value 必须是 evidence_quote 中逐字连续出现的短语；evidence_quote 必须是 fact_text 中逐字连续出现的原文。
4. 不得计算、换算或补充原文没有的数字。系列“支持”能力不能改写成产品已经启用。
5. 品牌、厂商、型号、一级位置和分类已有独立字段，不得作为参数重复输出。优先提取架构、容量、电压、频率、接口资源、集成功能和主要用途。对于支架、转轴、壳体等结构件，优先提取原文明确写出的详细位置、结构形态、固定/承载对象、固定或密封方式、层数和材料；“透明”只能作为外观特征，不得推断为塑料、PC 或亚克力。纯“特写/一览”可返回空数组。
6. 每个器件最多 7 条，合并重复项。
只返回 JSON：{"items":[{"key":"输入key","parameters":[{"label":"最高主频","value":"120MHz","evidence_quote":"原文连续片段"}]}]}"""


def _parse_json_text(content: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    return json.loads(text)


def llm_candidates(rows: list[dict]) -> tuple[dict, str]:
    from openai import OpenAI

    api_key = os.environ.get("APP_TEXT_MODEL_TOKEN", "").strip()
    api_url = os.environ.get("APP_TEXT_MODEL_BASE_URL", "").strip()
    model = os.environ.get("APP_TEXT_MODEL_NAME", "deepseek-v4-flash").strip() or "deepseek-v4-flash"
    if not api_key or not api_url:
        raise RuntimeError("APP_TEXT_MODEL_TOKEN / APP_TEXT_MODEL_BASE_URL not configured")
    items = [
        {
            "key": bom_row_key(row),
            "classification": row.get("role") or row.get("classification") or "",
            "component": row.get("component") or "",
            "brand": row.get("brand") or "",
            "model": row.get("model") or "",
            "side": row.get("side") or "",
            "specification": row.get("specification") or "",
            "fact_text": row.get("fact_text") or "",
        }
        for row in rows
    ]
    client = OpenAI(api_key=api_key, base_url=api_url, timeout=120.0)
    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
        ],
    )
    parsed = _parse_json_text(response.choices[0].message.content or "{}")
    if not isinstance(parsed.get("items"), list):
        raise ValueError("model response missing items list")
    return parsed, model


def _merge_parameters(primary: dict[str, list[dict]], fallback: dict[str, list[dict]]) -> dict[str, list[dict]]:
    def label_key(item: dict) -> str:
        label = re.sub(r"[\s_/-]+", "", str(item.get("label") or "")).casefold()
        value = str(item.get("value") or "")
        if "ovp" in label and "耐压" in label:
            return "ovp耐压"
        if "sram" in label:
            return "sram"
        if "内核" in label or "架构" in label:
            return "处理器内核"
        if "接口资源" in label:
            return "接口资源"
        if label == "集成功能" and re.search(r"USART|UART|I2C|SPI|USB|I2S|EXMC", value, re.I):
            return "接口资源"
        return label

    merged: dict[str, list[dict]] = {}
    for key in set(primary) | set(fallback):
        rows: list[dict] = []
        seen: set[str] = set()
        for item in [*(primary.get(key) or []), *(fallback.get(key) or [])]:
            signature = re.sub(r"\s+", "", str(item.get("display") or "")).casefold()
            label = label_key(item)
            if not signature or signature in seen or label in {label_key(row) for row in rows}:
                continue
            seen.add(signature)
            rows.append(item)
            if len(rows) >= 7:
                break
        merged[key] = rows
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", help="只处理指定 canonical product ID")
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--reuse-candidates", action="store_true", help="重新校验已有缓存，不再次调用模型")
    args = parser.parse_args()

    output_dir = bom_parameters_enrich_dir(for_write=True)
    product_paths = [path for path in sorted(products_dir().glob("*.json")) if path.name != "index.json"]
    if args.product:
        product_paths = [path for path in product_paths if path.stem == args.product]
    stats = {"processed": 0, "cache_hits": 0, "api_calls": 0, "fallbacks": 0, "failed": 0}
    for path in product_paths:
        try:
            product = json.loads(path.read_text(encoding="utf-8"))
            rows = list(product.get("technical_facts") or product.get("teardown_inventory") or [])
            if not rows:
                continue
            fingerprint = parameter_source_hash(rows)
            output_path = output_dir / f"{product['canonical_id']}.json"
            if output_path.exists() and not args.force:
                cached = json.loads(output_path.read_text(encoding="utf-8"))
                if cached.get("source_hash") == fingerprint and cached.get("items"):
                    stats["cache_hits"] += 1
                    continue

            method, model = "deterministic_fallback", "rules-v1"
            rejected: list[dict] = []
            if args.reuse_candidates and output_path.exists():
                existing = json.loads(output_path.read_text(encoding="utf-8"))
                parameters, rejected = validate_bom_parameters({"items": existing.get("items") or []}, rows)
                old_meta = existing.get("meta") or {}
                base_method = re.sub(r"(?:_revalidated)+$", "", str(old_meta.get("method") or "cached"))
                method = f"{base_method}_revalidated"
                model = str(old_meta.get("model") or "unknown")
            elif args.use_llm:
                try:
                    candidates, model = llm_candidates(rows)
                    parameters, rejected = validate_bom_parameters(candidates, rows)
                    method = "llm_plus_code_validation"
                    stats["api_calls"] += 1
                except Exception as exc:
                    print(f"[warn] {product['canonical_id']}: text LLM unavailable, fallback: {exc}", file=sys.stderr)
                    parameters = {bom_row_key(row): [] for row in rows}
                    stats["fallbacks"] += 1
            else:
                parameters = {bom_row_key(row): [] for row in rows}
                stats["fallbacks"] += 1

            fallback, fallback_rejected = validate_bom_parameters(deterministic_candidates(rows), rows)
            parameters = _merge_parameters(parameters, fallback)
            rejected.extend(fallback_rejected)
            items = [
                {"key": bom_row_key(row), "component": row.get("component") or "", "model": row.get("model") or "", "parameters": parameters.get(bom_row_key(row)) or []}
                for row in rows
            ]
            result = {
                "canonical_id": product["canonical_id"],
                "source_hash": fingerprint,
                "items": items,
                "meta": {
                    "method": method,
                    "model": model,
                    "validator": "bom-parameters-v1",
                    "row_count": len(rows),
                    "parameter_count": sum(len(item["parameters"]) for item in items),
                    "rejected_count": len(rejected),
                },
                "rejected_audit": rejected,
            }
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stats["processed"] += 1
        except Exception as exc:
            stats["failed"] += 1
            print(f"[warn] {path.stem}: {exc}", file=sys.stderr)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
