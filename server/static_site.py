from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


ASSET_REFERENCE = re.compile(r"(?:^|[\"'=])([^\"']*/_astro/[^\"'?#\s>]+)")


@dataclass(frozen=True)
class StaticSiteState:
    ready: bool
    references: int
    missing: tuple[str, ...]
    reason: str = ""
    observed_base_paths: tuple[str, ...] = ()
    expected_base_path: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "references": self.references,
            "missing": list(self.missing),
            "reason": self.reason,
            "observed_base_paths": list(self.observed_base_paths),
            "expected_base_path": self.expected_base_path,
        }


def inspect_static_site(site_dir: Path, *, expected_base_path: str | None = None) -> StaticSiteState:
    """Verify that the HTML entry point and every referenced Astro asset exist."""
    site_dir = site_dir.resolve()
    index = site_dir / "index.html"
    if not index.is_file():
        return StaticSiteState(False, 0, ("index.html",), "index_missing")

    html = index.read_text(encoding="utf-8", errors="replace")
    references: set[str] = set()
    observed_base_paths: set[str] = set()
    for match in ASSET_REFERENCE.finditer(html):
        path = unquote(urlsplit(match.group(1)).path)
        marker = "/_astro/"
        if marker not in path:
            continue
        observed_base_paths.add(f"{path.split(marker, 1)[0]}/" or "/")
        references.add(f"_astro/{path.split(marker, 1)[1]}")

    if not references:
        return StaticSiteState(False, 0, (), "astro_asset_references_missing")
    missing = tuple(sorted(rel for rel in references if not (site_dir / rel).is_file()))
    expected = ""
    if expected_base_path:
        expected = f"/{expected_base_path.strip('/')}" if expected_base_path.strip("/") else ""
        expected = f"{expected}/" if expected else "/"
    observed = tuple(sorted(observed_base_paths))
    if expected and observed != (expected,):
        return StaticSiteState(
            False,
            len(references),
            missing,
            "base_path_mismatch",
            observed,
            expected,
        )
    return StaticSiteState(
        not missing,
        len(references),
        missing,
        "" if not missing else "assets_missing",
        observed,
        expected,
    )


def assert_static_site_complete(site_dir: Path, *, expected_base_path: str | None = None) -> StaticSiteState:
    state = inspect_static_site(site_dir, expected_base_path=expected_base_path)
    if not state.ready:
        detail = ", ".join(state.missing[:5]) or state.reason
        raise RuntimeError(f"static site is incomplete: {detail}")
    return state
