"""Refresh broken unboxing text from 52audio while preserving image identities."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.paths import products_dir, reports_dir, unboxing_enrich_dir, write_unboxing_enrich  # noqa: E402
from core.text_quality import (  # noqa: E402
    contains_c1_controls,
    iter_display_strings,
    repair_utf8_mojibake,
)
from scripts.enrich_unboxing import build_unboxing_payload  # noqa: E402
from sources.audio52.source_v2 import Audio52SourceV2  # noqa: E402


MODULES = ("packaging", "charging_case", "earbuds")


def _read_json(path: Path) -> dict:
    read_path = path
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        read_path = Path(f"\\\\?\\{path.resolve()}")
    payload = json.loads(read_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _write_json(path: Path, payload: dict) -> None:
    write_path = path
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        write_path = Path(f"\\\\?\\{path.resolve()}")
    write_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _url_key(url: str) -> str:
    repaired = repair_utf8_mojibake(url)
    original_cjk = sum("\u3400" <= char <= "\u9fff" for char in url)
    repaired_cjk = sum("\u3400" <= char <= "\u9fff" for char in repaired)
    if contains_c1_controls(url) or repaired_cjk > original_cjk:
        url = repaired
    return unquote(url).replace("\u00a0", " ").strip()


def _iter_images(payload: dict):
    for module_name in MODULES:
        module = payload.get(module_name) or {}
        for collection in ("images", "appearance_images"):
            for image in module.get(collection) or []:
                if isinstance(image, dict) and image.get("url"):
                    yield image


def _original_url_map(*payloads: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for payload in payloads:
        for image in _iter_images(payload):
            url = str(image.get("url") or "")
            result.setdefault(_url_key(url), url)
    return result


def _preserve_image_urls(fresh: dict, original_urls: dict[str, str]) -> None:
    for image in _iter_images(fresh):
        original = original_urls.get(_url_key(str(image.get("url") or "")))
        if original:
            image["url"] = original


def _repair_display_values(value, *, key: str = ""):
    if isinstance(value, dict):
        return {
            child_key: (
                child
                if child_key == "url"
                else _repair_display_values(child, key=child_key)
            )
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_repair_display_values(child, key=key) for child in value]
    if isinstance(value, str) and contains_c1_controls(value):
        return repair_utf8_mojibake(value)
    return value


def _fresh_image_map(fresh: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for image in _iter_images(fresh):
        result.setdefault(_url_key(str(image.get("url") or "")), image)
    return result


def _repair_staging_snapshot(old: dict, fresh: dict) -> dict:
    """Repair text in place so upstream image/module structure stays stable."""
    result = copy.deepcopy(old)
    fresh_images = _fresh_image_map(fresh)
    for image in _iter_images(result):
        source_image = fresh_images.get(_url_key(str(image.get("url") or "")))
        for field in ("alt", "caption"):
            current = str(image.get(field) or "")
            if not contains_c1_controls(current):
                continue
            replacement = str((source_image or {}).get(field) or "")
            image[field] = replacement or repair_utf8_mojibake(current)
    return _repair_display_values(result)


def _repair_product_snapshot(product: dict, fresh: dict, source_text: str) -> dict[str, int]:
    snapshot = product.get("unboxing") or {}
    fresh_images = _fresh_image_map(fresh)
    stats = {
        "descriptions": 0,
        "image_texts": 0,
        "other_texts": 0,
        "matched_images": 0,
        "fallbacks": 0,
    }
    normalized_source = " ".join(source_text.split())

    for module_name in MODULES:
        module = snapshot.get(module_name) or {}
        description = str(module.get("description") or "")
        if contains_c1_controls(description):
            repaired = repair_utf8_mojibake(description)
            if contains_c1_controls(repaired):
                raise ValueError(f"unresolved description in {product.get('canonical_id')}:{module_name}")
            # The repaired value must be traceable to the freshly decoded source.
            if " ".join(repaired.split()) not in normalized_source:
                stats["fallbacks"] += 1
            module["description"] = repaired
            stats["descriptions"] += 1
            description = repaired

        for image in module.get("appearance_images") or []:
            if not isinstance(image, dict):
                continue
            fresh_image = fresh_images.get(_url_key(str(image.get("url") or "")))
            if fresh_image:
                stats["matched_images"] += 1
            for field in ("alt", "caption"):
                current = str(image.get(field) or "")
                if not contains_c1_controls(current):
                    continue
                replacement = str((fresh_image or {}).get(field) or "")
                if not replacement:
                    replacement = repair_utf8_mojibake(current)
                    stats["fallbacks"] += 1
                if contains_c1_controls(replacement):
                    raise ValueError(
                        f"unresolved {field} in {product.get('canonical_id')}:{module_name}"
                    )
                image[field] = replacement
                stats["image_texts"] += 1

        for bullet in module.get("display_bullets") or []:
            if not isinstance(bullet, dict):
                continue
            for field in ("text", "evidence_quote"):
                current = str(bullet.get(field) or "")
                if not contains_c1_controls(current):
                    continue
                replacement = repair_utf8_mojibake(current)
                if contains_c1_controls(replacement):
                    # Some legacy strings lost individual source bytes and
                    # cannot be reversed losslessly.  The repaired module
                    # description is freshly decoded from the same report and
                    # is therefore the safest evidence-grounded replacement.
                    replacement = description
                    if field == "text" and len(replacement) > 160:
                        replacement = replacement[:160].rstrip("，。；、 ") + "……"
                    stats["fallbacks"] += 1
                bullet[field] = replacement
                stats["other_texts"] += 1

    # Display bullets and their evidence quotes are derived from the same
    # source-backed module text.  Older snapshots copied their mojibake after
    # the description was built, so repair those remaining reversible strings
    # without changing URLs or inventing replacement content.
    stats["other_texts"] += sum(
        contains_c1_controls(value) for _, value in iter_display_strings(snapshot)
    )
    product["unboxing"] = _repair_display_values(snapshot)
    unresolved = [
        field
        for field, value in iter_display_strings(product["unboxing"])
        if contains_c1_controls(value)
    ]
    if unresolved:
        raise ValueError(
            f"unresolved display text in {product.get('canonical_id')}: {unresolved[:5]}"
        )
    return stats


def _is_affected(product: dict) -> bool:
    return any(
        contains_c1_controls(value)
        for _, value in iter_display_strings(product.get("unboxing") or {})
    )


def _fetch_fresh(item: tuple[Path, dict]) -> tuple[Path, dict, str, dict, str]:
    product_path, product = item
    report_id = str((product.get("unboxing") or {}).get("report_id") or "")
    if not report_id:
        raise ValueError(f"missing unboxing report_id for {product.get('canonical_id')}")
    report = _read_json(reports_dir() / f"{report_id}.json")
    source = Audio52SourceV2(timeout=45)
    html = source.fetch_article_html(str(report.get("url") or ""))
    fresh = build_unboxing_payload(
        report,
        html=html,
        canonical_id=str(product.get("canonical_id") or ""),
    )
    return product_path, product, report_id, fresh, html


def repair_all(*, apply: bool, delay_sec: float, workers: int) -> dict:
    affected: list[tuple[Path, dict]] = []
    for path in sorted(products_dir().glob("*.json")):
        if path.name == "index.json":
            continue
        product = _read_json(path)
        if _is_affected(product):
            affected.append((path, product))

    totals = {
        "products": len(affected),
        "reports": 0,
        "descriptions": 0,
        "image_texts": 0,
        "other_texts": 0,
        "matched_images": 0,
        "fallbacks": 0,
    }
    if not apply:
        return totals

    staging_dir = unboxing_enrich_dir(for_write=True)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        refreshed = list(executor.map(_fetch_fresh, affected))

    for index, (product_path, product, report_id, fresh, html) in enumerate(refreshed, 1):

        staging_path = staging_dir / f"{report_id}.json"
        old_staging = _read_json(staging_path) if staging_path.exists() else {}
        original_urls = _original_url_map(old_staging, product.get("unboxing") or {})
        _preserve_image_urls(fresh, original_urls)
        staging_payload = (
            _repair_staging_snapshot(old_staging, fresh) if old_staging else fresh
        )

        source_text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        product_stats = _repair_product_snapshot(product, fresh, source_text)
        for key, value in product_stats.items():
            totals[key] += value

        write_unboxing_enrich(report_id, staging_payload)
        _write_json(product_path, product)
        totals["reports"] += 1
        print(
            f"[repair-unboxing] {index}/{len(affected)} "
            f"{product.get('canonical_id')} report={report_id}",
            flush=True,
        )
        if delay_sec > 0 and index < len(affected):
            time.sleep(delay_sec)
    return totals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write refreshed staging and curated product data")
    parser.add_argument("--delay", type=float, default=0.2, help="delay between source requests")
    parser.add_argument("--workers", type=int, default=4, help="parallel source requests")
    args = parser.parse_args()
    stats = repair_all(
        apply=args.apply,
        delay_sec=max(0.0, args.delay),
        workers=max(1, args.workers),
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
