"""技术数据抽取（充电方式/接口、说明书内容、产品标记等）。

来源以正文文字为主（正文里经常直接写"包装盒底部标注有产品参数信息：...充电接口：Type-C..."）。
图片里的表格/标签文字（比如产品铭牌照片）暂不做真正 OCR，交给 core/extract/images.py
里的图片分级 + OCR 队列处理，这里只处理"已经是文字"的部分。

这一步同样是"关键词/正则规则 -> 命中就归类"的启发式方法，规则表由外部传入，
方便针对不同类目产品调整（比如音箱类可能没有"充电接口"但有"电源适配器"）。
"""

from __future__ import annotations

import re

from core.extract.text_utils import split_sentences
from core.models import TechSpecs


def extract_tech_specs(plain_text: str, spec_rules: dict[str, list[str]]) -> TechSpecs:
    """spec_rules 形如：

    {
      "charging_method": ["充电方式", "无线充电", "有线充电", "磁吸充电"],
      "charging_port": ["充电接口", "Type-C", "Micro USB", "Lightning"],
      "manual_notes": ["说明书", "使用说明", "警示语"],
      "product_markings": ["额定输入", "型号：", "认证", "3C", "CE", "FCC", "频响范围"],
    }
    """

    sentences = split_sentences(plain_text)
    specs = TechSpecs()
    seen_raw: set[str] = set()

    field_map = {
        "charging_method": specs.charging_method,
        "charging_port": specs.charging_port,
        "manual_notes": specs.manual_notes,
        "product_markings": specs.product_markings,
    }

    for sent in sentences:
        matched_any_field = False
        for field_name, keywords in spec_rules.items():
            if field_name not in field_map:
                continue
            if any(kw in sent for kw in keywords):
                bucket = field_map[field_name]
                if sent not in bucket:
                    bucket.append(sent)
                matched_any_field = True
        if not matched_any_field:
            # 一些参数句用"XX：YY，AA：BB"这种键值对格式罗列，即便没命中关键词表，
            # 只要形似规格罗列（含多个中文/英文冒号），也收进 raw_candidates 供人工复核。
            colon_count = sent.count("：") + sent.count(":")
            if colon_count >= 2 and sent not in seen_raw:
                specs.raw_candidates.append(sent)
                seen_raw.add(sent)

    return specs


_PORT_PATTERNS = [r"Type-?C", r"Micro\s?USB", r"Lightning", r"USB-?A"]


def guess_charging_ports(plain_text: str) -> list[str]:
    """从全文里直接找常见充电接口关键字（大小写/连字符不敏感），作为补充信号。"""

    found = []
    for pat in _PORT_PATTERNS:
        if re.search(pat, plain_text, flags=re.IGNORECASE):
            m = re.search(pat, plain_text, flags=re.IGNORECASE)
            found.append(m.group(0))
    return list(dict.fromkeys(found))
