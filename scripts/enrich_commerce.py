#!/usr/bin/env python3
"""官方渠道优先、ZOL / 京东兜底的可溯源价格 enrich。

用法:
  python scripts/enrich_commerce.py huawei--freebuds-pro-5
  python scripts/enrich_commerce.py --headphones --limit 20

价格优先级：官网/官方发布页 > 官方旗舰店 > ZOL / 京东第三方参考价。
第三方价格只在官方渠道未检索到明确价格时启用，并始终标记为参考价。
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.products import (  # noqa: E402
    canonical_product_id,
    identity_review_reason,
    is_identity_searchable,
    normalize_brand,
    normalize_model,
)
import re

from sources.channel.jd_client import fetch_jd_price, pick_best_hit, search_jd  # noqa: E402
from sources.channel.jd_union_client import (  # noqa: E402
    pick_best_union_hit,
    union_configured,
    union_detail,
    union_search,
)
from sources.channel.zol_client import (  # noqa: E402
    _commerce_search_query,
    best_channel_price,
    fetch_zol_prices,
    score_product_title,
)
from sources.official.fetcher import (  # noqa: E402
    fetch_official_page,
    resolve_official_url,
    search_official_site,
)

from core.paths import (
    channel_enrich_dir,
    commerce_hints_path,
    official_enrich_dir,
    products_dir,
    products_index_path,
    write_channel_enrich,
    write_official_enrich,
)
from core.scope import HEADPHONE_CATEGORIES  # noqa: E402

INDEX_PATH = products_index_path()


def _load_hints() -> dict:
    path = commerce_hints_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_product(canonical_id: str) -> dict | None:
    path = products_dir() / f"{canonical_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _list_headphone_products(limit: int | None = None) -> list[str]:
    idx_path = products_index_path()
    if not idx_path.exists():
        return []
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    ids: list[str] = []
    for p in index.get("products") or []:
        if p.get("category") in HEADPHONE_CATEGORIES:
            ids.append(p["canonical_id"])
    if limit:
        return ids[:limit]
    return ids


def _is_auto_channel_price(record: dict) -> bool:
    """Whether a channel record came from a live, automatically matched listing.

    These records are safe to refresh after matching logic improves.  Manually
    curated evidence (for example a cited launch report) is intentionally never
    replaced by a background lookup.
    """
    source = str(record.get("price_source") or "")
    return source in {"jd", "jd_api", "jd_union", "jd_hint", "zol_reference"} or source.startswith("zol_")


def _list_priority_unpriced_products(
    limit: int | None = None, *, refresh_auto_channel: bool = False
) -> list[str]:
    """Return current-priority products that need a price lookup.

    With ``refresh_auto_channel`` enabled, include only records whose previous
    result was an automatic marketplace match.  This is used to revalidate
    those records when the model-matching rules become stricter.
    """
    idx_path = products_index_path()
    if not idx_path.exists():
        return []
    try:
        index = json.loads(idx_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    candidates: list[tuple[int, str, str]] = []
    for item in index.get("products") or []:
        if not isinstance(item, dict):
            continue
        canonical_id = str(item.get("canonical_id") or "")
        if not canonical_id or int(item.get("priority_rank") or 99) > 2:
            continue
        product = _load_product(canonical_id)
        if not product:
            continue
        detail_has_price = (product.get("cost_snapshot") or {}).get("price_cny") is not None
        # A full run may start before product details have been rebuilt after a
        # newly-added evidence record.  Preserve those records instead of
        # replacing them with a weaker or unresolved live lookup result.
        already_priced = False
        refreshable_auto_channel = False
        for enrich_dir, field in ((official_enrich_dir(), "msrp_cny"), (channel_enrich_dir(), "price_cny")):
            enrich_path = enrich_dir / f"{canonical_id}.json"
            if not enrich_path.exists():
                continue
            try:
                record = json.loads(enrich_path.read_text(encoding="utf-8"))
                already_priced = record.get(field) is not None
                if enrich_dir == channel_enrich_dir() and already_priced:
                    refreshable_auto_channel = _is_auto_channel_price(record)
            except json.JSONDecodeError:
                pass
            if already_priced:
                break
        if refresh_auto_channel:
            if not refreshable_auto_channel:
                continue
        elif detail_has_price or already_priced:
            continue
        candidates.append((int(item.get("priority_rank") or 99), str(item.get("first_seen") or ""), canonical_id))

    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    ids = [canonical_id for _, _, canonical_id in candidates]
    return ids[:limit] if limit else ids


def enrich_channel(canonical_id: str, brand: str, model: str, hints: dict) -> dict:
    product_hint = hints.get(canonical_id) or {}
    zol_hint = product_hint.get("zol") or {}
    jd_hint = product_hint.get("jd") or {}
    query = _commerce_search_query(brand, model) or f"{brand} {model}".strip()

    zol_info = fetch_zol_prices(
        brand=brand,
        model=model,
        query=query,
        product_id=zol_hint.get("product_id"),
        product_url=zol_hint.get("product_url"),
    )
    # Generic names such as "Earbuds" must never match merely because another
    # product title contains that common word.  They need a much stronger
    # identifier match than a normal explicit model number.
    generic_models = {"earbuds", "earphone", "earphones", "headphones", "headset", "buds"}
    normalized_model = re.sub(r"[^a-z0-9]+", "", (model or "").lower())
    min_title_score = 4.0 if normalized_model in generic_models else 1.5
    # ZOL 详情页产品名与目标型号不一致时，丢弃 ZOL 结果（避免错链到牧士 MC2 等）
    if zol_info.product_name and score_product_title(zol_info.product_name, brand, model) < min_title_score:
        zol_info.fetch_error = "zol_product_mismatch"
        zol_info.reference_price_cny = None
        zol_info.channel_quotes = []

    price_cny, price_platform, channel_url = best_channel_price(zol_info)

    sku_id = None
    msrp_cny = zol_info.reference_price_cny
    shop_hint = ""
    live_error = zol_info.fetch_error or ""
    price_source = ""

    if price_cny is not None:
        price_source = "zol_reference" if price_platform == "zol_reference" else f"zol_{price_platform}"
        for q in zol_info.channel_quotes:
            if q.platform == "jd" and q.sku_id:
                sku_id = q.sku_id
            if q.platform == "jd":
                shop_hint = "京东（ZOL 溯源）"
    else:
        live_error = live_error or "zol_no_price"

    # 京东联盟 API（优先于已失效的页面直连）
    if union_configured():
        if sku_id and price_cny is None:
            u = union_detail(sku_id)
            if u and u.price_cny is not None:
                price_cny = u.price_cny
                msrp_cny = msrp_cny or u.msrp_cny
                price_source = "jd_union"
                channel_url = channel_url or u.channel_url
                shop_hint = shop_hint or u.shop_hint
        if price_cny is None or not sku_id:
            u_hits = union_search(query)
            u_best = pick_best_union_hit(u_hits, brand, model)
            if u_best:
                if price_cny is None and u_best.price_cny is not None:
                    price_cny = u_best.price_cny
                    price_source = "jd_union"
                sku_id = sku_id or u_best.sku_id
                channel_url = channel_url or u_best.channel_url
                shop_hint = shop_hint or u_best.shop_hint

    # 直连京东页面/API 兜底（ZOL + 联盟均失败时）
    jd_hit = None
    if price_cny is None or not sku_id:
        jd_hits = search_jd(query)
        if jd_hits:
            jd_hit = pick_best_hit(jd_hits, brand, model)
        elif not live_error and not union_configured():
            live_error = "jd_search_unreachable"

    if jd_hit:
        if price_cny is None and jd_hit.price_cny is not None:
            price_cny = jd_hit.price_cny
            price_source = "jd"
        sku_id = sku_id or jd_hit.sku_id
        channel_url = channel_url or jd_hit.channel_url
        shop_hint = shop_hint or jd_hit.shop_hint

    if price_cny is None:
        sku_id = sku_id or jd_hint.get("sku_id")
        channel_url = channel_url or jd_hint.get("channel_url", "")
        price_cny = jd_hint.get("price_cny")
        msrp_cny = msrp_cny or jd_hint.get("msrp_cny")
        if price_cny is not None:
            price_source = "jd_hint"
        else:
            price_source = "unresolved"

    if sku_id and price_cny is None:
        live_price = fetch_jd_price(str(sku_id))
        if live_price:
            price_cny = live_price.get("price_cny")
            msrp_cny = msrp_cny or live_price.get("msrp_cny")
            price_source = "jd_api"

    source_labels = {
        "zol_reference": "中关村在线参考价",
        "zol_jd": "京东渠道参考价（ZOL 溯源）",
        "zol_tmall": "天猫渠道参考价（ZOL 溯源）",
        "jd_union": "京东渠道参考价",
        "jd": "京东渠道参考价",
        "jd_hint": "京东渠道参考价",
        "jd_api": "京东渠道参考价",
    }
    price_label = source_labels.get(price_source, "第三方渠道参考价" if price_cny is not None else "")
    reference_notice = (
        "第三方渠道价格，仅作参考；具体官网售价请自行搜索。"
        if price_cny is not None
        else ""
    )
    return {
        "canonical_id": canonical_id,
        "price_cny": price_cny,
        "price_kind": "reference" if price_cny is not None else "",
        "price_label": price_label,
        "msrp_cny": msrp_cny,
        "reference_price_cny": zol_info.reference_price_cny,
        "price_source": price_source,
        "channel_url": channel_url,
        "sku_id": sku_id,
        "shop_hint": shop_hint or jd_hint.get("shop_hint", ""),
        "search_query": query,
        "price_note": reference_notice,
        "price_disclaimer": reference_notice,
        "live_error": live_error if price_cny is None else "",
        "zol": zol_info.to_dict(),
        "captured_at": datetime.now(timezone.utc).date().isoformat(),
        "source_layer": "channel",
    }


def _zol_needs_official_fallback(zol_info) -> bool:
    if isinstance(zol_info, dict):
        err = zol_info.get("fetch_error") or ""
        product_id = zol_info.get("product_id") or ""
    else:
        err = zol_info.fetch_error or ""
        product_id = zol_info.product_id or ""
    return err in ("zol_product_mismatch", "zol_search_no_hit") or (
        err == "zol_no_price" and not product_id
    )


def _merge_official_into_channel(channel: dict, official_page) -> dict:
    """渠道层无 ZOL/JD 价时，用官网 MSRP 兜底。"""
    if channel.get("price_cny") is not None:
        return channel
    msrp = official_page.msrp_cny if official_page else None
    if msrp is None:
        return channel
    channel = dict(channel)
    channel["price_cny"] = msrp
    channel["msrp_cny"] = channel.get("msrp_cny") or msrp
    channel["price_source"] = "official_msrp"
    channel["live_error"] = ""
    if official_page.official_url and not channel.get("channel_url"):
        channel["channel_url"] = official_page.official_url
        channel["shop_hint"] = channel.get("shop_hint") or "品牌官网"
    return channel


def enrich_official(
    canonical_id: str,
    brand: str,
    model: str,
    hints: dict,
    *,
    official_page=None,
    force_search: bool = False,
) -> dict:
    hint = (hints.get(canonical_id) or {}).get("official") or {}
    page = official_page

    if page is None:
        url = resolve_official_url(brand, model, hint.get("url"))
        if url and not force_search:
            page = fetch_official_page(url, brand=brand)
            if page.fetch_error or (not page.msrp_cny and not page.selling_points):
                page = search_official_site(brand, model)
        elif force_search or not url:
            page = search_official_site(brand, model)
        else:
            page = None

    url = (page.official_url if page else "") or hint.get("url", "")
    msrp = (page.msrp_cny if page and page.msrp_cny else None) or hint.get("msrp_cny")
    tagline = (page.tagline if page and page.tagline else "") or hint.get("tagline", "")
    highlights = (page.highlights if page and page.highlights else []) or hint.get("highlights", [])
    selling_points = page.selling_points if page and page.selling_points else []
    if not selling_points and highlights:
        selling_points = [{"text": h, "tag": "其他", "source_type": "official_hint"} for h in highlights]
    fetch_error = ""
    if page:
        fetch_error = page.fetch_error
    elif not url:
        fetch_error = "no_official_url"
    return {
        "canonical_id": canonical_id,
        "official_url": url or "",
        "vmall_url": hint.get("vmall_url", ""),
        "product_name": (page.product_name if page else "") or f"{brand} {model}".strip(),
        "msrp_cny": msrp,
        "tagline": tagline,
        "selling_points": selling_points,
        "highlights": highlights,
        "search_query": page.search_query if page else "",
        "fetch_error": fetch_error,
        "captured_at": datetime.now(timezone.utc).date().isoformat(),
        "source_layer": "official",
    }


def write_enrich(canonical_id: str, channel: dict, official: dict) -> None:
    write_channel_enrich(canonical_id, channel)
    write_official_enrich(canonical_id, official)


def enrich_one(canonical_id: str, hints: dict) -> dict:
    product = _load_product(canonical_id)
    if product:
        brand = product.get("brand", "")
        model = product.get("model", "")
    else:
        h = hints.get(canonical_id) or {}
        brand = h.get("brand", "")
        model = h.get("model", "")

    reason = identity_review_reason(brand, model)
    if not is_identity_searchable(brand, model):
        # Do not turn a broad title (for example just “Earbuds”) into a price
        # lookup query.  It is recorded for review instead of risking a
        # plausible-looking but unrelated channel or official price.
        channel = {
            "canonical_id": canonical_id,
            "price_cny": None,
            "msrp_cny": None,
            "reference_price_cny": None,
            "price_source": "unresolved",
            "channel_url": "",
            "sku_id": None,
            "shop_hint": "",
            "search_query": f"{brand} {model}".strip(),
            "price_note": "",
            "live_error": f"identity_needs_review:{reason}",
            "captured_at": datetime.now(timezone.utc).date().isoformat(),
            "source_layer": "channel",
        }
        official = {
            "canonical_id": canonical_id,
            "official_url": "",
            "vmall_url": "",
            "product_name": f"{brand} {model}".strip(),
            "msrp_cny": None,
            "tagline": "",
            "selling_points": [],
            "highlights": [],
            "search_query": channel["search_query"],
            "fetch_error": f"identity_needs_review:{reason}",
            "captured_at": channel["captured_at"],
            "source_layer": "official",
        }
        write_enrich(canonical_id, channel, official)
        return {"canonical_id": canonical_id, "channel": channel, "official": official}

    # 官方渠道是第一优先级。只有官方页面没有明确价格时，才访问
    # ZOL / 京东，避免较弱的渠道报价掩盖可核验的官方售价。
    official = enrich_official(
        canonical_id,
        brand,
        model,
        hints,
        force_search=True,
    )
    if official.get("msrp_cny") is not None:
        channel = {
            "canonical_id": canonical_id,
            "price_cny": None,
            "price_kind": "",
            "price_label": "",
            "msrp_cny": None,
            "reference_price_cny": None,
            "price_source": "official_price_available",
            "channel_url": "",
            "sku_id": None,
            "shop_hint": "",
            "search_query": f"{brand} {model}".strip(),
            "price_note": "",
            "price_disclaimer": "",
            "live_error": "",
            "captured_at": datetime.now(timezone.utc).date().isoformat(),
            "source_layer": "channel",
        }
    else:
        channel = enrich_channel(canonical_id, brand, model, hints)
    write_enrich(canonical_id, channel, official)
    return {"canonical_id": canonical_id, "channel": channel, "official": official}


def enrich_many(ids: list[str], hints: dict, workers: int) -> list[dict]:
    """Run the complete queue concurrently while preserving safe per-product writes.

    A small bounded pool prevents one slow or rate-limited official site from
    serially blocking every other brand.  Each task only writes its own
    canonical-id files, so no shared staging file is mutated concurrently.
    """
    if workers <= 1 or len(ids) <= 1:
        return [enrich_one(canonical_id, hints) for canonical_id in ids]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(lambda canonical_id: enrich_one(canonical_id, hints), ids))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("canonical_id", nargs="?", help="产品 canonical_id")
    parser.add_argument("--canonical", dest="canonical_alt", help="同上")
    parser.add_argument("--headphones", action="store_true", help="批量处理耳机品类")
    parser.add_argument("--priority-unpriced", action="store_true", help="全量处理当前高优先级无价格队列")
    parser.add_argument(
        "--refresh-auto-channel",
        action="store_true",
        help="重新核验此前由自动渠道匹配得到的高优先级价格",
    )
    parser.add_argument("--workers", type=int, default=4, help="并发检索数（默认 4，避免站点限流）")
    parser.add_argument("--limit", type=int, default=None, help="批量上限（默认全部）")
    args = parser.parse_args()

    hints = _load_hints()
    cid = args.canonical_id or args.canonical_alt

    if args.headphones:
        ids = _list_headphone_products(args.limit if args.limit else None)
        results = enrich_many(ids, hints, max(1, args.workers))
        print(json.dumps({"count": len(results), "ids": ids}, ensure_ascii=False, indent=2))
        return

    if args.priority_unpriced or args.refresh_auto_channel:
        ids = _list_priority_unpriced_products(
            args.limit if args.limit else None,
            refresh_auto_channel=args.refresh_auto_channel,
        )
        results = enrich_many(ids, hints, max(1, args.workers))
        priced = sum(
            1
            for result in results
            if (result.get("official") or {}).get("msrp_cny") is not None
            or (result.get("channel") or {}).get("price_cny") is not None
        )
        review = sum(
            1
            for result in results
            if str((result.get("official") or {}).get("fetch_error") or "").startswith("identity_needs_review:")
        )
        print(json.dumps({"count": len(results), "priced": priced, "identity_review": review, "ids": ids}, ensure_ascii=False, indent=2))
        return

    if not cid:
        parser.error("请提供 canonical_id 或 --headphones")
    result = enrich_one(cid, hints)
    out = json.dumps(result, ensure_ascii=False, indent=2)
    sys.stdout.buffer.write(out.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")


if __name__ == "__main__":
    main()
