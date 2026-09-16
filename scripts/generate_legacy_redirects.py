"""生成旧版 URL 跳转页，兼容 /reports/{id}.html 等 V3/V4 书签。"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DATA = ROOT / "data"
REPORTS = DATA / "reports"
PRODUCTS = DATA / "products"
COMPARE = DATA / "compare"

# 与 web/astro.config.mjs 中 base 一致（无前导域名）
BASE = "/headphone-component-intelligence"


def _redirect_html(target: str, title: str = "跳转中…") -> str:
    esc = target.replace("&", "&amp;").replace('"', "&quot;")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="refresh" content="0; url={esc}" />
  <link rel="canonical" href="{esc}" />
  <title>{title}</title>
  <script>location.replace("{target.replace('"', '\\"')}");</script>
</head>
<body>
  <p>页面已迁移，正在跳转… <a href="{esc}">点此继续</a></p>
</body>
</html>
"""


def _write(path: Path, target: str, title: str = "跳转中…") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_redirect_html(target, title), encoding="utf-8")


def generate() -> dict:
    if not SITE.exists():
        SITE.mkdir(parents=True)

    home = f"{BASE}/"
    written = {"reports": 0, "products": 0, "compare": 0, "misc": 0}

    reports_dir = SITE / "reports"
    if REPORTS.exists():
        for path in REPORTS.glob("*.json"):
            rid = path.stem
            _write(
                reports_dir / f"{rid}.html",
                f"{BASE}/report/{rid}",
                f"拆解报告 {rid}",
            )
            written["reports"] += 1
    _write(reports_dir / "index.html", home, "拆解报告列表")

    products_dir = SITE / "products"
    if PRODUCTS.exists():
        for path in PRODUCTS.glob("*.json"):
            if path.name == "index.json":
                continue
            try:
                product = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            cid = product.get("canonical_id") or path.stem
            _write(
                products_dir / f"{cid}.html",
                f"{BASE}/product/{cid}",
                product.get("model") or cid,
            )
            written["products"] += 1

    compare_dir = SITE / "compare"
    if COMPARE.exists():
        for path in COMPARE.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                cat = payload.get("category", path.stem)
            except Exception:
                cat = path.stem
            safe = re.sub(r'[<>:"/\\|?*]', "_", cat.strip())
            _write(
                compare_dir / f"{safe}.html",
                f"{BASE}/category/{cat}",
                f"{cat} 对比",
            )
            written["compare"] += 1

    _write(SITE / "matrix" / "index.html", f"{BASE}/category/开放式耳机", "竞品矩阵")
    _write(SITE / "about.html", home, "关于本站")
    written["misc"] += 2

    videos_dir = SITE / "videos"
    _write(videos_dir / "index.html", home, "拆解视频")
    written["misc"] += 1

    return written


def main() -> None:
    stats = generate()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
