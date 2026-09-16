"""将 OCR enrich 结果合并进 report views（hardware.specs + cost.bom_table）。

用法：
  python scripts/merge_ocr.py --id 265818
  python scripts/merge_ocr.py --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.paths import ocr_enrich_dir  # noqa: E402

from core.ingest import REPORTS_DIR  # noqa: E402
from core.views.role_extract import _bom_dedup_key, _merge_bom_with_summary, _normalize_bom_row  # noqa: E402

OCR_DIR = ocr_enrich_dir()

SOURCE_CONFIDENCE = {
    "migrated": 0.4,
    "text": 0.5,
    "summary_prose": 0.88,
    "summary_ocr": 0.72,
    "ocr": 0.75,
}


def _spec_confidence(spec: dict) -> float:
    if "confidence" in spec and spec["confidence"] is not None:
        return float(spec["confidence"])
    return SOURCE_CONFIDENCE.get(spec.get("source_ref", ""), 0.45)


def _spec_key(spec: dict) -> tuple[str, str, str]:
    part = (spec.get("part") or spec.get("param") or "").strip()
    value = str(spec.get("value") or spec.get("model") or "").strip().upper()
    unit = (spec.get("unit") or "").strip().lower()
    return part, value, unit


def _bom_confidence(row: dict) -> float:
    ev = row.get("evidence") or {}
    if isinstance(ev, dict) and ev.get("confidence") is not None:
        return float(ev["confidence"])
    st = ev.get("source_type", "text") if isinstance(ev, dict) else "text"
    return SOURCE_CONFIDENCE.get(st, 0.5)


def merge_ocr_specs(report: dict, ocr_payload: dict) -> tuple[dict, int]:
    views = dict(report.get("views", {}))
    hardware = dict(views.get("hardware", {}))
    specs: list[dict] = list(hardware.get("specs", []))

    best: dict[tuple[str, str, str], float] = {}
    for s in specs:
        best[_spec_key(s)] = max(best.get(_spec_key(s), 0.0), _spec_confidence(s))

    added = 0
    for img in ocr_payload.get("images", []):
        for raw_spec in img.get("specs_extracted", []):
            new_spec = {
                "param": raw_spec.get("part", ""),
                "part": raw_spec.get("part", ""),
                "brand": "",
                "model": "",
                "value": raw_spec.get("value", ""),
                "unit": raw_spec.get("unit", ""),
                "source_ref": "ocr",
                "confidence": float(raw_spec.get("confidence", SOURCE_CONFIDENCE["ocr"])),
            }
            key = _spec_key(new_spec)
            conf = new_spec["confidence"]
            if conf <= best.get(key, 0.0):
                continue
            specs.append(new_spec)
            best[key] = conf
            added += 1

    hardware["specs"] = specs
    views["hardware"] = hardware
    out = dict(report)
    out["views"] = views
    return out, added


def merge_ocr_bom(report: dict, ocr_payload: dict) -> tuple[dict, int]:
    """OCR BOM 行合并进 cost.bom_table（不覆盖高置信度 summary_prose）。"""
    views = dict(report.get("views", {}))
    cost = dict(views.get("cost", {}))
    existing = list(cost.get("bom_table") or [])

    best_conf: dict[tuple, float] = {}
    for row in existing:
        norm = _normalize_bom_row(row) if row.get("evidence") else row
        best_conf[_bom_dedup_key(norm)] = _bom_confidence(norm)

    ocr_rows: list[dict] = []
    for img in ocr_payload.get("images", []):
        for row in img.get("bom_rows_extracted", []):
            ocr_rows.append(_normalize_bom_row(row))

    added = 0
    for row in ocr_rows:
        key = _bom_dedup_key(row)
        conf = _bom_confidence(row)
        if conf <= best_conf.get(key, 0.0):
            continue
        best_conf[key] = conf
        added += 1

    if not ocr_rows:
        return report, 0

    merged = _merge_bom_with_summary(existing, ocr_rows)
    # 重新应用置信度过滤：仅保留 OCR 新增或升级的行
    if added:
        cost["bom_table"] = merged
        views["cost"] = cost
        out = dict(report)
        out["views"] = views
        return out, added
    return report, 0


def merge_ocr_into_report(report: dict, ocr_payload: dict) -> tuple[dict, int]:
    report, specs_added = merge_ocr_specs(report, ocr_payload)
    report, bom_added = merge_ocr_bom(report, ocr_payload)
    return report, specs_added + bom_added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", help="指定报告 ID")
    parser.add_argument("--all", action="store_true", help="合并全部已有 OCR enrich")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写回")
    args = parser.parse_args()

    ocr_files: list[Path] = []
    if args.id:
        p = OCR_DIR / f"{args.id}.json"
        if not p.exists():
            parser.error(f"OCR enrich 不存在: {args.id}")
        ocr_files = [p]
    elif args.all:
        ocr_files = sorted(OCR_DIR.glob("*.json"))
    else:
        parser.error("需要 --id 或 --all")

    total_added = 0
    for ocr_path in ocr_files:
        ocr_payload = json.loads(ocr_path.read_text(encoding="utf-8"))
        rid = ocr_payload.get("report_id") or ocr_path.stem
        report_path = REPORTS_DIR / f"{rid}.json"
        if not report_path.exists():
            print(f"[skip] 报告不存在: {rid}")
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        merged, added = merge_ocr_into_report(report, ocr_payload)
        total_added += added
        print(f"{rid}: +{added} fields")
        if not args.dry_run and added:
            report_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Done. total_added={total_added}")


if __name__ == "__main__":
    main()
