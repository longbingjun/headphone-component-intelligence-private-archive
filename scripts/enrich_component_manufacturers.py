#!/usr/bin/env python3
"""Use a text model only for component manufacturers unresolved by rules."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.bom_taxonomy import component_key  # noqa: E402
from core.component_analytics import (  # noqa: E402
    COMPONENT_LABELS,
    STRUCTURAL_COMPONENT_KEYS,
    component_evidence_supported,
    component_fact_key,
    manufacturer_from_evidence,
    validated_manufacturer_enrichment,
)
from core.paths import component_manufacturers_enrich_dir, products_dir  # noqa: E402


SYSTEM_PROMPT = """你是消费电子拆解报告的器件厂商字段抽取器。只处理输入 items 中当前器件的原文。
规则：
1. manufacturer 必须是 fact_text 中逐字连续出现的器件生产商名称；不能根据型号、常识或外部知识推断。
2. evidence_quote 必须逐字连续复制 fact_text，并且同时包含 manufacturer，以及能证明它属于当前器件的型号或器件名称。
3. 产品品牌、耳机制造商、文章作者、方案对比中提到的其他厂商都不是当前器件生产商。
4. 一段话出现多个候选厂商且无法确定当前器件对应关系时，manufacturer 和 evidence_quote 返回空字符串。
5. 原文没有披露时必须返回空字符串，不要猜测。
只返回 JSON：{"items":[{"key":"输入key","manufacturer":"原文厂商或空字符串","evidence_quote":"原文连续证据或空字符串"}]}"""

# Do not spend model calls on evidence that contains no plausible vendor
# wording.  The fallback is for long-tail phrasing, not external lookup from a
# bare model number.
_LLM_VENDOR_SIGNAL = re.compile(
    r"(?:生产(?:厂家|厂商|厂|商)|制造商|供应商|来自|源自|品牌|"
    r"科技|半导体|电子|微电子|集成电路|新能源|电芯|"
    r"Qualcomm|Realtek|Airoha|Bluetrum|Actions|BEKEN|WUQI|UNISOC|BES|JL)",
    re.IGNORECASE,
)


def _parse_json_text(content: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    return json.loads(text)


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _call_model(items: list[dict]) -> tuple[list[dict], str]:
    from openai import OpenAI

    api_key = os.environ.get("DEFAULT_MODEL_API_KEY", "").strip()
    api_url = os.environ.get("DEFAULT_MODEL_API_URL", "").strip()
    model = os.environ.get("DEFAULT_MODEL_NAME", "deepseek-v4-flash").strip() or "deepseek-v4-flash"
    if not api_key or not api_url:
        raise RuntimeError("DEFAULT_MODEL_API_KEY / DEFAULT_MODEL_API_URL not configured")
    client = OpenAI(api_key=api_key, base_url=api_url, timeout=120.0)
    response = client.chat.completions.create(
        model=model,
        temperature=0.0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
        ],
    )
    payload = _parse_json_text(response.choices[0].message.content or "{}")
    if not isinstance(payload.get("items"), list):
        raise ValueError("model response missing items list")
    return payload["items"], model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()

    output_dir = component_manufacturers_enrich_dir(for_write=True)
    existing: dict[str, dict[str, dict]] = {}
    for path in output_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        existing[path.stem] = {
            str(item.get("key") or ""): item
            for item in payload.get("items") or []
            if isinstance(item, dict) and item.get("key")
        }

    candidates: list[dict] = []
    for path in sorted(products_dir().glob("*.json")):
        if path.name == "index.json":
            continue
        product = json.loads(path.read_text(encoding="utf-8"))
        product_id = str(product.get("canonical_id") or path.stem)
        rows = product.get("technical_facts") or product.get("teardown_inventory") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = str(row.get("fact_text") or "").strip()
            key = component_key(row.get("component"), text)
            if key not in COMPONENT_LABELS or key in STRUCTURAL_COMPONENT_KEYS:
                continue
            if not component_evidence_supported(key, row.get("component"), text):
                continue
            manufacturer, _, _ = manufacturer_from_evidence(
                text,
                row.get("manufacturer") or row.get("brand"),
                component_key=key,
            )
            if manufacturer:
                continue
            if not _LLM_VENDOR_SIGNAL.search(text):
                continue
            fact_key = component_fact_key(row)
            fingerprint = _source_hash(text)
            cached = existing.get(product_id, {}).get(fact_key)
            if cached and cached.get("source_hash") == fingerprint and not args.force:
                continue
            candidates.append(
                {
                    "key": f"{product_id}:{fact_key}",
                    "product_id": product_id,
                    "fact_key": fact_key,
                    "source_hash": fingerprint,
                    "component_key": key,
                    "component": str(row.get("component") or ""),
                    "model": str(row.get("model") or ""),
                    "fact_text": text,
                }
            )
    if args.limit > 0:
        candidates = candidates[: args.limit]

    stats = {"candidates": len(candidates), "api_calls": 0, "accepted": 0, "not_disclosed": 0, "rejected": 0}
    if not args.use_llm:
        print(json.dumps(stats, ensure_ascii=False))
        return

    updates: dict[str, dict[str, dict]] = defaultdict(dict)
    model = ""
    for start in range(0, len(candidates), max(1, args.batch_size)):
        batch = candidates[start : start + max(1, args.batch_size)]
        response_items, model = _call_model(
            [
                {
                    "key": item["key"],
                    "component_type": COMPONENT_LABELS[item["component_key"]],
                    "component": item["component"],
                    "reported_model": item["model"],
                    "fact_text": item["fact_text"],
                }
                for item in batch
            ]
        )
        stats["api_calls"] += 1
        response_by_key = {
            str(item.get("key") or ""): item for item in response_items if isinstance(item, dict)
        }
        touched_products: set[str] = set()
        for source in batch:
            raw = response_by_key.get(source["key"], {})
            candidate = {
                "manufacturer": str(raw.get("manufacturer") or "").strip(),
                "evidence_quote": str(raw.get("evidence_quote") or "").strip(),
            }
            validated = validated_manufacturer_enrichment(source["fact_text"], candidate)
            if validated:
                manufacturer, _, quote = validated
                status = "accepted"
                stats["accepted"] += 1
            elif not candidate["manufacturer"] and not candidate["evidence_quote"]:
                manufacturer, quote, status = "", "", "not_disclosed"
                stats["not_disclosed"] += 1
            else:
                manufacturer, quote, status = "", "", "rejected_by_verbatim_validator"
                stats["rejected"] += 1
            updates[source["product_id"]][source["fact_key"]] = {
                "key": source["fact_key"],
                "source_hash": source["source_hash"],
                "manufacturer": manufacturer,
                "evidence_quote": quote,
                "status": status,
            }
            touched_products.add(source["product_id"])

        # Checkpoint every API batch so a slow company gateway or interrupted
        # terminal never discards already validated work.
        for product_id in touched_products:
            merged = dict(existing.get(product_id, {}))
            merged.update(updates.get(product_id, {}))
            payload = {
                "canonical_id": product_id,
                "items": list(merged.values()),
                "meta": {
                    "method": "rules_first_llm_fallback_with_verbatim_validation",
                    "model": model,
                    "validator": "component-manufacturer-verbatim-v1",
                },
            }
            (output_dir / f"{product_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            existing[product_id] = merged
        print(
            f"[progress] {min(start + len(batch), len(candidates))}/{len(candidates)} "
            f"accepted={stats['accepted']} not_disclosed={stats['not_disclosed']} rejected={stats['rejected']}",
            file=sys.stderr,
            flush=True,
        )

    for product_id, rows in updates.items():
        merged = dict(existing.get(product_id, {}))
        merged.update(rows)
        payload = {
            "canonical_id": product_id,
            "items": list(merged.values()),
            "meta": {
                "method": "rules_first_llm_fallback_with_verbatim_validation",
                "model": model,
                "validator": "component-manufacturer-verbatim-v1",
            },
        }
        (output_dir / f"{product_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
