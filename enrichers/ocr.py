"""轻量 OCR enrich：对文本密集图跑 tesseract，输出 data/enrich/ocr/{report_id}.json。

用法：
  python -m enrichers.ocr --id 265818
  python -m enrichers.ocr --pending   # 处理 images_queue 中尚未 enrich 的报告
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.extract.images import _classify_image_bytes, _run_ocr, tesseract_available  # noqa: E402
from core.extract.text_utils import extract_all_image_urls  # noqa: E402
from core.ingest import REPORTS_DIR, load_all_records  # noqa: E402

ENRICH_DIR = ROOT / "data" / "enrich" / "ocr"
QUEUE_PATH = ROOT / "data" / "images_queue.json"
FEED_BASE = "https://www.52audio.com/archives/category/teardowns/feed/"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 52audio-intel-bot/0.2"
)

_SPEC_PATTERNS: list[tuple[str, str, str, float]] = [
    ("电池", r"(\d+)\s*mAh", "mAh", 0.85),
    ("尺寸", r"(\d+(?:\.\d+)?)\s*mm", "mm", 0.7),
    ("重量", r"(\d+(?:\.\d+)?)\s*(?:g|克)", "g", 0.75),
    ("防护等级", r"(IP\s*X?\d{1,2})", "", 0.9),
    ("充电接口", r"(Type-?C)", "", 0.85),
    ("蓝牙", r"蓝牙[^0-9]*V?([0-9]\.[0-9]+)", "", 0.85),
    ("蓝牙", r"Bluetooth\s*V?([0-9]\.[0-9]+)", "", 0.85),
]


def extract_specs_from_ocr_text(text: str) -> list[dict]:
    """从 OCR 文本正则提取硬件规格。"""
    if not text or not text.strip():
        return []
    specs: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for part, pattern, unit, conf in _SPEC_PATTERNS:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            raw = m.group(0)
            value = m.group(1) if m.lastindex else raw
            key = (part, value.strip().upper())
            if key in seen:
                continue
            seen.add(key)
            specs.append(
                {
                    "part": part,
                    "value": value.strip(),
                    "unit": unit,
                    "confidence": conf,
                    "raw": raw.strip(),
                }
            )
    return specs


_BOM_LINE_SPLIT = re.compile(r"[\t|｜]+")
_BOM_CHIP_IN_LINE = re.compile(
    r"(INJOINIC英集芯|英集芯|Bluetrum中科蓝讯|中科蓝讯|Qualcomm高通|BES恒玄|恒玄|"
    r"JL杰理|杰理|SY\d+|IP\d+|BT\d+)[^\n，。；]*",
    re.I,
)


def parse_bom_table_from_ocr(text: str) -> list[dict]:
    """从 OCR 文本解析 BOM 行。"""
    if not text or not text.strip():
        return []
    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add_row(component: str, brand: str, model: str, side: str = "", role: str = "") -> None:
        key = (component, brand, model)
        if key in seen or not (component or model):
            return
        seen.add(key)
        rows.append(
            {
                "component": component or "部件",
                "brand": brand,
                "model": model,
                "qty_hint": "",
                "side": side,
                "role": role,
                "evidence": {
                    "value": model or component,
                    "confidence": 0.72,
                    "source_type": "summary_ocr",
                    "source_text": (text[:120] + "…") if len(text) > 120 else text,
                },
            }
        )

    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) < 4:
            continue
        parts = [p.strip() for p in _BOM_LINE_SPLIT.split(line) if p.strip()]
        if len(parts) >= 3:
            add_row(parts[0], parts[1], parts[2])
            continue
        if "电池" in line and "mAh" in line:
            m = re.search(r"(\d+)\s*mAh", line, re.I)
            vendor = re.search(r"([A-Za-z\u4e00-\u9fff]{2,12})", line)
            add_row("电池", vendor.group(1) if vendor else "", m.group(0) if m else "", side="")
        for m in _BOM_CHIP_IN_LINE.finditer(line):
            frag = m.group(0)
            chip_m = re.search(r"([A-Z]{1,4}\d+[A-Z0-9]*)", frag, re.I)
            if chip_m:
                role = "PMIC/充电仓管理" if chip_m.group(1).upper().startswith("IP") else "主控/蓝牙"
                add_row("芯片/模组", "", chip_m.group(1), role=role)

    return rows[:40]


def _fetch_content_html_for_report(report: dict, session: requests.Session) -> str:    """从 RSS feed 或文章页按 URL 找回正文 HTML。"""
    article_url = report.get("url", "")
    if not article_url:
        return ""

    # 1) RSS（近期文章）
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    from xml.etree import ElementTree as ET

    for page in range(1, 16):
        url = FEED_BASE if page == 1 else f"{FEED_BASE}?paged={page}"
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
        except Exception:
            break
        root = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            break
        for it in channel.findall("item"):
            link = (it.findtext("link") or "").strip()
            if link == article_url or report["id"] in link:
                html = it.findtext("content:encoded", namespaces=ns) or ""
                if html:
                    return html
        time.sleep(0.3)

    # 2) 文章详情页（backfill 老文章 RSS 翻不到时）
    try:
        resp = session.get(article_url, timeout=20)
        if resp.status_code == 200 and resp.text:
            return resp.text
    except Exception:
        pass
    return ""


def _urls_from_images_queue(report: dict) -> list[str]:
    """从 images_queue 按 article_url 兜底取已知文本密集图。"""
    if not QUEUE_PATH.exists():
        return []
    try:
        queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    article_url = report.get("url", "")
    urls = []
    for item in queue.get("items", []):
        if item.get("article_url") == article_url:
            u = item.get("image_url")
            if u:
                urls.append(u)
    return urls


def resolve_image_urls(report: dict, session: requests.Session) -> list[str]:
    """优先 summary_image_urls，其次 key_image_urls，再正文/queue。"""
    views = report.get("views") or {}
    cost = views.get("cost") or {}
    summary_urls: list[str] = []
    for item in cost.get("summary_image_urls") or []:
        if isinstance(item, str) and item.strip():
            summary_urls.append(item.strip())
        elif isinstance(item, dict) and item.get("url"):
            summary_urls.append(item["url"])
    if summary_urls:
        return summary_urls

    key_urls = report.get("key_image_urls")    if isinstance(key_urls, list) and key_urls:
        return [u for u in key_urls if isinstance(u, str) and u.strip()]

    queue_urls = _urls_from_images_queue(report)
    if queue_urls:
        return queue_urls

    content_html = report.get("content_html") or ""
    if not content_html:
        content_html = _fetch_content_html_for_report(report, session)
    if not content_html:
        return []

    raw = extract_all_image_urls(content_html)
    return [img["url"] for img in raw if img.get("url")]


def _download_image(url: str, session: requests.Session, timeout: int = 15) -> bytes | None:
    headers = {"Referer": "https://www.52audio.com/", "User-Agent": DEFAULT_UA}
    try:
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


def enrich_one_report(report: dict, session: requests.Session, *, delay_sec: float = 0.4) -> dict:
    """对单篇报告跑 OCR enrich，返回写入 JSON 的 payload。"""
    views = report.get("views") or {}
    cost = views.get("cost") or {}
    summary_url_set = set()
    for item in cost.get("summary_image_urls") or []:
        if isinstance(item, str):
            summary_url_set.add(item)
        elif isinstance(item, dict) and item.get("url"):
            summary_url_set.add(item["url"])

    urls = resolve_image_urls(report, session)    images_out: list[dict] = []

    for url in urls:
        entry: dict = {
            "url": url,
            "ocr_status": "skipped",
            "ocr_text": None,
            "classification": "unknown",
            "specs_extracted": [],
        }
        img_bytes = _download_image(url, session)
        if not img_bytes:
            entry["ocr_status"] = "download_failed"
            images_out.append(entry)
            continue

        info = _classify_image_bytes(img_bytes)
        entry["classification"] = info.get("classification", "unknown")

        if info.get("classification") != "text_dense":
            is_summary = url in summary_url_set
            if not is_summary:                entry["ocr_status"] = "skipped"
                images_out.append(entry)
                if delay_sec:
                    time.sleep(delay_sec)
                continue

        status, text = _run_ocr(img_bytes)
        entry["ocr_status"] = status
        entry["ocr_text"] = text
        if text:
            entry["specs_extracted"] = extract_specs_from_ocr_text(text)
            entry["bom_rows_extracted"] = parse_bom_table_from_ocr(text)
        images_out.append(entry)
        if delay_sec:
            time.sleep(delay_sec)

    return {
        "report_id": report["id"],
        "tesseract_available": tesseract_available(),
        "enriched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "images": images_out,
    }


def _pending_report_ids() -> list[str]:
    """images_queue 里出现过、且尚无 OCR enrich 结果的报告 ID。"""
    if not QUEUE_PATH.exists():
        return []
    try:
        queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    ids: set[str] = set()
    for item in queue.get("items", []):
        url = item.get("article_url", "")
        m = re.search(r"/archives/(\d+)\.html", url)
        if m:
            ids.add(m.group(1))
    done = {p.stem for p in ENRICH_DIR.glob("*.json")}
    return sorted(ids - done)[:20]


def main() -> None:
    parser = argparse.ArgumentParser(description="报告 OCR enrich")
    parser.add_argument("--id", help="指定报告 ID")
    parser.add_argument("--pending", action="store_true", help="处理 queue 中待 enrich 的报告（最多 20 条）")
    args = parser.parse_args()
    ENRICH_DIR.mkdir(parents=True, exist_ok=True)

    targets: list[dict] = []
    if args.id:
        p = REPORTS_DIR / f"{args.id}.json"
        if p.exists():
            targets.append(json.loads(p.read_text(encoding="utf-8")))
        else:
            parser.error(f"报告不存在: {args.id}")
    elif args.pending:
        for rid in _pending_report_ids():
            p = REPORTS_DIR / f"{rid}.json"
            if p.exists():
                targets.append(json.loads(p.read_text(encoding="utf-8")))
    else:
        parser.error("需要 --id 或 --pending")

    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_UA})

    for report in targets:
        print(f"OCR enrich {report['id']}...")
        result = enrich_one_report(report, session)
        out_path = ENRICH_DIR / f"{report['id']}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
