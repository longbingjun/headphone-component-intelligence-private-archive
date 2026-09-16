from __future__ import annotations

import unittest

from scripts.enrich_product_details import _merge_evidence_records


class EvidenceMergeTests(unittest.TestCase):
    def test_validated_baseline_is_never_truncated(self) -> None:
        baseline = [
            {"text": f"baseline-{index}", "evidence_quote": f"quote-{index}"}
            for index in range(8)
        ]
        merged = _merge_evidence_records(
            baseline,
            [{"text": "model addition", "evidence_quote": "model quote"}],
            limit=max(6, len(baseline)),
        )
        self.assertEqual(len(merged), 8)
        self.assertEqual(
            [item["text"] for item in merged],
            [item["text"] for item in baseline],
        )


if __name__ == "__main__":
    unittest.main()
