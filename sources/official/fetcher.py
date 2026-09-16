"""品牌官网产品页抓取（官方层）。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

_PRICE_RE = re.compile(r"[¥￥]\s*(\d{2,5})(?:\s*起)?")
_TAGLINE_RE = re.compile(r"母带|无损|降噪|星闪|佩戴|音质|续航|蓝牙", re.I)
_PRODUCT_PATH_RE = re.compile(
    r"/(?:products?|headphones?|earphones?|wearables?|accessories?|buy|shop)/",
    re.I,
)

# 品牌 → 官网搜索 URL 模板（{query} = URL 编码后的关键词）
OFFICIAL_SEARCH: dict[str, str] = {
    "huawei": "https://consumer.huawei.com/cn/search/?q={query}",
    "honor": "https://www.honor.com/cn/search/?q={query}",
    "xiaomi": "https://www.mi.com/search/{query}",
    "oppo": "https://www.oppo.com/cn/search/?q={query}",
    "vivo": "https://www.vivo.com.cn/search?keyword={query}",
    "sony": "https://www.sonystyle.com.cn/search?q={query}",
    "baseus": "https://www.baseus.com/search?q={query}",
    "edifier": "https://www.edifier.com/cn/search?q={query}",
    "nothing": "https://nothing.tech/search?q={query}",
    "soundcore": "https://www.soundcore.com/search?q={query}",
    "realme": "https://www.realme.com/cn/search?q={query}",
}

# 品牌 → 官网首页（预热 cookie / Referer，缓解 429）
OFFICIAL_HOME: dict[str, str] = {
    "baseus": "https://www.baseus.com/",
    "huawei": "https://consumer.huawei.com/cn/",
    "xiaomi": "https://www.mi.com/",
    "sony": "https://www.sonystyle.com.cn/",
    "oppo": "https://www.oppo.com/cn/",
    "vivo": "https://www.vivo.com.cn/",
}


@dataclass
class OfficialPageData:
    official_url: str
    product_name: str = ""
    msrp_cny: float | None = None
    tagline: str = ""
    selling_points: list[dict] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    fetch_error: str = ""
    search_query: str = ""
    search_candidates: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "official_url": self.official_url,
            "product_name": self.product_name,
            "msrp_cny": self.msrp_cny,
            "tagline": self.tagline,
            "selling_points": self.selling_points,
            "highlights": self.highlights,
            "fetch_error": self.fetch_error,
            "search_query": self.search_query,
            "search_candidates": self.search_candidates,
        }


def _brand_slug(brand: str) -> str:
    b = (brand or "").lower()
    if "huawei" in b or "华为" in b:
        return "huawei"
    if "honor" in b or "荣耀" in b:
        return "honor"
    if "xiaomi" in b or "小米" in b or "redmi" in b:
        return "xiaomi"
    if "oppo" in b:
        return "oppo"
    if "vivo" in b:
        return "vivo"
    if "sony" in b or "索尼" in b:
        return "sony"
    if "baseus" in b or "倍思" in b:
        return "baseus"
    if "edifier" in b or "漫步者" in b:
        return "edifier"
    if "nothing" in b:
        return "nothing"
    if "soundcore" in b or "anker" in b:
        return "soundcore"
    if "realme" in b:
        return "realme"
    return ""


def _tag_for_text(text: str) -> str:
    if any(k in text for k in ("音质", "无损", "编解码", "音效", "低音", "高音")):
        return "音质体验"
    if any(k in text for k in ("佩戴", "舒适", "耳塞", "轻盈")):
        return "佩戴体验"
    if any(k in text for k in ("降噪", "通透", "通话")):
        return "通话办公"
    if any(k in text for k in ("外观", "设计", "配色", "星环")):
        return "外观美学"
    if any(k in text for k in ("星闪", "蓝牙", "连接", "续航", "充电")):
        return "交互连接"
    return "其他"


def _search_query(brand: str, model: str) -> str:
    b = (brand or "").strip()
    bl = b.lower()
    if "倍思" in b or "baseus" in bl:
        b = "Baseus"
    elif "华为" in b or "huawei" in bl:
        b = "华为"
    elif "索尼" in b or "sony" in bl:
        b = "索尼"
    m = re.sub(r"悦彰|耳机|真无线|TWS|开放式", "", model or "", flags=re.I).strip()
    return f"{b} {m}".strip()


def _score_link(title: str, url: str, brand: str, model: str) -> float:
    """与 ZOL 类似的标题/URL 匹配打分。"""
    text = f"{title} {url}".lower()
    score = 0.0
    for part in re.split(r"[/\s（）()]+", (brand or "").lower()):
        if len(part) >= 2 and part in text:
            score += 2.0
    model_l = (model or "").lower()
    for part in re.split(r"[\s\-]+", model_l):
        if len(part) >= 2 and part in text:
            score += 1.5
    slug = normalize_model_slug(model)
    if slug and slug.replace("-", "") in text.replace("-", ""):
        score += 2.0
    if any(x in title for x in ("保护套", "保护壳", "耳塞套", "数据线", "充电线")):
        score -= 4.0
    if _PRODUCT_PATH_RE.search(url):
        score += 0.5
    return score


def _session_for_brand(brand_slug: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(_DEFAULT_HEADERS)
    home = OFFICIAL_HOME.get(brand_slug)
    if home:
        try:
            session.get(home, timeout=12)
            session.headers["Referer"] = home
            time.sleep(0.6)
        except Exception:
            pass
    return session


def _fetch_html(
    url: str,
    *,
    session: requests.Session | None = None,
    timeout: int = 20,
    retries: int = 3,
) -> tuple[str, str]:
    """返回 (html, error)。429/5xx 时退避重试。"""
    last_err = ""
    client = session or requests.Session()
    if session is None:
        client.headers.update(_DEFAULT_HEADERS)

    for attempt in range(retries):
        if attempt:
            time.sleep(1.5 * attempt)
        try:
            r = client.get(url, timeout=timeout)
            if r.status_code == 429:
                last_err = f"429 Too Many Requests for {url}"
                continue
            if r.status_code >= 500:
                last_err = f"{r.status_code} Server Error for {url}"
                continue
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text, ""
        except Exception as exc:
            last_err = str(exc)
    return "", last_err


def parse_official_html(html: str, url: str) -> OfficialPageData:
    soup = BeautifulSoup(html, "lxml")
    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True).split("-")[0].strip()
    h1 = soup.find("h1")
    product_name = h1.get_text(strip=True) if h1 else title

    text = soup.get_text("\n", strip=True)
    msrp = None
    for m in _PRICE_RE.finditer(text[:12000]):
        val = float(m.group(1))
        if 99 <= val <= 99999:
            msrp = val
            break

    # JSON-LD 价格兜底
    if msrp is None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                offers = item.get("offers") if isinstance(item, dict) else None
                if isinstance(offers, dict):
                    price = offers.get("price") or offers.get("lowPrice")
                    if price:
                        val = float(price)
                        if 99 <= val <= 99999:
                            msrp = val
                            break
                if msrp is not None:
                    break

    tagline = ""
    for line in text.split("\n"):
        if _TAGLINE_RE.search(line) and 10 <= len(line) <= 120:
            tagline = line
            break

    highlights: list[str] = []
    for h in soup.find_all(["h2", "h3", "h4"]):
        t = h.get_text(strip=True)
        if 4 <= len(t) <= 40 and t not in highlights:
            if any(k in t for k in ("音质", "降噪", "星闪", "佩戴", "续航", "录音", "听力", "设计")):
                highlights.append(t)

    selling_points: list[dict] = []
    for p in soup.find_all("p"):
        t = re.sub(r"\s+", " ", p.get_text(strip=True))
        if len(t) < 20 or len(t) > 280:
            continue
        if not _TAGLINE_RE.search(t):
            continue
        selling_points.append(
            {
                "text": t,
                "tag": _tag_for_text(t),
                "source_type": "official",
            }
        )
        if len(selling_points) >= 8:
            break

    return OfficialPageData(
        official_url=url,
        product_name=product_name,
        msrp_cny=msrp,
        tagline=tagline,
        selling_points=selling_points,
        highlights=highlights[:12],
    )


def fetch_official_page(url: str, *, timeout: int = 20, brand: str = "") -> OfficialPageData:
    slug = _brand_slug(brand) or _brand_slug(urlparse(url).netloc)
    session = _session_for_brand(slug) if slug else None
    html, err = _fetch_html(url, session=session, timeout=timeout)
    if err:
        return OfficialPageData(official_url=url, fetch_error=err)
    return parse_official_html(html, url)


def normalize_model_slug(model: str) -> str:
    m = (model or "").strip()
    m = re.sub(r"悦彰|耳机|TWS|真无线|开放式", "", m, flags=re.I)
    m = re.sub(r"\s+", " ", m).strip()
    m = m.lower().replace(" ", "-")
    m = re.sub(r"[^a-z0-9\-]", "", m)
    return m or "unknown"


def guess_official_url(brand: str, model: str) -> str | None:
    """根据品牌启发式拼接官网 URL（仅覆盖常见路径）。"""
    slug = _brand_slug(brand)
    m = normalize_model_slug(model)
    if slug == "huawei":
        return f"https://consumer.huawei.com/cn/headphones/{m}/"
    if slug == "honor":
        return f"https://www.honor.com/cn/earbuds/{m}/"
    if slug == "baseus":
        return f"https://www.baseus.com/products/{m}-open-ear-true-wireless-earbuds"
    if slug == "sony":
        return f"https://www.sonystyle.com.cn/products/{m}/{m}.html"
    if slug == "nothing":
        return f"https://nothing.tech/products/{m}"
    if slug == "edifier":
        return f"https://www.edifier.com/cn/products/{m}"
    return None


def _guess_product_urls(brand: str, model: str) -> list[str]:
    """多路径猜测候选产品页（比单一 guess 更宽）。"""
    slug = _brand_slug(brand)
    m = normalize_model_slug(model)
    urls: list[str] = []

    primary = guess_official_url(brand, model)
    if primary:
        urls.append(primary)

    if slug == "huawei":
        urls.extend(
            [
                f"https://consumer.huawei.com/cn/wearables/{m}/",
                f"https://consumer.huawei.com/cn/accessories/{m}/",
            ]
        )
    elif slug == "baseus":
        urls.extend(
            [
                f"https://www.baseus.com/products/{m}",
                f"https://www.baseus.com/products/{m}-wireless-earbuds",
                f"https://www.baseus.com/products/{m}-true-wireless-earbuds",
            ]
        )
    elif slug == "xiaomi":
        urls.extend(
            [
                f"https://www.mi.com/earphone/{m}",
                f"https://www.mi.com/buy/{m}",
            ]
        )
    elif slug == "oppo":
        urls.append(f"https://www.oppo.com/cn/accessories/{m}/")
    elif slug == "vivo":
        urls.append(f"https://www.vivo.com.cn/vivo/product/{m}")

    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _extract_search_candidates(html: str, base_url: str, brand: str, model: str) -> list[dict]:
    """从搜索页 HTML 提取产品链接候选。"""
    soup = BeautifulSoup(html, "lxml")
    candidates: list[dict] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        abs_url = urljoin(base_url, href)
        if abs_url in seen:
            continue
        path = urlparse(abs_url).path.lower()
        if not _PRODUCT_PATH_RE.search(path):
            continue
        title = a.get_text(" ", strip=True)
        if not title and a.get("title"):
            title = a.get("title", "").strip()
        score = _score_link(title, abs_url, brand, model)
        if score < 1.0:
            continue
        seen.add(abs_url)
        candidates.append({"url": abs_url, "title": title, "score": score})

    # script 内嵌 JSON 链接（部分 SPA 搜索页）
    for script in soup.find_all("script"):
        text = script.string or ""
        if "http" not in text:
            continue
        for m in re.finditer(r'https?://[^\s"\'<>]+/(?:products?|headphones?)/[^\s"\'<>]+', text, re.I):
            abs_url = m.group(0).rstrip("\\",)
            if abs_url in seen:
                continue
            score = _score_link("", abs_url, brand, model)
            if score < 1.5:
                continue
            seen.add(abs_url)
            candidates.append({"url": abs_url, "title": "", "score": score})

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:12]


def _search_official_candidates(brand: str, model: str, *, session: requests.Session) -> list[dict]:
    slug = _brand_slug(brand)
    if not slug or slug not in OFFICIAL_SEARCH:
        return []

    query = _search_query(brand, model)
    search_url = OFFICIAL_SEARCH[slug].format(query=quote(query))
    html, err = _fetch_html(search_url, session=session)
    if err or not html:
        return []
    return _extract_search_candidates(html, search_url, brand, model)


def search_official_site(brand: str, model: str) -> OfficialPageData:
    """在品牌官网搜索/猜测产品页，抓取价格与卖点。

    策略：URL 猜测 → 站内搜索解析 → 按匹配分选最佳页 → 抓取详情。
    """
    slug = _brand_slug(brand)
    query = _search_query(brand, model)
    session = _session_for_brand(slug) if slug else requests.Session()
    if not slug:
        session.headers.update(_DEFAULT_HEADERS)

    candidates: list[dict] = []
    for url in _guess_product_urls(brand, model):
        candidates.append({"url": url, "title": "", "score": _score_link("", url, brand, model) + 1.0})

    search_hits = _search_official_candidates(brand, model, session=session)
    seen = {c["url"] for c in candidates}
    for hit in search_hits:
        if hit["url"] not in seen:
            candidates.append(hit)
            seen.add(hit["url"])

    candidates.sort(key=lambda c: c["score"], reverse=True)

    last_err = ""
    for cand in candidates[:8]:
        url = cand["url"]
        html, err = _fetch_html(url, session=session)
        if err:
            last_err = err
            continue
        page = parse_official_html(html, url)
        if page.msrp_cny or page.selling_points or page.highlights or page.product_name:
            page.search_query = query
            page.search_candidates = candidates[:6]
            return page
        if not page.fetch_error:
            page.search_query = query
            page.search_candidates = candidates[:6]
            return page
        last_err = page.fetch_error or err

    best_url = candidates[0]["url"] if candidates else ""
    return OfficialPageData(
        official_url=best_url,
        search_query=query,
        search_candidates=candidates[:6],
        fetch_error=last_err or "official_search_no_hit",
    )


def resolve_official_url(brand: str, model: str, hint_url: str | None = None) -> str | None:
    if hint_url:
        return hint_url
    return guess_official_url(brand, model)
