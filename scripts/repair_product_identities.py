#!/usr/bin/env python3
"""Resolve ambiguous product identities from the original 52audio article.

This creates an auditable staging layer rather than changing raw reports.  It
may split a roundup/comparison article into several headphones, but only when
the model returns quoted source evidence and a high-confidence, exact identity.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ingest import load_all_records  # noqa: E402
from core.paths import identity_overrides_path  # noqa: E402
from core.products import (  # noqa: E402
    guess_brand_from_text,
    identity_review_reason,
    normalize_brand,
    normalize_model,
)
from sources.audio52.lexicon import BRAND_ALIASES  # noqa: E402

MAX_ARTICLE_CHARS = 14_000
PROMPT = """You are resolving product identities from an original 52audio article.
Return JSON only. Decide whether the article is about one headphone product,
multiple distinct headphone products, or not a headphone product. Do not infer
names not supported by the supplied article text. For each product, return the
consumer brand, the exact sellable model (without teardown/review wording), a
headphone category, confidence from 0 to 1, and a short exact evidence quote.
Use decision one of: single_headphone, multi_headphone, not_headphone, needs_review.
For an article comparing or summarising several products, use multi_headphone
and list each identifiable product. A generic term such as Buds/Earbuds without
a distinguishing model must be needs_review. JSON schema:
{"decision":"...","reason":"...","products":[{"brand":"","model":"","category":"","confidence":0,"evidence_quote":""}]}"""

TITLE_PROMPT = """你是消费电子产品身份字段抽取器。只根据输入的来源标题提取耳机品牌和可销售型号。
规则：
1. 不得使用标题之外的知识猜测品牌；品牌证据和型号证据必须逐字出现在标题中。
2. 去掉“拆解、评测、视频、真无线耳机、开放式耳机”等文章或品类描述，只保留品牌和具体型号。
3. 标题只包含 Buds、Earbuds、耳机等泛称，或不能确认具体品牌/型号时，必须返回 needs_review。
4. 标题包含多个不同产品时返回 multi_headphone；不是耳机产品时返回 not_headphone。
5. evidence_quote 必须是标题中的连续原文，confidence 仅在品牌和型号都明确时才能达到 0.9。
只返回 JSON：
{"decision":"single_headphone|multi_headphone|not_headphone|needs_review","reason":"...","products":[{"brand":"标题中的品牌原文","model":"标题中的具体型号","category":"耳机类型","confidence":0,"evidence_quote":"标题连续原文"}]}"""

TITLE_BATCH_PROMPT = """你是消费电子产品身份字段抽取器。输入包含多个 items，请逐项处理，且不得跨标题引用信息。
只根据每个 item 的 title 提取耳机品牌和可销售型号。
规则：
1. 不得使用标题之外的知识猜测品牌；品牌证据和型号证据必须逐字出现在同一个标题中。
2. 去掉“拆解、评测、视频、真无线耳机、开放式耳机”等文章或品类描述，只保留品牌和具体型号。
3. 标题只包含 Buds、Earbuds、耳机等泛称，或不能确认具体品牌/型号时，必须返回 needs_review。
4. 标题包含多个不同产品时返回 multi_headphone；不是耳机产品时返回 not_headphone。
5. evidence_quote 必须是该标题中的连续原文，confidence 仅在品牌和型号都明确时才能达到 0.9。
6. 必须原样返回输入的 report_id，并为每个输入 item 返回且只返回一个结果。
只返回 JSON：
{"items":[{"report_id":"输入report_id","decision":"single_headphone|multi_headphone|not_headphone|needs_review","reason":"...","products":[{"brand":"标题中的品牌原文","model":"标题中的具体型号","category":"耳机类型","confidence":0,"evidence_quote":"标题连续原文"}]}]}"""


def model_config() -> tuple[str, str, str]:
    """Use the same deployment-safe model configuration as the other enrichers."""

    api_key = (
        os.environ.get("APP_TEXT_MODEL_TOKEN", "").strip()
        or os.environ.get("APP_TEXT_MODEL_TOKEN", "").strip()
    )
    api_url = (
        os.environ.get("APP_TEXT_MODEL_BASE_URL", "").strip()
        or os.environ.get("APP_TEXT_MODEL_BASE_URL", "").strip()
        or "https://api.deepseek.com/v1"
    )
    model = (
        os.environ.get("APP_TEXT_MODEL_NAME", "").strip()
        or os.environ.get("DEEPSEEK_MODEL", "").strip()
        or "deepseek-v4-flash"
    )
    if not api_key:
        raise RuntimeError("APP_TEXT_MODEL_TOKEN / APP_TEXT_MODEL_TOKEN not configured")
    return api_key, api_url.rstrip("/"), model


def article_text(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; 52audio-identity-repair/1.0)"}, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup.select("script, style, noscript, svg, nav, footer"):
        node.decompose()
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)[:MAX_ARTICLE_CHARS]


def is_candidate(record: dict) -> bool:
    title = str(record.get("title") or "")
    brand = normalize_brand(str(record.get("brand") or "")) or guess_brand_from_text(title)
    model = normalize_model(str(record.get("model") or ""), brand)
    if not model and title:
        model = normalize_model(title.replace(brand, "", 1) if brand else title, brand)
    return bool(
        not brand
        or len(model) > 48
        or identity_review_reason(brand, model, title)
        or any(marker in title.lower() for marker in ("对比", "汇总", "盘点", "top", "合集"))
    )


def deepseek_identity(
    api_key: str,
    api_url: str,
    model: str,
    record: dict,
    text: str,
    *,
    title_only: bool = False,
) -> dict:
    payload = {
        "source": {"url": record.get("url"), "title": record.get("title"), "existing_brand": record.get("brand"), "existing_model": record.get("model")},
        "source_title" if title_only else "article_text": text,
    }
    response = requests.post(
        f"{api_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "thinking": {"type": "disabled"}, "reasoning_effort": "low", "temperature": 0, "max_tokens": 4000,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": TITLE_PROMPT if title_only else PROMPT}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]},
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I))


def has_unresolved_brand(record: dict) -> bool:
    title = str(record.get("title") or "")
    brand = normalize_brand(str(record.get("brand") or "")) or guess_brand_from_text(title)
    return not brand


def deepseek_title_batch(
    api_key: str,
    api_url: str,
    model: str,
    records: list[dict],
) -> dict[str, dict]:
    payload = {
        "items": [
            {
                "report_id": str(record.get("id") or ""),
                "title": str(record.get("title") or ""),
                "existing_brand": str(record.get("brand") or ""),
                "existing_model": str(record.get("model") or ""),
            }
            for record in records
        ]
    }
    response = requests.post(
        f"{api_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "thinking": {"type": "disabled"},
            "reasoning_effort": "low",
            "temperature": 0,
            # The company gateway exposes this model as a reasoning model and
            # may ignore thinking=disabled. Reserve enough output headroom for
            # reasoning, while the caller keeps batches deliberately small.
            "max_tokens": max(4000, len(records) * 3000 + 1000),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": TITLE_BATCH_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        },
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I))
    if not isinstance(parsed.get("items"), list):
        raise ValueError("model response missing items list")
    return {
        str(item.get("report_id") or ""): item
        for item in parsed["items"]
        if isinstance(item, dict) and item.get("report_id")
    }


def _evidence_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()


def _identity_key(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", _evidence_text(value).casefold())


def _brand_supported(brand: str, quote: str) -> bool:
    quote_key = _identity_key(quote)
    aliases = next((items for display, items in BRAND_ALIASES if display == brand), [])
    return any(
        _identity_key(candidate) and _identity_key(candidate) in quote_key
        for candidate in {brand, *aliases}
    )


def validate(result: dict, source_text: str) -> dict:
    decision = str(result.get("decision") or "needs_review")
    if decision not in {"single_headphone", "multi_headphone", "not_headphone", "needs_review"}:
        decision = "needs_review"
    normalized_source = _evidence_text(source_text)
    products = []
    for item in result.get("products") or []:
        brand = normalize_brand(str(item.get("brand") or ""))
        model = normalize_model(str(item.get("model") or ""), brand)
        confidence = float(item.get("confidence") or 0)
        quote = str(item.get("evidence_quote") or "").strip()[:500]
        normalized_quote = _evidence_text(quote)
        if (
            not brand
            or not model
            or confidence < 0.9
            or not normalized_quote
            or normalized_quote not in normalized_source
            or not _brand_supported(brand, normalized_quote)
            or _identity_key(model) not in _identity_key(normalized_quote)
            or identity_review_reason(brand, model)
        ):
            continue
        products.append({"brand": brand, "model": model, "category": str(item.get("category") or ""), "confidence": confidence, "evidence_quote": quote})
    if decision == "single_headphone" and len(products) != 1:
        decision = "needs_review"
    if decision == "multi_headphone" and len(products) < 2:
        decision = "needs_review"
    if decision == "needs_review":
        products = []
    return {"decision": decision, "reason": str(result.get("reason") or "")[:500], "products": products}


def resolve_one(
    api_key: str,
    api_url: str,
    model: str,
    record: dict,
    *,
    title_only: bool,
) -> dict:
    base = {"report_id": str(record.get("id")), "source_url": record.get("url") or "", "title": record.get("title") or ""}
    try:
        text = base["title"] if title_only else article_text(base["source_url"])
        if not text:
            return {**base, "decision": "needs_review", "reason": "source had no extractable text", "products": [], "cacheable": True}
        resolved = validate(
            deepseek_identity(
                api_key,
                api_url,
                model,
                record,
                text,
                title_only=title_only,
            ),
            text,
        )
        return {
            **base,
            **resolved,
            "method": "title_only_llm_with_verbatim_validation" if title_only else "article_llm_with_verbatim_validation",
            "model": model,
            "cacheable": True,
        }
    except requests.RequestException as exc:
        return {**base, "decision": "needs_review", "reason": f"request failed: {exc.__class__.__name__}", "products": [], "cacheable": False}
    except Exception as exc:
        return {**base, "decision": "needs_review", "reason": f"identity extraction failed: {exc.__class__.__name__}", "products": [], "cacheable": False}


def resolve_title_batch(
    api_key: str,
    api_url: str,
    model: str,
    records: list[dict],
) -> list[dict]:
    bases = {
        str(record.get("id") or ""): {
            "report_id": str(record.get("id") or ""),
            "source_url": record.get("url") or "",
            "title": record.get("title") or "",
        }
        for record in records
    }
    try:
        raw_items = deepseek_title_batch(api_key, api_url, model, records)
    except requests.RequestException as exc:
        return [
            {
                **base,
                "decision": "needs_review",
                "reason": f"request failed: {exc.__class__.__name__}",
                "products": [],
                "cacheable": False,
            }
            for base in bases.values()
        ]
    except Exception as exc:
        return [
            {
                **base,
                "decision": "needs_review",
                "reason": f"identity extraction failed: {exc.__class__.__name__}",
                "products": [],
                "cacheable": False,
            }
            for base in bases.values()
        ]

    results = []
    for report_id, base in bases.items():
        raw = raw_items.get(report_id)
        if raw is None:
            results.append(
                {
                    **base,
                    "decision": "needs_review",
                    "reason": "model response omitted this report_id",
                    "products": [],
                    "cacheable": False,
                }
            )
            continue
        results.append(
            {
                **base,
                **validate(raw, base["title"]),
                "method": "title_only_llm_with_verbatim_validation",
                "model": model,
                "cacheable": True,
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve ambiguous 52audio report identities from original articles")
    parser.add_argument("--all", action="store_true", help="review every candidate, not just the initial limit")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=5, help="title-only 模式下每次模型请求包含的标题数")
    parser.add_argument("--write-overrides", action="store_true")
    parser.add_argument(
        "--title-only",
        action="store_true",
        help="只向模型发送来源标题；用于品牌/型号缺失的低成本增量兜底",
    )
    parser.add_argument(
        "--missing-brand-only",
        action="store_true",
        help="只处理经过现有别名规则后品牌仍为空的来源",
    )
    parser.add_argument(
        "--retry-needs-review",
        action="store_true",
        help="重新处理已缓存为 needs_review 的来源",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计候选，不调用模型")
    args = parser.parse_args()
    existing_path = identity_overrides_path()
    existing_items: dict[str, dict] = {}
    if existing_path.exists():
        try:
            existing_items = (json.loads(existing_path.read_text(encoding="utf-8")).get("items") or {})
        except (OSError, json.JSONDecodeError):
            existing_items = {}

    # Identity repair is intentionally incremental.  Accepted results and
    # source-grounded negative results are both cache entries; transient API
    # failures are not persisted and can be retried by the next scheduled run.
    cached_ids = {
        report_id
        for report_id, item in existing_items.items()
        if not args.retry_needs_review or item.get("decision") != "needs_review"
    }
    candidates = [
        record for record in load_all_records("report")
        if is_candidate(record)
        and (not args.missing_brand_only or has_unresolved_brand(record))
        and str(record.get("id") or "") not in cached_ids
    ]
    if not args.all:
        candidates = candidates[:max(1, args.limit)]
    if args.dry_run or not candidates:
        print(
            json.dumps(
                {
                    "candidates": len(candidates),
                    "existing_cached": len(existing_items),
                    "title_only": args.title_only,
                    "missing_brand_only": args.missing_brand_only,
                    "dry_run": args.dry_run,
                },
                ensure_ascii=False,
            )
        )
        return

    api_key, api_url, model = model_config()
    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 3))) as executor:
        if args.title_only:
            batch_size = max(1, min(args.batch_size, 5))
            batches = [candidates[index:index + batch_size] for index in range(0, len(candidates), batch_size)]
            futures = [
                executor.submit(resolve_title_batch, api_key, api_url, model, batch)
                for batch in batches
            ]
            for future in as_completed(futures):
                results.extend(future.result())
        else:
            futures = [
                executor.submit(
                    resolve_one,
                    api_key,
                    api_url,
                    model,
                    record,
                    title_only=False,
                )
                for record in candidates
            ]
            for future in as_completed(futures):
                results.append(future.result())
    results.sort(key=lambda item: item["report_id"])
    accepted = {item["report_id"]: item for item in results if item["decision"] in {"single_headphone", "multi_headphone", "not_headphone"}}
    review = [item for item in results if item["decision"] in {"needs_review", "not_headphone"}]
    cacheable = {
        item["report_id"]: {key: value for key, value in item.items() if key != "cacheable"}
        for item in results
        if item.get("cacheable")
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": "Raw articles remain unchanged; only source-verbatim, evidence-backed >=0.9 identities are consumed by the product builder. needs_review is a negative cache, not a product identity.",
        "items": {**existing_items, **cacheable},
    }
    out_dir = ROOT / "scratch_price_research"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "identity_repair_review.json").write_text(json.dumps({"summary": {"processed": len(results), "accepted": len(accepted), "needs_review": sum(item["decision"] == "needs_review" for item in results), "not_headphone": sum(item["decision"] == "not_headphone" for item in results)}, "items": review}, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_overrides:
        identity_overrides_path(for_write=True).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"processed": len(results), "accepted": len(accepted), "cached": len(cacheable), "transient_failures": sum(not item.get("cacheable") for item in results), "existing_skipped": len(existing_items), "needs_review": sum(item["decision"] == "needs_review" for item in results), "not_headphone": sum(item["decision"] == "not_headphone" for item in results), "model": model, "title_only": args.title_only, "missing_brand_only": args.missing_brand_only}, ensure_ascii=False))


if __name__ == "__main__":
    main()
