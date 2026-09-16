"""Fail the build when product-facing unboxing text contains UTF-8 mojibake."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.paths import products_dir  # noqa: E402
from core.text_quality import contains_c1_controls, iter_display_strings  # noqa: E402


def _read_json(path: Path) -> dict:
    read_path = path
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        read_path = Path(f"\\\\?\\{path.resolve()}")
    payload = json.loads(read_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def find_invalid_unboxing_text() -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for path in sorted(products_dir().glob("*.json")):
        if path.name == "index.json":
            continue
        product = _read_json(path)
        for field, value in iter_display_strings(product.get("unboxing") or {}):
            if contains_c1_controls(value):
                failures.append(
                    {
                        "product_id": str(product.get("canonical_id") or path.stem),
                        "field": field,
                        "sample": value[:100],
                    }
                )
    return failures


def main() -> None:
    failures = find_invalid_unboxing_text()
    if failures:
        print(json.dumps({"status": "failed", "count": len(failures), "items": failures[:50]}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps({"status": "ok", "invalid_unboxing_strings": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
