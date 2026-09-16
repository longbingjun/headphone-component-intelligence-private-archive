"""批量为现有 report/video 记录写入 source_layer=technical（默认）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ingest import REPORTS_DIR, VIDEOS_DIR  # noqa: E402

DEFAULTS = {"source_layer": "technical", "source_id": "audio52"}


def tag_file(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    changed = False
    for key, val in DEFAULTS.items():
        if not data.get(key):
            data[key] = val
            changed = True
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def main() -> None:
    n = 0
    for base in (REPORTS_DIR, VIDEOS_DIR):
        if not base.exists():
            continue
        for path in base.glob("*.json"):
            if tag_file(path):
                n += 1
    print(f"Tagged {n} records with source_layer=technical")


if __name__ == "__main__":
    main()
