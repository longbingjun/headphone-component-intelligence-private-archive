"""Build explainable research priority fields for the lightweight product index.

This intentionally does not change product prices or product-detail records.  It
only enriches ``data/curated/products/index.json`` so the discovery UI and the official
price-research queue can focus on products that are both recent and traceable to
an official page.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import sys

sys.path.insert(0, str(ROOT))

from core.paths import official_enrich_dir, products_index_path, write_products_index  # noqa: E402


PRIORITY_LAUNCH_START = date(2025, 1, 1)
TRUSTED_PRICE_EVIDENCE_KINDS = {
    "official_product_page",
    "brand_official_news",
    "brand_official_social_post",
    "brand_announcement_report",
}


def parse_product_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def official_page_status(canonical_id: str) -> tuple[str, str, bool]:
    """Return page state, a traceable URL, and whether its price is verified.

    A live product page alone does not verify a price.  Conversely, a quoted
    launch price from a traceable official announcement is enough to verify a
    product even when the listing page has later been removed.
    """
    path = official_enrich_dir() / f"{canonical_id}.json"
    if not path.exists():
        return "unknown", "", False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown", "", False

    url = str(
        payload.get("official_url")
        or payload.get("vmall_url")
        or payload.get("price_source_url")
        or ""
    ).strip()
    error = str(payload.get("fetch_error") or "").lower()
    has_price = payload.get("msrp_cny") is not None
    evidence_kind = str(payload.get("price_evidence_kind") or "").strip()
    verified_price = has_price and (
        evidence_kind in TRUSTED_PRICE_EVIDENCE_KINDS
        or (not evidence_kind and bool(payload.get("official_url") or payload.get("vmall_url")))
    )
    if not url:
        return "not_found", "", verified_price
    # A known 404 is not evidence that the product is still present.  Rate-limit
    # errors still retain the official URL, so they remain reviewable rather than
    # being incorrectly downgraded to not-found.
    if "404" in error or "not found" in error or "mismatch" in error or "ambiguous" in error:
        return "not_found", "", False
    return "found", url, verified_price


def priority_for(product: dict, *, cutoff: date) -> tuple[str, int, str, str, bool]:
    first_seen = parse_product_date(product.get("launch_date")) or parse_product_date(product.get("first_seen"))
    canonical_id = str(product.get("canonical_id") or "")
    status, url, verified_price = official_page_status(canonical_id)
    if first_seen is not None and first_seen >= cutoff and verified_price:
        return "official_current", 1, status, url, True
    if first_seen is not None and first_seen >= cutoff:
        return "recent_pending_check", 2, status, url, False
    return "historical_reference", 3, status, url, verified_price


def build_priority_index(today: date | None = None) -> dict:
    today = today or date.today()
    cutoff = PRIORITY_LAUNCH_START
    path = products_index_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    products = payload.get("products", [])
    if not isinstance(products, list):
        raise ValueError("products index must contain a products list")

    counts: dict[str, int] = {
        "official_current": 0,
        "recent_pending_check": 0,
        "historical_reference": 0,
    }
    for product in products:
        if not isinstance(product, dict):
            continue
        priority, rank, presence, url, verified_price = priority_for(product, cutoff=cutoff)
        product["research_priority"] = priority
        product["priority_rank"] = rank
        product["official_page_status"] = presence
        product["official_page_url"] = url
        product["price_verification_status"] = "verified" if verified_price else "pending"
        counts[priority] += 1

    payload["priority_generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    payload["priority_policy"] = {
        "recent_since": cutoff.isoformat(),
        "rule": "2025年及以后上市产品优先查价；无明确上市时间时仅以最早拆解报告日期作为队列兜底，不作为上市日期展示",
    }
    payload["priority_counts"] = counts
    write_products_index(payload)
    return {"total": len(products), "counts": counts, "recent_since": cutoff.isoformat()}


def main() -> None:
    print(json.dumps(build_priority_index(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
