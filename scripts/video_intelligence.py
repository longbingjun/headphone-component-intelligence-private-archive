"""Local/worker CLI for the subtitle/OCR video intelligence workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.db import session_scope  # noqa: E402
from server.storage import get_storage  # noqa: E402
from server.video_intelligence import discover_video, publish_artifacts  # noqa: E402
from server.video_pipeline import run_pipeline  # noqa: E402
from server.video_worker import run_once  # noqa: E402


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        description="Discover, gate and publish teardown-video intelligence from visual text."
    )
    commands = cli.add_subparsers(dest="command", required=True)

    discover = commands.add_parser("discover", help="register metadata and run product gate")
    discover.add_argument("--metadata", type=Path, required=True, help="yt-dlp/source JSON")
    discover.add_argument("--brand", default="")
    discover.add_argument("--model", default="")
    discover.add_argument("--category", default="")
    discover.add_argument("--source-id", default="bilibili")

    publish = commands.add_parser("publish", help="import completed OCR/keyframe artifacts")
    publish.add_argument("--video-id", required=True)
    publish.add_argument("--artifacts-dir", type=Path, required=True)

    process_file = commands.add_parser(
        "process-file", help="run subtitle/fact/keyframe extraction on one local video"
    )
    process_file.add_argument("--video", type=Path, required=True)
    process_file.add_argument("--subtitle", type=Path)
    process_file.add_argument("--reuse-segments", type=Path)
    process_file.add_argument("--reuse-events", type=Path)
    process_file.add_argument("--output", type=Path, required=True)
    process_file.add_argument("--sample-interval", type=float, default=0.5)

    commands.add_parser("work-once", help="claim and process at most one database job")
    return cli


def main() -> int:
    args = parser().parse_args()
    if args.command == "discover":
        payload = json.loads(args.metadata.read_text(encoding="utf-8"))
        payload["source_id"] = args.source_id
        if args.brand:
            payload["brand"] = args.brand
        if args.model:
            payload["model"] = args.model
        if args.category:
            payload["category"] = args.category
        with session_scope() as session:
            result = discover_video(session, payload).to_dict()
    elif args.command == "publish":
        with session_scope() as session:
            result = publish_artifacts(
                session,
                video_id=args.video_id,
                artifacts_dir=args.artifacts_dir,
                storage=get_storage(),
            ).to_dict()
    elif args.command == "process-file":
        result = run_pipeline(
            args.video,
            args.output,
            subtitle_path=args.subtitle,
            reuse_segments_path=args.reuse_segments,
            reuse_events_path=args.reuse_events,
            sample_interval=args.sample_interval,
        )
    else:
        result = [item.to_dict() for item in run_once()]
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
