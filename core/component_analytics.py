"""Source-grounded cross-product component analysis for cost engineers.

The analytical grain is one product x component type x usage location x
reported model x reported manufacturer.  Source report publication date is
retained as business provenance; system extraction time is deliberately not a
business field.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.bom_taxonomy import component_key, material_hint


SUPPORTED_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("battery", "电池"),
    ("speaker_driver", "扬声器/发声单元"),
    ("microphone", "麦克风"),
    ("bluetooth_audio_soc", "蓝牙音频 SoC"),
    ("wireless_audio_ic", "无线音频收发 IC"),
    ("audio_codec_dsp", "音频编解码 / DSP"),
    ("mcu", "微控制器 MCU"),
    ("power_management_ic", "电源管理 IC"),
    ("charging_ic", "充电管理 IC"),
    ("battery_protection_ic", "电池保护 IC"),
    ("overvoltage_protection", "过压保护 IC"),
    ("sensor", "传感器"),
    ("memory", "存储器"),
    ("connector", "连接器/排线"),
    ("pcb", "PCB/电路板"),
    ("enclosure", "外壳/壳体"),
    ("structural_support", "支架/转轴"),
)
COMPONENT_LABELS = dict(SUPPORTED_COMPONENTS)
STRUCTURAL_COMPONENT_KEYS = frozenset({"enclosure", "structural_support"})

# A value can be present in an upstream ``model`` field without actually being
# a model number.  Battery reports are the clearest example: prose extractors
# sometimes capture ``58mAh锂电池`` as a model.  Keep the raw value for audit,
# but exclude these parameter-shaped values from model counts.
_BATTERY_PARAMETER_TOKEN_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mAh|Ah|mWh|Wh|V)", re.IGNORECASE
)
_BATTERY_PARAMETER_WORD_RE = re.compile(
    r"(?:锂离子|锂聚合物|锂电池|电池|钢壳|扣式|软包|圆柱|聚合物|额定|标称|容量|电压)"
)
_GENERIC_MODEL_NAMES = frozenset(
    {
        "电池",
        "锂电池",
        "麦克风",
        "mems麦克风",
        "memsmicrophone",
        "microphone",
        "蓝牙音频soc",
        "soc",
        "电源管理ic",
        "电池保护ic",
        "扬声器",
        "喇叭",
        "发声单元",
        "连接器",
        "排线",
        "pcb",
        "电路板",
        "外壳",
        "壳体",
        "支架",
        "转轴",
    }
)


def _looks_like_parameter_or_component_description(value: str, *, component: str) -> bool:
    normalized = re.sub(r"[\s/_-]+", "", value).casefold()
    if normalized in _GENERIC_MODEL_NAMES:
        return True
    if component != "battery" or not _BATTERY_PARAMETER_TOKEN_RE.search(value):
        return False
    residue = _BATTERY_PARAMETER_TOKEN_RE.sub("", value)
    residue = _BATTERY_PARAMETER_WORD_RE.sub("", residue)
    residue = re.sub(r"[\s/\\,，、;；:：·+\-()（）]", "", residue)
    return not residue


def normalized_component_model(
    value: Any,
    *,
    component: str = "",
    manufacturer: str = "",
) -> tuple[str, str, str]:
    """Return raw display, normalized comparison key and disclosure status.

    The raw report wording remains visible.  The comparison key only removes
    formatting noise that would otherwise inflate distinct-model metrics.  It
    is deliberately conservative and does not infer a model from capacity or
    other parameters.
    """

    display = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()
    if not display:
        return "", "", "not_disclosed"
    if _looks_like_parameter_or_component_description(display, component=component):
        return display, "", "invalid_parameter_value"

    comparison = display
    # VDL is a source-side manufacturer prefix for 紫建电子.  Only strip it
    # when it is separated from the actual model, preserving strings such as
    # VDL1150M2 exactly as reported.
    if component == "battery" and canonical_manufacturer(manufacturer) == "紫建电子":
        comparison = re.sub(r"^VDL[\s_-]+(?=[A-Z0-9])", "", comparison, flags=re.IGNORECASE)
    comparison_key = re.sub(r"[\s_-]+", "", comparison).casefold()
    return display, comparison_key, "reported"

_MANUFACTURER_PATTERNS = (
    re.compile(
        r"(?P<prefix>生产厂家|生产厂商?|生产厂|生产商|制造商|供应商)"
        r"(?:来自|为|是|：|:)?\s*(?P<name>[^，。；;]{2,80})"
    ),
    re.compile(r"(?P<prefix>来自|源自)\s*(?P<name>[^，。；;]{2,80})"),
)
# Teardown reports often concatenate a chip vendor, model and component noun
# without a relation word, for example ``JL杰理科技AC7106F6蓝牙音频SoC``.
# These aliases are source-grounded: a vendor is accepted only when it appears
# in the same short clause as an IC/SoC component noun.  A model prefix alone
# is deliberately insufficient.
_COMPACT_COMPONENT_VENDOR_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("JL杰理科技", ("珠海市杰理科技", "JL杰理科技", "JL杰理", "杰理科技", "杰理")),
    ("BES恒玄科技", ("BES恒玄科技", "BES恒玄", "恒玄科技", "恒玄")),
    ("Qualcomm高通", ("Qualcomm高通", "高通Qualcomm", "Qualcomm", "高通")),
    ("Bluetrum中科蓝讯", ("Bluetrum中科蓝讯", "中科蓝讯", "Bluetrum")),
    ("Actions炬芯", ("Actions炬芯", "炬芯", "Actions")),
    ("Realtek瑞昱", ("REALTEK瑞昱", "Realtek瑞昱", "REALTEK", "Realtek", "瑞昱")),
    ("Airoha络达", ("AIROHA络达", "Airoha络达", "AIROHA", "Airoha", "络达")),
    ("INJOINIC英集芯", ("INJOINIC英集芯", "INJOINIC", "英集芯")),
    ("ConvenientPower易冲半导体", ("ConvenientPower易冲半导体", "ConvenientPower", "易冲半导体")),
    ("XHSC小华半导体", ("XHSC小华半导体", "XHSC", "小华半导体")),
    ("WUQI物奇", ("WUQI物奇", "WUQi物奇", "WUQI", "WUQi", "物奇")),
    ("BEKEN博通集成", ("BEKEN博通集成", "BEKEN", "博通集成")),
    ("UNISOC紫光展锐", ("UNISOC紫光展锐", "UNISOC", "紫光展锐")),
    ("思佳讯", ("思佳讯",)),
    ("旭化成", ("旭化成",)),
    ("NXP恩智浦", ("NXP恩智浦", "恩智浦")),
    ("微源半导体", ("LPS微源半导体", "LPS微源", "微源半导体", "微源")),
    ("昇生微电子", ("SinhMicro昇生微电子", "昇生微电子", "昇生微")),
    ("泉声电子", ("泉声电子",)),
)
_COMPACT_COMPONENT_NOUN = re.compile(
    r"(?:蓝牙(?:音频)?\s*(?:SoC|SOC|主控(?:芯片)?)|音频\s*(?:SoC|SOC|DSP)|"
    r"无线音频(?:传输|发送|接收)?\s*(?:IC|芯片|接收器)|音频芯片|"
    r"(?:充电|电源|电池保护|过压过流保护|触控|降噪|射频|传感器|存储|功放)(?:管理)?\s*(?:SoC|SOC|IC|芯片)|"
    r"可编程升压\s*(?:IC|芯片)|"
    r"(?:MCU|DSP|SoC|SOC|主控芯片|芯片|存储器|稳压器))",
    re.IGNORECASE,
)
_COMPACT_MODEL_TOKEN = re.compile(r"^[\s的]*[A-Z][A-Z0-9._/-]{2,28}[\s，,:：、-]*", re.IGNORECASE)
_BATTERY_CELL_EVIDENCE = re.compile(
    r"(?:"
    r"(?:内置|搭载|采用|使用|配备|装有)[^，。；]{0,24}(?:锂离子|锂聚合物|聚合物锂离子|钢壳|扣式|软包)?电池(?:组|标签)?"
    r"|(?:电池组|电池标签|扣式电池|钢壳电池)"
    r"|电池[^。；]{0,55}(?:额定容量|标称电压|充电限制电压|\d+(?:\.\d+)?\s*(?:mAh|Wh))"
    r")",
    re.IGNORECASE,
)
_BATTERY_VENDOR_PATTERNS = (
    re.compile(
        r"(?:采用|使用|搭载)(?:了)?\s*(?P<name>[A-Za-z0-9\u4e00-\u9fff（）()·& .-]{2,50})"
        r"的(?:钢壳|扣式|锂离子|锂聚合物|聚合物锂离子)?电池",
        re.IGNORECASE,
    ),
)
_GENERIC_MANUFACTURER_VALUES = {
    "未知", "不详", "供应商", "生产商", "制造商", "电池", "锂电池", "原厂",
    "钢壳扣式", "软包扣式", "扣式", "钢壳", "锂离子", "锂聚合物",
}
_MANUFACTURER_CANONICAL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("JL杰理科技", ("JL杰理科技", "JL杰理", "杰理科技", "珠海市杰理科技", "杰理")),
    ("BES恒玄科技", ("BES恒玄科技", "BES恒玄", "恒玄科技", "恒玄")),
    ("Qualcomm高通", ("Qualcomm高通", "Qualcomm", "高通")),
    ("Bluetrum中科蓝讯", ("Bluetrum中科蓝讯", "Bluetrum", "中科蓝讯")),
    ("Actions炬芯", ("Actions炬芯", "Actions", "炬芯")),
    ("Realtek瑞昱", ("Realtek瑞昱", "REALTEK瑞昱", "Realtek", "REALTEK", "瑞昱")),
    ("Airoha络达", ("Airoha络达", "AIROHA络达", "Airoha", "AIROHA", "络达")),
    ("INJOINIC英集芯", ("INJOINIC英集芯", "INJOINIC", "英集芯")),
    ("ConvenientPower易冲半导体", ("ConvenientPower易冲半导体", "ConvenientPower", "易冲半导体")),
    ("XHSC小华半导体", ("XHSC小华半导体", "XHSC", "小华半导体")),
    ("WUQI物奇", ("WUQI物奇", "WUQi物奇", "WUQI", "WUQi", "物奇")),
    ("BEKEN博通集成", ("BEKEN博通集成", "BEKEN", "博通集成")),
    ("UNISOC紫光展锐", ("UNISOC紫光展锐", "UNISOC", "紫光展锐")),
    ("思远半导体", ("SouthChip思远半导体", "思远半导体", "SouthChip")),
    ("微源半导体", ("LPS微源半导体", "LPS微源", "微源半导体", "微源")),
    (
        "苏州赛芯电子科技",
        (
            "Xysemi苏州赛芯电子科技股份有限公司",
            "Xysemi苏州赛芯",
            "苏州赛芯电子科技股份有限公司",
            "苏州赛芯电子科技有限公司",
            "苏州赛芯电子科技股份",
            "苏州赛芯电子科技",
            "苏州赛芯微电子",
            "赛芯微",
        ),
    ),
    ("钰泰半导体", ("ETA钰泰半导体", "钰泰半导体")),
    ("来远电子", ("LY来远电子", "LY来远", "来远电子")),
    ("瑞勤电子", ("Richtek瑞勤电子", "瑞勤电子")),
    ("昇生微电子", ("SinhMicro昇生微电子", "昇生微电子", "昇生微")),
    ("泉声电子", ("泉声电子",)),
    ("创芯微", ("ICM创芯微", "创芯微")),
    ("楼氏电子", ("Knowles楼氏电子", "楼氏电子", "Knowles", "楼氏")),
    (
        "矽睿半导体",
        ("SiliconWisdom矽睿半导体", "SiliconWisdom", "矽睿半导体"),
    ),
    ("慧联科技", ("SmartLink慧联科技", "珠海慧联科技SmartLink", "慧联科技")),
    ("凌扬微", ("LYMICRO凌扬微", "凌扬微电子", "凌扬微")),
    ("原相", ("PixArt原相", "PixArt", "原相")),
    ("韦尔半导体", ("WillSemi韦尔半导体", "WillSemi", "韦尔半导体")),
    ("新余赣锋", ("GF新余赣锋", "新余赣锋电子", "新余赣锋")),
    ("亚奇科技", ("亚奇科技（OCN）", "亚奇科技")),
    ("紫建电子", ("紫建", "VDL")),
    ("微电新能源", ("微电新能源", "MIC-POWER")),
    ("ZeniPower 至力", ("ZeniPower", "至力")),
    ("神通天下", ("神通天下", "ST神通")),
    ("旭航诚", ("旭航诚", "WEL")),
    ("金赛尔", ("金赛尔", "CEL")),
    ("亿纬锂能", ("亿纬", "EVE")),
)

_MANUFACTURER_ALIAS_FILE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "config"
    / "manufacturer_aliases.json"
)


def _manufacturer_alias_key(value: Any) -> str:
    """Normalize formatting only; never use fuzzy similarity as identity proof."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s·•._/\\,，、;；:：()（）\[\]【】'\"“”‘’_-]+", "", normalized)


@lru_cache(maxsize=1)
def _configured_manufacturer_payload() -> dict[str, Any]:
    """Load the auditable supplier identity registry once per process."""

    if not _MANUFACTURER_ALIAS_FILE.exists():
        return {}
    payload = json.loads(_MANUFACTURER_ALIAS_FILE.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=1)
def _configured_manufacturer_aliases() -> dict[str, str]:
    """Load auditable exact aliases maintained separately from extraction code."""

    payload = _configured_manufacturer_payload()
    index: dict[str, str] = {}
    for group in payload.get("groups", []):
        canonical = str(group.get("canonical") or "").strip()
        if not canonical:
            continue
        for alias in [canonical, *(group.get("aliases") or [])]:
            key = _manufacturer_alias_key(alias)
            if not key:
                continue
            previous = index.get(key)
            if previous and previous != canonical:
                raise ValueError(
                    f"manufacturer alias {alias!r} maps to both {previous!r} and {canonical!r}"
                )
            index[key] = canonical
    return index


@lru_cache(maxsize=1)
def _configured_invalid_manufacturers() -> set[str]:
    """Return exact extraction artefacts that must not become suppliers."""

    return {
        key
        for value in _configured_manufacturer_payload().get("invalid_values", [])
        if (key := _manufacturer_alias_key(value))
    }


def manufacturer_is_rejected(value: Any) -> bool:
    """Whether a raw value is a reviewed non-supplier extraction artefact."""

    return _manufacturer_alias_key(value) in _configured_invalid_manufacturers()


def _compact_component_manufacturer(text: str) -> tuple[str, str, str] | None:
    """Extract one unambiguous vendor adjacent to a reported IC/SoC noun."""

    for clause in re.split(r"[。；;\n]", text):
        if not clause.strip():
            continue
        candidates: list[tuple[str, re.Match[str]]] = []
        for canonical, aliases in _COMPACT_COMPONENT_VENDOR_ALIASES:
            alias_pattern = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
            match = re.search(alias_pattern, clause, re.IGNORECASE)
            if match:
                candidates.append((canonical, match))
        # A summary clause naming several suppliers is not evidence that any
        # one of them manufactured the current component row.
        if len({canonical for canonical, _ in candidates}) != 1:
            continue
        _, vendor_match = candidates[0]
        prefix = clause[max(0, vendor_match.start() - 28):vendor_match.start()]
        suffix = clause[vendor_match.end():vendor_match.end() + 104]
        model_match = _COMPACT_MODEL_TOKEN.match(suffix)
        component_start = model_match.end() if model_match else 0
        component_match = _COMPACT_COMPONENT_NOUN.search(suffix, component_start, component_start + 88)
        prefix_component = list(_COMPACT_COMPONENT_NOUN.finditer(prefix))
        component_suffix_in_model = bool(
            model_match
            and re.search(r"(?:MCU|DSP|SoC|SOC|IC)[\s，,:：、-]*$", model_match.group(0), re.IGNORECASE)
        )
        if not component_match and not prefix_component and not component_suffix_in_model:
            continue
        name = vendor_match.group(0).strip()
        quote_end = (
            vendor_match.end() + component_match.end()
            if component_match
            else vendor_match.end() + (model_match.end() if model_match else 0)
        )
        quote = clause[vendor_match.start():quote_end].strip()
        return name, "compact_source_component_wording", quote
    return None

_STRUCTURAL_LOCATION_PATTERNS = (
    re.compile(r"(?:耳机|充电盒)?前腔内部"),
    re.compile(r"(?:耳机|充电盒)?后腔内部"),
    re.compile(r"(?:耳机|充电盒)?腔体内部"),
    re.compile(r"外壳内侧"),
    re.compile(r"盒体内侧"),
    re.compile(r"主板下方"),
    re.compile(r"电池下方"),
    re.compile(r"弯臂处"),
    re.compile(r"音腔盖板内侧"),
)
_STRUCTURAL_FORM_PATTERNS = (
    re.compile(r"透明(?:固定)?支架"),
    re.compile(r"(?:电池|主板|麦克风|充电|固定)支架"),
    re.compile(r"支架结构"),
    re.compile(r"转轴(?:结构|组件|模组)?"),
    re.compile(r"(?:前|后)?腔体外壳"),
    re.compile(r"(?:内|外)壳"),
)
_STRUCTURAL_TARGETS = (
    "电池单元", "电池", "主板单元", "主板", "麦克风模组", "麦克风",
    "扬声器单元", "扬声器", "电池排线", "排线", "充电板",
)
_STRUCTURAL_FASTENERS = (
    "螺丝固定", "螺丝", "卡扣固定", "卡扣", "胶水固定密封", "胶水固定",
    "胶水", "热熔柱", "超声波焊接", "双面胶", "泡棉胶",
)
_STRUCTURAL_MATERIALS = (
    "铝合金", "不锈钢", "金属", "塑料", "硅胶", "橡胶", "陶瓷", "PC+ABS", "ABS",
)


def structural_features(
    component_key_value: str,
    component: Any,
    source_text: Any,
    material: Any = "",
) -> list[dict[str, str]]:
    """Extract conservative, verbatim structure attributes from report prose.

    Structural parts rarely disclose a supplier or commercial model. These
    fields retain useful report facts instead: location, form, fixed objects
    and fastening method. Every value must occur verbatim in the source; a
    material is never inferred from appearance words such as ``透明``.
    """

    if component_key_value not in STRUCTURAL_COMPONENT_KEYS:
        return []
    text = str(source_text or "").strip()
    if not text:
        return []
    features: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(label: str, value: str) -> None:
        value = value.strip()
        signature = (label, value)
        if not value or value not in text or signature in seen:
            return
        seen.add(signature)
        features.append(
            {
                "label": label,
                "value": value,
                "evidence_quote": text,
                "basis": "verbatim_source_rule",
            }
        )

    for pattern in _STRUCTURAL_LOCATION_PATTERNS:
        match = pattern.search(text)
        if match:
            add("详细位置", match.group(0))
            break

    for pattern in _STRUCTURAL_FORM_PATTERNS:
        match = pattern.search(text)
        if match:
            add("结构形态", match.group(0))
            break

    if "透明" in text:
        add("外观特征", "透明")

    target_contexts = [
        match.group(1)
        for match in re.finditer(r"(?:固定|承托|支撑)(?:了|住)?([^，。；]{1,50})", text)
    ]
    target_contexts.extend(
        match.group(1)
        for match in re.finditer(r"([^，。；]{1,24})的(?:透明)?(?:固定)?支架", text)
    )
    covered_targets: list[str] = []
    for target in _STRUCTURAL_TARGETS:
        compound_support = f"{target}支架" in text
        if not compound_support and not any(target in context for context in target_contexts):
            continue
        if any(target in selected for selected in covered_targets):
            continue
        covered_targets.append(target)
        add("固定/承载对象", target)

    for fastener in _STRUCTURAL_FASTENERS:
        if fastener in text:
            add("固定/密封方式", fastener)
            break

    layer = re.search(r"[一二两三四五六七八九十\d]+层(?:固定)?支架", text)
    if layer:
        add("结构层级", layer.group(0))

    if "LDS镭射天线" in text:
        add("集成结构", "LDS镭射天线")

    explicit_material = str(material or "").strip()
    if explicit_material and explicit_material in text:
        add("明确材料", explicit_material)
    else:
        for candidate in _STRUCTURAL_MATERIALS:
            if candidate in text:
                add("明确材料", candidate)
                break

    return features


def normalize_usage_location(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    if "充电盒" in text or "盒" == text:
        return "charging_case", "充电盒"
    if any(marker in text for marker in ("耳机", "左耳", "右耳", "耳塞")):
        return "earbud", "耳机"
    if text:
        return "other", text
    return "unknown", "未知"


def _clean_manufacturer(value: str) -> str:
    value = re.sub(r"^[：:、，,\s]+|[：:、，,\s]+$", "", value)
    value = re.sub(r"^(?:电池|电芯)(?:供应商|生产商|制造商)?", "", value).strip()
    return value[:255]


def manufacturer_from_evidence(
    source_text: Any, reported_brand: Any = "", *, component_key: str = ""
) -> tuple[str, str, str]:
    """Return manufacturer, provenance basis and exact supporting quote.

    Explicit source wording wins.  A report-provided component brand is kept as
    a lower-confidence fallback and labelled as such; no company is guessed
    from a model number or outside knowledge.
    """

    text = str(source_text or "").strip()
    if component_key == "battery":
        for pattern in _BATTERY_VENDOR_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            name = _clean_manufacturer(match.group("name"))
            if name and name not in _GENERIC_MANUFACTURER_VALUES:
                return name, "explicit_source_text", match.group(0).strip()
    else:
        compact = _compact_component_manufacturer(text)
        if compact:
            return compact
    for pattern in _MANUFACTURER_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if component_key == "battery":
            before = text[max(0, match.start() - 180):match.start()]
            last_battery = before.rfind("电池")
            relation = before[last_battery + 2:] if last_battery >= 0 else before
            # A product label may list battery capacity and then name the
            # finished-product manufacturer.  That is not the cell supplier.
            if not _BATTERY_CELL_EVIDENCE.search(before) or re.search(
                r"(?:产品名|产品型号|耳机型号|充电盒型号|盒盖内侧|产品信息)", relation
            ):
                continue
            if match.group("prefix") == "制造商" and re.search(
                r"(?:产品名|耳机型号|充电盒型号|盒盖内侧|产品信息)", before
            ) and not re.search(r"电池(?:组)?[^。；]{0,30}制造商", before):
                continue
            if match.group("prefix") in {"来自", "源自"} and re.search(
                r"(?:IC|芯片|SoC|电感|充电管理|电源管理)", relation, re.IGNORECASE
            ):
                continue
        name = _clean_manufacturer(match.group("name"))
        if name and name not in _GENERIC_MANUFACTURER_VALUES:
            return name, "explicit_source_text", match.group(0).strip()

    brand = _clean_manufacturer(str(reported_brand or ""))
    if brand and brand not in _GENERIC_MANUFACTURER_VALUES:
        # Upstream ``brand`` fields can be contaminated by finished-product
        # brands or roundup lists.  Never publish that fallback unless the
        # exact reported string occurs in this component's evidence.
        if brand.casefold() not in text.casefold():
            return "", "unknown", ""
        if component_key == "battery":
            brand_pattern = re.escape(brand)
            if not re.search(
                rf"(?:{brand_pattern}[^，。；]{{0,18}}电池|电池[^，。；]{{0,24}}(?:来自|生产厂|生产商|制造商|供应商)[^，。；]{{0,12}}{brand_pattern})",
                text,
                re.IGNORECASE,
            ):
                return "", "unknown", ""
        quote = ""
        brand_at = text.casefold().find(brand.casefold())
        if brand_at >= 0:
            sentence_start = max(text.rfind("。", 0, brand_at), text.rfind("；", 0, brand_at)) + 1
            sentence_end_candidates = [
                pos for pos in (text.find("。", brand_at), text.find("；", brand_at)) if pos >= 0
            ]
            sentence_end = min(sentence_end_candidates) + 1 if sentence_end_candidates else len(text)
            quote = text[sentence_start:sentence_end].strip()
        return brand, "reported_component_brand", quote or text
    return "", "unknown", ""


def component_fact_key(fact: Mapping[str, Any]) -> str:
    """Stable key for attaching staged enrichment without mutating raw reports."""

    payload = {
        "component": str(fact.get("component") or ""),
        "model": str(fact.get("model") or ""),
        "side": str(fact.get("side") or ""),
        "fact_text": str(fact.get("fact_text") or ""),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def validated_manufacturer_enrichment(
    source_text: Any, enrichment: Mapping[str, Any] | None
) -> tuple[str, str, str] | None:
    """Accept an LLM fallback only when manufacturer and quote are verbatim."""

    if not enrichment:
        return None
    text = str(source_text or "").strip()
    manufacturer = _clean_manufacturer(str(enrichment.get("manufacturer") or ""))
    quote = str(enrichment.get("evidence_quote") or "").strip()
    if (
        not text
        or not manufacturer
        or manufacturer in _GENERIC_MANUFACTURER_VALUES
        or not quote
        or quote not in text
        or manufacturer not in quote
    ):
        return None
    return manufacturer, "llm_source_extraction_validated", quote


def component_evidence_supported(key: str, component: Any, source_text: Any) -> bool:
    """Reject upstream taxonomy rows whose evidence describes another item."""

    text = str(source_text or "").strip()
    if key == "battery":
        return bool(_BATTERY_CELL_EVIDENCE.search(text))
    return bool(str(component or "").strip() and text)


def canonical_manufacturer(value: Any) -> str:
    """Conservatively merge known source aliases while retaining the raw name."""

    raw = str(value or "").strip()
    if manufacturer_is_rejected(raw):
        return ""
    configured = _configured_manufacturer_aliases().get(_manufacturer_alias_key(raw))
    if configured:
        return configured
    folded = raw.casefold()
    for canonical, aliases in _MANUFACTURER_CANONICAL_RULES:
        if any(alias.casefold() in folded for alias in aliases):
            return canonical
    return raw


def _parameters(values: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or item.get("value_text") or "").strip()
        quote = str(item.get("evidence_quote") or "").strip()
        if not label or not value or (label, value) in seen:
            continue
        seen.add((label, value))
        result.append({"label": label, "value": value, "evidence_quote": quote})
    return result


def _evidence_image(values: Any) -> dict[str, Any] | None:
    """Keep the first component-linked report image, never a generic cover."""

    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        return {
            "url": url,
            "alt": str(item.get("alt") or "").strip(),
            "caption": str(item.get("caption") or "").strip(),
            "index": item.get("index"),
        }
    return None


def rows_from_products(
    products: Iterable[Mapping[str, Any]],
    reports_by_id: Mapping[str, Mapping[str, Any]],
    manufacturer_enrichments: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    videos_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build auditable analysis rows from the repository compatibility JSON."""

    output: list[dict[str, Any]] = []
    for product in products:
        product_id = str(product.get("canonical_id") or product.get("id") or "")
        report_ids = [str(value) for value in product.get("report_ids") or [] if value]
        video_ids = [str(value) for value in product.get("video_ids") or [] if value]
        source_rows = (
            product.get("technical_facts")
            or product.get("teardown_inventory")
            or product.get("bom_table")
            or []
        )
        for ordinal, fact in enumerate(source_rows):
            if not isinstance(fact, dict):
                continue
            evidence = fact.get("evidence") if isinstance(fact.get("evidence"), dict) else {}
            source_text = str(evidence.get("text") or fact.get("fact_text") or "").strip()
            key = component_key(fact.get("component"), source_text)
            if key not in COMPONENT_LABELS:
                continue
            if not component_evidence_supported(key, fact.get("component"), source_text):
                continue
            report_id = str(
                fact.get("report_id")
                or evidence.get("report_id")
                or (report_ids[0] if report_ids else "")
            )
            report = reports_by_id.get(report_id)
            evidence_source = str(evidence.get("source_type") or "")
            video_id = str(evidence.get("video_id") or (video_ids[0] if video_ids else ""))
            video = (videos_by_id or {}).get(video_id)
            source_is_video = evidence_source.startswith("video") and not report_ids and bool(video)
            if not report and not source_is_video:
                continue
            manufacturer, basis, manufacturer_quote = manufacturer_from_evidence(
                source_text,
                fact.get("manufacturer") or fact.get("brand"),
                component_key=key,
            )
            if not manufacturer:
                staged = (manufacturer_enrichments or {}).get(product_id, {}).get(
                    component_fact_key(fact)
                )
                validated = validated_manufacturer_enrichment(source_text, staged)
                if validated:
                    manufacturer, basis, manufacturer_quote = validated
            manufacturer_canonical = canonical_manufacturer(manufacturer)
            model_display, model_normalized, model_status = normalized_component_model(
                fact.get("model"),
                component=key,
                manufacturer=manufacturer_canonical or manufacturer,
            )
            location_key, location_label = normalize_usage_location(fact.get("side"))
            parameters = _parameters(fact.get("parameters"))
            material = material_hint(parameters, source_text, fact.get("component"))
            features = structural_features(key, fact.get("component"), source_text, material)
            evidence_image = _evidence_image(fact.get("evidence_images"))
            if evidence_image is None and source_is_video:
                public_path = str(evidence.get("keyframe_path") or "").strip()
                if public_path:
                    evidence_image = {
                        "url": public_path,
                        "public_path": public_path,
                        "caption": f"{fact.get('component') or COMPONENT_LABELS[key]}对应视频关键帧",
                        "alt": "",
                    }
            source_title = str(
                (video or {}).get("title") if source_is_video else (report or {}).get("title")
                or ""
            ).strip()
            source_url = str(
                ((video or {}).get("source_url") or (video or {}).get("url"))
                if source_is_video
                else (report or {}).get("url")
                or ""
            ).strip()
            source_date = str(
                ((video or {}).get("published_at") or (video or {}).get("date"))
                if source_is_video
                else (report or {}).get("published_at")
                or ""
            ).strip()
            output.append(
                {
                    "row_id": f"{product_id}:{ordinal}",
                    "component_key": key,
                    "component_label": COMPONENT_LABELS[key],
                    "component_name": str(fact.get("component") or "").strip(),
                    "component_manufacturer": manufacturer,
                    "component_manufacturer_canonical": manufacturer_canonical,
                    "manufacturer_basis": basis,
                    "manufacturer_evidence_quote": manufacturer_quote,
                    "manufacturer_status": (
                        "not_disclosed"
                        if not manufacturer
                        else "invalid_extraction"
                        if manufacturer_is_rejected(manufacturer)
                        else "reported"
                    ),
                    "component_brand": str(fact.get("brand") or "").strip(),
                    "component_model": model_display,
                    "component_model_normalized": model_normalized,
                    "model_status": model_status,
                    "material": material,
                    "parameters": parameters,
                    "semantic_features": features,
                    "detail_status": (
                        "source_structured" if features or parameters else "evidence_only"
                    ),
                    "usage_location": location_key,
                    "usage_location_label": location_label,
                    "product_id": product_id,
                    "product_brand": str(product.get("brand") or "").strip(),
                    "product_brand_status": (
                        "resolved" if str(product.get("brand") or "").strip()
                        else "unresolved"
                    ),
                    "product_model": str(product.get("model") or "").strip(),
                    "product_category": str(product.get("category") or "").strip(),
                    "source_type": "video" if source_is_video else "report",
                    "source_report_id": report_id,
                    "source_video_id": video_id if source_is_video else "",
                    "source_title": source_title,
                    "source_report_title": source_title,
                    "source_report_url": source_url,
                    "source_published_at": source_date,
                    "evidence_quote": source_text,
                    "evidence_image": evidence_image,
                    "confidence": evidence.get("confidence"),
                }
            )
    return deduplicate_rows(output)


def deduplicate_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated reports at the defined analytical grain.

    Left/right ear variants are intentionally collapsed to the shared "earbud"
    location.  Product versions and batch differences are not modelled in this
    iteration, matching the approved scope.
    """

    selected: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        grain = (
            str(row.get("product_id") or ""),
            str(row.get("component_key") or ""),
            str(row.get("usage_location") or "unknown"),
            str(row.get("component_manufacturer_canonical") or row.get("component_manufacturer") or "").casefold(),
            str(row.get("component_model_normalized") or row.get("component_model") or "").casefold(),
            str(row.get("material") or "").casefold(),
        )
        current = selected.get(grain)
        if current is None:
            selected[grain] = row
            continue
        current_score = (
            len(current.get("parameters") or []),
            bool(current.get("evidence_quote")),
            str(current.get("source_published_at") or ""),
        )
        candidate_score = (
            len(row.get("parameters") or []),
            bool(row.get("evidence_quote")),
            str(row.get("source_published_at") or ""),
        )
        if candidate_score > current_score:
            selected[grain] = row
    return sorted(
        selected.values(),
        key=lambda row: (
            str(row.get("source_published_at") or ""),
            str(row.get("product_brand") or ""),
            str(row.get("product_model") or ""),
        ),
        reverse=True,
    )


def _distribution(rows: list[dict[str, Any]], field: str, *, unknown: str = "未知") -> list[dict[str, Any]]:
    counts = Counter(str(row.get(field) or "").strip() or unknown for row in rows)
    return [
        {"label": label, "rows": count}
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def component_payload(rows: Iterable[dict[str, Any]], key: str) -> dict[str, Any]:
    selected = [row for row in rows if row.get("component_key") == key]
    total = len(selected)
    product_count = len({row.get("product_id") for row in selected if row.get("product_id")})
    known_manufacturer = sum(bool(row.get("component_manufacturer")) for row in selected)
    known_model = sum(row.get("model_status") == "reported" for row in selected)
    known_model_products = len(
        {
            row.get("product_id")
            for row in selected
            if row.get("product_id") and row.get("model_status") == "reported"
        }
    )
    known_brand_products = len(
        {
            row.get("product_id")
            for row in selected
            if row.get("product_id") and row.get("product_brand_status") == "resolved"
        }
    )
    parameterized = sum(bool(row.get("parameters")) for row in selected)
    parameterized_products = len(
        {
            row.get("product_id")
            for row in selected
            if row.get("product_id")
            and bool(row.get("semantic_features") or row.get("parameters"))
        }
    )
    feature_rows = sum(
        bool(row.get("semantic_features") or row.get("parameters")) for row in selected
    )
    sourced = sum(
        bool(row.get("source_report_id") and row.get("evidence_quote")) for row in selected
    )
    scope_notes = [
        "左右耳合并为“耳机”口径",
        "暂不区分同产品版本、左右耳不同料号和批次差异",
        "未知生产商保留在分母中，不按型号或外部知识猜测",
        "日期仅使用来源报告发布日期，不保存系统提取时间字段",
    ]
    if key in STRUCTURAL_COMPONENT_KEYS:
        scope_notes[2] = "结构特征只取原文明确表述；生产商、型号和材料未披露时不推断"
    return {
        "component_key": key,
        "component_label": COMPONENT_LABELS.get(key, key),
        "view_mode": "structural" if key in STRUCTURAL_COMPONENT_KEYS else "standard",
        "grain": "产品 × 器件类型 × 使用位置 × 报告型号 × 报告生产商",
        "scope_notes": scope_notes,
        "summary": {
            "rows": total,
            "products": product_count,
            "manufacturer_known_rows": known_manufacturer,
            "manufacturer_coverage": round(known_manufacturer / total, 4) if total else 0,
            "model_known_rows": known_model,
            "model_coverage": round(known_model / total, 4) if total else 0,
            "model_known_products": known_model_products,
            "model_product_coverage": (
                round(known_model_products / product_count, 4) if product_count else 0
            ),
            "brand_known_products": known_brand_products,
            "brand_product_coverage": (
                round(known_brand_products / product_count, 4) if product_count else 0
            ),
            "parameterized_rows": parameterized,
            "parameter_coverage": round(parameterized / total, 4) if total else 0,
            "parameterized_products": parameterized_products,
            "parameter_product_coverage": (
                round(parameterized_products / product_count, 4)
                if product_count
                else 0
            ),
            "feature_rows": feature_rows,
            "feature_coverage": round(feature_rows / total, 4) if total else 0,
            "source_evidence_rows": sourced,
            "source_evidence_coverage": round(sourced / total, 4) if total else 0,
        },
        "distributions": {
            "manufacturers": _distribution(selected, "component_manufacturer_canonical"),
            "product_categories": _distribution(selected, "product_category"),
            "product_brands": _distribution(selected, "product_brand"),
            "usage_locations": _distribution(selected, "usage_location_label"),
        },
        "rows": selected,
    }


def manifest_payload(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    components = []
    for key, label in SUPPORTED_COMPONENTS:
        selected = [row for row in rows if row.get("component_key") == key]
        if not selected:
            continue
        components.append(
            {
                "key": key,
                "label": label,
                "rows": len(selected),
                "products": len({row.get("product_id") for row in selected}),
                "file": f"{key}.json",
            }
        )
    return {
        "default_component": "battery",
        "components": components,
        "business_fields": [
            "器件类型", "耳机类型", "耳机制造商/品牌", "耳机产品",
            "使用位置", "器件生产商", "器件型号", "参数", "图片证据",
            "报告发布日期", "来源证据",
        ],
    }


def brand_supply_key(brand: str) -> str:
    """Return a stable filesystem-safe key for one normalized product brand."""

    return "brand-" + hashlib.sha1(brand.encode("utf-8")).hexdigest()[:12]


def brand_supply_payload(
    rows: Iterable[dict[str, Any]], brand: str
) -> dict[str, Any]:
    """Build one brand-first, source-backed supply-chain analysis payload."""

    selected = [row for row in rows if row.get("product_brand") == brand]
    products = {
        str(row.get("product_id") or "")
        for row in selected
        if row.get("product_id")
    }
    components = {
        str(row.get("component_key") or "")
        for row in selected
        if row.get("component_key")
    }
    suppliers = {_row_supplier(row) for row in selected}
    suppliers.discard("")
    known = sum(bool(_row_supplier(row)) for row in selected)
    return {
        "brand": brand,
        "brand_key": brand_supply_key(brand),
        "grain": "产品 × 器件类型 × 使用位置 × 报告型号 × 报告生产商",
        "summary": {
            "rows": len(selected),
            "products": len(products),
            "components": len(components),
            "suppliers": len(suppliers),
            "manufacturer_coverage": round(known / len(selected), 4)
            if selected
            else 0,
        },
        "scope_notes": [
            "供应关系按拆解报告样本观察，不代表真实采购份额",
            "年份使用供应关系首次被报告观察到的年份，不等同于产品上市年份",
            "未知生产商保留，不按器件型号或外部知识猜测",
            "一个产品存在多个有证据供应商时全部保留",
        ],
        "rows": selected,
    }


def brand_supply_manifest(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """List brands that have source-backed component analysis rows."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        brand = str(row.get("product_brand") or "").strip()
        if not brand or brand in {"未知", "未知品牌"}:
            continue
        grouped.setdefault(brand, []).append(row)

    brands = []
    for brand, selected in grouped.items():
        products = {
            str(row.get("product_id") or "")
            for row in selected
            if row.get("product_id")
        }
        components = {
            str(row.get("component_key") or "")
            for row in selected
            if row.get("component_key")
        }
        brands.append(
            {
                "brand": brand,
                "key": brand_supply_key(brand),
                "products": len(products),
                "components": len(components),
                "rows": len(selected),
            }
        )
    brands.sort(key=lambda item: (-item["products"], item["brand"]))
    return {
        "brands": brands,
        "scope_notes": [
            "仅列出具有可追溯器件记录的耳机品牌",
            "产品数按 product_id 去重",
        ],
    }


def supplier_supply_key(supplier: str) -> str:
    """Return a stable filesystem-safe key for one normalized supplier."""

    return "supplier-" + hashlib.sha1(supplier.encode("utf-8")).hexdigest()[:12]


def _row_supplier(row: Mapping[str, Any]) -> str:
    if row.get("manufacturer_status") == "invalid_extraction":
        return ""
    return str(
        row.get("component_manufacturer_canonical")
        or row.get("component_manufacturer")
        or ""
    ).strip()


def supplier_supply_payload(
    rows: Iterable[dict[str, Any]], supplier: str
) -> dict[str, Any]:
    """Build one supplier-first component portfolio with source-backed rows."""

    selected = [row for row in rows if _row_supplier(row) == supplier]
    products = {
        str(row.get("product_id") or "")
        for row in selected
        if row.get("product_id")
    }
    components = {
        str(row.get("component_key") or "")
        for row in selected
        if row.get("component_key")
    }
    models = {
        str(row.get("component_model_normalized") or "").strip()
        for row in selected
        if row.get("model_status") == "reported"
        and str(row.get("component_model_normalized") or "").strip()
    }
    brands = {
        str(row.get("product_brand") or "").strip()
        for row in selected
        if str(row.get("product_brand") or "").strip()
        not in {"未知", "未知品牌"}
    }
    return {
        "supplier": supplier,
        "supplier_key": supplier_supply_key(supplier),
        "grain": "产品 × 器件类型 × 使用位置 × 报告型号 × 报告生产商",
        "summary": {
            "rows": len(selected),
            "products": len(products),
            "components": len(components),
            "models": len(models),
            "brands": len(brands),
            "model_known_products": len(
                {
                    str(row.get("product_id") or "")
                    for row in selected
                    if row.get("product_id") and row.get("model_status") == "reported"
                }
            ),
            "brand_known_products": len(
                {
                    str(row.get("product_id") or "")
                    for row in selected
                    if row.get("product_id")
                    and str(row.get("product_brand") or "").strip()
                    not in {"", "未知", "未知品牌"}
                }
            ),
        },
        "scope_notes": [
            "供应关系来自拆解报告或无报告视频产品的可追溯证据，不代表真实采购份额",
            "产品数按 product_id 去重，重复器件记录不会重复计算产品款数",
            "年份使用来源发布日期，不等同于产品上市年份或供应商导入年份",
            "仅展示原文明确披露或通过原文校验的供应商，不根据型号反向猜测",
        ],
        "rows": sorted(
            selected,
            key=lambda row: (
                str(row.get("source_published_at") or ""),
                str(row.get("component_label") or ""),
                str(row.get("product_brand") or ""),
                str(row.get("product_model") or ""),
            ),
            reverse=True,
        ),
    }


def supplier_supply_manifest(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """List normalized suppliers available to the supplier-first explorer."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        supplier = _row_supplier(row)
        if not supplier or supplier in {"未知", "报告未披露"}:
            continue
        grouped.setdefault(supplier, []).append(row)

    suppliers = []
    for supplier, selected in grouped.items():
        products = {
            str(row.get("product_id") or "")
            for row in selected
            if row.get("product_id")
        }
        components = {
            str(row.get("component_key") or "")
            for row in selected
            if row.get("component_key")
        }
        models = {
            str(row.get("component_model_normalized") or "").strip()
            for row in selected
            if row.get("model_status") == "reported"
            and str(row.get("component_model_normalized") or "").strip()
        }
        brands = {
            str(row.get("product_brand") or "").strip()
            for row in selected
            if str(row.get("product_brand") or "").strip()
            not in {"未知", "未知品牌"}
        }
        suppliers.append(
            {
                "supplier": supplier,
                "key": supplier_supply_key(supplier),
                "products": len(products),
                "components": len(components),
                "models": len(models),
                "brands": len(brands),
                "model_known_products": len(
                    {
                        str(row.get("product_id") or "")
                        for row in selected
                        if row.get("product_id")
                        and row.get("model_status") == "reported"
                    }
                ),
                "brand_known_products": len(
                    {
                        str(row.get("product_id") or "")
                        for row in selected
                        if row.get("product_id")
                        and str(row.get("product_brand") or "").strip()
                        not in {"", "未知", "未知品牌"}
                    }
                ),
                "rows": len(selected),
            }
        )
    suppliers.sort(
        key=lambda item: (
            -item["products"],
            -item["components"],
            item["supplier"],
        )
    )
    return {
        "suppliers": suppliers,
        "scope_notes": [
            "仅列出具有可追溯器件记录的已识别供应商",
            "产品数按 product_id 去重，仅表示当前拆解样本覆盖",
        ],
    }
