#!/usr/bin/env python3
"""一键跑通原文优先的产品详情重构主链路。

用法:
  py -3 scripts/build_all.py
  py -3 scripts/build_all.py --skip-unboxing
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(script: str, *args: str) -> dict:
    cmd = [sys.executable, str(ROOT / "scripts" / script), *args]
    child_env = dict(os.environ)
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
        raise SystemExit(proc.returncode)
    try:
        return json.loads((proc.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        return {"stdout": (proc.stdout or "").strip()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-matrix", action="store_true")
    parser.add_argument("--skip-unboxing", action="store_true")
    parser.add_argument("--skip-prune", action="store_true")
    parser.add_argument("--reprocess-source", action="store_true", help="从原始文章 HTML 重新提取全部报告 views")
    parser.add_argument("--use-llm", action="store_true", help="使用配置的文本模型整理详情文字")
    parser.add_argument("--force", action="store_true", help="覆盖现有派生缓存")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    stats: dict = {}
    if args.reprocess_source:
        stats["reprocess_report_views"] = _run(
            "reprocess_views.py",
            "--reports-only",
            "--cache-only",
            "--workers",
            str(max(1, args.workers)),
        )
    if args.use_llm:
        stats["identity_repair"] = _run(
            "repair_product_identities.py",
            "--all",
            "--write-overrides",
            "--title-only",
            "--missing-brand-only",
            "--workers",
            str(max(1, min(args.workers, 3))),
        )
    stats["build_products"] = _run("build_products.py")
    if args.use_llm:
        stats["earphone_categories"] = _run(
            "classify_earphone_categories.py",
            "--resume",
            "--workers",
            str(max(1, min(args.workers, 3))),
        )
        stats["build_products_after_categories"] = _run("build_products.py")
    if not args.skip_prune:
        stats["prune_non_headphones"] = _run("prune_non_headphones.py")
    if not args.skip_unboxing:
        unboxing_args = ["--headphones"]
        if args.force:
            unboxing_args.append("--force")
        stats["enrich_unboxing"] = _run("enrich_unboxing.py", *unboxing_args)
    detail_args = ["--workers", str(max(1, args.workers))]
    if args.use_llm:
        detail_args.append("--use-llm")
    if args.force:
        detail_args.append("--force")
    stats["enrich_product_details"] = _run("enrich_product_details.py", *detail_args)
    stats["build_products_after_details"] = _run("build_products.py")
    if not args.skip_matrix:
        stats["build_matrix"] = _run("build_matrix.py")
    stats["prepare_web_data"] = _run("prepare_web_data.py")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
