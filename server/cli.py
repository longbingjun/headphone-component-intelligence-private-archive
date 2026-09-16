from __future__ import annotations

import argparse
import json
from pathlib import Path

from server.db import session_scope
from sqlalchemy import func, select

from server.catalog_reconciliation import reconcile_release_catalog
from server.db import get_engine
from server.exporter import export_repository
from server.importer import import_deployment_seeds, import_repository
from server.migrate import upgrade_database
from server.models import Product
from server.release_validation import validate_release_data
from server.storage import sync_cached_images
from server.tasks import rebuild_site_from_database, run_image_bootstrap, run_refresh


def main() -> None:
    parser = argparse.ArgumentParser(description="PostgreSQL/MinIO runtime operations")
    parser.add_argument(
        "command",
        choices=(
            "migrate",
            "import-data",
            "export-data",
            "reconcile-release-data",
            "validate-release-data",
            "rebuild-site",
            "sync-images",
            "bootstrap-images",
            "refresh",
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply reconcile-release-data; omission performs a read-only dry-run",
    )
    parser.add_argument(
        "--allow-sqlite",
        action="store_true",
        help="allow --apply against SQLite for isolated local migration rehearsal only",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="application-level JSON snapshot directory for export-data",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="optional JSON result path for reconcile-release-data",
    )
    args = parser.parse_args()

    if args.command == "migrate":
        upgrade_database()
        result = {"status": "ok"}
    elif args.command == "import-data":
        upgrade_database()
        with session_scope() as session:
            result = import_repository(session).to_dict()
    elif args.command == "export-data":
        if args.output_dir is None:
            parser.error("export-data requires --output-dir")
        output = args.output_dir.resolve()
        with session_scope() as session:
            result = export_repository(
                session,
                report_output=output / "reports",
                product_output=output / "products",
                video_output=output / "videos",
            ).to_dict()
        result["output_dir"] = str(output)
    elif args.command == "reconcile-release-data":
        upgrade_database()
        if args.apply and get_engine().dialect.name != "postgresql" and not args.allow_sqlite:
            parser.error(
                "--apply is restricted to PostgreSQL; use --allow-sqlite only for an isolated local rehearsal"
            )
        with session_scope() as session:
            result = reconcile_release_catalog(session, apply=args.apply).to_dict()
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
    elif args.command == "rebuild-site":
        result = rebuild_site_from_database()
    elif args.command == "validate-release-data":
        with session_scope() as session:
            result = validate_release_data(session)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
    elif args.command == "sync-images":
        with session_scope() as session:
            result = sync_cached_images(session).to_dict()
    elif args.command == "bootstrap-images":
        upgrade_database()
        with session_scope() as session:
            product_count = session.scalar(select(func.count()).select_from(Product)) or 0
            if product_count == 0:
                import_repository(session)
            else:
                import_deployment_seeds(session)
        result = run_image_bootstrap(trigger="image_bootstrap")
    else:
        result = run_refresh(trigger="manual")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
