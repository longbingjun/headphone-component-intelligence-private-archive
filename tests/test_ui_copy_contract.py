from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UiCopyContractTests(unittest.TestCase):
    def test_component_analysis_uses_two_task_paths_without_matrix(self) -> None:
        source = (
            ROOT / "web" / "src" / "components" / "ComponentSourcingExplorer.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("按器件找供应商", source)
        self.assertIn("按供应商看能力", source)
        self.assertIn("跨耳机类型探索", source)
        self.assertIn("聚焦某类耳机", source)
        self.assertIn("历史累计供应商排名", source)
        self.assertIn("当年真实前五", source)
        self.assertIn("供应商名称", source)
        self.assertIn("随其他条件联动更新", source)
        self.assertIn("不代表市场份额、实际采购量或供应商出货排名", source)
        self.assertIn("提取依据与公开来源", source)
        self.assertIn("型号均未披露", source)
        self.assertIn("耳机品牌尚未识别", source)
        self.assertIn("参数均未披露", source)
        self.assertIn("参数证据", source)
        self.assertIn("参数披露", source)
        self.assertIn("当前范围寻源结果", source)
        self.assertIn("按供应商查看", source)
        self.assertIn("按具体型号查看", source)
        self.assertIn("仅披露生产商，可作为线下询盘线索", source)
        self.assertIn("型号仅在 1 款产品中出现，不制作候选卡片", source)
        self.assertIn("产品规格与证据", source)
        self.assertIn("同一产品的耳机与充电盒并排呈现", source)
        self.assertNotIn("高级核验：查看原始证据记录", source)
        self.assertNotIn("电池参数速览", source)
        self.assertNotIn("按使用位置拆分的核心参数", source)
        self.assertNotIn("function BatteryProfile", source)
        self.assertNotIn("function ParameterProfile", source)
        self.assertIn("供应商能力目录", source)
        self.assertIn("搜索并定位耳机品牌", source)
        self.assertIn("当前找到", source)
        self.assertNotIn("<section className=\"sourcing-detail-summary\"", source)
        self.assertNotIn("<h3>按产品配对查看电池规格</h3>", source)
        self.assertNotIn("function ReuseMatrix", source)
        self.assertNotIn("<ReuseMatrix", source)
        self.assertNotIn("componentPayload && category &&", source)
        self.assertNotIn("各耳机品牌的有记录产品数", source)
        self.assertNotIn("供应商 × 器件能力矩阵", source)
        self.assertNotIn("器件生产商 × 耳机品牌产品矩阵", source)
        self.assertNotIn("供应商跨耳机类型应用", source)
        self.assertNotIn("来源年份（可选）", source)

    def test_global_sample_coverage_is_rendered_on_every_page(self) -> None:
        layout = (ROOT / "web" / "src" / "layouts" / "BaseLayout.astro").read_text(
            encoding="utf-8"
        )
        publisher = (ROOT / "scripts" / "prepare_web_data.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('class="global-sample-coverage"', layout)
        self.assertIn("供应商名称", layout)
        self.assertIn("拆解报告", layout)
        self.assertIn("拆解视频", layout)
        self.assertIn('WEB_DATA / "coverage-summary.json"', publisher)

    def test_product_provenance_does_not_offer_internal_archive_link(self) -> None:
        source = (
            ROOT / "web" / "src" / "pages" / "product" / "[id].astro"
        ).read_text(encoding="utf-8")
        self.assertNotIn("站内档案", source)

    def test_compare_drawers_link_to_source_not_internal_report_route(self) -> None:
        for name in ("CompareWorkbench.tsx", "CrossCategoryCompare.tsx"):
            source = (ROOT / "web" / "src" / "components" / name).read_text(
                encoding="utf-8"
            )
            self.assertIn("查看来源原文", source)
            self.assertNotIn("/report/${drawer.product.best_report_id}", source)

    def test_bom_comparison_has_component_alignment_and_evidence(self) -> None:
        source = (
            ROOT / "web" / "src" / "components" / "BomComparisonPanel.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("产品 BOM 对比", source)
        self.assertIn("共同器件类型", source)
        self.assertIn("查看提取依据与公开来源", source)


if __name__ == "__main__":
    unittest.main()
