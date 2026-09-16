#!/usr/bin/env python3
"""Build evidence-grounded display bullets for unboxing/appearance modules."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.extract.unboxing_summary import (  # noqa: E402
    MODULE_KEYS,
    collect_unboxing_source,
    deterministic_candidates,
    source_hash,
    validate_unboxing_summary,
)
from core.paths import products_dir, unboxing_enrich_dir, unboxing_summary_enrich_dir  # noqa: E402


SYSTEM_PROMPT = """你是消费电子竞品研究编辑。请分别整理包装、充电盒、耳机三个外观模块。
规则：
1. 只保留对产品识别、外观设计、接口、配件、佩戴结构、尺寸重量和公开参数有用的信息。
2. 不输出拆解步骤、芯片、电路、主板、丝印等内部 BOM 信息。
3. 每条 evidence_quote 必须逐字复制对应模块原文；不能增加原文没有的数字。
4. 合并重复图注，每个模块最多 6 条，文字简洁，适合网页 bullet point。
5. priority 为 1-5，5 表示对竞品外观/配置判断最有用。
只返回 JSON：{"modules":{"packaging":[{"text":"...","topic":"...","evidence_quote":"...","priority":5}],"charging_case":[],"earbuds":[]}}"""


def _parse_json_text(content: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    return json.loads(text)


def llm_candidates(source: dict[str, list[str]]) -> tuple[dict, str]:
    from openai import OpenAI

    api_key = os.environ.get("DEFAULT_MODEL_API_KEY", "").strip()
    api_url = os.environ.get("DEFAULT_MODEL_API_URL", "").strip()
    model = os.environ.get("DEFAULT_MODEL_NAME", "deepseek-v4-flash").strip() or "deepseek-v4-flash"
    if not api_key or not api_url:
        raise RuntimeError("DEFAULT_MODEL_API_KEY / DEFAULT_MODEL_API_URL not configured")
    client = OpenAI(api_key=api_key, base_url=api_url, timeout=60.0)
    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"module_sources": source}, ensure_ascii=False)},
        ],
    )
    parsed = _parse_json_text(response.choices[0].message.content or "{}")
    modules = parsed.get("modules")
    if not isinstance(modules, dict):
        raise ValueError("model response missing modules object")
    return modules, model


def _selected_report_ids(product_id: str | None) -> set[str] | None:
    if not product_id:
        return None
    path = products_dir() / f"{product_id}.json"
    if not path.exists():
        raise SystemExit(f"product not found: {product_id}")
    product = json.loads(path.read_text(encoding="utf-8"))
    return {str(value) for value in product.get("report_ids") or []}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", help="只处理指定 canonical product ID")
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    selected = _selected_report_ids(args.product)
    output_dir = unboxing_summary_enrich_dir(for_write=True)
    stats = {"processed": 0, "cache_hits": 0, "api_calls": 0, "fallbacks": 0, "failed": 0}
    for path in sorted(unboxing_enrich_dir().glob("*.json")):
        report_id = path.stem
        if selected is not None and report_id not in selected:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            source = collect_unboxing_source(payload)
            fingerprint = source_hash(source)
            output_path = output_dir / f"{report_id}.json"
            if output_path.exists() and not args.force:
                cached = json.loads(output_path.read_text(encoding="utf-8"))
                if cached.get("source_hash") == fingerprint and cached.get("modules"):
                    stats["cache_hits"] += 1
                    continue

            method, model = "deterministic_fallback", "rules-v1"
            if args.use_llm:
                try:
                    candidates, model = llm_candidates(source)
                    method = "llm_plus_code_validation"
                    stats["api_calls"] += 1
                except Exception as exc:
                    print(f"[warn] report {report_id}: text LLM unavailable, fallback: {exc}", file=sys.stderr)
                    candidates = deterministic_candidates(source)
                    stats["fallbacks"] += 1
            else:
                candidates = deterministic_candidates(source)
                stats["fallbacks"] += 1

            modules, rejected = validate_unboxing_summary(candidates, source)
            if not any(modules.values()) and method != "deterministic_fallback":
                method, model = "deterministic_fallback_after_validation", "rules-v1"
                modules, fallback_rejected = validate_unboxing_summary(deterministic_candidates(source), source)
                rejected.extend(fallback_rejected)
                stats["fallbacks"] += 1
            result = {
                "report_id": report_id,
                "source_hash": fingerprint,
                "modules": modules,
                "meta": {
                    "method": method,
                    "model": model,
                    "validator": "unboxing-summary-v1",
                    "published_count": sum(len(modules[key]) for key in MODULE_KEYS),
                    "rejected_count": len(rejected),
                },
                "rejected_audit": rejected,
            }
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stats["processed"] += 1
        except Exception as exc:
            stats["failed"] += 1
            print(f"[warn] report {report_id}: {exc}", file=sys.stderr)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
