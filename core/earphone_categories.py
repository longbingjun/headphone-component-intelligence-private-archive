"""Apply the source-backed earphone form-factor taxonomy to product data."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Mapping

from core.paths import earphone_category_overrides_path


LEGACY_COARSE_CATEGORIES = frozenset({"开放式耳机", "真无线耳机TWS"})
FINE_CATEGORIES = frozenset({"耳夹式耳机", "耳挂式耳机", "全入耳式耳机", "半入耳式耳机"})
FIXED_CATEGORIES = frozenset({"头戴式耳机", "有线耳机", "骨传导耳机", "颈挂式蓝牙耳机"})
PENDING_CATEGORY = "待细分耳机"
BUSINESS_CATEGORIES = frozenset({*FINE_CATEGORIES, *FIXED_CATEGORIES, PENDING_CATEGORY})


@lru_cache(maxsize=1)
def load_category_overrides() -> dict[str, dict[str, Any]]:
    path = earphone_category_overrides_path()
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return {
        str(item.get("product_id") or ""): item
        for item in payload.get("items") or []
        if isinstance(item, dict) and item.get("product_id")
    }


def product_category_fields(
    product_id: str,
    category_raw: str,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return traceable category fields while making the fine taxonomy primary."""

    raw = str(category_raw or "").strip()
    record = (overrides if overrides is not None else load_category_overrides()).get(product_id)
    if record:
        current = str(record.get("category_current") or PENDING_CATEGORY).strip()
        if current not in BUSINESS_CATEGORIES:
            current = PENDING_CATEGORY
        return {
            "category": current,
            "category_raw": str(record.get("category_raw") or raw),
            "category_status": str(record.get("status") or "needs_review"),
            "category_basis": str(record.get("basis") or "insufficient_evidence"),
            "category_confidence": str(record.get("confidence") or "review"),
            "category_evidence_quote": str(record.get("evidence_quote") or ""),
            "category_source_report_id": str(record.get("report_id") or ""),
            "category_classifier_version": str(record.get("classifier_version") or ""),
        }
    if raw in FIXED_CATEGORIES or raw in FINE_CATEGORIES:
        return {
            "category": raw,
            "category_raw": raw,
            "category_status": "source_taxonomy",
            "category_basis": "existing_specific_category",
            "category_confidence": "high",
            "category_evidence_quote": "",
            "category_source_report_id": "",
            "category_classifier_version": "",
        }
    return {
        "category": PENDING_CATEGORY if raw in LEGACY_COARSE_CATEGORIES else raw,
        "category_raw": raw,
        "category_status": "needs_review" if raw in LEGACY_COARSE_CATEGORIES else "source_taxonomy",
        "category_basis": "missing_fine_category" if raw in LEGACY_COARSE_CATEGORIES else "existing_category",
        "category_confidence": "review" if raw in LEGACY_COARSE_CATEGORIES else "high",
        "category_evidence_quote": "",
        "category_source_report_id": "",
        "category_classifier_version": "",
    }
