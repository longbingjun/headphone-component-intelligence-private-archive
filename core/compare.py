"""内部产品对标：读取 internal_products.json 与竞品记录做模糊匹配。"""

from __future__ import annotations

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INTERNAL_PATH = DATA_DIR / "internal_products.json"


def load_internal_products() -> list[dict]:
    if not INTERNAL_PATH.exists():
        return []
    try:
        data = json.loads(INTERNAL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return data
    return data.get("products", [])


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def find_internal_matches(record: dict, products: list[dict] | None = None) -> list[dict]:
    """按品类 + 品牌/型号关键词返回可能对标内部 SKU。"""
    products = products if products is not None else load_internal_products()
    if not products:
        return []

    cat = (record.get("category") or "").strip()
    brand = _norm(record.get("brand") or "")
    model = _norm(record.get("model") or "")
    title = _norm(record.get("title") or record.get("product_title") or "")

    matches: list[dict] = []
    for p in products:
        p_cat = (p.get("category") or "").strip()
        if cat and p_cat and cat != p_cat:
            continue
        p_brand = _norm(p.get("brand") or "")
        p_model = _norm(p.get("model") or p.get("name") or "")
        p_sku = p.get("sku") or p.get("id") or ""
        score = 0
        if brand and p_brand and (brand in p_brand or p_brand in brand):
            score += 2
        if model and p_model and (model in p_model or p_model in model or model in title):
            score += 2
        if not brand and not model and cat and p_cat == cat:
            score += 1
        if score >= 2:
            matches.append({**p, "match_score": score, "sku": p_sku})
    matches.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return matches[:5]


def suggest_internal_match(brand: str, model: str, category: str) -> list[dict]:
    """返回疑似对标的内部 SKU 列表，按置信度降序。

    每项: {"sku": str, "score": float, "reason": str}
    """
    from difflib import SequenceMatcher

    products = load_internal_products()
    if not products:
        return []

    query = f"{brand} {model}".strip()
    results: list[dict] = []

    for item in products:
        sku = item.get("sku") or item.get("id") or ""
        aliases = [sku, *item.get("aliases", [])]
        if item.get("model"):
            aliases.append(item["model"])
        item_category = item.get("category", "")

        best_score = 0.0
        best_alias = ""
        for alias in aliases:
            if not alias:
                continue
            score = SequenceMatcher(None, _norm(query), _norm(alias)).ratio()
            if score > best_score:
                best_score = score
                best_alias = alias

        if category and item_category and category == item_category:
            best_score = min(1.0, best_score + 0.15)

        if best_score < 0.45:
            continue

        reason_parts = [f"型号相似度 {best_score:.0%}（匹配 {best_alias}）"]
        if category and item_category and category == item_category:
            reason_parts.append("品类一致")
        results.append({"sku": sku, "score": round(best_score, 3), "reason": "；".join(reason_parts)})

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:5]
