"""52audio v2 解析：消费 RSS，输出 ReportRecord / VideoRecord（不含 content_html）。"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from core.base_source import BaseSource
from core.models_v2 import ReportRecord, RoleViews, VideoRecord
from core.views.role_extract import extract_role_views, views_to_dict_with_completeness
from core.extract.text_utils import extract_video_embed_urls
from sources.audio52.parse_title import parse_title

FEED_BASE = "https://www.52audio.com/archives/category/teardowns/feed/"

_NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "52audio-intel-bot/0.2"
)

_ID_RE = re.compile(r"/archives/(\d+)\.html")


def _to_iso_date(pub_date_raw: str) -> str:
    try:
        dt = parsedate_to_datetime(pub_date_raw)
        return dt.date().isoformat()
    except Exception:
        return ""


def _parse_pub_date(pub_date_raw: str) -> date | None:
    try:
        return parsedate_to_datetime(pub_date_raw).date()
    except Exception:
        return None


def _extract_id(url: str) -> str:
    m = _ID_RE.search(url)
    return m.group(1) if m else re.sub(r"\W+", "_", url)[-32:]


def _clean_description(desc: str) -> str:
    desc = re.sub(r"<[^>]+>", "", desc or "")
    return desc.replace("&#8230;", "…").replace("&nbsp;", " ").strip()


def _guess_video_source_site(embed_url: str) -> str:
    if not embed_url:
        return ""
    if "bilibili" in embed_url:
        return "哔哩哔哩 (Bilibili)"
    if "youtube" in embed_url or "youtu.be" in embed_url:
        return "YouTube"
    return "未知视频平台"


class Audio52SourceV2(BaseSource):
    source_id = "audio52"
    display_name = "我爱音频网 52audio.com（拆解分类）"

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        request_delay_sec: float = 1.0,
        timeout: int = 20,
        max_pages: int = 50,
    ) -> None:
        self.user_agent = user_agent
        self.request_delay_sec = request_delay_sec
        self.timeout = timeout
        self.max_pages = max_pages
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/rss+xml, text/xml"})

    def _fetch_page(self, page: int) -> list[dict]:
        url = FEED_BASE if page == 1 else f"{FEED_BASE}?paged={page}"
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            return []
        items = []
        for it in channel.findall("item"):
            link = (it.findtext("link") or "").strip()
            if not link:
                continue
            pub_raw = (it.findtext("pubDate") or "").strip()
            items.append(
                {
                    "url": link,
                    "title": (it.findtext("title") or "").strip(),
                    "author": (it.findtext("dc:creator", namespaces=_NS) or "").strip(),
                    "date": _to_iso_date(pub_raw),
                    "pub_date": _parse_pub_date(pub_raw),
                    "description": _clean_description(it.findtext("description") or ""),
                    "content_html": it.findtext("content:encoded", namespaces=_NS) or "",
                    "raw_categories": [c.text.strip() for c in it.findall("category") if c.text],
                }
            )
        return items

    def iter_feed_items(
        self,
        *,
        year: int | None = None,
        max_pages: int | None = None,
        stop_before: date | None = None,
    ):
        """遍历 RSS 页。year=2026 时只 yield 该年 pubDate 条目，翻过边界后停止。"""
        limit_pages = max_pages or self.max_pages
        for page in range(1, limit_pages + 1):
            try:
                page_items = self._fetch_page(page)
            except Exception as e:
                print(f"[audio52] feed 第{page}页失败: {e}")
                break
            if not page_items:
                break

            dates_on_page = [it["pub_date"] for it in page_items if it.get("pub_date")]
            oldest = min(dates_on_page) if dates_on_page else None

            for it in page_items:
                if year and it.get("pub_date"):
                    if it["pub_date"].year != year:
                        continue
                if stop_before and it.get("pub_date") and it["pub_date"] < stop_before:
                    continue
                yield it

            if year and oldest and oldest.year < year:
                break
            if stop_before and oldest and oldest < stop_before:
                break

            time.sleep(self.request_delay_sec)

    def build_feed_content_index(self, max_pages: int | None = None) -> dict[str, str]:
        """遍历 RSS，返回 {article_id: content_html}（仅内存，不入库）。"""
        index: dict[str, str] = {}
        for it in self.iter_feed_items(max_pages=max_pages):
            item_id = _extract_id(it["url"])
            html_body = it.get("content_html") or ""
            if html_body:
                index[item_id] = html_body
        return index

    def fetch_article_html(self, url: str) -> str:
        """从单篇 URL 抓取正文 HTML（带 Referer，不入库）。"""
        resp = self.session.get(
            url,
            headers={"Referer": "https://www.52audio.com/", "Accept": "text/html"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        # 52audio serves UTF-8 HTML without a charset parameter.  Requests
        # consequently defaults ``text/html`` to ISO-8859-1 and turns Chinese
        # into mojibake when ``resp.text`` is used.  Parse the original bytes
        # with the site's actual encoding instead.
        soup = BeautifulSoup(resp.content, "lxml", from_encoding="utf-8")
        node = (
            soup.select_one(".entry-content")
            or soup.select_one("article .post-content")
            or soup.select_one(".post-content")
            or soup.select_one("article")
        )
        if not node:
            return ""
        return str(node)

    def resolve_content_html(self, item_id: str, url: str, feed_index: dict[str, str] | None = None) -> str:
        """优先 RSS 缓存，否则单篇 fetch。"""
        if feed_index and item_id in feed_index:
            return feed_index[item_id]
        try:
            return self.fetch_article_html(url)
        except Exception as e:
            print(f"[audio52] fetch {item_id} 失败: {e}")
            return ""

    def parse_item(self, item: dict) -> ReportRecord | VideoRecord | None:
        parsed = parse_title(item["title"])
        content_html = item.get("content_html", "")
        video_embeds = extract_video_embed_urls(content_html)
        content_type = parsed.content_type
        if content_type == "unknown":
            content_type = "video" if video_embeds else "report"

        captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        item_id = _extract_id(item["url"])

        if content_type == "video":
            embed = video_embeds[0] if video_embeds else ""
            views = RoleViews()
            data_completeness = 0.0
            if content_html:
                views = extract_role_views(
                    content_html,
                    brand=parsed.brand,
                    model=parsed.model,
                    category=parsed.category,
                )
                data_completeness = views_to_dict_with_completeness(views)[1]
            return VideoRecord(
                id=item_id,
                url=item["url"],
                title=parsed.product_title,
                product_title=parsed.product_title,
                brand=parsed.brand,
                model=parsed.model,
                category=parsed.category,
                published_at=item.get("date", ""),
                publisher=item.get("author", ""),
                summary=item.get("description", ""),
                source_site=_guess_video_source_site(embed),
                video_embed_url=embed,
                captured_at=captured_at,
                data_completeness=data_completeness,
                views=views,
            )

        views = extract_role_views(
            content_html,
            brand=parsed.brand,
            model=parsed.model,
            category=parsed.category,
        )
        data_completeness = views_to_dict_with_completeness(views)[1]
        return ReportRecord(
            id=item_id,
            url=item["url"],
            title=parsed.product_title,
            brand=parsed.brand,
            model=parsed.model,
            category=parsed.category,
            published_at=item.get("date", ""),
            author=item.get("author", ""),
            summary=item.get("description", ""),
            captured_at=captured_at,
            data_completeness=data_completeness,
            views=views,
        )

    # BaseSource 兼容占位
    def fetch_list(self, limit: int = 30) -> list[dict]:
        out = []
        for it in self.iter_feed_items(max_pages=10):
            out.append(it)
            if len(out) >= limit:
                break
        return out

    def parse_detail(self, item: dict):
        return self.parse_item(item)
