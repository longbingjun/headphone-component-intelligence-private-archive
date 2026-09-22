#!/usr/bin/env python3
"""Extract report-grounded consumer claims with optional LLM summarization.

The LLM proposes concise wording and categories. Code performs the publication
decision. If the API is unavailable, the exact report sentences are used as a
safe deterministic fallback.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.extract.consumer_claims import (  # noqa: E402
    CATEGORY_KEYWORDS,
    deterministic_candidates,
    extract_consumer_source,
    source_hash,
    validate_consumer_claims,
)
from core.ingest import load_all_records  # noqa: E402
from core.paths import consumer_claims_enrich_dir, products_dir  # noqa: E402


SYSTEM_PROMPT = """你是消费电子竞品研究编辑。请从给出的拆解报告原文中提炼消费者可感知的卖点。
规则：
1. 只允许使用原文事实，evidence_quote 必须逐字复制一段原文；不得补充原文没有的数字或结论。
2. 消费者卖点回答“用户获得什么体验/收益”。芯片型号、供应商、电路和丝印等纯技术事实不能单独作为卖点。
3. category 只能取：佩戴体验、音质体验、通话体验、续航充电、连接与智能、耐用防护、外观设计。
4. 可用 supporting_bom_keys 连接技术支撑，但只能从提供的 BOM key 中选择。
5. 合并重复表达，最多 7 条。
只返回 JSON：{"consumer_claims":[{"text":"...","category":"...","consumer_benefit":"...","scenario":"...","evidence_quote":"...","supporting_bom_keys":[],"confidence":0.0}]}"""


def _load_report_sources() -> dict[str, dict]:
    """Load compact records, then restore immutable HTML from legacy archive."""
    reports = {str(item.get("id")): item for item in load_all_records("report")}
    archive_path = ROOT / "data" / "reports.json"
    if not archive_path.exists():
        return reports
    try:
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
        rows = (archive.get("reports") or archive.get("items")) if isinstance(archive, dict) else archive
        for row in rows or []:
            report_id = str(row.get("id") or "")
            if not report_id or not row.get("content_html"):
                continue
            reports.setdefault(report_id, {}).update({"content_html": row["content_html"]})
    except Exception as exc:
        print(f"[warn] cannot read report source archive: {exc}", file=sys.stderr)
    return reports


def _parse_json_text(content: str) -> dict:
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    return json.loads(text)


def llm_candidates(paragraphs: list[str], bom_rows: list[dict]) -> tuple[list[dict], str]:
    from openai import OpenAI

    api_key = os.environ.get("APP_TEXT_MODEL_TOKEN", "").strip()
    api_url = os.environ.get("APP_TEXT_MODEL_BASE_URL", "").strip()
    model = os.environ.get("APP_TEXT_MODEL_NAME", "deepseek-v4-flash").strip() or "deepseek-v4-flash"
    if not api_key or not api_url:
        raise RuntimeError("APP_TEXT_MODEL_TOKEN / APP_TEXT_MODEL_BASE_URL not configured")
    bom_keys = list(dict.fromkeys(
        str(row.get("model") or row.get("component") or "").strip()
        for row in bom_rows
        if row.get("role") != "unidentified_marking" and (row.get("model") or row.get("component"))
    ))
    client = OpenAI(api_key=api_key, base_url=api_url, timeout=60.0)
    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"report_paragraphs": paragraphs, "allowed_bom_keys": bom_keys},
                    ensure_ascii=False,
                ),
            },
        ],
    )
    parsed = _parse_json_text(response.choices[0].message.content or "{}")
    rows = parsed.get("consumer_claims")
    if not isinstance(rows, list):
        raise ValueError("model response missing consumer_claims list")
    return rows, model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", help="只处理指定 canonical product ID")
    parser.add_argument("--use-llm", action="store_true", help="使用已配置的文本模型 API；失败时自动降级")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    reports = _load_report_sources()
    cache_dir = consumer_claims_enrich_dir(for_write=True)
    stats = {"processed": 0, "cache_hits": 0, "api_calls": 0, "fallbacks": 0, "failed": 0}

    product_paths = [path for path in sorted(products_dir().glob("*.json")) if path.name != "index.json"]
    if args.product:
        product_paths = [path for path in product_paths if path.stem == args.product]

    for product_path in product_paths:
        try:
            product = json.loads(product_path.read_text(encoding="utf-8"))
            report_ids = [str(value) for value in product.get("report_ids") or []]
            if not report_ids:
                continue
            preferred = str(((product.get("market") or {}).get("best_report_id") or report_ids[0]))
            report = reports.get(preferred) or next((reports.get(rid) for rid in report_ids if reports.get(rid)), None)
            if not report or not report.get("content_html"):
                stats["failed"] += 1
                continue

            paragraphs = extract_consumer_source(str(report["content_html"]))
            fingerprint = source_hash(paragraphs)
            cache_path = cache_dir / f"{product['canonical_id']}.json"
            if cache_path.exists() and not args.force:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached.get("source_hash") == fingerprint and cached.get("consumer_claims"):
                    stats["cache_hits"] += 1
                    continue

            bom_rows = list(product.get("technical_facts") or product.get("teardown_inventory") or product.get("bom_table") or [])
            method = "deterministic_fallback"
            model = "rules-v1"
            raw_candidates: list[dict]
            if args.use_llm:
                try:
                    raw_candidates, model = llm_candidates(paragraphs, bom_rows)
                    method = "llm_plus_code_validation"
                    stats["api_calls"] += 1
                except Exception as exc:
                    print(f"[warn] {product['canonical_id']}: text LLM unavailable, fallback: {exc}", file=sys.stderr)
                    raw_candidates = deterministic_candidates(paragraphs)
                    stats["fallbacks"] += 1
            else:
                raw_candidates = deterministic_candidates(paragraphs)
                stats["fallbacks"] += 1

            claims, rejected = validate_consumer_claims(raw_candidates, paragraphs, bom_rows)
            # 若模型只因自行计算/改写了数字而被拒绝，保留其逐字证据作为安全降级卖点。
            # 例如原文只有“9h+28h”，模型不能自行发布“总计37小时”。
            for audit in list(rejected):
                if audit.get("reason") != "unsupported_number" or len(claims) >= 7:
                    continue
                candidate = audit.get("candidate") or {}
                quote = str(candidate.get("evidence_quote") or "").strip()
                repaired, _ = validate_consumer_claims(
                    [{
                        "text": quote,
                        "category": candidate.get("category"),
                        "consumer_benefit": "",
                        "scenario": candidate.get("scenario") or "",
                        "evidence_quote": quote,
                        "supporting_bom_keys": candidate.get("supporting_bom_keys") or [],
                        "confidence": 0.8,
                    }],
                    paragraphs,
                    bom_rows,
                    max_claims=1,
                )
                if repaired and repaired[0]["text"] not in {item["text"] for item in claims}:
                    claims.extend(repaired)
            if not claims and method != "deterministic_fallback":
                method = "deterministic_fallback_after_validation"
                model = "rules-v1"
                claims, fallback_rejected = validate_consumer_claims(
                    deterministic_candidates(paragraphs), paragraphs, bom_rows
                )
                rejected.extend(fallback_rejected)
                stats["fallbacks"] += 1
            payload = {
                "canonical_id": product["canonical_id"],
                "source_report_id": str(report.get("id") or preferred),
                "source_hash": fingerprint,
                "consumer_claims": claims,
                "meta": {
                    "method": method,
                    "model": model,
                    "validator": "consumer-claims-v1",
                    "source_paragraph_count": len(paragraphs),
                    "published_count": len(claims),
                    "rejected_count": len(rejected),
                    "allowed_categories": list(CATEGORY_KEYWORDS),
                },
                "rejected_audit": rejected,
            }
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stats["processed"] += 1
        except Exception as exc:
            stats["failed"] += 1
            print(f"[warn] {product_path.stem}: {exc}", file=sys.stderr)

    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
