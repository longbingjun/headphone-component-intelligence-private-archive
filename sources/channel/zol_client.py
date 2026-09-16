"""中关村在线（ZOL）搜索与参考报价 / 电商溯源抓取。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

ZOL_BASE = "https://detail.zol.com.cn"
SEARCH_URL = "https://search.zol.com.cn/s/all.php"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.zol.com.cn/",
}

_PRICE_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")
_PRO_ID_RE = re.compile(r"microphone/index(\d+)\.shtml", re.I)
_JD_SKU_RE = re.compile(r"(?:item\.jd\.com/(\d+)|[?&]e=jd_(\d+)|jd_(\d+))", re.I)


@dataclass
class ZolChannelQuote:
    platform: str
    platform_label: str
    price_cny: float | None
    url: str
    sku_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "platform_label": self.platform_label,
            "price_cny": self.price_cny,
            "url": self.url,
            "sku_id": self.sku_id,
        }


@dataclass
class ZolSearchHit:
    product_id: str
    title: str
    product_url: str
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "title": self.title,
            "product_url": self.product_url,
            "score": self.score,
        }


@dataclass
class ZolPriceInfo:
    product_id: str
    product_url: str
    product_name: str = ""
    reference_price_cny: float | None = None
    reference_price_note: str = ""
    channel_quotes: list[ZolChannelQuote] = field(default_factory=list)
    search_query: str = ""
    search_hit: ZolSearchHit | None = None
    fetch_error: str = ""

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "product_url": self.product_url,
            "product_name": self.product_name,
            "reference_price_cny": self.reference_price_cny,
            "reference_price_note": self.reference_price_note,
            "channel_quotes": [q.to_dict() for q in self.channel_quotes],
            "search_query": self.search_query,
            "search_hit": self.search_hit.to_dict() if self.search_hit else None,
            "fetch_error": self.fetch_error,
        }


def _get(url: str, *, timeout: int = 20) -> requests.Response:
    return requests.get(url, headers=_HEADERS, timeout=timeout)


def _abs_url(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return ZOL_BASE + href
    return href


def _parse_price(text: str) -> float | None:
    if not text:
        return None
    m = _PRICE_NUM_RE.search(text.replace(",", ""))
    if not m:
        return None
    val = float(m.group(1))
    return val if 10 <= val <= 99999 else None


def _extract_jd_sku(url: str) -> str | None:
    m = _JD_SKU_RE.search(url or "")
    if not m:
        return None
    return next((g for g in m.groups() if g), None)


def _tokenize(text: str) -> set[str]:
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (text or "").lower())
    parts = {p for p in t.split() if len(p) >= 2}
    for i in range(len(t) - 1):
        if "\u4e00" <= t[i] <= "\u9fff":
            parts.add(t[i : i + 2])
    return parts


_GENERIC_CJK_MODEL_WORDS = ("耳机", "真无线", "开放式", "头戴式", "蓝牙", "智能", "降噪", "带屏")


def has_model_identity_match(title: str, model: str) -> bool:
    """Require a model-level match before accepting a marketplace result.

    Brand-only matches are unsafe: several prior results reused a single JD/ZOL
    SKU across unrelated models from the same brand.  Model comparison is
    intentionally conservative; uncertain results remain unresolved.
    """
    title_l = (title or "").lower()
    model_l = (model or "").lower()
    title_key = re.sub(r"[^a-z0-9]+", "", title_l)
    model_key = re.sub(r"[^a-z0-9]+", "", model_l)
    if len(model_key) >= 4 and model_key in title_key:
        return True

    cjk_model = "".join(re.findall(r"[\u4e00-\u9fff]+", model or ""))
    for word in _GENERIC_CJK_MODEL_WORDS:
        cjk_model = cjk_model.replace(word, "")
    return len(cjk_model) >= 3 and cjk_model in (title or "")


def score_product_title(title: str, brand: str, model: str) -> float:
    title_l = (title or "").lower()
    score = 0.0
    for part in re.split(r"[/\s（）()]+", (brand or "").lower()):
        if len(part) >= 2 and part in title_l:
            score += 2.0
    model_l = (model or "").lower()
    for part in re.split(r"[\s\-]+", model_l):
        if len(part) >= 2 and part in title_l:
            score += 1.5
    if "耳机" in title:
        score += 0.3
    if any(x in title for x in ("保护套", "保护壳", "耳塞套", "数据线")):
        score -= 4.0
    return score


def search_zol_products(query: str, *, limit: int = 10) -> list[ZolSearchHit]:
    """ZOL 综合搜索，返回耳机类产品详情链接。"""
    try:
        r = _get(f"{SEARCH_URL}?keyword={quote(query)}")
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    hits: list[ZolSearchHit] = []
    seen: set[str] = set()
    for a in soup.select("a[href*='microphone/index']"):
        href = a.get("href", "")
        m = _PRO_ID_RE.search(href)
        if not m:
            continue
        pid = m.group(1)
        if pid in seen:
            continue
        seen.add(pid)
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        hits.append(
            ZolSearchHit(
                product_id=pid,
                title=title,
                product_url=_abs_url(href),
                score=0.0,
            )
        )
        if len(hits) >= limit:
            break
    return hits


def _commerce_search_query(brand: str, model: str) -> str:
    """构造 ZOL 搜索词（中文品牌 + 核心型号）。"""
    b = (brand or "").strip()
    bl = b.lower()
    if "倍思" in b or "baseus" in bl:
        b = "倍思"
    elif "华为" in b or "huawei" in bl:
        b = "华为"
    elif "荣耀" in b or "honor" in bl:
        b = "荣耀"
    elif "索尼" in b or "sony" in bl:
        b = "索尼"
    m = re.sub(r"悦彰|耳机|真无线|TWS", "", model or "", flags=re.I).strip()
    return f"{b} {m}".strip()


def pick_best_zol_hit(hits: list[ZolSearchHit], brand: str, model: str) -> ZolSearchHit | None:
    if not hits:
        return None
    for h in hits:
        h.score = score_product_title(h.title, brand, model)
    ranked = sorted(hits, key=lambda x: x.score, reverse=True)
    best = ranked[0]
    normalized_model = re.sub(r"[^a-z0-9]+", "", (model or "").lower())
    generic_models = {"earbuds", "earphone", "earphones", "headphones", "headset", "buds"}
    min_score = 4.0 if normalized_model in generic_models else 1.5
    if best.score < min_score or not has_model_identity_match(best.title, model):
        return None
    return best


def parse_zol_product_html(html: str, product_url: str, product_id: str) -> ZolPriceInfo:
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else ""

    ref_price = None
    ref_node = soup.select_one(".price__reference .price-type")
    if ref_node:
        ref_price = _parse_price(ref_node.get_text())

    # 参考报价区间：￥369-389
    if ref_price is None:
        ref_block = soup.select_one(".price__reference")
        if ref_block:
            ref_price = _parse_price(ref_block.get_text())

    quotes: list[ZolChannelQuote] = []
    for li in soup.select("ul.price-b2c > li"):
        cls = " ".join(li.get("class") or [])
        if "b2c-jd" in cls:
            platform, label = "jd", "京东"
        elif "b2c-tmall" in cls:
            platform, label = "tmall", "天猫"
        else:
            continue
        a = li.find("a", href=True)
        if not a:
            continue
        url = _abs_url(a["href"])
        price_node = li.select_one(".m-price") or a
        price = _parse_price(price_node.get_text())
        quotes.append(
            ZolChannelQuote(
                platform=platform,
                platform_label=label,
                price_cny=price,
                url=url,
                sku_id=_extract_jd_sku(url) if platform == "jd" else None,
            )
        )

    note = ""
    if ref_price and "-" in (ref_node.parent.get_text() if ref_node and ref_node.parent else ""):
        note = "区间价"

    return ZolPriceInfo(
        product_id=product_id,
        product_url=product_url,
        product_name=name,
        reference_price_cny=ref_price,
        reference_price_note=note,
        channel_quotes=quotes,
    )


def fetch_zol_prices(
    *,
    brand: str,
    model: str,
    query: str | None = None,
    product_id: str | None = None,
    product_url: str | None = None,
) -> ZolPriceInfo:
    """搜索（或使用已知 product_id）并抓取参考报价 + 京东/天猫溯源。"""
    q = query or f"{brand} {model}".strip()
    hit: ZolSearchHit | None = None

    if product_id:
        url = product_url or f"{ZOL_BASE}/microphone/index{product_id}.shtml"
        hit = ZolSearchHit(product_id=product_id, title="", product_url=url)
    else:
        hits = search_zol_products(q)
        hit = pick_best_zol_hit(hits, brand, model)
        if not hit:
            return ZolPriceInfo(
                product_id="",
                product_url="",
                search_query=q,
                fetch_error="zol_search_no_hit",
            )

    try:
        r = _get(hit.product_url)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        info = parse_zol_product_html(r.text, hit.product_url, hit.product_id)
        info.search_query = q
        info.search_hit = hit
        if hit.title and not info.product_name:
            info.product_name = hit.title
        return info
    except Exception as exc:
        return ZolPriceInfo(
            product_id=hit.product_id,
            product_url=hit.product_url,
            search_query=q,
            search_hit=hit,
            fetch_error=str(exc),
        )


def best_channel_price(info: ZolPriceInfo) -> tuple[float | None, str, str]:
    """返回 (price, platform, url) — 优先京东价，其次天猫，最后参考报价。"""
    for platform in ("jd", "tmall"):
        for q in info.channel_quotes:
            if q.platform == platform and q.price_cny is not None:
                return q.price_cny, platform, q.url
    if info.reference_price_cny is not None:
        return info.reference_price_cny, "zol_reference", info.product_url
    return None, "", ""
