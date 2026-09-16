#!/usr/bin/env python3
"""Verify that the imported BOM corpus supports cross-product analysis."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.models import BomItem, BomItemParameter, Product  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--ascii", action="store_true", help="escape non-ASCII output for terminals")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    engine = create_engine(args.database_url)
    with engine.connect() as connection:
        count_row = connection.execute(
            select(
                select(func.count()).select_from(Product).scalar_subquery().label("products"),
                select(func.count()).select_from(BomItem).scalar_subquery().label("bom_items"),
                select(func.count()).select_from(BomItemParameter).scalar_subquery().label("parameters"),
                select(func.count())
                .select_from(BomItem)
                .where(BomItem.material != "")
                .scalar_subquery()
                .label("material_items"),
            )
        ).mappings().one()
        top_components = connection.execute(
            select(
                BomItem.component_key,
                func.count(func.distinct(BomItem.product_id)).label("products"),
                func.count(BomItem.id).label("rows"),
            )
            .group_by(BomItem.component_key)
            .order_by(func.count(func.distinct(BomItem.product_id)).desc())
            .limit(max(1, args.limit))
        ).mappings().all()
        material_examples = connection.execute(
            select(
                Product.category,
                Product.brand.label("product_brand"),
                Product.first_seen,
                Product.id.label("product_id"),
                BomItem.component,
                BomItem.component_key,
                BomItem.material,
                BomItem.source_text,
                func.count(BomItem.id).label("rows"),
            )
            .join(Product, Product.id == BomItem.product_id)
            .where(BomItem.material != "")
            .group_by(
                Product.category,
                Product.brand,
                Product.first_seen,
                Product.id,
                BomItem.component,
                BomItem.component_key,
                BomItem.material,
                BomItem.source_text,
            )
            .order_by(func.count(BomItem.id).desc())
            .limit(max(1, args.limit))
        ).mappings().all()
        evidence_coverage = connection.execute(
            select(
                func.count(BomItem.id).label("items"),
                func.count(BomItem.brand).filter(BomItem.brand != "").label("branded"),
                func.count(BomItem.model).filter(BomItem.model != "").label("modeled"),
                func.count(BomItem.source_text)
                .filter(BomItem.source_text != "")
                .label("evidenced"),
            )
        ).mappings().one()

    payload = {
        "counts": dict(count_row),
        "evidence_coverage": dict(evidence_coverage),
        "top_component_keys": [dict(row) for row in top_components],
        "material_examples": [dict(row) for row in material_examples],
        "supported_dimensions": [
            "product.category",
            "product.brand",
            "product.first_seen(year)",
            "bom_item.component_key",
            "bom_item.material",
            "bom_item.brand/model",
        ],
    }
    print(json.dumps(payload, ensure_ascii=args.ascii, indent=2, default=str))


if __name__ == "__main__":
    main()
