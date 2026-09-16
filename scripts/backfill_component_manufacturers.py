#!/usr/bin/env python3
"""Recompute auditable component manufacturer fields after rule updates."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.component_analytics import manufacturer_from_evidence  # noqa: E402
from server.models import BomItem  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    engine = create_engine(args.database_url)
    changed = 0
    with Session(engine) as session:
        for item in session.scalars(select(BomItem).order_by(BomItem.id)).yield_per(500):
            manufacturer, basis, quote = manufacturer_from_evidence(
                item.source_text,
                item.brand,
                component_key=item.component_key,
            )
            values = (manufacturer, basis, quote)
            current = (
                item.manufacturer,
                item.manufacturer_basis,
                item.manufacturer_evidence_quote,
            )
            if values == current:
                continue
            item.manufacturer, item.manufacturer_basis, item.manufacturer_evidence_quote = values
            changed += 1
        session.commit()
    print(f"component manufacturer rows updated: {changed}")


if __name__ == "__main__":
    main()
