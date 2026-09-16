#!/usr/bin/env python3
"""Copy legacy derived data into raw/staging/curated without overwriting conflicts."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

MAPPINGS = (
    (DATA / "products", DATA / "curated" / "products"),
    (DATA / "compare", DATA / "curated" / "compare"),
    (DATA / "matrix", DATA / "curated" / "matrix"),
)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def mappings() -> list[tuple[Path, Path]]:
    result = list(MAPPINGS)
    enrich = DATA / "enrich"
    if enrich.exists():
        result.extend((folder, DATA / "staging" / folder.name) for folder in enrich.iterdir() if folder.is_dir())
    return result


def migrate(*, apply: bool) -> dict:
    stats = {"missing_sources": 0, "already_identical": 0, "copied": 0, "conflicts": []}
    for source, target in mappings():
        if not source.exists():
            stats["missing_sources"] += 1
            continue
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            destination = target / path.relative_to(source)
            if destination.exists():
                if digest(path) == digest(destination):
                    stats["already_identical"] += 1
                else:
                    stats["conflicts"].append(str(path.relative_to(ROOT)))
                continue
            if apply:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
            stats["copied"] += 1
    stats["mode"] = "apply" if apply else "dry-run"
    stats["conflict_count"] = len(stats["conflicts"])
    stats["conflicts"] = stats["conflicts"][:50]
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="copy missing files into canonical directories")
    args = parser.parse_args()
    print(json.dumps(migrate(apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
