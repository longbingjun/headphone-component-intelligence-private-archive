"""Run both product-gate branches against an existing local video artifact set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.models import Base, Product, Report  # noqa: E402
from server.storage import LocalObjectStorage  # noqa: E402
from server.video_intelligence import (  # noqa: E402
    discover_video,
    publish_artifacts,
    serialize_product_video_intelligence,
)


def arguments() -> argparse.Namespace:
    cli = argparse.ArgumentParser(description="Isolated local video-intelligence integration test")
    cli.add_argument("--source-info", type=Path, required=True)
    cli.add_argument("--product-json", type=Path, required=True)
    cli.add_argument("--artifacts-dir", type=Path, required=True)
    cli.add_argument("--output-dir", type=Path, required=True)
    return cli.parse_args()


def engine(database_path: Path | None = None):
    url = (
        f"sqlite+pysqlite:///{database_path.as_posix()}"
        if database_path is not None
        else "sqlite+pysqlite:///:memory:"
    )
    result = create_engine(url)
    Base.metadata.create_all(result)
    return result


def main() -> int:
    args = arguments()
    source = json.loads(args.source_info.read_text(encoding="utf-8"))
    product_payload = json.loads(args.product_json.read_text(encoding="utf-8"))
    metadata = {
        **source,
        "brand": product_payload["brand"],
        "model": product_payload["model"],
        "category": product_payload["category"],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)

    covered_engine = engine()
    with Session(covered_engine) as session:
        product = Product(
            id=product_payload["canonical_id"],
            brand=product_payload["brand"],
            model=product_payload["model"],
            category=product_payload["category"],
            payload=product_payload,
        )
        report = Report(
            id="manual-covered-report",
            title=f"{product_payload['model']} 完整拆解报告",
            brand=product_payload["brand"],
            model=product_payload["model"],
            category=product_payload["category"],
            payload={
                "views": {
                    "cost": {
                        "teardown_inventory": [
                            {"component": "主控芯片"},
                            {"component": "电源管理芯片"},
                            {"component": "电池"},
                            {"component": "扬声器单元"},
                            {"component": "麦克风"},
                        ]
                    },
                    "structure": {"report_image_urls": [{"url": "https://example.invalid/evidence.jpg"}]},
                }
            },
        )
        product.reports.append(report)
        session.add(product)
        session.commit()
        covered = discover_video(session, metadata).to_dict()
        session.commit()

    database_path = args.output_dir / "video-uncovered.db"
    if database_path.exists():
        database_path.unlink()
    uncovered_engine = engine(database_path)
    with Session(uncovered_engine) as session:
        queued = discover_video(session, metadata).to_dict()
        published = publish_artifacts(
            session,
            video_id=queued["video_id"],
            artifacts_dir=args.artifacts_dir,
            storage=LocalObjectStorage(args.output_dir / "objects"),
        ).to_dict()
        session.commit()
        product = session.get(Product, published["product_id"])
        product_view = dict(product.payload or {})
        product_view["video_intelligence"] = serialize_product_video_intelligence(
            session, product.id
        )

    report = {
        "covered_product_branch": covered,
        "uncovered_product_branch": queued,
        "publication": published,
        "source_video_retained": any(args.output_dir.rglob("*.mp4")),
        "database": database_path.name,
        "published_product_json": "published_product.json",
    }
    (args.output_dir / "published_product.json").write_text(
        json.dumps(product_view, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "test_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
