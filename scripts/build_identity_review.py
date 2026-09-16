#!/usr/bin/env python3
"""Build an auditable queue for identities unsafe for automatic price lookup."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import sys

sys.path.insert(0, str(ROOT))

from core.paths import STAGING, products_index_path  # noqa: E402
from core.products import identity_review_reason  # noqa: E402


def main() -> None:
    payload = json.loads(products_index_path().read_text(encoding="utf-8"))
    reasons: Counter[str] = Counter()
    items = []
    for product in payload.get("products") or []:
        reason = identity_review_reason(
            str(product.get("brand") or ""),
            str(product.get("model") or ""),
            str(product.get("title") or ""),
        )
        if not reason:
            continue
        reasons[reason] += 1
        items.append(
            {
                "canonical_id": product.get("canonical_id"),
                "brand": product.get("brand") or "",
                "model": product.get("model") or "",
                "category": product.get("category") or "",
                "first_seen": product.get("first_seen") or "",
                "report_count": product.get("report_count") or 0,
                "video_count": product.get("video_count") or 0,
                "reason": reason,
            }
        )

    items.sort(key=lambda x: (x["reason"], x["first_seen"], x["canonical_id"] or ""), reverse=True)
    result = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy": "Only resolved single-SKU identities may enter automatic official-price lookup.",
        "summary": {"total": len(items), "by_reason": dict(sorted(reasons.items()))},
        "items": items,
    }
    STAGING.mkdir(parents=True, exist_ok=True)
    out_path = STAGING / "identity_review.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
