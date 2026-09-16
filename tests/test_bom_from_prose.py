from __future__ import annotations

import unittest

from core.extract.bom_from_prose import extract_bom_from_prose


class BomFromProseTests(unittest.TestCase):
    def test_extracts_capacity_only_battery_without_borrowing_chip_model(self) -> None:
        rows = extract_bom_from_prose(
            "主控芯片为高通CSR8675蓝牙音频SoC；此外耳机内置电池容量1200mAh。"
        )
        battery = next(row for row in rows if row["component"] == "电池")
        self.assertEqual(battery["model"], "1200mAh")
        self.assertEqual(battery["side"], "耳机")
        self.assertIn("内置电池容量1200mAh", battery["evidence"]["source_text"])


if __name__ == "__main__":
    unittest.main()
