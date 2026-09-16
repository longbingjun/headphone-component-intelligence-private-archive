"""Evidence-first consumer claim extraction and deterministic validation.

The LLM is used only to summarize and classify report prose. Publication is
decided by code: every claim must retain an exact report quote, numerical
values may not exceed that quote, and pure BOM descriptions are rejected.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable

from core.extract.text_utils import parse_content_blocks


CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "佩戴体验": ("佩戴", "舒适", "稳固", "耳型", "亲肤", "轻巧", "无感"),
    "音质体验": ("音质", "音频", "低频", "高频", "声音", "听感", "扬声器", "驱动单元", "DSEE", "空间音频"),
    "通话体验": ("通话", "降噪", "麦克风", "拾音", "人声", "风噪"),
    "续航充电": ("续航", "播放", "小时", "快充", "充电"),
    "连接与智能": ("蓝牙", "连接", "多点", "多设备", "APP", "智能", "Quick Access"),
    "耐用防护": ("防水", "防尘", "IPX", "耐用", "防护"),
    "外观设计": ("外观", "配色", "质感", "辨识度", "设计"),
}

_BOILERPLATE = (
    "我爱音频网还拆解过", "我爱音频网此前还", "下面就来看看", "详细拆解报告",
    "点击图片即可查看", "最后附上", "方便大家查阅",
)
_PURE_TECH = (
    "微控制器", "电源管理芯片", "保护IC", "MOS管", "晶振", "丝印", "镭雕",
    "详细资料图", "物料清单", "主板电路", "BIS认证", "电池保护板",
)
_NON_CONSUMER_CONTEXT = (
    "公司", "生产商", "制造商", "供应商", "包装盒", "包装清单", "拆解报告",
    "主板", "器件型号", "料号", "BOM",
)
_EXPERIENCE_TERMS = (
    "舒适", "稳固", "轻巧", "无感", "音质", "听感", "低频", "高频", "空间音频",
    "通话", "降噪", "风噪", "续航", "快充", "小时", "连接", "多点", "防水",
    "防尘", "外观", "质感", "佩戴", "低延迟", "提升", "减少", "更",
)
_STRICT_NON_CLAIM = ("包装盒", "包装清单", "公司信息", "据我爱音频网拆解了解到")
_BENEFIT_TERMS = tuple({word for words in CATEGORY_KEYWORDS.values() for word in words})
_NUMBER_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mAh|mWh|Wh|W|V|mm|h|小时|分钟|dB|kHz|Hz|g|%|倍)", re.I
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def source_hash(source_paragraphs: Iterable[str]) -> str:
    raw = json.dumps(list(source_paragraphs), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_consumer_source(content_html: str, *, max_paragraphs: int = 24) -> list[str]:
    """Select consumer-relevant report prose before the teardown section."""
    selected: list[tuple[int, int, str]] = []
    for index, block in enumerate(parse_content_blocks(content_html)):
        if block.kind == "heading" and "拆解" in block.text and not any(
            marker in block.text for marker in ("开箱", "外观")
        ):
            break
        if block.kind != "paragraph":
            continue
        text = re.sub(r"\s+", " ", block.text).strip()
        if len(text) < 12 or any(marker in text for marker in _BOILERPLATE):
            continue
        score = sum(2 for words in CATEGORY_KEYWORDS.values() if any(word.casefold() in text.casefold() for word in words))
        score += min(3, len(_NUMBER_UNIT_RE.findall(text)))
        if score:
            selected.append((score, index, text))
    # Retain article order after selecting the most informative paragraphs.
    chosen = sorted(sorted(selected, key=lambda item: (-item[0], item[1]))[:max_paragraphs], key=lambda item: item[1])
    return [item[2] for item in chosen]


def _is_pure_technical(text: str) -> bool:
    if any(term.casefold() in text.casefold() for term in _STRICT_NON_CLAIM):
        return True
    tech_hits = sum(1 for term in _PURE_TECH if term.casefold() in text.casefold())
    benefit_hit = any(term.casefold() in text.casefold() for term in _BENEFIT_TERMS)
    context_hit = any(term.casefold() in text.casefold() for term in _NON_CONSUMER_CONTEXT)
    experience_hit = any(term.casefold() in text.casefold() for term in _EXPERIENCE_TERMS)
    return (tech_hits > 0 and not benefit_hit) or (context_hit and not experience_hit)


def is_publishable_consumer_claim(text: object, category: object) -> bool:
    """Conservative publication gate for cached and newly extracted claims.

    Historical caches pre-date the evidence-first extractor, so the Web export
    must not trust their labels blindly. This intentionally prefers omitting a
    weak claim over presenting BOM or packaging prose as a consumer benefit.
    """

    claim = str(text or "").strip()
    label = str(category or "").strip()
    return bool(claim) and label in CATEGORY_KEYWORDS and not _is_pure_technical(claim)


def _numbers_supported(claim: str, evidence_quote: str) -> bool:
    claim_values = {_normalize(value).casefold() for value in _NUMBER_UNIT_RE.findall(claim)}
    evidence_values = {_normalize(value).casefold() for value in _NUMBER_UNIT_RE.findall(evidence_quote)}
    return claim_values.issubset(evidence_values)


def _valid_bom_links(raw_links: object, bom_rows: list[dict]) -> list[str]:
    if not isinstance(raw_links, list):
        return []
    recognized = [row for row in bom_rows if row.get("role") not in {"unidentified_marking", "review"}]
    aliases: dict[str, str] = {}
    for row in recognized:
        key = str(row.get("model") or row.get("component") or "").strip()
        for value in (row.get("model"), row.get("component"), key):
            normalized = _normalize(str(value or "")).casefold()
            if normalized:
                aliases[normalized] = key
    result: list[str] = []
    for raw in raw_links:
        match = aliases.get(_normalize(str(raw)).casefold())
        if match and match not in result:
            result.append(match)
    return result


def validate_consumer_claims(
    candidates: object,
    source_paragraphs: list[str],
    bom_rows: list[dict],
    *,
    max_claims: int = 7,
) -> tuple[list[dict], list[dict]]:
    """Return (publishable claims, rejected audit rows)."""
    source_corpus = "\n".join(source_paragraphs)
    if not isinstance(candidates, list):
        return [], [{"reason": "response_not_a_list"}]
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    for raw in candidates:
        if not isinstance(raw, dict):
            rejected.append({"reason": "row_not_an_object"})
            continue
        text = str(raw.get("text") or raw.get("claim") or "").strip()
        category = str(raw.get("category") or raw.get("tag") or "").strip()
        quote = str(raw.get("evidence_quote") or "").strip()
        confidence = min(1.0, max(0.0, float(raw.get("confidence") or 0.8)))
        reason = ""
        if not text or len(text) < 8:
            reason = "claim_too_short"
        elif category not in CATEGORY_KEYWORDS:
            reason = "invalid_category"
        elif not quote or _normalize(quote) not in _normalize(source_corpus):
            reason = "evidence_not_in_report"
        elif _is_pure_technical(text) or _is_pure_technical(quote):
            reason = "pure_technical_fact"
        elif not _numbers_supported(text, quote):
            reason = "unsupported_number"
        elif confidence < 0.75:
            reason = "low_confidence"
        key = _normalize(text).casefold()
        if not reason and key in seen:
            reason = "duplicate"
        if reason:
            rejected.append({"reason": reason, "candidate": raw})
            continue
        seen.add(key)
        benefit = str(raw.get("consumer_benefit") or "").strip()
        scenario = str(raw.get("scenario") or "").strip()
        accepted.append(
            {
                "text": text[:120],
                "category": category,
                "tag": category,
                "consumer_benefit": benefit[:80],
                "scenario": scenario[:30],
                "supporting_bom_keys": _valid_bom_links(raw.get("supporting_bom_keys"), bom_rows),
                "evidence_quote": quote,
                "evidence": {"source_type": "report_prose", "source_text": quote, "confidence": confidence},
            }
        )
        if len(accepted) >= max_claims:
            break
    return accepted, rejected


def deterministic_candidates(source_paragraphs: list[str]) -> list[dict]:
    """Safe fallback: classify exact report sentences without inventing prose."""
    result: list[dict] = []
    for paragraph in source_paragraphs:
        for sentence in re.split(r"(?<=[。！？；])", paragraph):
            sentence = sentence.strip()
            if len(sentence) < 12 or _is_pure_technical(sentence):
                continue
            category = next(
                (name for name, words in CATEGORY_KEYWORDS.items() if any(word.casefold() in sentence.casefold() for word in words)),
                "",
            )
            if not category:
                continue
            result.append(
                {
                    "text": sentence[:120],
                    "category": category,
                    "consumer_benefit": "",
                    "scenario": "",
                    "evidence_quote": sentence,
                    "supporting_bom_keys": [],
                    "confidence": 0.8,
                }
            )
    return result
