"""Evidence-led price research with explicit source tiers.

The workflow writes only source-linked enrich records. Official pages are tried
first, then verified official flagship stores, launch reports, and finally ZOL;
each fallback carries its own visible label instead of being presented as an
official price.
"""

from __future__ import annotations

import json
import math
import os
import time
import argparse
import base64
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlsplit

import requests
from bs4 import BeautifulSoup

import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "scratch_price_research" / "price_text_pilot_20.json"
MODEL = "deepseek-v4-flash"
MAX_PAGE_CHARS = 6_000
sys.path.insert(0, str(ROOT))

from core.paths import official_enrich_dir, write_official_enrich  # noqa: E402
from core.products import is_identity_searchable  # noqa: E402

# These are intentionally a conservative allow-list.  Adding a brand means
# adding its real mainland/offical domain here; unrecognised domains cannot
# produce an accepted price in this pilot.
OFFICIAL_DOMAIN_HINTS = {
    "amazfit": ("amazfit.com", "zepp.com"),
    "anker": ("anker.com", "soundcore.com"),
    "soundcore": ("cn.soundcore.com", "soundcore.com"),
    "baseus": ("baseus.com",),
    "bose": ("bose.cn", "bose.com"),
    "huawei": ("huawei.com", "vmall.com"),
    "honor": ("honor.com",),
    "jbl": ("jbl.com", "harman.com"),
    "xiaomi": ("mi.com", "xiaomi.com"),
    "oppo": ("oppo.com",),
    "oneplus": ("oneplus.com",),
    "philips": ("philips.com.cn", "philips.com"),
    "samsung": ("samsung.com",),
    "sony": ("sony.com",),
    "apple": ("apple.com",),
    "beats": ("beatsbydre.com", "apple.com"),
    "sennheiser": ("sennheiser.com",),
    "shokz": ("shokz.com",),
    "nothing": ("nothing.tech",),
    "edifier": ("edifier.com",),
    "qcy": ("qcy.com.cn", "qcy.com"),
    "realme": ("realme.com",),
    "vivo": ("vivo.com.cn", "vivo.com"),
    "1more": ("1more.com",),
    "ugreen": ("ugreen.com",),
    "lenovo": ("lenovo.com",),
    "haylou": ("haylou.com",),
    "soundpeats": ("soundpeats.com",),
    "meizu": ("meizu.com",),
    "cleer": ("cleeraudio.com",),
    "redmi": ("mi.com", "xiaomi.com"),
    "fiil": ("fiil.com",),
    "libratone": ("libratone.com",),
    "earfun": ("myearfun.com", "earfun.com"),
    "jabra": ("jabra.cn", "jabra.com"),
    "marshall": ("marshall.com",),
    "motorola": ("motorola.com",),
}

# Mainland-China pages should be considered before generic global domains when
# both are owned by the same brand.
OFFICIAL_DOMAIN_HINTS["sony"] = ("sony.com.cn", "sonystyle.com.cn", "sony.com")

# Product records commonly use a Chinese distributor/manufacturer name together
# with the consumer brand (for example ``华米Amazfit``).  Match only explicit,
# unambiguous aliases so that a brand still has to resolve to a curated official
# domain before any price can be accepted.
BRAND_ALIASES = {
    "amazfit": ("amazfit", "华米"),
    "anker": ("anker", "安克"),
    "soundcore": ("soundcore", "声阔"),
    "baseus": ("baseus", "倍思"),
    "bose": ("bose", "博士"),
    "huawei": ("huawei", "华为"),
    "honor": ("honor", "荣耀"),
    "jbl": ("jbl",),
    "xiaomi": ("xiaomi", "小米"),
    "oppo": ("oppo",),
    "oneplus": ("oneplus", "一加"),
    "philips": ("philips", "飞利浦"),
    "samsung": ("samsung", "三星"),
    "sony": ("sony", "索尼"),
    "apple": ("apple", "苹果"),
    "beats": ("beats",),
    "sennheiser": ("sennheiser", "森海塞尔"),
    "shokz": ("shokz", "韶音"),
    "nothing": ("nothing",),
    "edifier": ("edifier", "漫步者"),
    "qcy": ("qcy",),
    "realme": ("realme", "真我"),
    "vivo": ("vivo",),
    "1more": ("1more", "万魔"),
    "ugreen": ("ugreen", "绿联"),
    "lenovo": ("lenovo", "联想"),
    "haylou": ("haylou",),
    "soundpeats": ("soundpeats", "泥炭"),
    "meizu": ("meizu", "魅族"),
    "cleer": ("cleer",),
    "redmi": ("redmi", "红米"),
    "fiil": ("fiil",),
    "libratone": ("libratone", "小鸟"),
    "earfun": ("earfun",),
    "jabra": ("jabra",),
    "marshall": ("marshall",),
    "motorola": ("motorola", "moto", "摩托罗拉"),
}

PROMPT = """You are a strict price-evidence auditor. Each supplied page has a source
tier and allowed price types. Extract a price only when that exact product and a
mainland-China price are explicit in the page. Reject transaction, promotion,
subsidy, coupon, pre-sale, used, overseas, or different-configuration prices.
Never upgrade a media or ZOL price into an official price. If evidence is
insufficient, return null. Return JSON only:
{"price_cny":number|null,"price_type":"official_msrp|official_launch_price|reported_launch_price|reference_price|no_reliable_result",
"evidence_index":number|null,"evidence_quote":string,"confidence":number,"reason":string}."""

# Fallback sources are deliberately labelled in the UI. They are not treated as
# official prices, and ZOL is only reached after every preceding tier fails.
MEDIA_DOMAINS = ("ithome.com", "cnmo.com", "mydrivers.com", "pchome.net")
ZOL_DOMAINS = ("zol.com.cn",)
FLAGSHIP_DOMAINS = ("jd.com", "tmall.com")


def canonical_brand(brand: str) -> str:
    """Resolve mixed Chinese/English record labels to an allow-list brand key."""
    normalised = "".join(brand.lower().split())
    for brand_key, aliases in BRAND_ALIASES.items():
        if any(alias in normalised for alias in aliases):
            return brand_key
    return normalised


def official_domains(product: dict) -> tuple[str, ...]:
    return OFFICIAL_DOMAIN_HINTS.get(canonical_brand(product["brand"]), ())


def is_official_url(url: str, domains: tuple[str, ...]) -> bool:
    host = urlsplit(url).hostname or ""
    host = host.lower().removeprefix("www.")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def resolve_bing_result_url(url: str) -> str:
    """Decode Bing's ``ck/a`` outbound-link wrapper when it is present."""
    parsed = urlsplit(url)
    if not parsed.hostname or not parsed.hostname.lower().endswith("bing.com"):
        return url
    token = parse_qs(parsed.query).get("u", [""])[0]
    if not token.startswith("a1"):
        return url
    try:
        encoded = token[2:] + "=" * (-len(token[2:]) % 4)
        decoded = base64.urlsafe_b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return url
    return decoded if decoded.startswith(("https://", "http://")) else url


def _recent_priority(product: dict) -> int:
    """Use the persisted UI priority when present, with a safe date fallback."""
    rank = product.get("priority_rank")
    if isinstance(rank, int):
        return rank
    first_seen = str(product.get("first_seen") or "")[:10]
    try:
        observed = datetime.strptime(first_seen, "%Y-%m-%d").date()
    except ValueError:
        return 3
    return 2 if observed >= date.today() - timedelta(days=730) else 3


def _has_verified_official_price(canonical_id: str) -> bool:
    """Use the source-of-truth enrich record, not a potentially stale snapshot."""
    path = official_enrich_dir() / f"{canonical_id}.json"
    if not path.exists():
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("msrp_cny") is not None
    except (OSError, json.JSONDecodeError):
        return False


def missing_price_products(limit: int | None = 20) -> tuple[list[dict], list[dict]]:
    priority_by_id: dict[str, dict] = {}
    index_path = ROOT / "data" / "products" / "index.json"
    try:
        index_payload = json.loads(index_path.read_text(encoding="utf-8"))
        priority_by_id = {
            str(item.get("canonical_id")): item
            for item in index_payload.get("products", [])
            if isinstance(item, dict) and item.get("canonical_id")
        }
    except (OSError, json.JSONDecodeError):
        # The date fallback keeps research safe even before the priority builder
        # has produced an index for the first time.
        pass

    products, skipped_identity = [], []
    for path in sorted((ROOT / "data" / "products").glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            product = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        snapshot = product.get("cost_snapshot") or {}
        canonical_id = str(product.get("canonical_id") or "")
        brand = str(product.get("brand") or "")
        model = str(product.get("model") or "")
        if not canonical_id or _has_verified_official_price(canonical_id):
            continue
        priority = priority_by_id.get(canonical_id, {})
        candidate = {
            "sku": canonical_id,
            "brand": brand,
            "model": model,
            "first_seen": priority.get("first_seen", product.get("first_seen", "")),
            "priority_rank": priority.get("priority_rank"),
            "research_priority": priority.get("research_priority", ""),
        }
        if (
            canonical_id.startswith("unknown--")
            or len(model) > 48
            or not is_identity_searchable(brand, model)
        ):
            skipped_identity.append(candidate)
            continue
        products.append(candidate)

    # Official-price research deliberately spends its limited request budget on
    # recent products first.  Historical products remain in the data set, but do
    # not displace current candidates unless the user asks for them explicitly.
    products.sort(key=lambda item: item.get("first_seen", ""), reverse=True)
    products.sort(key=_recent_priority)
    # ``--full`` passes zero and deliberately includes historical products too.
    # Recent products remain first because of the ordering above, but are never
    # allowed to suppress an older, identity-safe SKU from a user-requested full pass.
    if not limit or limit <= 0:
        return products, skipped_identity
    ordered = [item for item in products if _recent_priority(item) <= 2]
    return ordered[:limit], skipped_identity


def bing_search(query: str) -> list[dict]:
    response = requests.get(
        "https://www.bing.com/search?q=" + quote_plus(query),
        headers={"User-Agent": "Mozilla/5.0 (compatible; official-price-research/1.0)"},
        timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    hits = []
    for item in soup.select("li.b_algo")[:8]:
        anchor = item.select_one("h2 a")
        snippet = item.select_one(".b_caption p") or item.select_one("p")
        if anchor and anchor.get("href"):
            hits.append({
                "title": anchor.get_text(" ", strip=True),
                "url": anchor["href"],
                "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            })
    return hits


def extract_page_text(url: str) -> tuple[str, str]:
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; official-price-research/1.0)"},
        timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return title, soup.get_text(" ", strip=True)[:MAX_PAGE_CHARS]


def model_queries(product: dict) -> list[str]:
    """Generate safe exact-model alternatives without relying on CJK query text."""
    model = str(product.get("model") or "").strip()
    canonical = str(product.get("sku") or "").partition("--")[2].replace("-", " ")
    ascii_model = re.sub(r"[^A-Za-z0-9 .+_-]+", " ", model)
    queries = [model, ascii_model.strip(), canonical.strip()]
    seen: set[str] = set()
    return [query for query in queries if len(query) >= 3 and not (query.lower() in seen or seen.add(query.lower()))]


def _host_matches(url: str, domains: tuple[str, ...]) -> bool:
    return is_official_url(url, domains)


def _evidence_for_queries(
    queries: list[str], *, domains: tuple[str, ...], source_type: str,
    evidence_kind: str, allowed_price_types: tuple[str, ...], require_flagship: bool = False,
) -> tuple[list[dict], list[str]]:
    """Search a single source tier and return only fetchable, source-qualified pages."""
    raw_hits: list[dict] = []
    notes: list[str] = []
    for query in queries:
        try:
            raw_hits.extend(bing_search(query))
        except requests.RequestException as exc:
            notes.append(f"search failed for {evidence_kind}: {exc.__class__.__name__}")
    seen, evidence = set(), []
    for hit in raw_hits:
        url = resolve_bing_result_url(hit["url"])
        if url in seen or not _host_matches(url, domains):
            continue
        seen.add(url)
        try:
            title, page_text = extract_page_text(url)
        except requests.RequestException as exc:
            notes.append(f"could not fetch {evidence_kind}: {urlsplit(url).hostname} ({exc.__class__.__name__})")
            continue
        combined = f"{title} {page_text}".lower()
        if require_flagship and "官方旗舰店" not in combined and "official flagship" not in combined:
            continue
        if page_text:
            evidence.append({
                "source_type": source_type,
                "price_evidence_kind": evidence_kind,
                "allowed_price_types": list(allowed_price_types),
                "title": title or hit["title"],
                "url": url,
                "search_snippet": hit["snippet"],
                "page_text": page_text,
            })
        if len(evidence) >= 5:
            break
    return evidence, notes


def price_evidence_tiers(product: dict) -> list[tuple[str, list[dict], list[str]]]:
    """Return price sources in policy order; no fallback is mislabelled as official."""
    brand, models = str(product["brand"]), model_queries(product)
    domains = official_domains(product)
    tiers: list[tuple[str, list[dict], list[str]]] = []
    if domains:
        queries = [f'site:{domain} "{model}" (售价 OR 价格 OR 首发 OR 建议零售价)' for domain in domains for model in models]
        evidence, notes = _evidence_for_queries(queries, domains=domains, source_type="official_site_or_store_or_newsroom", evidence_kind="official_product_page", allowed_price_types=("official_msrp", "official_launch_price"))
        tiers.append(("official_site", evidence, notes))
    else:
        tiers.append(("official_site", [], ["brand has no curated official-domain allow-list entry"]))

    flagship_queries = [f'site:{domain} "{brand}" "{model}" 官方旗舰店 价格' for domain in FLAGSHIP_DOMAINS for model in models]
    evidence, notes = _evidence_for_queries(flagship_queries, domains=FLAGSHIP_DOMAINS, source_type="official_flagship_store", evidence_kind="official_flagship_store", allowed_price_types=("official_msrp", "official_launch_price"), require_flagship=True)
    tiers.append(("official_flagship_store", evidence, notes))

    media_queries = [f'site:{domain} "{brand}" "{model}" 发布 售价' for domain in MEDIA_DOMAINS for model in models]
    evidence, notes = _evidence_for_queries(media_queries, domains=MEDIA_DOMAINS, source_type="brand_announcement_report", evidence_kind="brand_announcement_report", allowed_price_types=("reported_launch_price",))
    tiers.append(("announcement_report", evidence, notes))

    zol_queries = [f'site:{domain} "{brand}" "{model}" 价格' for domain in ZOL_DOMAINS for model in models]
    evidence, notes = _evidence_for_queries(zol_queries, domains=ZOL_DOMAINS, source_type="zol_reference", evidence_kind="zol_reference_price", allowed_price_types=("reference_price",))
    tiers.append(("zol_reference", evidence, notes))
    return tiers


def finite_number(value: object, default: float = 0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def normalise_extraction(result: dict, evidence: list[dict]) -> dict:
    index = result.get("evidence_index")
    price = finite_number(result.get("price_cny"), default=-1)
    valid = (
        isinstance(index, int)
        and 0 <= index < len(evidence)
        and price > 0
        and result.get("price_type") in {"official_msrp", "official_launch_price", "reported_launch_price", "reference_price"}
        and bool(str(result.get("evidence_quote", "")).strip())
    )
    if valid and result.get("price_type") not in set(evidence[index].get("allowed_price_types") or []):
        valid = False
    if not valid:
        return {"price_cny": None, "price_type": "no_reliable_result", "confidence": 0,
                "evidence_url": "", "evidence_quote": "", "reason": str(result.get("reason", "insufficient official evidence"))}
    return {
        "price_cny": int(price) if price.is_integer() else price,
        "price_type": result["price_type"],
        "confidence": min(1, max(0, finite_number(result.get("confidence")))),
        "evidence_url": evidence[index]["url"],
        "evidence_quote": str(result["evidence_quote"]).strip()[:600],
        "reason": str(result.get("reason", "")).strip()[:500],
    }


def extract_official_price(api_key: str, product: dict, evidence: list[dict]) -> dict:
    response = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": MODEL, "thinking": {"type": "disabled"}, "temperature": 0, "max_tokens": 350,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": PROMPT},
                           {"role": "user", "content": json.dumps({"product": product, "official_page_evidence": evidence}, ensure_ascii=False)}]},
        timeout=45,
    )
    response.raise_for_status()
    return normalise_extraction(json.loads(response.json()["choices"][0]["message"]["content"]), evidence)


def write_accepted_price(product: dict, extraction: dict, evidence: list[dict]) -> None:
    """Persist only strict, source-linked model output to both official enrich paths."""
    if extraction.get("price_cny") is None or not extraction.get("evidence_url"):
        return
    canonical_id = str(product["sku"])
    path = official_enrich_dir() / f"{canonical_id}.json"
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    matching_evidence = next(
        (item for item in evidence if item.get("url") == extraction.get("evidence_url")), {}
    )
    evidence_kind = str(matching_evidence.get("price_evidence_kind") or "official_product_page")
    payload = {
        **existing,
        "canonical_id": canonical_id,
        "official_url": str(existing.get("official_url") or extraction["evidence_url"]),
        "vmall_url": str(existing.get("vmall_url") or ""),
        "product_name": str(existing.get("product_name") or f"{product['brand']} {product['model']}"),
        "msrp_cny": extraction["price_cny"],
        "price_evidence_kind": evidence_kind,
        "price_source_url": extraction["evidence_url"],
        "price_evidence": extraction.get("evidence_quote") or matching_evidence.get("title") or "",
        "tagline": str(existing.get("tagline") or ""),
        "selling_points": existing.get("selling_points") or [],
        "highlights": existing.get("highlights") or [],
        "search_query": str(existing.get("search_query") or f"{product['brand']} {product['model']} 官方售价"),
        "fetch_error": "",
        "captured_at": date.today().isoformat(),
        "source_layer": "official",
    }
    write_official_enrich(canonical_id, payload)


def research_one(api_key: str, product: dict) -> dict:
    try:
        all_notes: list[str] = []
        evidence, extraction = [], None
        for _tier, tier_evidence, notes in price_evidence_tiers(product):
            all_notes.extend(notes)
            if not tier_evidence:
                continue
            candidate = extract_official_price(api_key, product, tier_evidence)
            if candidate.get("price_cny") is not None:
                evidence, extraction = tier_evidence, candidate
                break
        if extraction is None:
            extraction = {
                "price_cny": None, "price_type": "no_reliable_result", "confidence": 0,
                "evidence_url": "", "evidence_quote": "", "reason": "; ".join(all_notes) or "no source-tier evidence",
            }
    except Exception as exc:
        evidence, extraction = [], {
            "price_cny": None, "price_type": "no_reliable_result", "confidence": 0,
            "evidence_url": "", "evidence_quote": "", "reason": f"research error: {exc.__class__.__name__}",
        }
    return {
        **product,
        "query_time": datetime.now(timezone.utc).isoformat(),
        "official_page_evidence": evidence,
        "extraction": extraction,
    }


def write_checkpoint(path: Path, *, mode: str, records: list[dict], skipped_identity: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "mode": mode,
        "scope": "exact-SKU, official-domain evidence only; no promotion, subsidy, transaction, or near-model prices",
        "processed": len(records),
        "accepted": sum(item["extraction"].get("price_cny") is not None for item in records),
        "skipped_identity_count": len(skipped_identity),
        "skipped_identity": skipped_identity,
        "records": records,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Research recent products with traceable official-price evidence")
    parser.add_argument("--limit", type=int, default=20, help="maximum recent products to research")
    parser.add_argument("--full", action="store_true", help="scan every identity-safe product without a verified official price")
    parser.add_argument("--workers", type=int, default=1, help="parallel exact-SKU searches (default: 1)")
    parser.add_argument("--write-accepted", action="store_true", help="write only strict accepted results into official enrich")
    parser.add_argument("--checkpoint-every", type=int, default=10, help="persist the review artifact every N completed products")
    args = parser.parse_args()
    api_key = os.environ["DEEPSEEK_API_KEY"]
    limit = 0 if args.full else args.limit
    products, skipped_identity = missing_price_products(limit)
    output_path = ROOT / "scratch_price_research" / ("price_text_full.json" if args.full else OUT.name)
    records = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as executor:
        futures = [executor.submit(research_one, api_key, product) for product in products]
        for index, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            records.append(record)
            if args.write_accepted and record["extraction"].get("price_cny") is not None:
                write_accepted_price(record, record["extraction"], record["official_page_evidence"])
            if index % max(1, args.checkpoint_every) == 0:
                write_checkpoint(output_path, mode="full" if args.full else "pilot", records=records, skipped_identity=skipped_identity)
            time.sleep(0.2)
    records.sort(key=lambda item: item["sku"])
    write_checkpoint(output_path, mode="full" if args.full else "pilot", records=records, skipped_identity=skipped_identity)
    print(json.dumps({"processed": len(records), "accepted": sum(r["extraction"].get("price_cny") is not None for r in records), "skipped_identity": len(skipped_identity)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
