"""定位 52audio 拆解报告结尾的「我爱音频网总结」段。

总结段是拆解报告的标准结尾，结构为：

    <h4 class="wp-block-heading"><strong>三、我爱音频网总结</strong></h4>
    <figure><img src="...物料表.png" /></figure>
    <p>最后附上...已知核心物料清单...</p>
    <p>外观方面...</p>
    <p>内部主要配置方面，充电盒搭载了...；耳机内部搭载...</p>

少数稿件命名为「智研所总结」「52audio总结」，因此按优先级 fallback：
1. 我爱音频网总结
2. 智研所总结
3. 52audio总结
4. 兜底正则：含「总结」的 H2/H3/H4 + 段内出现「物料清单/核心物料」

下载总结段 PNG 物料表时必须带 `Referer: https://www.52audio.com/`（OSS 防盗链），
参考 core/extract/images.py。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

# 优先级序：第一个命中的就是总结段标题
SUMMARY_HEADING_PATTERNS: list[str] = [
    r"我爱音频网总结",
    r"智研所总结",
    r"52audio\s*总结",
    r"52音频网总结",
]

# Fallback：含「总结」字样且段内出现「物料清单 / 核心物料」关键词
_FALLBACK_HEADING_RE = re.compile(r"总结")
_FALLBACK_BODY_KEYWORDS = ("物料清单", "核心物料", "物料表")

_HEADING_TAGS = ("h2", "h3", "h4", "h5")


def _heading_text(tag: Tag) -> str:
    return tag.get_text(strip=True)


def _match_heading(text: str) -> bool:
    for pat in SUMMARY_HEADING_PATTERNS:
        if re.search(pat, text):
            return True
    return False


def _collect_section(heading_tag: Tag) -> tuple[str, list[Tag]]:
    """从 heading 节点向后收集同级/低级兄弟节点，直到遇到下一个同级或更高级 heading。

    返回 (section_html, [sibling_tags])。
    """
    heading_level = int(heading_tag.name[1])
    siblings: list[Tag] = []
    html_parts: list[str] = [str(heading_tag)]

    node = heading_tag.next_sibling
    while node is not None:
        if isinstance(node, Tag):
            name = node.name.lower() if node.name else ""
            if name in _HEADING_TAGS:
                level = int(name[1])
                # 遇到同级或更高级 heading → section 结束
                if level <= heading_level:
                    break
            siblings.append(node)
            html_parts.append(str(node))
        node = node.next_sibling

    return "\n".join(html_parts), siblings


def locate_summary_section(content_html: str) -> dict | None:
    """定位总结段，返回 dict 或 None。

    返回结构：
        {
          "heading": "三、我爱音频网总结",
          "html": "<h4>...</h4><figure>...</figure><p>...</p>",
          "text": "三、我爱音频网总结 最后附上...内部主要配置方面...",
          "image_urls": ["https://.../物料表.png", ...],
        }

    image_urls 仅返回 URL 字符串列表；如需下载二进制，Referer 头请参考
    core/extract/images.py 的 `build_image_assets`（必须带
    `Referer: https://www.52audio.com/`）。
    """
    if not content_html:
        return None

    soup = BeautifulSoup(content_html, "lxml")

    # 第一轮：精确匹配已知命名
    for tag in soup.find_all(_HEADING_TAGS):
        text = _heading_text(tag)
        if text and _match_heading(text):
            return _build_section_result(tag)

    # 第二轮：fallback - 含「总结」+ 段内出现物料清单关键词
    for tag in soup.find_all(_HEADING_TAGS):
        text = _heading_text(tag)
        if not text or not _FALLBACK_HEADING_RE.search(text):
            continue
        section_html, siblings = _collect_section(tag)
        body_text = " ".join(s.get_text(" ", strip=True) for s in siblings)
        if any(kw in body_text for kw in _FALLBACK_BODY_KEYWORDS):
            return _build_section_result(tag)

    return None


def _build_section_result(heading_tag: Tag) -> dict:
    text = _heading_text(heading_tag)
    section_html, siblings = _collect_section(heading_tag)

    # 收集段内所有 <img> URL，保持顺序、去重
    image_urls: list[str] = []
    seen: set[str] = set()
    for sib in siblings:
        for img in sib.find_all("img"):
            url = (img.get("src") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            image_urls.append(url)

    # 文本：heading + 段内所有段落纯文本
    parts = [text]
    for sib in siblings:
        t = sib.get_text(" ", strip=True)
        if t:
            parts.append(t)
    section_text = " ".join(parts)

    return {
        "heading": text,
        "html": section_html,
        "text": section_text,
        "image_urls": image_urls,
    }
