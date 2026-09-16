"""Evidence-grounded semantic parameters for teardown BOM rows."""

from __future__ import annotations

import hashlib
import json
import re


_NUMBER_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mAh|mWh|Wh|MHz|kHz|Hz|KB|MB|GB|mm|cm|V|W|Ω|ohm|%|小时|分钟)",
    re.I,
)
_GENERIC_LABELS = {"参数", "规格", "信息", "其他", "品牌", "厂商", "型号", "位置", "分类"}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def bom_row_key(row: dict) -> str:
    parts = (
        row.get("role"), row.get("component"), row.get("model"), row.get("side"),
        row.get("fact_text"),
    )
    raw = "|".join(_normalize(value).casefold() for value in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _row_source_texts(row: dict) -> list[str]:
    values = [str(row.get("fact_text") or ""), *[str(value or "") for value in row.get("evidence_texts") or []]]
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def parameter_source_hash(rows: list[dict]) -> str:
    payload = [
        {
            "key": bom_row_key(row),
            "component": row.get("component") or "",
            "model": row.get("model") or "",
            "specification": row.get("specification") or "",
            "fact_text": row.get("fact_text") or "",
            "evidence_texts": _row_source_texts(row),
        }
        for row in rows
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _numbers_supported(display: str, evidence: str) -> bool:
    values = {_normalize(value).casefold() for value in _NUMBER_UNIT_RE.findall(display)}
    source_values = {_normalize(value).casefold() for value in _NUMBER_UNIT_RE.findall(evidence)}
    return values.issubset(source_values)


def validate_bom_parameters(
    candidates: object,
    rows: list[dict],
    *,
    max_per_row: int = 7,
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Validate row identity, exact evidence, values and numerical claims."""
    accepted = {bom_row_key(row): [] for row in rows}
    rejected: list[dict] = []
    row_by_key = {bom_row_key(row): row for row in rows}
    items = candidates.get("items") if isinstance(candidates, dict) else candidates
    if not isinstance(items, list):
        return accepted, [{"reason": "response_items_not_a_list"}]
    for item in items:
        if not isinstance(item, dict):
            rejected.append({"reason": "item_not_an_object"})
            continue
        key = str(item.get("key") or "")
        row = row_by_key.get(key)
        if not row:
            rejected.append({"reason": "unknown_row_key", "candidate": item})
            continue
        source_text = "\n".join(_row_source_texts(row))
        params = item.get("parameters") or []
        if not isinstance(params, list):
            rejected.append({"key": key, "reason": "parameters_not_a_list"})
            continue
        seen: set[str] = set()
        for raw in params:
            if not isinstance(raw, dict):
                rejected.append({"key": key, "reason": "parameter_not_an_object"})
                continue
            label = str(raw.get("label") or "").strip().rstrip("：:")
            value = str(raw.get("value") or "").strip().rstrip("。")
            quote = str(raw.get("evidence_quote") or "").strip()
            reason = ""
            if len(label) < 2 or label in _GENERIC_LABELS:
                reason = "invalid_label"
            elif not value or len(_normalize(value)) < 2:
                reason = "missing_value"
            elif not quote or _normalize(quote) not in _normalize(source_text):
                reason = "evidence_not_in_row"
            elif _normalize(value).casefold() not in _normalize(quote).casefold():
                reason = "value_not_in_evidence"
            display = f"{label}：{value}"
            if not reason and not _numbers_supported(display, quote):
                reason = "unsupported_number"
            signature = _normalize(display).casefold()
            if not reason and signature in seen:
                reason = "duplicate"
            if reason:
                rejected.append({"key": key, "reason": reason, "candidate": raw})
                continue
            seen.add(signature)
            accepted[key].append(
                {
                    "label": label[:30],
                    "value": value[:180],
                    "display": display[:220],
                    "evidence_quote": quote,
                }
            )
            if len(accepted[key]) >= max_per_row:
                break
    return accepted, rejected


_PATTERNS: tuple[tuple[str, str], ...] = (
    ("最高主频", r"最高主频(?:为|：|:)?\s*(\d+(?:\.\d+)?\s*MHz)"),
    ("片上闪存", r"(?:高达)?\s*(\d+(?:\.\d+)?\s*KB)\s*的?片上闪存"),
    ("SRAM", r"(\d+(?:\.\d+)?\s*KB)\s*SRAM"),
    ("额定容量", r"额定容量[：:]?\s*(\d+(?:\.\d+)?\s*mAh)"),
    ("额定能量", r"额定能量[：:]?\s*(\d+(?:\.\d+)?\s*mWh|\d+(?:\.\d+)?\s*Wh)"),
    ("标称电压", r"标称电压[：:]?\s*(\d+(?:\.\d+)?\s*V)"),
    ("充电限制电压", r"充电限制电压[：:]?\s*(\d+(?:\.\d+)?\s*V)"),
    ("OVP耐压", r"(\d+(?:\.\d+)?\s*V)\s*耐压\s*OVP"),
    ("驱动单元尺寸", r"(\d+(?:\.\d+)?\s*mm)\s*高性能驱动单元"),
    ("实测直径", r"直径约(?:为)?\s*(\d+(?:\.\d+)?\s*mm)"),
)


def deterministic_candidates(rows: list[dict]) -> dict:
    """Fallback for common labelled values and exact functional phrases."""
    items: list[dict] = []
    for row in rows:
        params: list[dict] = []
        for text in _row_source_texts(row):
            for label, pattern in _PATTERNS:
                match = re.search(pattern, text, re.I)
                if match:
                    params.append({"label": label, "value": match.group(1).strip(), "evidence_quote": text})
            for label, pattern in (
                ("处理器内核", r"(Arm\s+Cortex-[A-Za-z0-9]+(?:\s+RISC内核)?)"),
                ("主要用途", r"(用于[^。；;]{3,80})"),
                ("集成功能", r"(集成了?[^。；;]{3,100})"),
                ("接口资源", r"(集成了?\s*USART\+UART[^。；;]{3,120})"),
            ):
                match = re.search(pattern, text, re.I)
                if match:
                    params.append({"label": label, "value": match.group(1).strip(), "evidence_quote": text})
        items.append({"key": bom_row_key(row), "parameters": params})
    return {"items": items}
