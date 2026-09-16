#!/usr/bin/env python3
"""Audit the complete generated corpus and create human-review queues.

The audit is deliberately non-mutating: it never accepts an earphone category,
merges a brand, or merges a supplier.  It only reports source-backed problems
and suspected aliases for business confirmation.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
WEB_DATA = ROOT / "web" / "public" / "data"
OUTPUT = ROOT / "reports" / "data-quality" / date.today().isoformat()
ALLOWED_CATEGORIES = {
    "耳夹式耳机", "耳挂式耳机", "全入耳式耳机", "半入耳式耳机",
    "头戴式耳机", "颈挂式蓝牙耳机", "有线耳机", "骨传导耳机",
    "待细分耳机",
}
LEGACY_CATEGORIES = {"开放式耳机", "真无线耳机TWS"}
UNKNOWN_BRANDS = {"", "未知", "未知品牌"}
NON_CONSUMER = re.compile(
    r"(?:公司|生产商|制造商|供应商|包装盒|包装清单|拆解|主板|丝印|镭雕|"
    r"微控制器|芯片型号|电源管理芯片|保护IC|MOS管|晶振|物料清单)",
    re.I,
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> int:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def signature(value: str, *, company: bool = False) -> str:
    value = unicodedata.normalize("NFKC", clean(value)).casefold()
    value = re.sub(r"[（(].*?[）)]", "", value)
    if company:
        value = re.sub(
            r"(?:中华人民共和国|中国|广东省|广东|东莞市|东莞|深圳市|深圳|惠州市|惠州|"
            r"广州市|广州|苏州市|苏州|天津市|天津|科技|电子|新能源|能源|半导体|声学|"
            r"股份|有限责任|有限公司|公司|集团)$",
            "",
            value,
        )
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def latin_tokens(value: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", value)}


def report_urls() -> dict[str, str]:
    result: dict[str, str] = {}
    reports_dir = ROOT / "data" / "reports"
    if reports_dir.exists():
        for path in reports_dir.glob("*.json"):
            try:
                row = load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            result[str(row.get("id") or path.stem)] = clean(row.get("url") or row.get("source_url"))
    return result


def product_sources(product_id: str) -> list[str]:
    path = WEB_DATA / "products" / f"{product_id}.json"
    if not path.exists():
        return []
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    return list(dict.fromkeys(
        clean(item.get("source_url") or item.get("video_url"))
        for item in payload.get("related_intelligence") or []
        if clean(item.get("source_url") or item.get("video_url"))
    ))


def latest_supplier_llm_review() -> tuple[list[dict[str, Any]], str]:
    """Load the newest non-mutating LLM supplier-name audit, if present."""

    root = ROOT.parent / "pilot-results" / "supplier-alias-audit"
    candidates = sorted(root.glob("*/result.json"), reverse=True) if root.exists() else []
    if not candidates:
        return [], ""
    path = candidates[0]
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return [], str(path)
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(payload.get("issues") or [], 1):
        if not isinstance(item, dict):
            continue
        rows.append({
            "group_id": index,
            "status": clean(item.get("status")),
            "members": " | ".join(clean(value) for value in item.get("members") or []),
            "suggested_canonical": clean(item.get("canonical")),
            "reason": clean(item.get("reason")),
            "action": "模型仅发现候选；请人工确认，禁止据此自动合并企业",
        })
    return rows, str(path)


def category_review(products: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    path = ROOT / "data" / "staging" / "earphone_categories" / "classifications.json"
    payload = load_json(path)
    urls = report_urls()
    classified = {
        clean(item.get("product_id")): item
        for item in payload.get("items") or []
        if clean(item.get("product_id"))
    }
    rows: list[dict[str, Any]] = []
    accepted_mismatches = 0
    accepted_without_evidence = 0
    for product_id, item in classified.items():
        current_product = products.get(product_id)
        if not current_product:
            continue
        if item.get("status") == "accepted":
            # A later human-reviewed source override legitimately supersedes
            # the automated classifier.  Only count unexplained drift.
            if (
                clean(current_product.get("category_status")) != "human_reviewed"
                and clean(current_product.get("category")) != clean(item.get("category_current"))
            ):
                accepted_mismatches += 1
            if not clean(item.get("evidence_quote")):
                accepted_without_evidence += 1
        if clean(current_product.get("category")) != "待细分耳机":
            continue
        report_id = clean(item.get("report_id"))
        source_url = clean(item.get("source_url")) or urls.get(report_id, "")
        if not source_url:
            sources = product_sources(product_id)
            source_url = sources[0] if sources else ""
        rows.append({
            "product_id": product_id,
            "brand": clean(current_product.get("brand")),
            "model": clean(current_product.get("model")),
            "current_category": clean(current_product.get("category") or item.get("category_current")),
            "proposed_category": clean(item.get("proposed_category")),
            "review_reason": clean(item.get("review_reason")),
            "basis": clean(item.get("basis")),
            "report_id": report_id,
            "source_url": source_url,
            "source_status": "可打开原来源" if source_url else "本地缺少可用来源链接",
        })
    return rows, {
        "target_products": sum(product_id in products for product_id in classified),
        "accepted": sum(
            clean(products[product_id].get("category")) != "待细分耳机"
            for product_id in classified
            if product_id in products
        ),
        "needs_review": len(rows),
        "accepted_index_mismatches": accepted_mismatches,
        "accepted_without_evidence": accepted_without_evidence,
    }


def alias_review(
    names: list[str],
    product_ids_by_name: dict[str, set[str]],
    source_by_name: dict[str, str],
    *,
    entity: str,
) -> list[dict[str, Any]]:
    seeded = {
        "brand": [
            ["Samsung", "三星", "Samsung三星"],
            ["ZMI", "紫米 ZMI", "紫米ZMI"],
            ["飞智", "飞智科技"],
            ["LIBRATONE小鸟", "小鸟"],
            ["BULL", "BULL公牛"],
            ["iWALK", "iWalk", "iWalk 爱魔"],
            ["earsopen逸鸥", "Earsopen逸鸥", "earsopen骨聆"],
            ["RAPOO", "RAPOO雷柏", "Rapoo"],
            ["Boltune", "Boltune博尔通"],
        ],
        "supplier": [
            ["JYZ金宇宙", "东莞市锂宇能源有限公司（ 深圳市金宇宙能源有限公司旗下子公司）"],
            ["GF赣锋锂业", "新余赣锋"],
            ["鹏辉能源", "广州鹏辉能源科技有限公司"],
            ["豪鹏科技", "广东省豪鹏新能源科技有限公司", "曙鹏科技（深圳）有限公司"],
            ["SDI三星", "天津三星视界有限公司"],
            ["JHY聚和源", "长虹聚和源"],
        ],
    }[entity]
    available = set(names)
    groups: dict[tuple[str, ...], tuple[str, str]] = {}
    for candidates in seeded:
        members = tuple(sorted((name for name in candidates if name in available), key=str.casefold))
        if len(members) >= 2:
            groups[members] = (
                "疑似别名" if entity == "brand" else "疑似企业关系，禁止自动合并",
                "已知高风险名称组合，需要业务确认",
            )

    by_exact: dict[str, list[str]] = defaultdict(list)
    for name in names:
        by_exact[signature(name)].append(name)
    for members in by_exact.values():
        unique_members = tuple(sorted(set(members), key=str.casefold))
        if len(unique_members) >= 2:
            groups.setdefault(unique_members, ("大小写/符号变体", "规范化后字符串完全一致"))

    # Shared Latin brand marks are useful for finding bilingual variants.  They
    # are only review candidates; legal entities are never merged by this rule.
    for index, left in enumerate(names):
        left_tokens = latin_tokens(left)
        if not left_tokens:
            continue
        for right in names[index + 1:]:
            shared = left_tokens & latin_tokens(right)
            if not shared:
                continue
            left_key = signature(left, company=entity == "supplier")
            right_key = signature(right, company=entity == "supplier")
            if left_key == right_key or left_key in right_key or right_key in left_key:
                members = tuple(sorted({left, right}, key=str.casefold))
                groups.setdefault(members, ("疑似中英文/简称变体", f"共享标识：{'、'.join(sorted(shared))}"))

    # Merge overlapping candidate pairs into one review group so the reviewer
    # sees e.g. RAPOO/Rapoo/RAPOO雷柏 once instead of three pairwise rows.
    parent = {name: name for members in groups for name in members}
    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name
    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root
    for members in groups:
        for name in members[1:]:
            union(members[0], name)
    connected: dict[str, set[str]] = defaultdict(set)
    for name in parent:
        connected[find(name)].add(name)

    rows: list[dict[str, Any]] = []
    for group_id, members_set in enumerate(sorted(connected.values(), key=lambda values: sorted(values, key=str.casefold)[0].casefold()), 1):
        members = tuple(sorted(members_set, key=str.casefold))
        related = [meta for candidate_members, meta in groups.items() if set(candidate_members) & members_set]
        status = "疑似企业关系，禁止自动合并" if any("企业关系" in meta[0] for meta in related) else "疑似别名"
        reason = "；".join(dict.fromkeys(meta[1] for meta in related))
        for name in members:
            rows.append({
                "group_id": group_id,
                "status": status,
                "name": name,
                "unique_products": len(product_ids_by_name.get(name, set())),
                "sample_source_url": source_by_name.get(name, ""),
                "reason": reason,
                "action": "请确认规范显示名；供应商涉及母子公司时请确认是否必须保持独立",
            })
    return rows


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    index = load_json(WEB_DATA / "products" / "index.json")
    products = {clean(item.get("canonical_id")): item for item in index.get("products") or []}

    category_rows, category_stats = category_review(products)
    write_csv(
        OUTPUT / "earphone-category-review.csv",
        category_rows,
        ["product_id", "brand", "model", "current_category", "proposed_category", "review_reason", "basis", "report_id", "source_url", "source_status"],
    )

    component_files = sorted((WEB_DATA / "bom-analysis").glob("*.json"))
    component_rows: list[dict[str, Any]] = []
    for path in component_files:
        if path.name == "manifest.json":
            continue
        component_rows.extend(load_json(path).get("rows") or [])

    supplier_products: dict[str, set[str]] = defaultdict(set)
    supplier_sources: dict[str, str] = {}
    brand_products: dict[str, set[str]] = defaultdict(set)
    brand_sources: dict[str, str] = {}
    source_types: Counter[str] = Counter()
    invalid_categories: Counter[str] = Counter()
    invalid_supplier_rows = 0
    malformed_models = 0
    component_issue_rows: list[dict[str, Any]] = []
    for row in component_rows:
        product_id = clean(row.get("product_id"))
        supplier = clean(row.get("component_manufacturer_canonical") or row.get("component_manufacturer"))
        brand = clean(row.get("product_brand"))
        source_url = clean(row.get("source_report_url"))
        if supplier and supplier not in {"未知", "报告未披露"}:
            supplier_products[supplier].add(product_id)
            supplier_sources.setdefault(supplier, source_url)
        if brand not in UNKNOWN_BRANDS:
            brand_products[brand].add(product_id)
            brand_sources.setdefault(brand, source_url)
        source_types[clean(row.get("source_type")) or "missing"] += 1
        category = clean(row.get("product_category"))
        if category not in ALLOWED_CATEGORIES:
            invalid_categories[category or "missing"] += 1
        invalid_supplier = row.get("manufacturer_status") == "invalid_extraction"
        malformed_model = row.get("model_status") == "invalid_parameter_value"
        invalid_supplier_rows += invalid_supplier
        malformed_models += malformed_model
        if invalid_supplier or malformed_model:
            component_issue_rows.append({
                "product_id": product_id,
                "product_brand": brand,
                "product_model": clean(row.get("product_model")),
                "component_type": clean(row.get("component_label")),
                "supplier": supplier,
                "component_model": clean(row.get("component_model")),
                "issue": "invalid_supplier_extraction" if invalid_supplier else "invalid_parameter_as_model",
                "evidence_quote": clean(row.get("evidence_quote")),
                "source_url": source_url,
            })

    write_csv(
        OUTPUT / "component-extraction-review.csv",
        component_issue_rows,
        [
            "product_id", "product_brand", "product_model", "component_type",
            "supplier", "component_model", "issue", "evidence_quote", "source_url",
        ],
    )

    supplier_names = [clean(item.get("supplier")) for item in load_json(WEB_DATA / "supplier-sourcing" / "manifest.json").get("suppliers") or []]
    supplier_alias_rows = alias_review(supplier_names, supplier_products, supplier_sources, entity="supplier")
    write_csv(
        OUTPUT / "supplier-alias-review.csv",
        supplier_alias_rows,
        ["group_id", "status", "name", "unique_products", "sample_source_url", "reason", "action"],
    )

    supplier_llm_rows, supplier_llm_source = latest_supplier_llm_review()
    write_csv(
        OUTPUT / "supplier-alias-llm-review.csv",
        supplier_llm_rows,
        ["group_id", "status", "members", "suggested_canonical", "reason", "action"],
    )

    brand_names = [clean(item.get("brand")) for item in load_json(WEB_DATA / "brand-supply-chain" / "manifest.json").get("brands") or []]
    brand_alias_rows = alias_review(brand_names, brand_products, brand_sources, entity="brand")
    write_csv(
        OUTPUT / "brand-alias-review.csv",
        brand_alias_rows,
        ["group_id", "status", "name", "unique_products", "sample_source_url", "reason", "action"],
    )

    unresolved_identity_rows = []
    for product_id, product in products.items():
        brand = clean(product.get("brand"))
        model = clean(product.get("model"))
        if brand not in UNKNOWN_BRANDS and model and model.casefold() not in {"unknown", "earbuds", "buds"}:
            continue
        sources = product_sources(product_id)
        unresolved_identity_rows.append({
            "product_id": product_id,
            "brand": brand,
            "model": model,
            "category": clean(product.get("category")),
            "reason": "brand_missing" if brand in UNKNOWN_BRANDS else "model_missing_or_generic",
            "source_url": sources[0] if sources else "",
        })
    write_csv(
        OUTPUT / "product-identity-review.csv",
        unresolved_identity_rows,
        ["product_id", "brand", "model", "category", "reason", "source_url"],
    )

    video_gap_rows: list[dict[str, Any]] = []
    missing_image_rows: list[dict[str, Any]] = []
    for product_id, product in products.items():
        detail_path = WEB_DATA / "products" / f"{product_id}.json"
        if not detail_path.exists():
            continue
        detail = load_json(detail_path)
        report_ids = [clean(value) for value in detail.get("report_ids") or [] if clean(value)]
        video_ids = [clean(value) for value in detail.get("video_ids") or [] if clean(value)]
        sources = product_sources(product_id)
        published_facts = (
            detail.get("technical_facts")
            or detail.get("teardown_inventory")
            or detail.get("bom_table")
            or []
        )
        if video_ids and not report_ids and not published_facts:
            video_gap_rows.append({
                "product_id": product_id,
                "brand": clean(product.get("brand")),
                "model": clean(product.get("model")),
                "category": clean(product.get("category")),
                "video_ids": " | ".join(video_ids),
                "reason": "video_only_product_without_published_component_facts",
                "source_url": sources[0] if sources else "",
            })
        card_path = clean(product.get("card_image_path"))
        if card_path:
            local_image = ROOT / "web" / "public" / Path(*card_path.lstrip("/").split("/"))
            if not local_image.is_file() or local_image.stat().st_size == 0:
                missing_image_rows.append({
                    "product_id": product_id,
                    "brand": clean(product.get("brand")),
                    "model": clean(product.get("model")),
                    "card_image_path": card_path,
                    "reason": "missing_or_empty_generated_image",
                    "source_url": sources[0] if sources else "",
                })
    write_csv(
        OUTPUT / "video-product-processing-review.csv",
        video_gap_rows,
        ["product_id", "brand", "model", "category", "video_ids", "reason", "source_url"],
    )
    write_csv(
        OUTPUT / "image-review.csv",
        missing_image_rows,
        ["product_id", "brand", "model", "card_image_path", "reason", "source_url"],
    )

    consumer_issues = []
    for product_id in products:
        path = WEB_DATA / "products" / f"{product_id}.json"
        if not path.exists():
            continue
        detail = load_json(path)
        claims = ((detail.get("market") or {}).get("consumer_claims") or (detail.get("market") or {}).get("selling_points") or [])
        for claim in claims:
            claim_text = clean(claim.get("text")) if isinstance(claim, dict) else clean(claim)
            if claim_text and NON_CONSUMER.search(claim_text):
                consumer_issues.append({
                    "product_id": product_id,
                    "brand": clean(detail.get("brand")),
                    "model": clean(detail.get("model")),
                    "claim": claim_text,
                    "reason": "疑似把企业、包装或纯BOM事实放入消费者卖点",
                    "source_url": (product_sources(product_id) or [""])[0],
                })
    write_csv(
        OUTPUT / "consumer-claim-review.csv",
        consumer_issues,
        ["product_id", "brand", "model", "claim", "reason", "source_url"],
    )

    public_categories = Counter(clean(item.get("category")) for item in products.values())
    legacy_count = sum(public_categories[value] for value in LEGACY_CATEGORIES)
    release = load_json(WEB_DATA / "release-meta.json") if (WEB_DATA / "release-meta.json").exists() else {}
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_version": clean((release.get("_release") or {}).get("data_version")),
        "products": len(products),
        "component_rows": len(component_rows),
        "component_source_types": dict(source_types),
        "fine_category_distribution": dict(public_categories),
        "legacy_category_rows": legacy_count,
        "category_audit": category_stats,
        "category_review_rows": len(category_rows),
        "supplier_names": len(supplier_names),
        "supplier_alias_review_rows": len(supplier_alias_rows),
        "brand_names_in_analysis": len(brand_names),
        "brand_alias_review_rows": len(brand_alias_rows),
        "unresolved_product_identities": len(unresolved_identity_rows),
        "invalid_component_categories": dict(invalid_categories),
        "invalid_supplier_rows": invalid_supplier_rows,
        "malformed_model_rows": malformed_models,
        "component_extraction_review_rows": len(component_issue_rows),
        "video_only_products_without_component_facts": len(video_gap_rows),
        "missing_or_empty_card_images": len(missing_image_rows),
        "supplier_llm_review_rows": len(supplier_llm_rows),
        "supplier_llm_review_source": supplier_llm_source,
        "consumer_claim_review_rows": len(consumer_issues),
    }
    (OUTPUT / "audit-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = f"""# 全量数据质量核查（{date.today().isoformat()}）

本报告只生成待确认清单，不会自动合并企业、品牌或接受证据不足的耳机细分类。

## 核查范围

- 产品档案：{len(products)} 款
- 器件证据记录：{len(component_rows)} 条
- 供应商规范显示名：{len(supplier_names)} 个
- 品牌供应链分析显示名：{len(brand_names)} 个
- 器件来源构成：{json.dumps(dict(source_types), ensure_ascii=False)}

## 结果

- 耳机细分类：已接受 {category_stats['accepted']} 款，待确认 {len(category_rows)} 款；当前前端仍出现旧粗分类 {legacy_count} 款。
- 已接受分类与当前产品索引不一致：{category_stats['accepted_index_mismatches']} 款；已接受但没有证据摘录：{category_stats['accepted_without_evidence']} 款。
- 疑似供应商别名/企业关系：{len({row['group_id'] for row in supplier_alias_rows})} 组。
- 疑似品牌别名：{len({row['group_id'] for row in brand_alias_rows})} 组。
- 品牌或型号仍未解析：{len(unresolved_identity_rows)} 款。
- 疑似消费者卖点污染：{len(consumer_issues)} 条。
- 器件厂商/型号抽取异常：{len(component_issue_rows)} 条。
- 仅有视频但尚未发布器件事实：{len(video_gap_rows)} 款。
- 产品卡片图片缺失或空文件：{len(missing_image_rows)} 款。
- DeepSeek 供应商名称补充复核：{len(supplier_llm_rows)} 组（仅候选，不写入别名表）。
- 器件分析中的旧/异常耳机分类：{json.dumps(dict(invalid_categories), ensure_ascii=False)}。
- 视频来源器件记录：{source_types.get('video', 0)} 条（没有拆解报告的视频产品若为 0，说明尚未进入器件分析）。

## 需要人工确认的清单

- `earphone-category-review.csv`：每款待细分产品、模型建议、拒绝原因和来源链接。
- `supplier-alias-review.csv`：疑似同名、简称或企业关系；母子公司不会自动合并。
- `brand-alias-review.csv`：疑似品牌大小写、中英文或简称变体。
- `product-identity-review.csv`：品牌缺失或型号泛化的产品。
- `consumer-claim-review.csv`：疑似混入消费者卖点的企业、包装或纯技术表述。
- `component-extraction-review.csv`：疑似把参数当型号或厂商抽取异常的器件行。
- `video-product-processing-review.csv`：没有拆解报告、但视频事实尚未进入器件统计的产品。
- `image-review.csv`：前端卡片引用缺失或空图片的产品。
- `supplier-alias-llm-review.csv`：DeepSeek 对全量供应商显示名的补充候选。
"""
    (OUTPUT / "audit-summary.md").write_text(markdown, encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
