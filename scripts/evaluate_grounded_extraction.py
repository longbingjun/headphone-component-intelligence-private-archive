#!/usr/bin/env python3
"""Compare deterministic extraction with the production hybrid pipeline."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.extract.bom_parameters import (
    deterministic_candidates as deterministic_bom,
    validate_bom_parameters,
)
from core.extract.consumer_claims import (
    deterministic_candidates as deterministic_claims,
    extract_consumer_source,
    validate_consumer_claims,
)
from core.extract.unboxing_summary import (
    MODULE_KEYS,
    collect_unboxing_source,
    deterministic_candidates as deterministic_unboxing,
    validate_unboxing_summary,
)
from core.paths import products_dir, unboxing_enrich_dir
from scripts.enrich_product_details import (
    HTML_CACHE,
    _llm_candidates,
    _merge_bom,
    _merge_evidence_records,
    _preferred_report_id,
    build_model_payload,
)


DEFAULT_PRODUCTS = (
    "sony--linkbuds-clip",
    "apple--airpods-4-anc",
    "huawei--freebuds-pro-4",
    "nothing--ear-2",
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _parameter_count(rows: dict[str, list[dict]]) -> int:
    return sum(len(items) for items in rows.values())


def _module_count(rows: dict[str, list[dict]]) -> int:
    return sum(len(rows.get(key) or []) for key in MODULE_KEYS)


def evaluate(product_id: str) -> dict:
    product_path = products_dir() / f"{product_id}.json"
    if not product_path.is_file():
        return {"product_id": product_id, "status": "missing_product"}
    product = _read(product_path)
    report_id = _preferred_report_id(product)
    html_path = HTML_CACHE / f"{report_id}.html"
    html = html_path.read_text(encoding="utf-8") if html_path.is_file() else ""
    paragraphs = extract_consumer_source(html) if html else []
    bom_rows = list(product.get("technical_facts") or product.get("teardown_inventory") or [])
    unboxing_path = unboxing_enrich_dir() / f"{report_id}.json"
    appearance_source = (
        collect_unboxing_source(_read(unboxing_path))
        if unboxing_path.is_file()
        else {key: [] for key in MODULE_KEYS}
    )

    baseline_claims, baseline_claim_rejected = validate_consumer_claims(
        deterministic_claims(paragraphs), paragraphs, bom_rows
    )
    baseline_parameters, baseline_parameter_rejected = validate_bom_parameters(
        deterministic_bom(bom_rows), bom_rows
    )
    baseline_modules, baseline_module_rejected = validate_unboxing_summary(
        deterministic_unboxing(appearance_source), appearance_source
    )

    started = time.perf_counter()
    raw, model = _llm_candidates(
        build_model_payload(product, product_id, paragraphs, bom_rows, appearance_source)
    )
    latency = round(time.perf_counter() - started, 3)

    model_claims, rejected_claims = validate_consumer_claims(
        raw.get("consumer_claims") or [], paragraphs, bom_rows
    )
    model_parameters, rejected_parameters = validate_bom_parameters(
        {"items": raw.get("bom_items") or []}, bom_rows
    )
    model_modules, rejected_modules = validate_unboxing_summary(
        raw.get("unboxing") or {}, appearance_source
    )

    hybrid_claims = _merge_evidence_records(
        baseline_claims, model_claims, limit=max(7, len(baseline_claims))
    )
    hybrid_parameters = _merge_bom(model_parameters, baseline_parameters)
    hybrid_modules = {
        key: _merge_evidence_records(
            baseline_modules.get(key) or [],
            model_modules.get(key) or [],
            limit=max(6, len(baseline_modules.get(key) or [])),
        )
        for key in MODULE_KEYS
    }

    return {
        "product_id": product_id,
        "report_id": report_id,
        "model": model,
        "status": "ok",
        "latency_seconds": latency,
        "source": {
            "paragraphs": len(paragraphs),
            "bom_rows": len(bom_rows),
            "appearance_lines": sum(len(appearance_source.get(key) or []) for key in MODULE_KEYS),
        },
        "baseline": {
            "claims": len(baseline_claims),
            "parameters": _parameter_count(baseline_parameters),
            "appearance_bullets": _module_count(baseline_modules),
            "rejected": len(baseline_claim_rejected)
            + len(baseline_parameter_rejected)
            + len(baseline_module_rejected),
        },
        "model_after_validation": {
            "claims": len(model_claims),
            "parameters": _parameter_count(model_parameters),
            "appearance_bullets": _module_count(model_modules),
            "rejected": len(rejected_claims)
            + len(rejected_parameters)
            + len(rejected_modules),
        },
        "hybrid": {
            "claims": len(hybrid_claims),
            "parameters": _parameter_count(hybrid_parameters),
            "appearance_bullets": _module_count(hybrid_modules),
        },
        "validator_rejections": {
            "claims": rejected_claims,
            "parameters": rejected_parameters,
            "appearance": rejected_modules,
        },
        "accepted_samples": {
            "claims": model_claims[:2],
            "parameters": [
                {"key": key, "values": values[:2]}
                for key, values in model_parameters.items()
                if values
            ][:3],
            "appearance": {
                key: (model_modules.get(key) or [])[:1] for key in MODULE_KEYS
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", default=",".join(DEFAULT_PRODUCTS))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    product_ids = [value.strip() for value in args.products.split(",") if value.strip()]
    rows: list[dict] = []
    for index, product_id in enumerate(product_ids, 1):
        print(f"[eval] {index}/{len(product_ids)} {product_id}", flush=True)
        try:
            rows.append(evaluate(product_id))
        except Exception as exc:
            rows.append({"product_id": product_id, "status": "failed", "error": str(exc)[:500]})

    ok = [row for row in rows if row.get("status") == "ok"]
    summary = {
        "products_requested": len(product_ids),
        "products_succeeded": len(ok),
        "api_calls": len(ok),
        "latency_seconds": round(sum(row["latency_seconds"] for row in ok), 3),
        "baseline": {
            key: sum(row["baseline"][key] for row in ok)
            for key in ("claims", "parameters", "appearance_bullets")
        },
        "model_after_validation": {
            key: sum(row["model_after_validation"][key] for row in ok)
            for key in ("claims", "parameters", "appearance_bullets", "rejected")
        },
        "hybrid": {
            key: sum(row["hybrid"][key] for row in ok)
            for key in ("claims", "parameters", "appearance_bullets")
        },
    }
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": summary,
        "products": rows,
    }
    output = args.output or (
        Path("runtime") / "manual-tests" / "grounded-extraction-small-sample.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
