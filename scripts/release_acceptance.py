"""Create a machine-readable local or server deployment acceptance report."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.release_metadata import (  # noqa: E402
    DATA_CONTRACT_VERSION,
    EXPECTED_ALEMBIC_HEAD,
    RELEASE_COMMIT,
)


def run(command: list[str], *, cwd: Path = ROOT) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "command": command,
        "passed": completed.returncode == 0,
        "exit_code": completed.returncode,
        "started_at": started.isoformat(),
        "stdout_tail": completed.stdout[-6000:],
        "stderr_tail": completed.stderr[-6000:],
    }


def git_value(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def environment_checks() -> list[dict[str, Any]]:
    database_url = os.getenv("DATABASE_URL", "")
    checks = [
        {
            "name": "postgresql_database",
            "passed": database_url.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")),
            "detail": "DATABASE_URL must point to PostgreSQL",
        },
        {
            "name": "minio_backend",
            "passed": os.getenv("STORAGE_BACKEND", "").lower() == "minio",
            "detail": "STORAGE_BACKEND=minio",
        },
        {
            "name": "minio_connection",
            "passed": all(
                os.getenv(name, "").strip()
                for name in ("MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_BUCKET")
            ),
            "detail": "MinIO endpoint, credentials and bucket are injected",
        },
        {
            "name": "ffmpeg_available",
            "passed": shutil.which("ffmpeg") is not None,
            "detail": shutil.which("ffmpeg") or "not found",
        },
        {
            "name": "npm_available_for_new_product_page",
            "passed": shutil.which("npm") is not None,
            "detail": shutil.which("npm") or "not found",
        },
    ]
    return checks


def migration_check() -> dict[str, Any]:
    try:
        from sqlalchemy import text

        from server.db import get_engine

        with get_engine().connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        return {
            "name": "database_migration_head",
            "passed": version == EXPECTED_ALEMBIC_HEAD,
            "detail": (
                f"actual={version or 'missing alembic_version'}; "
                f"expected={EXPECTED_ALEMBIC_HEAD}"
            ),
        }
    except Exception as exc:
        return {
            "name": "database_migration_head",
            "passed": False,
            "detail": f"{type(exc).__name__}: {exc}",
        }


def release_commit_check() -> dict[str, Any]:
    injected = os.getenv("APP_COMMIT_SHA", "").strip()
    passed = bool(injected) and RELEASE_COMMIT not in {"", "local", "unknown"}
    return {
        "name": "immutable_release_commit",
        "passed": passed,
        "detail": f"APP_COMMIT_SHA={injected or 'missing'}; runtime={RELEASE_COMMIT}",
    }


def application_data_check() -> dict[str, Any]:
    try:
        from server.db import session_scope
        from server.release_validation import validate_release_data

        with session_scope() as session:
            result = validate_release_data(session)
        return {
            "name": "post_migration_data_integrity",
            "passed": bool(result.get("passed")),
            "detail": json.dumps(
                {
                    "migration_status": result.get("migration_status"),
                    "counts": result.get("counts"),
                    "blockers": result.get("blockers"),
                    "warnings": result.get("warnings"),
                },
                ensure_ascii=False,
            ),
        }
    except Exception as exc:
        return {
            "name": "post_migration_data_integrity",
            "passed": False,
            "detail": f"{type(exc).__name__}: {exc}",
        }


def generated_data_version_check() -> dict[str, Any]:
    data_dir = ROOT / "web" / "public" / "data"
    required = [
        data_dir / "release-meta.json",
        data_dir / "coverage-summary.json",
        data_dir / "products" / "index.json",
        data_dir / "bom-analysis" / "manifest.json",
        data_dir / "brand-supply-chain" / "manifest.json",
        data_dir / "supplier-sourcing" / "manifest.json",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        return {
            "name": "generated_data_versions",
            "passed": False,
            "detail": f"missing required generated files: {', '.join(missing)}",
        }
    try:
        payloads = [json.loads(path.read_text(encoding="utf-8-sig")) for path in required]
        stamps = [payload.get("_release") or {} for payload in payloads]
        data_versions = {str(stamp.get("data_version") or "") for stamp in stamps}
        schema_versions = {str(stamp.get("schema_version") or "") for stamp in stamps}
        contract_versions = {str(stamp.get("contract_version") or "") for stamp in stamps}
        generated_times = {str(stamp.get("generated_at") or "") for stamp in stamps}
        passed = (
            len(data_versions) == 1
            and "" not in data_versions
            and schema_versions == {EXPECTED_ALEMBIC_HEAD}
            and contract_versions == {DATA_CONTRACT_VERSION}
            and len(generated_times) == 1
            and "" not in generated_times
        )
        return {
            "name": "generated_data_versions",
            "passed": passed,
            "detail": (
                f"data_versions={sorted(data_versions)}; "
                f"schema_versions={sorted(schema_versions)}; "
                f"contract_versions={sorted(contract_versions)}; "
                f"generated_at={sorted(generated_times)}"
            ),
        }
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "name": "generated_data_versions",
            "passed": False,
            "detail": f"{type(exc).__name__}: {exc}",
        }


CATALOG_SYNC_FIELDS = (
    "canonical_id",
    "brand",
    "model",
    "category",
    "category_status",
    "report_count",
    "video_count",
)


def catalog_projection(payload: dict[str, Any]) -> dict[str, tuple[Any, ...]]:
    """Return the published catalog contract at one row per canonical product."""

    return {
        str(item.get("canonical_id") or ""): tuple(item.get(field) for field in CATALOG_SYNC_FIELDS)
        for item in payload.get("products") or []
        if isinstance(item, dict) and str(item.get("canonical_id") or "")
    }


def catalog_data_sync_check() -> dict[str, Any]:
    """Block releases whose UI catalog is older than the current curated data."""

    paths = {
        "curated": ROOT / "data" / "curated" / "products" / "index.json",
        "web": ROOT / "web" / "public" / "data" / "products" / "index.json",
        "site": ROOT / "site" / "data" / "products" / "index.json",
    }
    missing = [str(path.relative_to(ROOT)) for path in paths.values() if not path.is_file()]
    if missing:
        return {
            "name": "catalog_data_sync",
            "passed": False,
            "detail": f"missing catalog indexes: {', '.join(missing)}",
        }
    try:
        payloads = {
            name: json.loads(path.read_text(encoding="utf-8-sig"))
            for name, path in paths.items()
        }
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "name": "catalog_data_sync",
            "passed": False,
            "detail": f"{type(exc).__name__}: {exc}",
        }

    projections = {name: catalog_projection(payload) for name, payload in payloads.items()}
    curated = projections["curated"]
    web = projections["web"]
    site = projections["site"]
    mismatch_ids = sorted(
        product_id
        for product_id in set(curated) | set(web) | set(site)
        if curated.get(product_id) != web.get(product_id)
        or web.get(product_id) != site.get(product_id)
    )
    pending = {
        name: sum(row[3] == "待细分耳机" for row in projection.values())
        for name, projection in projections.items()
    }
    return {
        "name": "catalog_data_sync",
        "passed": not mismatch_ids,
        "detail": (
            f"counts={{{', '.join(f'{name}:{len(rows)}' for name, rows in projections.items())}}}; "
            f"pending={pending}; mismatches={len(mismatch_ids)}; "
            f"sample={mismatch_ids[:10]}"
        ),
    }


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("local", "deployment"), default="local")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "runtime" / "acceptance" / "release-acceptance.json",
    )
    parser.add_argument("--skip-web-build", action="store_true")
    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help="quickly verify curated, Web and built-site catalog synchronization",
    )
    return parser.parse_args()


def main() -> int:
    args = arguments()
    if args.catalog_only:
        checks = [generated_data_version_check(), catalog_data_sync_check()]
        passed = all(item.get("passed") for item in checks)
        print(json.dumps({"passed": passed, "checks": checks}, ensure_ascii=False, indent=2))
        return 0 if passed else 1

    checks: list[dict[str, Any]] = []
    checks.append(run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]))
    checks.append(run([sys.executable, "-m", "server.cli", "migrate"]))
    if not args.skip_web_build:
        npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
        checks.append(run([npm, "run", "build:minio"], cwd=ROOT / "web"))
        checks.append(run([sys.executable, "scripts/verify_static_site.py"]))
    checks.append(generated_data_version_check())
    checks.append(catalog_data_sync_check())
    if args.mode == "deployment":
        checks.extend(environment_checks())
        checks.append(release_commit_check())
        checks.append(migration_check())
        checks.append(application_data_check())

    branch = git_value("branch", "--show-current")
    commit = git_value("rev-parse", "HEAD")
    dirty = bool(git_value("status", "--porcelain"))
    passed = all(item.get("passed") for item in checks)
    report = {
        "schema": "52audio-release-acceptance/v2",
        "mode": args.mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git": {"branch": branch, "commit": commit, "dirty": dirty},
        "passed": passed,
        "checks": checks,
        "handoff_rule": "IT must build and deploy this exact git commit; runtime data is not stored in Git.",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": passed, "report": str(args.report), "git": report["git"]}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
