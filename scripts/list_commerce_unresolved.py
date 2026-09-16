#!/usr/bin/env python3
"""列出渠道 enrich 中未解析到价格的产品。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.paths import channel_enrich_dir  # noqa: E402


def main() -> None:
    channel_dir = channel_enrich_dir()
    unresolved: list[dict] = []
    for path in sorted(channel_dir.glob("*.json")):
        if path.name == "example.csv":
            continue
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("price_cny") is None or row.get("price_source") in (None, "", "unresolved", "jd_unresolved"):
            unresolved.append(
                {
                    "canonical_id": row.get("canonical_id", path.stem),
                    "price_source": row.get("price_source"),
                    "live_error": row.get("live_error"),
                    "search_query": row.get("search_query"),
                }
            )
    print(json.dumps({"count": len(unresolved), "items": unresolved}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
