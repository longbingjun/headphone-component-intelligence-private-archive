"""Evidence-grounded summaries for packaging, charging-case and earbud views."""

from __future__ import annotations

import hashlib
import json
import re


MODULE_KEYS = ("packaging", "charging_case", "earbuds")
_NUMBER_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mAh|mWh|Wh|W|V|mm|h|小时|分钟|g|%|倍)", re.I
)
_TEARDOWN_TERMS = ("拆解", "主板", "芯片", "电路", "焊接", "屏蔽罩", "电池保护", "丝印", "镭雕")
_GENERIC = ("外观一览", "特写", "拆解报告")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def collect_unboxing_source(payload: dict) -> dict[str, list[str]]:
    """Collect report text that is already assigned to each appearance module."""
    result: dict[str, list[str]] = {}
    for key in MODULE_KEYS:
        module = payload.get(key) or {}
        captions = [str(item.get("caption") or "") for item in module.get("appearance_images") or []]
        # The legacy unboxing extractor occasionally put the report lead into
        # packaging.description. When packaging captions exist they are the
        # section-local evidence and therefore take precedence.
        candidates = [] if key == "packaging" and captions else [module.get("description") or ""]
        candidates.extend(str(item) for item in module.get("accessories") or [])
        candidates.extend(captions)
        seen: set[str] = set()
        rows: list[str] = []
        for raw in candidates:
            text = re.sub(r"\s+", " ", raw).strip()
            normalized = _normalize(text)
            if len(normalized) < 4 or normalized in seen:
                continue
            if text.startswith("拆解报告："):
                continue
            seen.add(normalized)
            rows.append(text)
        result[key] = rows
    return result


def source_hash(source: dict[str, list[str]]) -> str:
    raw = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _numbers_supported(text: str, quote: str) -> bool:
    values = {_normalize(value).casefold() for value in _NUMBER_UNIT_RE.findall(text)}
    source_values = {_normalize(value).casefold() for value in _NUMBER_UNIT_RE.findall(quote)}
    return values.issubset(source_values)


def validate_unboxing_summary(
    candidates: object,
    source: dict[str, list[str]],
    *,
    max_per_module: int = 6,
) -> tuple[dict[str, list[dict]], list[dict]]:
    accepted = {key: [] for key in MODULE_KEYS}
    rejected: list[dict] = []
    if not isinstance(candidates, dict):
        return accepted, [{"reason": "response_not_an_object"}]
    for key in MODULE_KEYS:
        rows = candidates.get(key) or []
        if not isinstance(rows, list):
            rejected.append({"module": key, "reason": "module_not_a_list"})
            continue
        corpus = "\n".join(source.get(key) or [])
        seen: set[str] = set()
        for raw in rows:
            if not isinstance(raw, dict):
                rejected.append({"module": key, "reason": "row_not_an_object"})
                continue
            text = str(raw.get("text") or "").strip().rstrip("。")
            quote = str(raw.get("evidence_quote") or "").strip()
            reason = ""
            if len(_normalize(text)) < 6:
                reason = "summary_too_short"
            elif not quote or _normalize(quote) not in _normalize(corpus):
                reason = "evidence_not_in_module"
            elif any(term in text for term in _TEARDOWN_TERMS):
                reason = "teardown_fact_in_appearance_summary"
            elif not _numbers_supported(text, quote):
                reason = "unsupported_number"
            elif all(term in text for term in _GENERIC[:2]) or text in _GENERIC:
                reason = "generic_caption"
            normalized = _normalize(text).casefold()
            if not reason and normalized in seen:
                reason = "duplicate"
            if reason:
                rejected.append({"module": key, "reason": reason, "candidate": raw})
                continue
            seen.add(normalized)
            accepted[key].append(
                {
                    "text": text[:140],
                    "topic": str(raw.get("topic") or "外观与配置").strip()[:24],
                    "evidence_quote": quote,
                    "priority": min(5, max(1, int(raw.get("priority") or 3))),
                }
            )
            if len(accepted[key]) >= max_per_module:
                break
    return accepted, rejected


def deterministic_candidates(source: dict[str, list[str]]) -> dict[str, list[dict]]:
    """Use exact report sentences when the API is unavailable."""
    result: dict[str, list[dict]] = {key: [] for key in MODULE_KEYS}
    for key in MODULE_KEYS:
        for quote in source.get(key) or []:
            for sentence in re.split(r"[。；;]+", quote):
                text = sentence.strip()
                if len(_normalize(text)) < 8 or any(term in text for term in _TEARDOWN_TERMS):
                    continue
                if text in _GENERIC:
                    continue
                score = 4 if _NUMBER_UNIT_RE.search(text) else 3
                result[key].append(
                    {"text": text, "topic": "外观与配置", "evidence_quote": quote, "priority": score}
                )
        result[key] = sorted(result[key], key=lambda row: -row["priority"])
    return result
