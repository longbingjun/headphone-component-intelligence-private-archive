from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.pipeline import derive


class DerivePipelineTests(unittest.TestCase):
    def test_ai_derivation_uses_grounded_product_details(self) -> None:
        calls: list[tuple[str, tuple[str, ...]]] = []

        def fake_run(name: str, *args: str, **_kwargs: object) -> dict:
            calls.append((name, args))
            return {"ok": True}

        with patch("scripts.pipeline.run_script", side_effect=fake_run):
            stats = derive(with_ai=True, with_unboxing=False, prune=False)

        self.assertIn(
            (
                "enrich_product_details.py",
                ("--changed-only", "--use-llm", "--workers", "2"),
            ),
            calls,
        )
        identity_call = (
            "repair_product_identities.py",
            (
                "--all",
                "--write-overrides",
                "--title-only",
                "--missing-brand-only",
                "--workers",
                "2",
            ),
        )
        self.assertIn(identity_call, calls)
        self.assertIn(("summarize_roundup_insights.py", ()), calls)
        self.assertNotIn("selling_point_summaries", stats)
        self.assertTrue(all(name != "summarize_selling_points.py" for name, _ in calls))
        self.assertLess(
            calls.index(identity_call),
            calls.index(("build_products.py", ())),
        )
        self.assertLess(
            calls.index(("build_products.py", ())),
            calls.index(("enrich_product_details.py", ("--changed-only", "--use-llm", "--workers", "2"))),
        )
        self.assertLess(
            calls.index(("enrich_product_details.py", ("--changed-only", "--use-llm", "--workers", "2"))),
            calls.index(("build_product_priority.py", ())),
        )

    def test_non_ai_derivation_does_not_run_model_enrichment(self) -> None:
        calls: list[str] = []

        def fake_run(name: str, *_args: str, **_kwargs: object) -> dict:
            calls.append(name)
            return {"ok": True}

        with patch("scripts.pipeline.run_script", side_effect=fake_run):
            derive(with_ai=False, with_unboxing=False, prune=False)

        self.assertNotIn("enrich_product_details.py", calls)
        self.assertNotIn("summarize_roundup_insights.py", calls)
        self.assertNotIn("repair_product_identities.py", calls)


if __name__ == "__main__":
    unittest.main()
