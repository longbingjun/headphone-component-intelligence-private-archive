"""项目数据范围：仅耳机品类。"""

from __future__ import annotations

import re

# 纳入范围的耳机形态（含颈挂）
HEADPHONE_CATEGORIES: frozenset[str] = frozenset(
    {
        "真无线耳机TWS",
        "开放式耳机",
        "耳夹式耳机",
        "耳挂式耳机",
        "全入耳式耳机",
        "半入耳式耳机",
        "待细分耳机",
        "头戴式耳机",
        "有线耳机",
        "骨传导耳机",
        "颈挂式蓝牙耳机",
    }
)

# 标题含以下词则明确不是耳机
_NON_HEADPHONE_MARKERS = (
    "音箱",
    "音响",
    "Speaker",
    "麦克风",
    "Mic",
    "录音卡",
    "手表",
    "Watch",
    "眼镜",
    "Vision",
    "VR",
    "AI智能眼镜",
    "监控伴侣",
    "MiFi",
    "路由器",
    "游戏机",
    "Switch",
)


def is_headphone_category(category: str | None) -> bool:
    return (category or "") in HEADPHONE_CATEGORIES


def infer_headphone_category(text: str) -> str | None:
    """从标题/型号文本推断耳机品类；非耳机返回 None。"""
    t = text or ""
    if any(m in t for m in _NON_HEADPHONE_MARKERS):
        return None
    if "骨传导" in t:
        return "骨传导耳机"
    if any(k in t for k in ("开放式", "耳夹式", "耳夹", "挂耳式", "耳挂式")):
        return "开放式耳机"
    if any(k in t for k in ("颈挂式", "颈挂", "颈戴式", "颈戴")):
        return "颈挂式蓝牙耳机"
    if "头戴" in t:
        return "头戴式耳机"
    if any(k in t for k in ("有线耳机", "有线")):
        return "有线耳机"
    if any(k in t for k in ("真无线", "TWS", "tws", "FreeBuds", "Freebuds", "AirPods", "Buds")):
        return "真无线耳机TWS"
    if "耳机" in t or re.search(r"\bTWS\b", t, re.I):
        return "真无线耳机TWS"
    return None


def normalize_headphone_category(text: str, existing: str = "") -> str:
    """归一化品类：优先已有耳机类，否则从文本推断。"""
    if existing in HEADPHONE_CATEGORIES:
        return existing
    inferred = infer_headphone_category(text)
    if inferred:
        return inferred
    # 延迟导入避免循环
    from sources.audio52.parse_title import classify_category

    cat = classify_category(text or existing)
    if cat in HEADPHONE_CATEGORIES:
        return cat
    return existing or cat


def is_headphone_record(record: dict) -> bool:
    """判断单条 report/video 是否属于耳机范围。"""
    title = record.get("title") or record.get("product_title") or ""
    brand = record.get("brand") or ""
    model = record.get("model") or ""
    text = title or f"{brand} {model}".strip()
    existing = (
        record.get("category")
        or (record.get("views") or {}).get("market", {}).get("category")
        or (record.get("views") or {}).get("structure", {}).get("form_factor", "")
    )
    return is_headphone_category(normalize_headphone_category(text, existing))
