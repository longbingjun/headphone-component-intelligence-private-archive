from __future__ import annotations

import unittest

from core.views.role_extract import _match_scenarios


class MarketScenarioEvidenceTests(unittest.TestCase):
    def test_scenarios_are_derived_from_filtered_selling_point_text(self) -> None:
        whole_report = "本产品支持清晰通话。我爱音频网此前还拆解过INZONE游戏耳机。"
        filtered_selling_points = "本产品支持清晰通话。"

        self.assertEqual(_match_scenarios(whole_report), ["游戏", "通话"])
        self.assertEqual(_match_scenarios(filtered_selling_points), ["通话"])


if __name__ == "__main__":
    unittest.main()
