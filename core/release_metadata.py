"""Shared release metadata for generated data and deployment checks."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_CONTRACT_VERSION = "52audio-web-data/v2"
EXPECTED_ALEMBIC_HEAD = "0008_data_migration_history"
RELEASE_GENERATED_AT = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _git_commit() -> str:
    injected = os.getenv("APP_COMMIT_SHA", "").strip()
    if injected:
        return injected[:40]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        return completed.stdout.strip() if completed.returncode == 0 else "local"
    except (OSError, subprocess.SubprocessError):
        return "local"


RELEASE_COMMIT = _git_commit()
DATA_VERSION = (
    datetime.fromisoformat(RELEASE_GENERATED_AT)
    .astimezone(timezone.utc)
    .strftime("%Y%m%dT%H%M%SZ")
    + f"-{RELEASE_COMMIT}"
)


def release_stamp() -> dict[str, str]:
    """Return metadata stamped onto every generated JSON file in one build."""

    return {
        "data_version": DATA_VERSION,
        "schema_version": EXPECTED_ALEMBIC_HEAD,
        "contract_version": DATA_CONTRACT_VERSION,
        "generated_at": RELEASE_GENERATED_AT,
    }
