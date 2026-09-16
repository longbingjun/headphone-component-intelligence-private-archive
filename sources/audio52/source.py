"""52audio（我爱音频网）"拆解"分类的情报源实现。

侦察结论（详见 docs/DESIGN.md 的"数据源侦察"一节）：
- 目标分类页 https://www.52audio.com/archives/category/teardowns 是标准 WordPress
  分类页，支持 /page/N/ 翻页；同时该分类还提供 RSS Feed：
  https://www.52audio.com/archives/category/teardowns/feed/
  这个 Feed 的 <item> 已经包含标题、原文链接、作者(dc:creator)、发布时间(pubDate)、
  分类标签(category)、摘要(description)、**完整正文 HTML**(content:encoded)。
  Feed 支持 ?paged=2 / ?paged=3 翻页拿到更早的文章（已用 scripts/recon.py 实测确认）。
- 因此不需要再单独请求每篇文章的详情页 HTML 做二次解析：直接消费分类 Feed 的
  content:encoded 字段即可拿到完整正文（含标题层级 H2-H4、图片 figure、
  视频 iframe 嵌入），这样请求量最小、结构最稳定，也最省服务器资源。
- 文章标题 100% 以"拆解报告：" 或 "拆解视频：" 为前缀，这是区分"拆解报告"和
  "拆解视频"两个板块最可靠的信号；同时以正文里是否有 <iframe>（B站/YouTube 等
  嵌入播放器）作为次要交叉验证信号（极少数标题前缀缺失/不规范时的兜底判断）。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

from core.base_source import BaseSource
from core.extract.components import extract_components
from core.extract.images import build_image_assets
from core.extract.selling_points import extract_selling_points
from core.extract.tech_specs import extract_tech_specs
from core.extract.text_utils import (
    extract_all_image_urls,
    extract_video_embed_urls,
    plain_text as html_plain_text,
)
from core.models import ImageAsset, TeardownReport, VideoItem
from sources.audio52 import lexicon
from sources.audio52.parse_title import parse_title

FEED_BASE = "https://www.52audio.com/archives/category/teardowns/feed/"
LIST_PAGE_BASE = "https://www.52audio.com/archives/category/teardowns"

_NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "52audio-intel-bot/0.1 (respectful research crawler; contact: local-dev)"
)

_ID_RE = re.compile(r"/archives/(\d+)\.html")


def _to_iso_date(pub_date_raw: str) -> str:
    try:
        dt = parsedate_to_datetime(pub_date_raw)
        return dt.date().isoformat()
    except Exception:
        return ""


def _extract_id(url: str) -> str:
    m = _ID_RE.search(url)
    return m.group(1) if m else re.sub(r"\W+", "_", url)[-32:]


def _clean_description(desc: str) -> str:
    desc = re.sub(r"<[^>]+>", "", desc or "")
    desc = desc.replace("&#8230;", "…").replace("&nbsp;", " ")
    return desc.strip()


def _guess_video_source_site(embed_url: str) -> str:
    if not embed_url:
        return ""
    if "bilibili" in embed_url:
        return "哔哩哔哩 (Bilibili)"
    if "youtube" in embed_url or "youtu.be" in embed_url:
        return "YouTube"
    if "v.qq.com" in embed_url:
        return "腾讯视频"
    if "youku" in embed_url:
        return "优酷"
    return "未知视频平台（待人工确认）"


def _images_from_saved(saved: list[dict]) -> list[ImageAsset]:
    return [
        ImageAsset(
            index=img.get("index", i),
            url=img.get("url", ""),
            alt=img.get("alt", ""),
            caption=img.get("caption", ""),
            width=img.get("width"),
            height=img.get("height"),
            aspect_ratio=img.get("aspect_ratio"),
            edge_density=img.get("edge_density"),
            classification=img.get("classification", "unknown"),
            classification_reason=img.get("classification_reason", ""),
            ocr_status=img.get("ocr_status", "not_applicable"),
            ocr_engine=img.get("ocr_engine"),
            ocr_text=img.get("ocr_text"),
        )
        for i, img in enumerate(saved)
    ]


class Audio52Source(BaseSource):
    source_id = "audio52"
    display_name = "我爱音频网 52audio.com（拆解分类）"

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        request_delay_sec: float = 1.2,
        image_request_delay_sec: float = 0.25,
        max_images_per_article: int = 90,
        fetch_images: bool = True,
        timeout: int = 15,
    ) -> None:
        self.user_agent = user_agent
        self.request_delay_sec = request_delay_sec
        self.image_request_delay_sec = image_request_delay_sec
        self.max_images_per_article = max_images_per_article
        self.fetch_images = fetch_images
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/rss+xml, text/xml"})
        self.image_queue_entries: list[dict] = []
        # 由 pipeline 注入：用于跳过正文未变文章的重复图片下载（大幅缩短定时任务耗时）
        self.existing_reports: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # fetch_list: 分页拉取分类 RSS Feed，直到集齐 limit 条（按 link 去重）
    # ------------------------------------------------------------------
    def fetch_list(self, limit: int = 30) -> list[dict]:
        items: list[dict] = []
        seen_links: set[str] = set()
        page = 1
        max_pages = 10  # 安全上限，防止 feed 结构异常时死循环

        while len(items) < limit and page <= max_pages:
            url = FEED_BASE if page == 1 else f"{FEED_BASE}?paged={page}"
            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
            except Exception as e:
                print(f"[audio52] 拉取 feed 第{page}页失败: {e}")
                break

            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError as e:
                print(f"[audio52] 解析 feed XML 失败（第{page}页）: {e}")
                break

            channel = root.find("channel")
            if channel is None:
                break
            page_items = channel.findall("item")
            if not page_items:
                break

            new_count = 0
            for it in page_items:
                link = (it.findtext("link") or "").strip()
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                title = (it.findtext("title") or "").strip()
                creator = (it.findtext("dc:creator", namespaces=_NS) or "").strip()
                pub_date_raw = (it.findtext("pubDate") or "").strip()
                description = _clean_description(it.findtext("description") or "")
                content_encoded = it.findtext("content:encoded", namespaces=_NS) or ""
                categories = [c.text.strip() for c in it.findall("category") if c.text]

                items.append(
                    {
                        "url": link,
                        "title": title,
                        "author": creator,
                        "date": _to_iso_date(pub_date_raw),
                        "description": description,
                        "content_html": content_encoded,
                        "raw_categories": categories,
                    }
                )
                new_count += 1
                if len(items) >= limit:
                    break

            if new_count == 0:
                break

            page += 1
            time.sleep(self.request_delay_sec)

        return items[:limit]

    # ------------------------------------------------------------------
    # parse_detail: feed 条目已经自带完整正文，这里主要做结构化抽取
    # ------------------------------------------------------------------
    def parse_detail(self, item: dict):
        parsed = parse_title(item["title"])
        content_html = item.get("content_html", "")
        video_embeds = extract_video_embed_urls(content_html)

        content_type = parsed.content_type
        if content_type == "unknown":
            # 兜底启发式：标题前缀缺失时，用"正文是否有视频嵌入"来判断
            content_type = "video" if video_embeds else "report"

        crawled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if content_type == "video":
            embed_url = video_embeds[0] if video_embeds else ""
            return VideoItem(
                id=_extract_id(item["url"]),
                source_id=self.source_id,
                type="video",
                url=item["url"],
                title=parsed.product_title,
                product_title=parsed.product_title,
                publisher=item.get("author", ""),
                date=item.get("date", ""),
                source_site=_guess_video_source_site(embed_url),
                video_embed_url=embed_url,
                summary=item.get("description", ""),
                brand=parsed.brand,
                model=parsed.model,
                category=parsed.category,
                raw_categories=item.get("raw_categories", []),
                crawled_at=crawled_at,
            )

        # ---- 拆解报告 ----
        plain = html_plain_text(content_html)
        raw_images = extract_all_image_urls(content_html)[: self.max_images_per_article]
        article_id = _extract_id(item["url"])
        prev = self.existing_reports.get(article_id)

        images: list[ImageAsset] = []
        if self.fetch_images and raw_images:
            # 正文未变时复用已分析过的图片元数据，避免每次定时任务重复下载数百张图
            if prev and prev.get("content_html") == content_html and prev.get("images"):
                images = _images_from_saved(prev["images"])
            else:
                images, queue_entries = build_image_assets(
                    raw_images,
                    self.session,
                    request_delay_sec=self.image_request_delay_sec,
                    download_timeout=self.timeout,
                    user_agent=self.user_agent,
                )
                for q in queue_entries:
                    q["article_url"] = item["url"]
                    q["article_title"] = parsed.product_title
                    self.image_queue_entries.append(q)

        selling_points = extract_selling_points(plain, lexicon.SELLING_POINT_KEYWORDS)
        major, minor = extract_components(content_html, lexicon.COMPONENT_LEXICON)
        tech_specs = extract_tech_specs(plain, lexicon.TECH_SPEC_RULES)

        cover_image = raw_images[0]["url"] if raw_images else ""

        return TeardownReport(
            id=_extract_id(item["url"]),
            source_id=self.source_id,
            type="report",
            url=item["url"],
            title=parsed.product_title,
            summary=item.get("description", ""),
            date=item.get("date", ""),
            author=item.get("author", ""),
            brand=parsed.brand,
            model=parsed.model,
            category=parsed.category,
            raw_categories=item.get("raw_categories", []),
            content_html=content_html,
            cover_image=cover_image,
            images=images,
            selling_points=selling_points,
            components_major=major,
            components_minor=minor,
            tech_specs=tech_specs,
            has_video_embed=bool(video_embeds),
            video_embed_urls=video_embeds,
            crawled_at=crawled_at,
        )
