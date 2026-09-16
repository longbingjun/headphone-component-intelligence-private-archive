"""Deterministic, evidence-first inventory extraction for teardown reports.

The classifier deliberately answers *what the part is*, not what it costs.
It also avoids product-version, left/right part-number and batch dimensions;
those require reviewed source data and are outside the current data contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from core.extract.text_utils import Block, extract_all_image_urls, parse_content_blocks


CLASS_LABELS = {
    "core": "核心BOM",
    "key": "关键BOM",
    "auxiliary": "辅助器件",
    "structure": "结构件",
    "unidentified_marking": "待识别丝印",
}


@dataclass(frozen=True)
class PartRule:
    component: str
    classification: str
    patterns: tuple[str, ...]
    reason: str


RULES: tuple[PartRule, ...] = (
    PartRule("蓝牙音频SoC", "core", (r"蓝牙音频\s*SoC", r"蓝牙主控"), "承担无线连接和音频处理"),
    PartRule("音频编解码器/DSP", "core", (r"音频编解码器", r"音频编解码芯片", r"音频\s*DSP"), "承担音频编解码或数字信号处理"),
    PartRule("微控制器", "core", (r"微控制器", r"\bMCU\b"), "承担整机控制"),
    PartRule("电源管理芯片", "core", (r"电源管理芯片", r"\bPMIC\b"), "承担系统级充放电或电源管理"),
    PartRule("电池", "core", (r"(?:锂电池|扣式电池|电池组|内置电池).{0,45}(?:mAh|mWh|型号)",), "系统能源器件，且原文给出型号或参数"),
    PartRule("MEMS麦克风", "core", (r"MEMS麦克风",), "核心拾音器件"),
    PartRule("VPU骨传导麦克风", "core", (r"VPU骨传导麦克风", r"骨传导麦克风"), "核心拾音器件"),
    PartRule("动圈单元", "core", (r"动圈单元",), "核心发声器件"),
    PartRule("动铁单元", "core", (r"动铁单元",), "核心发声器件"),
    PartRule("扬声器/驱动单元", "core", (r"(?:扬声器|驱动单元).{0,25}(?:mm|发声)",), "核心发声器件"),
    PartRule("电池保护IC", "key", (r"(?:锂电|电池)保护\s*IC",), "电池安全保护器件"),
    PartRule("MOS管", "key", (r"MOS管", r"MOSFET"), "电源开关或保护器件"),
    PartRule("热敏电阻", "key", (r"热敏电阻",), "电池温度检测器件"),
    PartRule("自恢复保险丝", "key", (r"自恢复保险丝",), "输入过流保护器件"),
    PartRule("TVS保护管", "key", (r"TVS保护管", r"TVS二极管"), "输入浪涌或过压保护器件"),
    PartRule("LC双工器", "key", (r"LC双工器", r"双工器"), "射频收发隔离器件"),
    PartRule("晶振", "key", (r"晶振",), "为主芯片提供时钟"),
    PartRule("功率电感", "key", (r"功率电感",), "电源转换外围器件"),
    PartRule("LDS天线", "key", (r"LDS(?:镭射)?天线", r"LDS镭射天线"), "无线射频关键器件"),
    PartRule("天线弹片", "key", (r"天线弹片", r"连接天线的金属弹片"), "连接射频天线"),
    PartRule("Type-C接口", "auxiliary", (r"Type-C(?:充电)?(?:母座|接口)", r"USB-C(?:母座|接口)"), "标准外部接口"),
    PartRule("Pogo Pin连接器", "auxiliary", (r"Pogo\s*Pin",), "标准充电连接器"),
    PartRule("BTB连接器", "auxiliary", (r"BTB连接器",), "板对板连接器"),
    PartRule("ZIF连接器", "auxiliary", (r"ZIF连接器",), "柔性排线连接器"),
    PartRule("功能按键", "auxiliary", (r"(?:功能|配对)按键",), "标准人机交互器件"),
    PartRule("指示灯/导光件", "auxiliary", (r"指示灯", r"导光(?:柱|件)"), "标准状态指示器件"),
    PartRule("固定支架", "structure", (r"(?:固定|透明|电池|主板|座舱|座)支架", r"支架结构"), "纯机械承载或固定结构"),
    PartRule("缓冲泡棉", "structure", (r"泡棉", r"缓冲棉"), "缓冲、密封或减振结构"),
    PartRule("屏蔽罩", "structure", (r"屏蔽罩",), "机械屏蔽结构"),
    PartRule("C形桥/硅胶结构", "structure", (r"C[形型].{0,8}桥", r"硅胶连接件"), "佩戴或连接结构"),
)


VENDORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("GigaDevice兆易创新", ("GigaDevice", "兆易创新")),
    ("SinhMicro昇生微电子", ("SinhMicro", "昇生微电子")),
    ("AIROHA达发（络达）", ("AIROHA", "达发", "络达")),
    ("ZeniPower至力", ("ZeniPower", "至力")),
    ("紫建电子", ("紫建电子",)),
    ("SONY索尼", ("SONY", "索尼")),
    ("Cirrus Logic凌云逻辑", ("Cirrus Logic", "凌云逻辑")),
)


def _vendor(text: str) -> str:
    for display, aliases in VENDORS:
        if any(alias.casefold() in text.casefold() for alias in aliases):
            return display
    return ""


_CASE_TERMS = ("充电盒", "充电仓", "座舱")
_EARBUD_TERMS = ("耳机", "前腔", "后腔", "音腔", "腔体")
_SUMMARY_PARAGRAPH_RE = re.compile(r"^(?:我爱音频网)?(?:拆解)?总结")
_LOCAL_CLAUSE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s*")


def _side_decision(text: str, heading: str) -> tuple[str, str, str]:
    """Resolve product location from report structure before sentence keywords.

    A section heading is stronger evidence than a relational mention in prose,
    such as a charging-case paragraph saying “为耳机充电”. Without a specific
    section heading, conflicting sentence terms are sent to review.
    """
    heading_case = any(term in heading for term in _CASE_TERMS)
    heading_earbud = any(term in heading for term in _EARBUD_TERMS)
    if heading_case != heading_earbud:
        side = "充电盒" if heading_case else "耳机"
        return side, f"依据报告章节标题“{heading}”", "resolved"

    text_case = any(term in text for term in _CASE_TERMS)
    text_earbud = any(term in text for term in _EARBUD_TERMS)
    if text_case != text_earbud:
        side = "充电盒" if text_case else "耳机"
        return side, "章节未明确位置，依据当前证据句中的部位词", "resolved"
    if text_case and text_earbud:
        return "", "同一证据句同时出现充电盒与耳机部位词，需要人工确认", "needs_review"
    return "", "原文章节和证据句均未给出明确部位", "needs_review"


def _side(text: str, heading: str) -> str:
    """Compatibility wrapper used by existing callers/tests."""
    return _side_decision(text, heading)[0]


def _marking(text: str) -> str:
    match = re.search(
        r"(?:丝印|镭雕|镭雕为|丝印为)\s*([A-Za-z0-9][A-Za-z0-9 ._/-]{0,24}?)(?=的|为|，|。|$)",
        text,
        re.I,
    )
    return re.sub(r"\s+", "", match.group(1)) if match else ""


def _model(text: str, component: str = "") -> str:
    explicit = re.search(r"型号[：:]\s*([^，。；;]{2,40})", text)
    if explicit:
        return explicit.group(1).strip()
    marking = _marking(text)
    if marking:
        return marking
    # Capacity/energy is useful for a battery, but an unrelated chip token in
    # the same source clause is not the battery model. Only an explicit 型号 or
    # marking is accepted for batteries.
    if component == "电池":
        return ""
    # Prefer part-number-like tokens with at least one letter and one digit.
    tokens = re.findall(
        r"(?<![A-Za-z0-9])(?=[A-Z0-9-]{4,24}(?![A-Za-z0-9]))(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)[A-Z0-9-]+(?![A-Za-z0-9])",
        text,
        re.I,
    )
    ignored = {"TYPE-C", "USB-C", "CORTEX-M4", "CORTEX-M33F", "SRAM", "UART", "USART", "OTG"}
    for token in tokens:
        if token.upper() not in ignored and not re.fullmatch(r"\d+(?:V|MAH|MWH|MHZ|KHZ|MM)", token, re.I):
            return token
    return ""


def _specification(text: str) -> str:
    specs = re.findall(
        r"\d+(?:\.\d+)?\s*(?:mAh|mWh|Wh|MHz|kHz|KB|MB|mm|ohm|V|W|Ω)",
        text,
        re.I,
    )
    return " / ".join(dict.fromkeys(specs))


def _nearest_image(blocks: list[Block], paragraph_index: int) -> dict | None:
    for distance in range(1, 5):
        before = paragraph_index - distance
        if before >= 0 and blocks[before].kind == "image" and blocks[before].img_url:
            image = blocks[before]
            return {"index": image.img_index, "url": image.img_url, "alt": image.img_alt, "caption": image.text}
        after = paragraph_index + distance
        if after < len(blocks) and blocks[after].kind == "image" and blocks[after].img_url:
            image = blocks[after]
            return {"index": image.img_index, "url": image.img_url, "alt": image.img_alt, "caption": image.text}
    return None


def _matches(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _marked_component(text: str) -> str:
    match = re.search(
        r"(?:丝印|镭雕)\s*[A-Za-z0-9 ._/-]{1,24}的\s*(MEMS麦克风|VPU骨传导麦克风|骨传导麦克风|MOS管|TVS保护管|晶振|IC)",
        text,
        re.I,
    )
    if not match:
        return ""
    value = match.group(1).casefold()
    if value == "骨传导麦克风".casefold():
        return "VPU骨传导麦克风"
    return match.group(1)


def _unidentified_marking(text: str) -> dict | None:
    marking = _marking(text)
    if not marking:
        return None
    # A named part type makes the marking identified enough for a functional
    # class. Generic "IC/元件" alone remains in the review queue.
    named_types = (
        "MOS管", "TVS", "晶振", "麦克风", "SoC", "微控制器", "电源管理",
        "保护IC", "双工器", "电感", "电池", "连接器",
    )
    if any(name.casefold() in text.casefold() for name in named_types):
        return None
    if re.search(r"(?:的|为)?\s*(?:IC|芯片|元件)(?:特写|。|，|$)", text, re.I):
        return {
            "component": "待识别器件",
            "brand": "",
            "model": marking,
            "classification": "unidentified_marking",
            "classification_label": CLASS_LABELS["unidentified_marking"],
            "classification_reason": "原文仅提供丝印，尚不足以确认器件类型或型号",
        }
    return None


def extract_teardown_inventory(content_html: str) -> list[dict]:
    """Extract fine-grained BOM/structure rows with auditable evidence."""

    blocks = parse_content_blocks(content_html)
    rows: list[dict] = []
    heading = ""
    has_teardown_heading = any(block.kind == "heading" and "拆解" in block.text for block in blocks)
    in_teardown = not has_teardown_heading
    seen: set[tuple[str, str, str, str]] = set()
    for index, block in enumerate(blocks):
        if block.kind == "heading":
            heading = block.text
            if "总结" in heading:
                in_teardown = False
            elif "拆解" in heading:
                in_teardown = True
            continue
        if block.kind != "paragraph" or len(block.text) < 5:
            continue
        if not in_teardown:
            continue
        text = block.text
        if _SUMMARY_PARAGRAPH_RE.match(text):
            in_teardown = False
            continue
        evidence_image = _nearest_image(blocks, index)
        clauses = [part.strip() for part in _LOCAL_CLAUSE_SPLIT_RE.split(text) if len(part.strip()) >= 5]
        for clause in clauses or [text]:
            candidates: list[dict] = []
            marked_component = _marked_component(clause)
            for rule in RULES:
                if not _matches(clause, rule.patterns):
                    continue
                # A sentence may mention a second component while describing a
                # marked close-up of the first. Do not attach that marking to the
                # secondary component (for example G411 MEMS text mentioning VPU).
                if marked_component and rule.component in {"MEMS麦克风", "VPU骨传导麦克风"} and rule.component != marked_component:
                    continue
                candidates.append(
                    {
                        "component": rule.component,
                        "brand": _vendor(clause),
                        "model": _model(clause, rule.component),
                        "classification": rule.classification,
                        "classification_label": CLASS_LABELS[rule.classification],
                        "classification_reason": rule.reason,
                    }
                )
            unknown = _unidentified_marking(clause)
            if unknown:
                candidates.append(unknown)
            if not candidates:
                continue
            for row in candidates:
                side, side_reason, side_status = _side_decision(clause, heading)
                key = (row["classification"], row["component"], row["model"].casefold(), side)
                if key in seen:
                    continue
                seen.add(key)
                row.update(
                    {
                        "side": side,
                        "side_reason": side_reason,
                        "side_status": side_status,
                        "qty_hint": "",
                        "role": row["classification"],
                        "specification": _specification(clause),
                        "fact_text": clause,
                        "evidence_images": [evidence_image] if evidence_image else [],
                        "evidence": {
                            "source_type": "report_prose",
                            "text": clause,
                            "extraction_method": "deterministic_rules",
                            "review_status": "needs_review" if row["classification"] == "unidentified_marking" else "auto_extracted",
                        },
                    }
                )
                rows.append(row)
    return rows


def extract_report_images(content_html: str) -> list[dict]:
    """Return every report-original image in article order for MinIO sync."""

    return extract_all_image_urls(content_html)
