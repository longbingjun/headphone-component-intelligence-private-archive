#!/usr/bin/env python3
"""Build the production earphone fine-category overlay with traceable evidence.

Explicit product-specific wording is resolved by deterministic rules.  Only
unclear/conflicting legacy TWS/open-ear products are sent to the configured
text model.  The curated product files are not edited here; ``build_products``
applies the versioned overlay on its next run.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.earphone_categories import PENDING_CATEGORY
from core.paths import earphone_category_overrides_path
from scripts.pilot_earphone_category_v2 import (
    ALLOWED_LABELS,
    EVIDENCE_REPAIR_PROMPT,
    LABEL_PATTERNS,
    PROMPT_VERSION,
    TARGET_LEGACY_CATEGORIES,
    add_usage,
    build_candidates,
    clean_text,
    llm_batch,
    legacy_category_of,
    load_products,
    model_config,
    parsed_items_by_id,
    rule_hint,
    safe_json,
    validate_result,
)


CLASSIFIER_VERSION = "earphone-fine-category-2026-09-11-v1"
DEFAULT_ENV_FILE = ROOT.parent / "secrets" / ".env.production"
DEFAULT_WORK_ROOT = ROOT / "runtime" / "earphone-category-production"


def direct_record(source: dict[str, Any]) -> dict[str, Any] | None:
    hint, quote, _ = rule_hint(source["segments"])
    if hint not in LABEL_PATTERNS or not quote:
        return None
    matching = [
        segment["segment_id"]
        for segment in source["segments"]
        if segment.get("target_match") == "true"
        and quote in segment.get("text", "")
    ]
    # A report often mentions older/comparison products in its introduction.
    # Such a sentence can be explicit about *another* product's form factor, so
    # it must not become an automatic decision for the target product.  Leave
    # it to the identity-aware LLM path unless the evidence paragraph itself
    # matches the target model.
    if not matching:
        return None
    return {
        "product_id": source["product_id"],
        "category_raw": source["legacy_category"],
        "category_current": ALLOWED_LABELS[hint],
        "proposed_category": ALLOWED_LABELS[hint],
        "status": "accepted",
        "basis": "deterministic_explicit_rule",
        "confidence": "high",
        "evidence_quote": quote,
        "evidence_segment_id": matching[0],
        "report_id": source["report_id"],
        "source_url": source["source_url"],
        "classifier_version": CLASSIFIER_VERSION,
        "review_reason": "原文包含唯一、明确且可逐字回查的产品形态表述。",
    }


def reviewed_record(source: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    accepted = result["pilot_review_status"] in {
        "high_confidence_candidate",
        "medium_confidence_candidate",
    }
    current = result["resolved_category_label"] if accepted else PENDING_CATEGORY
    return {
        "product_id": source["product_id"],
        "category_raw": source["legacy_category"],
        "category_current": current,
        "proposed_category": result["llm_category_label"],
        "status": "accepted" if accepted else "needs_review",
        "basis": result["classification_basis"],
        "confidence": result["classification_confidence"],
        "evidence_quote": result["llm_evidence_quote"] if accepted else "",
        "evidence_segment_id": result["resolved_evidence_segment_id"] if accepted else "",
        "report_id": source["report_id"],
        "source_url": source["source_url"],
        "classifier_version": CLASSIFIER_VERSION,
        "review_reason": result["llm_rationale"],
    }


def pending_record(product: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "product_id": clean_text(product.get("canonical_id")),
        "category_raw": legacy_category_of(product),
        "category_current": PENDING_CATEGORY,
        "proposed_category": PENDING_CATEGORY,
        "status": "needs_review",
        "basis": reason,
        "confidence": "review",
        "evidence_quote": "",
        "evidence_segment_id": "",
        "report_id": "",
        "source_url": "",
        "classifier_version": CLASSIFIER_VERSION,
        "review_reason": reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_ROOT / CLASSIFIER_VERSION))
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-review",
        action="store_true",
        help="retry existing needs-review rows; default daily mode only classifies new products",
    )
    args = parser.parse_args()

    output_path = earphone_category_overrides_path(for_write=True)
    try:
        existing_payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        existing_payload = {}
    existing_by_id = {
        clean_text(item.get("product_id")): item
        for item in existing_payload.get("items") or []
        if isinstance(item, dict) and clean_text(item.get("product_id"))
    }

    all_products = load_products()
    current_product_ids = {
        clean_text(product.get("canonical_id")) for product in all_products
    }
    reused: list[dict[str, Any]] = []
    reused_ids: set[str] = set()
    for product_id in sorted(current_product_ids):
        item = existing_by_id.get(product_id)
        if not item:
            continue
        if args.retry_review and item.get("status") == "needs_review":
            continue
        reused.append(item)
        reused_ids.add(product_id)

    candidates, profile = build_candidates()
    direct: list[dict[str, Any]] = []
    llm_sources: list[dict[str, Any]] = []
    for source in candidates:
        if source["product_id"] in reused_ids:
            continue
        record = direct_record(source)
        (direct if record else llm_sources).append(record or source)

    work_dir = Path(args.work_dir).resolve()
    raw_dir = work_dir / "raw_batches"
    raw_dir.mkdir(parents=True, exist_ok=True)
    api_key = ""
    api_url = ""
    model = "not_required"
    if llm_sources:
        api_key, api_url, model = model_config(Path(args.env_file).resolve())
    else:
        model = str((existing_payload.get("meta") or {}).get("model") or "not_required")
    llm_by_id: dict[str, dict[str, Any]] = {}
    usage = Counter()
    size = max(1, args.batch_size)
    workers = max(1, min(args.workers, 6))

    def run_initial_batch(batch_index: int, batch: list[dict[str, Any]]):
        batch_usage = Counter()
        raw_path = raw_dir / f"batch_{batch_index:03d}.json"
        if args.resume and raw_path.exists():
            response = json.loads(raw_path.read_text(encoding="utf-8"))
        else:
            response = llm_batch(api_key, api_url, model, batch)
            safe_json(raw_path, response)
        add_usage(batch_usage, response)
        by_id = parsed_items_by_id(response)
        for source in batch:
            product_id = source["product_id"]
            if product_id not in by_id:
                retry_path = raw_dir / f"batch_{batch_index:03d}_{product_id}_retry.json"
                if args.resume and retry_path.exists():
                    retry = json.loads(retry_path.read_text(encoding="utf-8"))
                else:
                    retry = llm_batch(api_key, api_url, model, [source])
                    safe_json(retry_path, retry)
                add_usage(batch_usage, retry)
                by_id.update(parsed_items_by_id(retry))
        return batch_index, len(batch), by_id, batch_usage

    batch_jobs = [
        (start // size + 1, llm_sources[start : start + size])
        for start in range(0, len(llm_sources), size)
    ]
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_initial_batch, batch_index, batch): (batch_index, batch)
            for batch_index, batch in batch_jobs
        }
        for future in as_completed(futures):
            batch_index, batch_count, by_id, batch_usage = future.result()
            llm_by_id.update(by_id)
            usage.update(batch_usage)
            completed += batch_count
            print(
                f"[category] {completed}/{len(llm_sources)} llm candidates "
                f"(batch {batch_index}/{len(batch_jobs)})",
                flush=True,
            )

    preliminary = [
        validate_result(source, llm_by_id.get(source["product_id"]))
        for source in llm_sources
    ]
    repair_sources = [
        source
        for source, result in zip(llm_sources, preliminary)
        if result["llm_category_v2"] not in {"needs_review", "invalid_output"}
        and not result["evidence_exact"]
    ]
    def run_repair_batch(batch_index: int, batch: list[dict[str, Any]]):
        raw_path = raw_dir / f"evidence_repair_{batch_index:03d}.json"
        if args.resume and raw_path.exists():
            response = json.loads(raw_path.read_text(encoding="utf-8"))
        else:
            response = llm_batch(
                api_key,
                api_url,
                model,
                batch,
                system_prompt=EVIDENCE_REPAIR_PROMPT,
            )
            safe_json(raw_path, response)
        batch_usage = Counter()
        add_usage(batch_usage, response)
        return batch_index, len(batch), parsed_items_by_id(response), batch_usage

    repair_jobs = [
        (start // size + 1, repair_sources[start : start + size])
        for start in range(0, len(repair_sources), size)
    ]
    repaired = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_repair_batch, batch_index, batch): (batch_index, batch)
            for batch_index, batch in repair_jobs
        }
        for future in as_completed(futures):
            batch_index, batch_count, by_id, batch_usage = future.result()
            llm_by_id.update(by_id)
            usage.update(batch_usage)
            repaired += batch_count
            print(
                f"[category evidence repair] {repaired}/{len(repair_sources)} "
                f"(batch {batch_index}/{len(repair_jobs)})",
                flush=True,
            )

    llm_records = [
        reviewed_record(source, validate_result(source, llm_by_id.get(source["product_id"])))
        for source in llm_sources
    ]

    eligible_ids = {item["product_id"] for item in candidates}
    missing = [
        pending_record(product, "missing_cached_report")
        for product in all_products
        if legacy_category_of(product) in TARGET_LEGACY_CATEGORIES
        and clean_text(product.get("canonical_id")) not in eligible_ids
        and clean_text(product.get("canonical_id")) not in reused_ids
    ]
    items = sorted([*reused, *direct, *llm_records, *missing], key=lambda item: item["product_id"])
    counts = Counter(item["category_current"] for item in items)
    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "classifier_version": CLASSIFIER_VERSION,
            "prompt_version": PROMPT_VERSION,
            "model": model,
            "policy": "fine category is primary; legacy category is provenance only",
            "profile": profile,
            "direct_rule_products": len(direct),
            "llm_products": len(llm_records),
            "reused_products": len(reused),
            "missing_source_products": len(missing),
            "usage": dict(usage),
            "distribution": dict(counts),
        },
        "items": items,
    }
    safe_json(output_path, payload)
    safe_json(work_dir / "classification_summary.json", payload["meta"])
    print(json.dumps({"output": str(output_path), **payload["meta"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
