#!/usr/bin/env python3
"""Rebuild product-detail text enrichments in one evidence-grounded LLM call.

The immutable article HTML and deterministic extraction remain the source of
truth.  The text model only summarizes/classifies three publication views:
consumer claims, appearance bullets and semantic BOM parameters.  Every output
is validated against exact source quotes before it enters staging.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.extract.bom_parameters import (  # noqa: E402
    bom_row_key,
    deterministic_candidates as deterministic_bom,
    parameter_source_hash,
    validate_bom_parameters,
)
from core.extract.consumer_claims import (  # noqa: E402
    CATEGORY_KEYWORDS,
    deterministic_candidates as deterministic_claims,
    extract_consumer_source,
    source_hash as consumer_source_hash,
    validate_consumer_claims,
)
from core.extract.unboxing_summary import (  # noqa: E402
    MODULE_KEYS,
    collect_unboxing_source,
    deterministic_candidates as deterministic_unboxing,
    source_hash as unboxing_source_hash,
    validate_unboxing_summary,
)
from core.paths import (  # noqa: E402
    bom_parameters_enrich_dir,
    consumer_claims_enrich_dir,
    products_dir,
    unboxing_enrich_dir,
    unboxing_summary_enrich_dir,
)


HTML_CACHE = ROOT / "data" / "cache" / "content_html"
SYSTEM_PROMPT = """你是消费电子竞品研究的数据编辑。输入来自同一产品的一篇拆解报告，请一次完成三类文本整理。

A. consumer_claims：消费者可感知的体验/收益。category 只能是：佩戴体验、音质体验、通话体验、续航充电、连接与智能、耐用防护、外观设计。最多7条。
B. unboxing：分别整理 packaging、charging_case、earbuds。只保留产品识别、外观、接口、配件、佩戴结构、尺寸重量与公开参数；不混入拆解步骤和内部电路。每模块最多6条。
C. bom_items：为每个输入 key 提取有明确含义的参数。品牌、型号、一级位置、分类已有字段，不重复；优先架构、容量、电压、频率、材料、接口、集成功能与用途。对于支架、转轴、壳体等结构件，提取原文明确写出的详细位置、结构形态、固定/承载对象、固定或密封方式和层数；“透明”只能作为外观特征，不得推断材料。每个器件最多7条。

共同约束：
1. 只允许使用当前输入提供的事实，不得跨器件、跨模块引用。
2. evidence_quote 必须逐字连续复制相应输入原文；value 必须逐字出现在 evidence_quote 中。
3. 不得计算、换算或补充原文没有的数字；“支持”不能改写成“已经启用”。
4. 合并重复表达；没有可靠信息返回空数组。
5. 只返回 JSON，不要解释。

返回格式：
{"consumer_claims":[{"text":"...","category":"...","consumer_benefit":"...","scenario":"...","evidence_quote":"...","supporting_bom_keys":[],"confidence":0.9}],"unboxing":{"packaging":[{"text":"...","topic":"...","evidence_quote":"...","priority":5}],"charging_case":[],"earbuds":[]},"bom_items":[{"key":"输入key","parameters":[{"label":"最高主频","value":"120MHz","evidence_quote":"原文连续片段"}]}]}"""


def _parse_json_text(content: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    return json.loads(text)


def _model_name() -> str:
    return os.environ.get("DEFAULT_MODEL_NAME", "deepseek-v4-flash").strip() or "deepseek-v4-flash"


def _llm_candidates(payload: dict) -> tuple[dict, str]:
    from openai import OpenAI

    api_key = os.environ.get("DEFAULT_MODEL_API_KEY", "").strip()
    api_url = os.environ.get("DEFAULT_MODEL_API_URL", "").strip()
    model = _model_name()
    if not api_key or not api_url:
        raise RuntimeError("DEFAULT_MODEL_API_KEY / DEFAULT_MODEL_API_URL not configured")
    client = OpenAI(api_key=api_key, base_url=api_url, timeout=180.0, max_retries=0)
    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        max_tokens=5000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )
    parsed = _parse_json_text(response.choices[0].message.content or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("model response is not an object")
    return parsed, model


def _preferred_report_id(product: dict) -> str:
    snapshot = product.get("cost_snapshot") or {}
    market = product.get("market") or {}
    preferred = snapshot.get("best_report_id") or market.get("best_report_id")
    report_ids = [str(value) for value in product.get("report_ids") or []]
    return str(preferred or (report_ids[0] if report_ids else ""))


def _merge_bom(primary: dict[str, list[dict]], fallback: dict[str, list[dict]]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for key in set(primary) | set(fallback):
        seen_labels: set[str] = set()
        rows: list[dict] = []
        for item in [*(primary.get(key) or []), *(fallback.get(key) or [])]:
            label = re.sub(r"[\s_/-]+", "", str(item.get("label") or "")).casefold()
            signature = re.sub(r"\s+", "", str(item.get("display") or "")).casefold()
            if not label or label in seen_labels or any(
                re.sub(r"\s+", "", str(row.get("display") or "")).casefold() == signature
                for row in rows
            ):
                continue
            seen_labels.add(label)
            rows.append(item)
            if len(rows) >= 7:
                break
        result[key] = rows
    return result


def _merge_evidence_records(
    baseline: list[dict], additions: list[dict], *, limit: int
) -> list[dict]:
    """Keep validated rule coverage and append non-duplicate model refinements."""

    rows: list[dict] = []
    seen: set[str] = set()
    for item in [*baseline, *additions]:
        signature = re.sub(
            r"\s+",
            "",
            str(item.get("evidence_quote") or item.get("text") or ""),
        ).casefold()
        if not signature or signature in seen:
            continue
        seen.add(signature)
        rows.append(item)
        if len(rows) >= limit:
            break
    return rows


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _cache_current(path: Path, source_hash: str, model: str, *, use_llm: bool) -> bool:
    cached = _read_json(path)
    if cached.get("source_hash") != source_hash:
        return False
    if not use_llm:
        return True
    meta = cached.get("meta") or {}
    return str(meta.get("model") or "") == model and str(meta.get("method") or "").startswith("llm_")


def build_model_payload(
    product: dict,
    canonical_id: str,
    paragraphs: list[str],
    bom_rows: list[dict],
    appearance_source: dict[str, list[str]],
) -> dict:
    """Build the bounded, evidence-bearing payload used by production and evals."""

    return {
        "product": {
            "canonical_id": canonical_id,
            "brand": product.get("brand") or "",
            "model": product.get("model") or "",
            "category": product.get("category") or "",
        },
        "consumer_paragraphs": paragraphs[:60],
        "allowed_bom_keys": list(
            dict.fromkeys(
                str(row.get("model") or row.get("component") or "").strip()
                for row in bom_rows
                if row.get("model") or row.get("component")
            )
        ),
        "unboxing_sources": {
            key: list(appearance_source.get(key) or [])[:20] for key in MODULE_KEYS
        },
        "bom_items": [
            {
                "key": bom_row_key(row),
                "classification": row.get("role") or row.get("classification") or "",
                "component": row.get("component") or "",
                "brand": row.get("brand") or "",
                "model": row.get("model") or "",
                "side": row.get("side") or "",
                "fact_text": row.get("fact_text") or "",
                "evidence_texts": row.get("evidence_texts") or [],
            }
            for row in bom_rows
        ],
    }


def process_product(path: Path, *, use_llm: bool, force: bool, changed_only: bool = False) -> dict:
    product = _read_json(path)
    canonical_id = str(product.get("canonical_id") or path.stem)
    report_id = _preferred_report_id(product)
    html_path = HTML_CACHE / f"{report_id}.html"
    html = html_path.read_text(encoding="utf-8") if report_id and html_path.exists() else ""
    paragraphs = extract_consumer_source(html) if html else []
    bom_rows = list(product.get("technical_facts") or product.get("teardown_inventory") or [])
    unboxing_path = unboxing_enrich_dir() / f"{report_id}.json"
    unboxing_payload = _read_json(unboxing_path) if report_id else {}
    appearance_source = collect_unboxing_source(unboxing_payload) if unboxing_payload else {key: [] for key in MODULE_KEYS}

    consumer_hash = consumer_source_hash(paragraphs)
    bom_hash = parameter_source_hash(bom_rows)
    appearance_hash = unboxing_source_hash(appearance_source)
    consumer_path = consumer_claims_enrich_dir(for_write=True) / f"{canonical_id}.json"
    bom_path = bom_parameters_enrich_dir(for_write=True) / f"{canonical_id}.json"
    appearance_path = unboxing_summary_enrich_dir(for_write=True) / f"{report_id}.json" if report_id else None
    model = _model_name() if use_llm else "rules-v1"
    cache_use_llm = False if changed_only else use_llm
    if not force and _cache_current(consumer_path, consumer_hash, model, use_llm=cache_use_llm) and _cache_current(
        bom_path, bom_hash, model, use_llm=cache_use_llm
    ) and (appearance_path is None or _cache_current(appearance_path, appearance_hash, model, use_llm=cache_use_llm)):
        return {"id": canonical_id, "status": "cache_hit", "api_call": 0}

    raw: dict = {}
    method = "deterministic_fallback"
    api_call = 0
    error = ""
    if use_llm:
        api_call = 1
        try:
            raw, model = _llm_candidates(
                build_model_payload(
                    product, canonical_id, paragraphs, bom_rows, appearance_source
                )
            )
            method = "llm_plus_code_validation"
        except Exception as exc:  # safe deterministic degradation
            error = str(exc)[:500]

    claims, claim_rejected = validate_consumer_claims(
        raw.get("consumer_claims") if raw else deterministic_claims(paragraphs),
        paragraphs,
        bom_rows,
    )
    baseline_claims, fallback_rejected = validate_consumer_claims(
        deterministic_claims(paragraphs), paragraphs, bom_rows
    )
    claim_rejected.extend(fallback_rejected)
    claims = _merge_evidence_records(
        baseline_claims, claims, limit=max(7, len(baseline_claims))
    )

    bom_candidates = {"items": raw.get("bom_items") or []} if raw else deterministic_bom(bom_rows)
    parameters, bom_rejected = validate_bom_parameters(bom_candidates, bom_rows)
    fallback_parameters, fallback_rejected = validate_bom_parameters(deterministic_bom(bom_rows), bom_rows)
    parameters = _merge_bom(parameters, fallback_parameters)
    bom_rejected.extend(fallback_rejected)

    appearance_candidates = raw.get("unboxing") if raw else deterministic_unboxing(appearance_source)
    modules, appearance_rejected = validate_unboxing_summary(
        appearance_candidates, appearance_source
    )
    baseline_modules, fallback_rejected = validate_unboxing_summary(
        deterministic_unboxing(appearance_source), appearance_source
    )
    appearance_rejected.extend(fallback_rejected)
    modules = {
        key: _merge_evidence_records(
            baseline_modules.get(key) or [],
            modules.get(key) or [],
            limit=max(6, len(baseline_modules.get(key) or [])),
        )
        for key in MODULE_KEYS
    }

    consumer_path.parent.mkdir(parents=True, exist_ok=True)
    consumer_path.write_text(json.dumps({
        "canonical_id": canonical_id,
        "source_report_id": report_id,
        "source_hash": consumer_hash,
        "consumer_claims": claims,
        "meta": {
            "method": method, "model": model, "validator": "consumer-claims-v1",
            "source_paragraph_count": len(paragraphs), "published_count": len(claims),
            "rejected_count": len(claim_rejected), "allowed_categories": list(CATEGORY_KEYWORDS),
        },
        "rejected_audit": claim_rejected,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    bom_path.parent.mkdir(parents=True, exist_ok=True)
    bom_items = [
        {"key": bom_row_key(row), "component": row.get("component") or "", "model": row.get("model") or "", "parameters": parameters.get(bom_row_key(row)) or []}
        for row in bom_rows
    ]
    bom_path.write_text(json.dumps({
        "canonical_id": canonical_id,
        "source_hash": bom_hash,
        "items": bom_items,
        "meta": {
            "method": method, "model": model, "validator": "bom-parameters-v1",
            "row_count": len(bom_rows), "parameter_count": sum(len(item["parameters"]) for item in bom_items),
            "rejected_count": len(bom_rejected),
        },
        "rejected_audit": bom_rejected,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if appearance_path is not None:
        appearance_path.parent.mkdir(parents=True, exist_ok=True)
        appearance_path.write_text(json.dumps({
            "report_id": report_id,
            "source_hash": appearance_hash,
            "modules": modules,
            "meta": {
                "method": method, "model": model, "validator": "unboxing-summary-v1",
                "published_count": sum(len(modules[key]) for key in MODULE_KEYS),
                "rejected_count": len(appearance_rejected),
            },
            "rejected_audit": appearance_rejected,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "id": canonical_id,
        "status": "ok" if not error else "fallback",
        "api_call": api_call,
        "error": error,
        "claims": len(claims),
        "parameters": sum(len(item["parameters"]) for item in bom_items),
        "appearance_bullets": sum(len(modules[key]) for key in MODULE_KEYS),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", help="只处理指定 canonical product ID")
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--changed-only", action="store_true", help="只处理源数据新增或发生变化的产品")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    paths = [path for path in sorted(products_dir().glob("*.json")) if path.name != "index.json"]
    if args.product:
        paths = [path for path in paths if path.stem == args.product]
    if args.limit:
        paths = paths[: max(0, args.limit)]
    stats = {"total": len(paths), "ok": 0, "fallback": 0, "cache_hits": 0, "failed": 0, "api_calls": 0}

    def run(path: Path) -> dict:
        try:
            return process_product(path, use_llm=args.use_llm, force=args.force, changed_only=args.changed_only)
        except Exception as exc:
            return {"id": path.stem, "status": "failed", "api_call": 0, "error": str(exc)[:500]}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(run, path): path for path in paths}
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            status = result.get("status") or "failed"
            stats["api_calls"] += int(result.get("api_call") or 0)
            if status == "cache_hit":
                stats["cache_hits"] += 1
            elif status in {"ok", "fallback"}:
                stats[status] += 1
                if status == "fallback" and result.get("error"):
                    print(f"[warn] {result.get('id')}: {result.get('error')}", file=sys.stderr, flush=True)
            else:
                stats["failed"] += 1
            if index % 25 == 0 or index == len(paths):
                print(f"[progress] {index}/{len(paths)} ok={stats['ok']} fallback={stats['fallback']} failed={stats['failed']}", file=sys.stderr, flush=True)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
