"""从 52audio 文章标题里解析出 类型/品牌/型号/分类。

52audio 标题非常规整，几乎都是："拆解报告：{品牌}{型号}{产品形态}" 或
"拆解视频：{品牌}{型号}{产品形态}" 这种模式，所以用"前缀判断类型 + 品牌别名表
+ 分类关键词表 + 产品形态后缀词表"这种纯规则的方式就能覆盖绝大多数情况，
不需要上模型。这是一个启发式方案，极少数不规则标题会导致 brand/model 抽取
不准，属于可接受的第一版精度，已经在 README/报告里说明。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.catalog_overrides import combined_brand_aliases
from sources.audio52.lexicon import (
    BRAND_ALIASES,
    CATEGORY_RULES,
    DEFAULT_CATEGORY,
    PRODUCT_TYPE_SUFFIXES,
)
from core.scope import HEADPHONE_CATEGORIES, infer_headphone_category

_REPORT_PREFIXES = ["拆解报告：", "拆解报告:"]
_VIDEO_PREFIXES = ["拆解视频：", "拆解视频:"]

_SORTED_SUFFIXES = sorted(PRODUCT_TYPE_SUFFIXES, key=len, reverse=True)


def _brand_aliases() -> list[tuple[str, list[str]]]:
    return combined_brand_aliases(BRAND_ALIASES)


@dataclass
class ParsedTitle:
    content_type: str  # "report" | "video" | "unknown"
    product_title: str  # 去掉"拆解报告：/拆解视频："前缀之后的产品线
    brand: str
    model: str
    category: str


def classify_category(text: str) -> str:
    for category, keywords in CATEGORY_RULES:
        if any(kw in text for kw in keywords):
            return category
    inferred = infer_headphone_category(text)
    if inferred:
        return inferred
    return DEFAULT_CATEGORY


def _guess_brand(product_title: str) -> tuple[str, str]:
    """返回 (展示品牌名, 命中的原始别名文本)。找不到则返回 ("", "")。"""

    best: tuple[int, int, str, str] | None = None  # (start_index, -len(alias), display, alias)
    for display, aliases in _brand_aliases():
        for alias in aliases:
            idx = product_title.find(alias)
            if idx == -1:
                continue
            candidate = (idx, -len(alias), display, alias)
            if best is None or candidate < best:
                best = candidate
    if best is None:
        return "", ""
    return best[2], best[3]


def _strip_suffix(text: str) -> str:
    for suffix in _SORTED_SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def parse_title(raw_title: str) -> ParsedTitle:
    content_type = "unknown"
    product_title = raw_title
    for p in _REPORT_PREFIXES:
        if raw_title.startswith(p):
            content_type = "report"
            product_title = raw_title[len(p):]
            break
    else:
        for p in _VIDEO_PREFIXES:
            if raw_title.startswith(p):
                content_type = "video"
                product_title = raw_title[len(p):]
                break

    product_title = product_title.strip()
    category = classify_category(product_title)
    brand, matched_alias = _guess_brand(product_title)

    model = product_title
    if matched_alias:
        model = model.replace(matched_alias, "", 1)
    model = _strip_suffix(model).strip(" -·、，,")
    # 型号里经常残留品牌本身重复出现一次英文名的情况（比如 "MOONDROP水月雨MOONDROP PILL"
    # 这种极少数重复标注），做一次弱清理。
    model = re.sub(r"\s{2,}", " ", model).strip()

    return ParsedTitle(
        content_type=content_type,
        product_title=product_title,
        brand=brand,
        model=model or product_title,
        category=category,
    )
