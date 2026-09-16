"""Load conservative, human-reviewed catalog overrides.

Raw report/video files remain immutable evidence.  The override registry only
controls whether a source participates in the product catalog and how its
reviewed identity and business taxonomy are represented.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Mapping

from core.paths import catalog_manual_overrides_path


@lru_cache(maxsize=1)
def load_catalog_manual_overrides() -> dict[str, Any]:
    path = catalog_manual_overrides_path()
    if not path.is_file():
        return {"records": {}}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("catalog manual overrides must be a JSON object")
    records = payload.get("records")
    if records is not None and not isinstance(records, dict):
        raise ValueError("catalog manual override records must be an object")
    return payload


def source_override(
    source_type: str,
    record: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = overrides if overrides is not None else load_catalog_manual_overrides()
    records = payload.get("records") if isinstance(payload, Mapping) else None
    if not isinstance(records, Mapping):
        return {}
    source_id = str(record.get("id") or "").strip()
    value = records.get(f"{source_type}:{source_id}")
    return dict(value) if isinstance(value, Mapping) else {}


def source_is_catalog_excluded(
    source_type: str,
    record: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> bool:
    return source_override(source_type, record, overrides).get("action") == "exclude_catalog"


def reviewed_brand_aliases(
    overrides: Mapping[str, Any] | None = None,
) -> list[tuple[str, list[str]]]:
    """Return only brand aliases explicitly accepted in human review."""

    payload = overrides if overrides is not None else load_catalog_manual_overrides()
    groups = payload.get("brand_aliases") if isinstance(payload, Mapping) else None
    resolved: list[tuple[str, list[str]]] = []
    for group in groups or []:
        if not isinstance(group, Mapping):
            continue
        canonical = str(group.get("canonical") or "").strip()
        aliases = [
            str(value).strip()
            for value in group.get("aliases") or []
            if str(value).strip()
        ]
        if canonical:
            resolved.append((canonical, list(dict.fromkeys([canonical, *aliases]))))
    return resolved


def combined_brand_aliases(
    base_aliases: list[tuple[str, list[str]]],
    overrides: Mapping[str, Any] | None = None,
) -> list[tuple[str, list[str]]]:
    """Overlay reviewed aliases on the parser's built-in brand lexicon.

    A reviewed alias must have one deterministic owner.  It is removed from
    any older built-in group before the accepted group is appended.  This
    prevents equal-length aliases from being resolved by display-name sort
    order instead of the human decision.
    """

    reviewed = reviewed_brand_aliases(overrides)
    claimed = {
        alias.casefold()
        for _canonical, aliases in reviewed
        for alias in aliases
    }
    combined: list[tuple[str, list[str]]] = []
    for canonical, aliases in base_aliases:
        remaining = [alias for alias in aliases if alias.casefold() not in claimed]
        if remaining:
            combined.append((canonical, remaining))
    combined.extend(reviewed)
    return combined
