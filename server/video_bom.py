"""Convert reviewed video facts into source-grounded BOM candidates.

The language model may propose structured fields, but identifiers, suppliers
and parameter values are accepted only when they occur in the source subtitle.
Deterministic report-prose rules remain the first extraction layer.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

from core.bom_taxonomy import component_key
from core.component_analytics import (
    COMPONENT_LABELS,
    component_evidence_supported,
    manufacturer_from_evidence,
)
from core.extract.bom_from_prose import extract_bom_from_prose


_LOCATION_RULES = (
    ("充电盒", ("充电盒", "充电仓", "座舱", "盒体")),
    ("耳机", ("耳机", "左耳", "右耳", "耳塞", "前腔", "后腔")),
)
_PARAMETER_RULES = (
    ("容量", re.compile(r"\d+(?:\.\d+)?\s*(?:mAh|毫安时)", re.IGNORECASE)),
    ("能量", re.compile(r"\d+(?:\.\d+)?\s*(?:Wh|瓦时)", re.IGNORECASE)),
    ("电压", re.compile(r"\d+(?:\.\d+)?\s*(?:mV|V|伏)", re.IGNORECASE)),
    (
        "尺寸",
        re.compile(
            r"(?:尺寸(?:为)?\s*[：:]?\s*)?"
            r"(?P<value>\d+(?:\.\d+)?\s*(?:mm|毫米)|\d+(?:\.\d+)?\s*[x×*]\s*\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
    ),
    ("频率", re.compile(r"\d+(?:\.\d+)?\s*(?:kHz|MHz|GHz|Hz)", re.IGNORECASE)),
    (
        "功率",
        re.compile(
            r"(?:(?:额定|充电|最大短时)?功率(?:约为)?\s*[：:]?\s*)"
            r"(?P<value>\d+(?:\.\d+)?\s*(?:毫瓦|瓦(?!时)))|"
            r"\d+(?:\.\d+)?\s*(?:mW|W)",
            re.IGNORECASE,
        ),
    ),
    ("阻抗", re.compile(r"\d+(?:\.\d+)?\s*(?:kΩ|Ω|ohm)", re.IGNORECASE)),
)
_MODEL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-]{4,})(?=[A-Za-z0-9-]*[A-Za-z])"
    r"(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{3,}(?![A-Za-z0-9])"
)
_PARAMETER_SHAPED_MODEL_RE = re.compile(
    r"^\d+(?:\.\d+)?(?:mAh|Ah|mWh|Wh|mV|V|mm|Hz|kHz|MHz|GHz|mW|W)$",
    re.IGNORECASE,
)
_DIMENSION_SHAPED_MODEL_RE = re.compile(r"^\d+(?:\.\d+)?\s*[x×*]\s*\d+(?:\.\d+)?$", re.IGNORECASE)
_GENERIC_MODEL_VALUES = {
    "芯片", "模组", "电池", "扬声器", "喇叭", "麦克风", "存储器", "储存器",
    "稳压器", "接收器", "mcu", "soc", "ic",
}
def _normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _is_verbatim(source: str, value: Any) -> bool:
    candidate = _normalized(value)
    return bool(candidate and candidate in _normalized(source))


def _side(source: str, proposed: Any = "", narrative_side: Any = "") -> str:
    proposed_text = str(proposed or "").strip()
    if proposed_text and any(marker in source for marker in (proposed_text,)):
        return proposed_text[:100]
    for label, markers in _LOCATION_RULES:
        if any(marker in source for marker in markers):
            return label
    narrative = str(narrative_side or "").strip()
    if narrative in {"耳机", "充电盒"}:
        return narrative
    return ""


def _classification(key: str) -> str:
    if key in {"enclosure", "structural_support"}:
        return "structure"
    if key in {
        "battery",
        "speaker_driver",
        "bluetooth_audio_soc",
        "wireless_audio_ic",
        "audio_codec_dsp",
        "mcu",
        "power_management_ic",
        "charging_ic",
    }:
        return "core"
    return "auxiliary"


def _model_from_source(source: str) -> str:
    """Extract only an identifier-shaped token that occurs in the subtitle."""

    labelled_numeric = re.search(r"型号\s*[：:]?\s*(\d{5,})", source)
    if labelled_numeric:
        return labelled_numeric.group(1)
    for match in _MODEL_TOKEN_RE.finditer(source):
        value = match.group(0).strip("-_")
        value = re.sub(r"(?:MCU|SOC|IC)$", "", value, flags=re.IGNORECASE)
        if (
            len(value) >= 4
            and not _PARAMETER_SHAPED_MODEL_RE.fullmatch(value)
            and not _DIMENSION_SHAPED_MODEL_RE.fullmatch(value)
            and "cortex" not in value.casefold()
            and value.casefold() not in {"type-c", "usb-a"}
        ):
            return value
    return ""


def _valid_model(source: str, value: Any) -> bool:
    model = str(value or "").strip()
    source_models = {
        _normalized(re.sub(r"(?:MCU|SOC|IC)$", "", match.group(0), flags=re.IGNORECASE))
        for match in _MODEL_TOKEN_RE.finditer(source)
        if "cortex" not in match.group(0).casefold()
    }
    source_models.update(
        _normalized(match.group(1))
        for match in re.finditer(r"型号\s*[：:]?\s*(\d{5,})", source)
    )
    return bool(
        model
        and _normalized(model) not in {_normalized(item) for item in _GENERIC_MODEL_VALUES}
        and not _PARAMETER_SHAPED_MODEL_RE.fullmatch(model)
        and not _DIMENSION_SHAPED_MODEL_RE.fullmatch(model)
        and _normalized(model) in source_models
    )


def _source_backed_identity(source: str, key: str, reported_brand: Any = "") -> tuple[str, str]:
    manufacturer, _basis, _quote = manufacturer_from_evidence(
        source.replace("；", "，"), reported_brand, component_key=key
    )
    return manufacturer, _model_from_source(source)


def _primary_component_key(source: str) -> str:
    """Use the first supported component anchor in a validated caption block."""

    for clause in re.split(r"[；;]", source):
        key = component_key("", clause)
        if key in COMPONENT_LABELS:
            return key
    key = component_key("", source)
    return key if key in COMPONENT_LABELS else ""


def _parameters(source: str, proposed: Iterable[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(label: Any, value: Any) -> None:
        label_text = str(label or "").strip()
        value_text = str(value or "").strip()
        signature = (label_text, value_text.casefold())
        if not label_text or not value_text or signature in seen or not _is_verbatim(source, value_text):
            return
        seen.add(signature)
        result.append(
            {"label": label_text[:255], "value": value_text, "evidence_quote": source}
        )

    for item in proposed or []:
        if isinstance(item, dict):
            add(item.get("label"), item.get("value") or item.get("value_text"))
    for label, pattern in _PARAMETER_RULES:
        for match in pattern.finditer(source):
            add(label, match.groupdict().get("value") or match.group(0))
    return result


def _validated_model_rows(fact: dict[str, Any], source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    primary_key = _primary_component_key(source)
    for candidate in fact.get("bom_items") or []:
        if not isinstance(candidate, dict):
            continue
        component = str(candidate.get("component") or "").strip()
        key = component_key(component, source)
        if primary_key and key != primary_key:
            continue
        if key not in COMPONENT_LABELS or not component_evidence_supported(key, component, source):
            continue
        manufacturer = str(candidate.get("manufacturer") or candidate.get("brand") or "").strip()
        model = str(candidate.get("model") or "").strip()
        # Supplier and model fields are never accepted from model knowledge.
        if manufacturer and not _is_verbatim(source, manufacturer):
            manufacturer = ""
        if model and not _valid_model(source, model):
            model = ""
        rows.append(
            {
                "component": component,
                "component_key": key,
                "brand": manufacturer,
                "model": model,
                "side": _side(source, candidate.get("side"), fact.get("narrative_side")),
                "role": _classification(key),
                "qty_hint": str(candidate.get("qty_hint") or ""),
                "parameters": _parameters(source, candidate.get("parameters")),
                "confidence": min(float(candidate.get("confidence") or 0.82), 0.95),
            }
        )
    return rows


def structured_rows_from_video_fact(fact: dict[str, Any]) -> list[dict[str, Any]]:
    """Return validated BOM rows for one already-reviewed video fact."""

    source = str(fact.get("bom_context_text") or fact.get("raw_text") or "").strip()
    if not source:
        return []
    rows: list[dict[str, Any]] = []
    primary_key = _primary_component_key(source)
    for row in extract_bom_from_prose(source):
        original_key = component_key(row.get("component"), source)
        key = original_key
        if key not in COMPONENT_LABELS:
            key = component_key("", source)
        if key not in COMPONENT_LABELS or not component_evidence_supported(
            key, row.get("component"), source
        ):
            continue
        if primary_key and key != primary_key:
            continue
        manufacturer, direct_model = _source_backed_identity(source, key, row.get("brand"))
        model = str(row.get("model") or "").strip()
        if not _valid_model(source, model):
            model = direct_model
        rows.append(
            {
                **row,
                "component": (
                    str(row.get("component") or "")
                    if original_key == key
                    else COMPONENT_LABELS[key]
                ),
                "component_key": key,
                "brand": manufacturer,
                "model": model,
                "side": _side(source, row.get("side"), fact.get("narrative_side")),
                "role": _classification(key),
                "parameters": _parameters(source, row.get("parameters")),
            }
        )
    rows.extend(_validated_model_rows(fact, source))

    # Preserve a supported source fact even when the specialized prose rules
    # cannot identify a supplier/model. This is preferable to silently losing
    # a source-backed component from cost-engineer analysis.
    if not rows:
        key = primary_key or component_key("", source)
        if key in COMPONENT_LABELS:
            manufacturer, model = _source_backed_identity(source, key)
            rows.append(
                {
                    "component": COMPONENT_LABELS[key],
                    "component_key": key,
                    "brand": manufacturer,
                    "model": model,
                    "side": _side(source, narrative_side=fact.get("narrative_side")),
                    "role": _classification(key),
                    "qty_hint": "",
                    "parameters": _parameters(source),
                    "confidence": 0.76,
                }
            )

    deduplicated: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        if not _normalized(row.get("model")):
            compatible = next(
                (
                    existing
                    for identity, existing in deduplicated.items()
                    if identity[0] == str(row.get("component_key") or "")
                    and identity[2] == str(row.get("side") or "")
                ),
                None,
            )
            if compatible is not None:
                merged = {
                    (item.get("label"), item.get("value")): item
                    for item in [
                        *(compatible.get("parameters") or []),
                        *(row.get("parameters") or []),
                    ]
                    if isinstance(item, dict)
                }
                compatible["parameters"] = list(merged.values())
                continue
        identity = (
            str(row.get("component_key") or ""),
            _normalized(row.get("model")),
            str(row.get("side") or ""),
            _normalized(row.get("brand")),
        )
        current = deduplicated.get(identity)
        if current is None or len(row.get("parameters") or []) > len(current.get("parameters") or []):
            deduplicated[identity] = row
    return list(deduplicated.values())
