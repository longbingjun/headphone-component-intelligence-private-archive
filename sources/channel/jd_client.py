"""京东搜索与 SKU 现价查询（渠道层）。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import quote

import requests

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.jd.com/",
}

_SKU_RE = re.compile(r'data-sku="(\d+)"')
_TITLE_RE = re.compile(
    r'class="p-name"[^>]*>\s*<a[^>]*title="([^"]+)"',
    re.I,
)
_PRICE_API = "https://p.3.cn/prices/mgets"


@dataclass
class JdSearchHit:
    sku_id: str
    title: str
    channel_url: str
    price_cny: float | None = None
    msrp_cny: float | None = None
    shop_hint: str = ""

    def to_dict(self) -> dict:
        return {
            "sku_id": self.sku_id,
            "title": self.title,
            "channel_url": self.channel_url,
            "price_cny": self.price_cny,
            "msrp_cny": self.msrp_cny,
            "shop_hint": self.shop_hint,
        }


def _get(url: str, *, params: dict | None = None, timeout: int = 15) -> requests.Response:
    return requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=timeout)


def fetch_jd_price(sku_id: str) -> dict | None:
    """调用京东公开价格接口，返回 {p, m, id} 或 None。"""
    try:
        r = _get(_PRICE_API, params={"skuIds": f"J_{sku_id}"}, timeout=12)
        r.raise_for_status()
        data = json.loads(r.text)
        if isinstance(data, list) and data:
            row = data[0]
            return {
                "price_cny": float(row["p"]) if row.get("p") else None,
                "msrp_cny": float(row["m"]) if row.get("m") else None,
                "sku_id": sku_id,
            }
    except Exception:
        return None
    return None


def search_jd(query: str, *, limit: int = 8) -> list[JdSearchHit]:
    """搜索京东 PC 页，解析 SKU 与标题（不保证一定能访问，取决于网络）。"""
    url = f"https://search.jd.com/Search?keyword={quote(query)}&enc=utf-8"
    try:
        r = _get(url, timeout=15)
        r.raise_for_status()
        html = r.text
    except Exception:
        return []

    skus = _SKU_RE.findall(html)
    titles = _TITLE_RE.findall(html)
    hits: list[JdSearchHit] = []
    seen: set[str] = set()
    for i, sku in enumerate(skus):
        if sku in seen:
            continue
        seen.add(sku)
        title = titles[i] if i < len(titles) else ""
        hits.append(
            JdSearchHit(
                sku_id=sku,
                title=title,
                channel_url=f"https://item.jd.com/{sku}.html",
            )
        )
        if len(hits) >= limit:
            break

    for hit in hits:
        price = fetch_jd_price(hit.sku_id)
        if price:
            hit.price_cny = price.get("price_cny")
            hit.msrp_cny = price.get("msrp_cny")
        if "官方旗舰店" in hit.title:
            hit.shop_hint = "京东官方旗舰店"
        elif "官方授权" in hit.title:
            hit.shop_hint = "官方授权店"
    return hits


def _tokenize(text: str) -> set[str]:
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (text or "").lower())
    parts = {p for p in t.split() if len(p) >= 2}
    for i in range(len(t) - 1):
        if "\u4e00" <= t[i] <= "\u9fff":
            parts.add(t[i : i + 2])
    return parts


def score_hit(hit: JdSearchHit, brand: str, model: str) -> float:
    """标题与品牌/型号匹配分。"""
    title = (hit.title or "").lower()
    score = 0.0
    brand_l = (brand or "").lower()
    for alias in re.split(r"[/\s（）()]+", brand_l):
        if len(alias) >= 2 and alias in title:
            score += 2.0
    model_l = (model or "").lower()
    for part in re.split(r"[\s\-]+", model_l):
        if len(part) >= 3 and part in title:
            score += 1.5
    if "官方旗舰店" in hit.title:
        score += 1.0
    if "耳机" in hit.title:
        score += 0.5
    # 排除明显非目标 SKU
    if any(x in hit.title for x in ("保护壳", "保护套", "耳帽", "数据线", "贴膜")):
        score -= 3.0
    return score


def pick_best_hit(hits: list[JdSearchHit], brand: str, model: str) -> JdSearchHit | None:
    from sources.channel.zol_client import has_model_identity_match

    if not hits:
        return None
    ranked = sorted(hits, key=lambda h: score_hit(h, brand, model), reverse=True)
    best = ranked[0]
    if score_hit(best, brand, model) <= 0:
        return None
    return best if has_model_identity_match(best.title, model) else None
