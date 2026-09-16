from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from server.config import get_settings
from server.models import Base, Video
from server.video_worker import _normalized_download_url, claim_next_video


class VideoWorkerQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.settings = replace(
            get_settings(),
            video_worker_max_attempts=3,
            video_worker_lease_seconds=60,
        )

    def test_claim_marks_one_job_processing_and_increments_attempt(self) -> None:
        with Session(self.engine) as session:
            session.add(Video(id="video-1", processing_status="queued"))
            session.commit()
            claimed = claim_next_video(session, self.settings)
            session.commit()
            row = session.get(Video, "video-1")
            self.assertEqual(claimed, "video-1")
            self.assertEqual(row.processing_status, "processing")
            self.assertEqual(row.processing_attempt_count, 1)
            self.assertIsNotNone(row.processing_started_at)

    def test_fresh_lease_is_not_claimed_but_stale_lease_is_recovered(self) -> None:
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session:
            session.add_all(
                [
                    Video(
                        id="fresh",
                        processing_status="processing",
                        processing_attempt_count=1,
                        processing_started_at=now - timedelta(seconds=10),
                    ),
                    Video(
                        id="stale",
                        processing_status="processing",
                        processing_attempt_count=1,
                        processing_started_at=now - timedelta(seconds=120),
                    ),
                ]
            )
            session.commit()
            claimed = claim_next_video(session, self.settings, now=now)
            self.assertEqual(claimed, "stale")
            self.assertEqual(session.get(Video, "stale").processing_attempt_count, 2)

    def test_job_at_attempt_limit_is_terminal(self) -> None:
        with Session(self.engine) as session:
            session.add(
                Video(
                    id="terminal",
                    processing_status="queued",
                    processing_attempt_count=3,
                )
            )
            session.commit()
            self.assertEqual(claim_next_video(session, self.settings), "")


class VideoDownloadUrlTests(unittest.TestCase):
    def test_uses_bvid_from_52audio_player_embed(self) -> None:
        snapshot = {
            "source_url": "https://www.52audio.com/archives/123456.html",
            "embed_url": "https://player.bilibili.com/player.html?bvid=BV1tsNm6FEA5&cid=1",
        }
        self.assertEqual(
            _normalized_download_url(snapshot),
            "https://www.bilibili.com/video/BV1tsNm6FEA5/",
        )

    def test_uses_aid_from_protocol_relative_player_embed(self) -> None:
        snapshot = {
            "source_url": "https://www.52audio.com/archives/123456.html",
            "payload": {"video_embed_url": "//player.bilibili.com/player.html?aid=98765"},
        }
        self.assertEqual(
            _normalized_download_url(snapshot),
            "https://www.bilibili.com/video/av98765/",
        )

    def test_keeps_explicit_bilibili_video_page(self) -> None:
        snapshot = {
            "source_url": "https://www.bilibili.com/video/BV1tsNm6FEA5/?spm_id_from=test"
        }
        self.assertEqual(
            _normalized_download_url(snapshot),
            "https://www.bilibili.com/video/BV1tsNm6FEA5/",
        )

    def test_rejects_52audio_article_without_video_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "no supported downloadable URL"):
            _normalized_download_url(
                {"source_url": "https://www.52audio.com/archives/123456.html"}
            )


if __name__ == "__main__":
    unittest.main()
