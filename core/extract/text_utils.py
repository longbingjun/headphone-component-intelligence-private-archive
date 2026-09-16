"""从 WordPress content:encoded 富文本 HTML 里拆出"结构化文本"的通用小工具。

52audio 的文章正文是标准 WordPress 区块 HTML（<p> <h2-4> <figure><img> 等），
这里只依赖这几个最通用的标签，不针对某个站点写死 CSS class 选择器，
方便以后新增站点时复用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?])\s*")


@dataclass
class Block:
    """正文里的一个顺序块：段落 / 标题 / 图片。"""

    kind: str  # "heading" | "paragraph" | "image"
    text: str = ""
    level: int = 0  # heading 用：2/3/4
    img_url: str = ""
    img_alt: str = ""
    img_index: int = -1  # 在全文图片序列里的下标


def parse_content_blocks(content_html: str) -> list[Block]:
    """把 content:encoded 的 HTML 顺序拆解成 Block 列表，保持原文顺序。"""

    if not content_html:
        return []
    soup = BeautifulSoup(content_html, "lxml")
    blocks: list[Block] = []
    img_counter = 0

    def walk(node) -> None:
        nonlocal img_counter
        for child in node.children:
            if not isinstance(child, Tag):
                continue
            name = child.name.lower()
            if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                text = child.get_text(strip=True)
                if text:
                    blocks.append(Block(kind="heading", text=text, level=int(name[1])))
            elif name == "p":
                text = child.get_text(strip=True)
                if text:
                    blocks.append(Block(kind="paragraph", text=text))
                # 段落里内嵌的图片（少数情况）也要收进来
                for img in child.find_all("img"):
                    blocks.append(
                        Block(
                            kind="image",
                            img_url=img.get("src", ""),
                            img_alt=img.get("alt", ""),
                            img_index=img_counter,
                        )
                    )
                    img_counter += 1
            elif name == "figure":
                img = child.find("img")
                if img:
                    caption_tag = child.find("figcaption")
                    caption = caption_tag.get_text(strip=True) if caption_tag else ""
                    blocks.append(
                        Block(
                            kind="image",
                            img_url=img.get("src", ""),
                            img_alt=img.get("alt", "") or caption,
                            text=caption,
                            img_index=img_counter,
                        )
                    )
                    img_counter += 1
                else:
                    # 可能是视频嵌入 figure，递归看看有没有更深层的东西
                    walk(child)
            elif name in ("div", "section", "blockquote", "ul", "ol", "li"):
                walk(child)
            else:
                walk(child)

    walk(soup)
    return blocks


def split_sentences(text: str) -> list[str]:
    """极简中文分句：按句号/问号/叹号切分，过滤过短的碎片。"""

    parts = [p.strip() for p in _SENT_SPLIT_RE.split(text) if p.strip()]
    return [p for p in parts if len(p) >= 6]


def extract_all_image_urls(content_html: str) -> list[dict]:
    """按顺序抽取正文里的所有图片，附带 alt/caption。"""

    blocks = parse_content_blocks(content_html)
    images = []
    for b in blocks:
        if b.kind == "image" and b.img_url:
            images.append({"index": b.img_index, "url": b.img_url, "alt": b.img_alt, "caption": b.text})
    return images


def extract_video_embed_urls(content_html: str) -> list[str]:
    """从正文 iframe/嵌入代码里找 B站/YouTube 等视频链接。"""

    if not content_html:
        return []
    soup = BeautifulSoup(content_html, "lxml")
    urls: list[str] = []
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if src:
            if src.startswith("//"):
                src = "https:" + src
            urls.append(src)
    return urls


def plain_text(content_html: str) -> str:
    """去标签后的纯文本，供关键词/情感分析使用。"""

    if not content_html:
        return ""
    soup = BeautifulSoup(content_html, "lxml")
    return soup.get_text("\n", strip=True)
