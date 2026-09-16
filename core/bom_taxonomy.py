"""Reusable normalization helpers for cross-product BOM analysis.

The source report wording remains untouched in ``BomItem.component``.  These
helpers add a conservative analysis key and material hint so products can be
grouped without losing the auditable source text.
"""

from __future__ import annotations

import re
from typing import Any, Iterable


_COMPONENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Specific battery-related ICs must be checked before the broad "电池"
    # marker, otherwise a battery-protection IC is incorrectly counted as a
    # battery cell in cross-product cost analysis.
    ("battery_protection_ic", ("电池保护芯片", "电池保护ic", "锂电保护", "保护ic")),
    ("battery", ("电池", "锂离子", "锂聚合物")),
    ("speaker_driver", ("扬声器", "喇叭", "发声单元", "驱动单元", "动圈单元", "动铁单元", "振膜")),
    ("microphone", ("麦克风", "mic", "mems")),
    ("bluetooth_audio_soc", ("蓝牙音频soc", "蓝牙soc", "主控soc", "蓝牙主控")),
    ("wireless_audio_ic", ("无线音频传输ic", "无线音频发送ic", "无线音频接收器", "无线音频接收芯片")),
    ("audio_codec_dsp", ("音频编解码器", "音频编解码芯片", "音频dsp", "音频芯片")),
    ("mcu", ("微控制器", "mcu")),
    ("power_management_ic", ("电源管理芯片", "电源管理ic", "电源管理soc", "pmic", "稳压器", "可编程升压ic")),
    ("charging_ic", ("充电管理芯片", "充电芯片", "charger")),
    ("overvoltage_protection", ("tvs保护", "过压保护", "ovp")),
    ("wireless_charging_ic", ("无线充电芯片", "无线充电ic")),
    ("mosfet", ("mos管", "mosfet")),
    ("sensor", ("传感器", "加速度计", "陀螺仪", "霍尔")),
    ("memory", ("存储器", "储存器", "flash", "eeprom")),
    ("crystal_oscillator", ("晶振", "晶体振荡器")),
    ("antenna", ("天线",)),
    (
        "connector",
        ("连接器", "连接座", "排线", "fpc", "弹片触点", "充电触点", "type-c", "typec", "usb-c"),
    ),
    ("pcb", ("pcb", "主板", "小板", "电路板")),
    ("indicator", ("指示灯", "导光", "led")),
    ("button", ("按键", "按钮")),
    ("fuse", ("保险丝",)),
    ("enclosure", ("外壳", "壳体", "上盖", "下盖", "腔体")),
    ("structural_support", ("支架", "固定架", "钢架", "转轴", "铰链")),
    ("generic_chip_module", ("芯片/模组", "芯片模组", "芯片模块")),
)

_KNOWN_MATERIALS: tuple[str, ...] = (
    "液态硅胶",
    "硅胶",
    "铝合金",
    "不锈钢",
    "镀镍钢壳",
    "钢壳",
    "铜箔",
    "铜",
    "陶瓷",
    "石墨",
    "泡棉",
    "塑料",
    "聚碳酸酯",
    "PC+ABS",
    "PC",
    "ABS",
    "PET",
    "TPU",
)

_COMPONENT_NOUNS: tuple[str, ...] = (
    "电池", "天线", "外壳", "壳体", "支架", "泡棉", "振膜", "扬声器", "喇叭",
    "动圈", "动铁", "麦克风", "连接器", "线圈", "主板", "电路板", "PCB", "按键",
)


def _component_tokens(component: Any) -> list[str]:
    raw = str(component or "").strip()
    tokens = [
        token.strip()
        for token in re.split(r"[/、|（）()\s]+", raw)
        if len(token.strip()) >= 2
    ]
    for noun in _COMPONENT_NOUNS:
        if noun.casefold() in raw.casefold() and noun not in tokens:
            tokens.append(noun)
    return tokens


def _material_tied_to_component(material: str, text: str, component: Any) -> bool:
    """Require explicit evidence that a material belongs to this component."""
    component_text = str(component or "")
    if material.casefold() in component_text.casefold():
        return True
    for token in _component_tokens(component):
        escaped_token = re.escape(token)
        escaped_material = re.escape(material)
        patterns = (
            rf"{escaped_token}[^，。；]{{0,10}}(?:采用|使用|材质|材料|由|制成|为)[^，。；]{{0,12}}{escaped_material}",
            rf"{escaped_material}(?:材质|材料)?(?:制成的?|的)?[^，。；]{{0,6}}{escaped_token}",
        )
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            return True
    return False


def component_key(component: Any, fact_text: Any = "") -> str:
    """Return a conservative, language-independent grouping key."""

    component_text = re.sub(r"\s+", "", str(component or "")).lower()
    for key, markers in _COMPONENT_RULES:
        if any(marker.lower() in component_text for marker in markers):
            return key
    # Keep unmatched names queryable without pretending two different items are
    # identical.  A broad summary paragraph must not override an explicit
    # component name merely because it happens to mention another component.
    fallback = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "_", component_text).strip("_")
    if fallback:
        return fallback[:100]
    fact = re.sub(r"\s+", "", str(fact_text or "")).lower()
    for key, markers in _COMPONENT_RULES:
        if any(marker.lower() in fact for marker in markers):
            return key
    return "unclassified"


def material_hint(
    parameters: Iterable[dict[str, Any]], fact_text: Any = "", component: Any = ""
) -> str:
    """Extract explicit material statements; never infer an undocumented material."""

    text = str(fact_text or "")
    found: list[str] = []
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        label = str(parameter.get("label") or "")
        if "材料" in label or "材质" in label:
            quote = str(parameter.get("evidence_quote") or "")
            value = str(parameter.get("value") or "").strip()
            relation_text = quote or text
            if value and _material_tied_to_component(value, relation_text, component) and value not in found:
                found.append(value)
    # Old reports occasionally attach an article-level teardown summary to
    # several different rows.  Such a paragraph is valid evidence context but
    # is not precise enough to assign a material to the current component.
    if len(text) > 500:
        return " / ".join(found)
    for material in _KNOWN_MATERIALS:
        if material in {"PC", "ABS", "PET", "TPU", "PC+ABS"}:
            pattern = rf"(?:采用|使用|材质|材料|制成|壳体)[^，。；]{{0,16}}{re.escape(material)}|{re.escape(material)}[^，。；]{{0,8}}(?:材质|材料|壳)"
        else:
            pattern = rf"(?<![A-Za-z]){re.escape(material)}(?![A-Za-z])"
        if re.search(pattern, text, re.IGNORECASE) and _material_tied_to_component(material, text, component):
            if material not in found:
                found.append(material)
    return " / ".join(found)


def numeric_value(value: Any) -> tuple[float | None, str]:
    """Split a simple leading number and unit while preserving the original text."""

    text = str(value or "").strip()
    match = re.fullmatch(
        r"([+-]?\d+(?:\.\d+)?)\s*(mAh|mWh|Wh|Ah|V|mV|A|mA|W|mW|Hz|kHz|MHz|GHz|mm|cm|g|kg|Ω|kΩ|%|℃|dB)?",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None, ""
    return float(match.group(1)), str(match.group(2) or "")
