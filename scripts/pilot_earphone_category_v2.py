#!/usr/bin/env python3
"""Run an auditable product-level pilot for the earphone category-v2 taxonomy.

The pilot is deliberately isolated from the production data pipeline. It reads
the existing product/report corpus and cached 52audio HTML, calls the configured
LLM in small batches, and writes review artifacts outside the Git worktree by
default. It never updates ``products.category`` or curated product JSON.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = ROOT.parent / "secrets" / ".env.production"
DEFAULT_OUTPUT_ROOT = ROOT.parent / "pilot-results" / "earphone-category-v2"

TARGET_LEGACY_CATEGORIES = {"开放式耳机", "真无线耳机TWS"}
ALLOWED_LABELS = {
    "ear_clip": "耳夹式耳机",
    "ear_hook": "耳挂式耳机",
    "full_in_ear": "全入耳式耳机",
    "semi_in_ear": "半入耳式耳机",
    "headphone": "头戴式耳机",
    "neckband": "颈挂式蓝牙耳机",
    "wired": "有线耳机",
    "bone_conduction": "骨传导耳机",
    "needs_review": "证据不足/待复核",
}
PROMPT_VERSION = "earphone-category-v2-pilot-2026-09-09-v2"

LABEL_PATTERNS = {
    "ear_clip": re.compile(r"耳夹式|耳夹耳机|耳夹设计|夹耳式|夹耳耳机|夹耳设计|夹持(?:在)?耳廓", re.I),
    "ear_hook": re.compile(r"耳挂式|挂耳式|耳挂耳机|挂耳耳机|耳挂设计|挂耳设计|挂在耳廓|绕(?:在)?耳廓|耳钩", re.I),
    "semi_in_ear": re.compile(r"半入耳式|半入耳", re.I),
    "full_in_ear": re.compile(r"全入耳式|全入耳|(?<!半)入耳式|豆(?:状|式)入耳设计|柄(?:状|式)入耳设计", re.I),
    "headphone": re.compile(r"头戴式.{0,8}耳机|头戴耳机|头戴式设计", re.I),
    "neckband": re.compile(r"颈挂式.{0,8}耳机|颈挂耳机|脖挂.{0,8}耳机|项圈(?:式)?.{0,8}耳机", re.I),
    "wired": re.compile(
        r"有线耳机|Lightning(?:接口)?耳机|USB[- ]?C(?:接口)?耳机|"
        r"(?:产品名称|佩戴方式).{0,100}(?:Lightning|USB[- ]?C)接口|"
        r"(?:Lightning|USB[- ]?C)接口.{0,30}(?:主动降噪|入耳式)?耳机|"
        r"耳机.{0,40}(?:Lightning|USB[- ]?C)接口直接供电",
        re.I,
    ),
    "bone_conduction": re.compile(r"骨传导(?:蓝牙)?耳机|骨传导设计", re.I),
}
FIXED_FORM_LABELS = {"headphone", "neckband", "wired", "bone_conduction"}
SIGNAL_PATTERN = re.compile(
    r"耳夹|夹耳|耳挂|挂耳|入耳|佩戴|耳机外观|整体外观|硅胶耳塞|耳塞套|C形桥|C桥|头戴|颈挂|项圈|有线|Lightning|USB[- ]?C|骨传导",
    re.I,
)
STRONG_STRUCTURAL_SUPPORT_PATTERNS = {
    "full_in_ear": re.compile(
        r"(?:随机标配|额外附赠|三种(?:不同)?尺寸|S[/／]M[/／]L).{0,30}硅胶(?:防滑)?耳塞|"
        r"硅胶(?:防滑)?耳塞.{0,40}(?:贴合|进入).{0,12}耳道",
        re.I,
    )
}

SYSTEM_PROMPT = """你是消费电子拆解报告的产品形态分类器。输入包含多个产品，每个产品都给出目标产品身份和若干原文片段。

任务：只判断“目标产品”本身，不得把同段中列举、对比或回顾的其他产品当成目标产品。每个输入必须返回且只返回一条结果。

category_v2 只能取：
- ear_clip：耳夹式/夹耳式，通过夹持耳廓或 C 形桥佩戴；
- ear_hook：耳挂式/挂耳式，通过耳挂绕在耳廓上佩戴；
- full_in_ear：全入耳式/入耳式，耳机主体或带耳塞套部分进入耳道；
- semi_in_ear：半入耳式，主要停留在耳甲腔或耳道入口；
- headphone：目标产品实际是头戴式耳机；
- neckband：目标产品实际是颈挂式/项圈式蓝牙耳机；
- wired：目标产品实际是有线耳机；
- bone_conduction：目标产品实际是骨传导耳机；
- needs_review：原文没有足够证据、描述冲突，或者只能确认 TWS/开放式而不能确认上述形态。

可靠性规则：
1. 不得根据品牌常识、型号记忆或“真无线/TWS/开放式”标签猜测具体形态。
2. evidence_quote 必须逐字来自输入片段中的一个连续原文；无直接证据就返回 needs_review。
3. evidence_segment_id 必须对应输入中的 segment_id。
4. 若目标产品明显属于头戴式、颈挂式、有线或骨传导，使用对应既有分类，以纠正旧分类污染。
5. 对复合结构按主要佩戴固定方式分类：耳夹优先于耳挂，耳挂优先于是否入耳。例如“耳挂式入耳耳机”归为 ear_hook。
6. “由 A 改为 B/变为 B”描述的是当前产品时，以变化后的 B 为准；若同一报告混有多款产品且无法锁定目标产品，返回 needs_review。
7. 仅出现硅胶耳塞、耳翼或产品外观图片，而没有能锁定目标产品形态的文字，不足以直接判定。
8. rationale 只写一句简短判断理由，不要输出思维过程。
9. 原样返回 product_id。

只返回 JSON：
{"items":[{"product_id":"输入ID","category_v2":"ear_clip|ear_hook|full_in_ear|semi_in_ear|headphone|neckband|wired|bone_conduction|needs_review","evidence_quote":"输入中的连续原文或空字符串","evidence_segment_id":"片段ID或空字符串","rationale":"一句话理由"}]}"""

EVIDENCE_REPAIR_PROMPT = SYSTEM_PROMPT + """

这是证据格式修复轮。先前输出的 evidence_quote 不是输入中的逐字连续原文。请重新判断，并特别确保：
- 只能复制一个输入片段中真实存在的连续文字，不得省略、改写或润色；
- evidence_segment_id 与该原文所在片段完全一致；
- 找不到逐字证据就返回 needs_review。"""


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE pairs without overriding an injected environment."""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def model_config(env_file: Path) -> tuple[str, str, str]:
    load_env_file(env_file)
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
        raise RuntimeError("APP_TEXT_MODEL_TOKEN / APP_TEXT_MODEL_TOKEN is not configured")
    return api_key, api_url.rstrip("/"), model


def chat_endpoint(api_url: str) -> str:
    return api_url if api_url.endswith("/chat/completions") else f"{api_url}/chat/completions"


def load_reports() -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for path in sorted((ROOT / "data" / "reports").glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8-sig"))
        reports[str(item.get("id") or path.stem)] = item
    return reports


def load_products() -> list[dict[str, Any]]:
    products_dir = ROOT / "data" / "curated" / "products"
    products: list[dict[str, Any]] = []
    for path in sorted(products_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        products.append(json.loads(path.read_text(encoding="utf-8-sig")))
    return products


def legacy_category_of(product: dict[str, Any]) -> str:
    """Keep reruns stable after curated products expose the fine category."""

    return clean_text(product.get("category_raw") or product.get("category"))


def mojibake_score(value: str) -> int:
    markers = ("Ã", "Â", "â", "ð", "�", "\x80", "\x81", "\x82", "\x83", "\x84", "\x85", "\x86", "\x87", "\x88", "\x89", "\x8a", "\x8b", "\x8c", "\x8d", "\x8e", "\x8f", "\x90", "\x91", "\x92", "\x93", "\x94", "\x95", "\x96", "\x97", "\x98", "\x99", "\x9a", "\x9b", "\x9c", "\x9d", "\x9e", "\x9f")
    return sum(value.count(marker) for marker in markers)


def repair_mojibake(value: str) -> str:
    """Repair the common UTF-8-as-Latin-1 cache failure only when quality improves."""

    original = clean_text(value)
    if not original or mojibake_score(original) == 0:
        return original
    for encoding in ("latin1", "cp1252"):
        try:
            candidate = clean_text(original.encode(encoding).decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if mojibake_score(candidate) < mojibake_score(original):
            return candidate
    return original


def html_paragraphs(path: Path) -> list[str]:
    """Extract ordered article text blocks, including leaf ``div`` containers."""

    decoded = path.read_bytes().decode("utf-8-sig", errors="replace")
    soup = BeautifulSoup(decoded, "html.parser")
    root = None
    for selector in (".nc-light-gallery", ".entry-content", ".post-content", "article", "main"):
        root = soup.select_one(selector)
        if root is not None:
            break
    root = root or soup
    block_names = {"p", "h1", "h2", "h3", "li"}
    nodes = []
    for node in root.find_all([*sorted(block_names), "div"]):
        if node.name == "div" and (node.find(list(block_names)) or node.find("div", recursive=False)):
            continue
        nodes.append(node)
    paragraphs: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        text = repair_mojibake(node.get_text(" ", strip=True))
        if len(text) < 8 or text in seen:
            continue
        seen.add(text)
        paragraphs.append(text[:1200])
    return paragraphs


def compact_identity(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", clean_text(value).lower())


def target_match_score(text: str, product: dict[str, Any]) -> int:
    compact_text = compact_identity(text)
    model = compact_identity(product.get("model"))
    brand = compact_identity(product.get("brand"))
    score = 0
    if len(model) >= 3 and model in compact_text:
        score += 18
    if len(brand) >= 2 and brand in compact_text:
        score += 3
    generic_tokens = {"buds", "earbuds", "earphone", "headphone", "tws", "pro", "plus", "max", "air"}
    model_tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9+._-]{1,}", clean_text(product.get("model")))
        if len(token) >= 3 and token.lower() not in generic_tokens
    }
    score += min(8, sum(2 for token in model_tokens if token in text.lower()))
    return score


def source_segments(path: Path, product: dict[str, Any], report: dict[str, Any]) -> list[dict[str, str]]:
    paragraphs = html_paragraphs(path)
    selected_indices = set(range(min(4, len(paragraphs))))
    ranked: list[tuple[int, int]] = []
    for index, text in enumerate(paragraphs):
        identity_score = target_match_score(text, product)
        signal_score = 7 if SIGNAL_PATTERN.search(text) else 0
        title_score = 2 if compact_identity(report.get("title")) and compact_identity(report.get("title")) in compact_identity(text) else 0
        total = identity_score + signal_score + title_score
        if total > 0:
            ranked.append((total, index))
    for _, index in sorted(ranked, key=lambda item: (-item[0], item[1]))[:12]:
        selected_indices.add(index)

    segments: list[dict[str, str]] = []
    for index in sorted(selected_indices):
        text = paragraphs[index]
        if index < 4:
            prefix = "intro"
        elif target_match_score(text, product) > 0:
            prefix = "target"
        else:
            prefix = "form"
        segments.append(
            {
                "segment_id": f"{prefix}_{index + 1}",
                "text": text,
                "target_match": "true" if target_match_score(text, product) >= 10 else "false",
            }
        )
    return segments


def exact_sentences(text: str) -> list[str]:
    return [clean_text(item) for item in re.split(r"(?<=[。！？；])", text) if clean_text(item)]


def rule_hint(segments: Iterable[dict[str, str]]) -> tuple[str, str, list[str]]:
    """Return a high-precision hint only; it is never written as a final category."""

    segment_list = list(segments)
    target_segments = [segment for segment in segment_list if segment.get("target_match") == "true"]
    evidence_segments = target_segments or segment_list
    evidence: dict[str, list[str]] = defaultdict(list)
    for segment in evidence_segments:
        for sentence in exact_sentences(segment.get("text", "")):
            for label in explicit_labels(sentence):
                evidence[label].append(sentence)
    labels = sorted(evidence)
    if len(labels) == 1:
        label = labels[0]
        return label, evidence[label][0], labels
    if len(labels) > 1:
        return "conflict", "", labels
    return "unclear", "", []


def choose_report(product: dict[str, Any], reports: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, Path | None]:
    candidates: list[tuple[str, dict[str, Any], Path]] = []
    for report_id in product.get("report_ids") or []:
        report = reports.get(str(report_id))
        cache_path = ROOT / "data" / "cache" / "content_html" / f"{report_id}.html"
        if report and cache_path.exists():
            candidates.append((str(report.get("published_at") or ""), report, cache_path))
    if not candidates:
        return None, None
    _, report, cache_path = sorted(candidates, key=lambda item: item[0], reverse=True)[0]
    return report, cache_path


def build_candidates() -> tuple[list[dict[str, Any]], dict[str, int]]:
    reports = load_reports()
    products = load_products()
    candidates: list[dict[str, Any]] = []
    profile = Counter()
    for product in products:
        legacy_category = legacy_category_of(product)
        if legacy_category not in TARGET_LEGACY_CATEGORIES:
            continue
        profile["target_products"] += 1
        report, cache_path = choose_report(product, reports)
        if not report or not cache_path:
            profile["missing_cached_report"] += 1
            continue
        segments = source_segments(cache_path, product, report)
        if not segments:
            profile["empty_segments"] += 1
            continue
        hint, hint_quote, signals = rule_hint(segments)
        candidates.append(
            {
                "product_id": clean_text(product.get("canonical_id")),
                "brand": clean_text(product.get("brand")),
                "model": clean_text(product.get("model")),
                "legacy_category": legacy_category,
                "report_id": str(report.get("id") or ""),
                "report_title": clean_text(report.get("title")),
                "published_at": str(report.get("published_at") or ""),
                "source_url": clean_text(report.get("url")),
                "segments": segments,
                "rule_hint": hint,
                "rule_evidence_quote": hint_quote,
                "rule_signals": signals,
            }
        )
    profile["eligible_products"] = len(candidates)
    return candidates, dict(profile)


def evenly_spaced(items: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0 or not items:
        return []
    ordered = sorted(items, key=lambda item: (item.get("published_at", ""), item["product_id"]))
    if count >= len(ordered):
        return ordered
    if count == 1:
        return [ordered[len(ordered) // 2]]
    indices = [round(i * (len(ordered) - 1) / (count - 1)) for i in range(count)]
    return [ordered[index] for index in indices]


def select_sample(candidates: list[dict[str, Any]], sample_size: int) -> list[dict[str, Any]]:
    """Balance explicit form signals with intentionally difficult records."""

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        groups[item["rule_hint"]].append(item)
    target_groups = [*LABEL_PATTERNS, "conflict", "unclear"]
    quota = max(1, sample_size // len(target_groups))
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for group in target_groups:
        for item in evenly_spaced(groups.get(group, []), quota):
            if item["product_id"] not in selected_ids:
                selected.append(item)
                selected_ids.add(item["product_id"])
    if len(selected) < sample_size:
        remainder = [item for item in candidates if item["product_id"] not in selected_ids]
        # Prefer ambiguous records for the fill, then spread across the full time range.
        remainder.sort(key=lambda item: (item["rule_hint"] not in {"unclear", "conflict"}, item.get("published_at", ""), item["product_id"]))
        for item in evenly_spaced(remainder, sample_size - len(selected)):
            if item["product_id"] not in selected_ids:
                selected.append(item)
                selected_ids.add(item["product_id"])
    return sorted(selected[:sample_size], key=lambda item: (item["rule_hint"], item.get("published_at", ""), item["product_id"]))


def select_sample_from_manifest(candidates: list[dict[str, Any]], manifest_path: Path) -> list[dict[str, Any]]:
    """Rebuild the exact same product sample with the latest evidence logic."""

    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    product_ids = [clean_text(item.get("product_id")) for item in payload.get("items") or []]
    product_ids = [product_id for product_id in product_ids if product_id]
    candidate_by_id = {item["product_id"]: item for item in candidates}
    missing = [product_id for product_id in product_ids if product_id not in candidate_by_id]
    if missing:
        raise RuntimeError(f"Manifest contains {len(missing)} unavailable products: {', '.join(missing[:5])}")
    return [candidate_by_id[product_id] for product_id in product_ids]


def llm_batch(
    api_key: str,
    api_url: str,
    model: str,
    items: list[dict[str, Any]],
    *,
    attempts: int = 3,
    system_prompt: str = SYSTEM_PROMPT,
) -> dict[str, Any]:
    user_items = [
        {
            "product_id": item["product_id"],
            "target_product": {
                "brand": item["brand"],
                "model": item["model"],
                "legacy_category": item["legacy_category"],
                "report_title": item["report_title"],
            },
            "segments": item["segments"],
        }
        for item in items
    ]
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(
                chat_endpoint(api_url),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "thinking": {"type": "disabled"},
                    "reasoning_effort": "low",
                    "temperature": 0,
                    "max_tokens": max(5000, len(items) * 1000),
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps({"items": user_items}, ensure_ascii=False)},
                    ],
                },
                timeout=180,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"].get("content") or ""
            parsed = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I))
            return {"parsed": parsed, "usage": payload.get("usage") or {}}
        except (requests.RequestException, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"LLM batch failed after {attempts} attempts: {last_error}")


def normalize_for_match(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def explicit_labels(value: str) -> list[str]:
    text = value or ""
    transition = re.search(r"(?:由|从).{0,40}?(?:改为|变为|升级为|转为|更换为)(?P<final>.{1,60})", text, re.I)
    if transition:
        final_labels = [label for label, pattern in LABEL_PATTERNS.items() if pattern.search(transition.group("final"))]
        if final_labels:
            return prioritized_labels(final_labels)
    contrast = re.search(r"(?:但|但是|实际(?:上)?|佩戴时)(?P<final>.{1,60})", text, re.I)
    if contrast:
        final_labels = [label for label, pattern in LABEL_PATTERNS.items() if pattern.search(contrast.group("final"))]
        if final_labels:
            return prioritized_labels(final_labels)
    labels = [label for label, pattern in LABEL_PATTERNS.items() if pattern.search(text)]
    return prioritized_labels(labels)


def prioritized_labels(labels: Iterable[str]) -> list[str]:
    unique = set(labels)
    fixed = sorted(unique & FIXED_FORM_LABELS)
    if fixed:
        return fixed
    if "ear_clip" in unique:
        return ["ear_clip"]
    if "ear_hook" in unique:
        return ["ear_hook"]
    return sorted(unique)


def structural_support(segments: Iterable[dict[str, str]]) -> tuple[str, str, str]:
    """Return a conservative, exact-quote inference for strong physical evidence."""

    segment_list = list(segments)
    direct_target_labels = {
        label
        for segment in segment_list
        if segment.get("target_match") == "true"
        for sentence in exact_sentences(segment.get("text", ""))
        for label in explicit_labels(sentence)
    }
    if direct_target_labels and direct_target_labels != {"full_in_ear"}:
        return "", "", ""
    matches: list[tuple[str, str, str]] = []
    for segment in segment_list:
        for sentence in exact_sentences(segment.get("text", "")):
            for label, pattern in STRONG_STRUCTURAL_SUPPORT_PATTERNS.items():
                if pattern.search(sentence):
                    matches.append((label, sentence, segment.get("segment_id", "")))
    labels = {label for label, _, _ in matches}
    if len(labels) != 1 or not matches:
        return "", "", ""
    return matches[0]


def validate_result(source: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    result = result or {}
    category = clean_text(result.get("category_v2"))
    quote = clean_text(result.get("evidence_quote"))
    segment_id = clean_text(result.get("evidence_segment_id"))
    rationale = clean_text(result.get("rationale"))
    segment_map = {item["segment_id"]: item["text"] for item in source["segments"]}
    all_text = " ".join(segment_map.values())
    evidence_exact = bool(quote and normalize_for_match(quote) in normalize_for_match(all_text))
    matching_segment_ids = [
        candidate_id
        for candidate_id, candidate_text in segment_map.items()
        if quote and normalize_for_match(quote) in normalize_for_match(candidate_text)
    ]
    submitted_segment_exact = bool(
        segment_id in segment_map
        and quote
        and normalize_for_match(quote) in normalize_for_match(segment_map[segment_id])
    )
    resolved_segment_id = segment_id if submitted_segment_exact else (matching_segment_ids[0] if len(matching_segment_ids) == 1 else "")
    segment_exact = bool(resolved_segment_id)
    quote_labels = explicit_labels(quote)
    allowed = category in ALLOWED_LABELS
    explicit_match = category in quote_labels
    conflicting_quote = bool(category != "needs_review" and any(label != category for label in quote_labels))
    support_label, support_quote, support_segment_id = structural_support(source["segments"])
    resolved_category = category if allowed else "invalid_output"
    classification_basis = "llm_direct_evidence"
    classification_confidence = "high"
    if category == "needs_review" and allowed:
        review_status = "needs_review"
    elif allowed and evidence_exact and segment_exact and explicit_match and not conflicting_quote:
        review_status = "high_confidence_candidate"
    else:
        review_status = "needs_review"
    if support_label and category in {"needs_review", support_label} and review_status != "high_confidence_candidate":
        resolved_category = support_label
        classification_basis = "deterministic_structural_support"
        classification_confidence = "medium"
        review_status = "medium_confidence_candidate"
        quote = support_quote
        resolved_segment_id = support_segment_id
        evidence_exact = True
        segment_exact = True
    elif review_status == "needs_review":
        classification_basis = "insufficient_or_conflicting_evidence"
        classification_confidence = "review"
    return {
        **source,
        "llm_category_v2": category if allowed else "invalid_output",
        "llm_category_label": ALLOWED_LABELS.get(category, "非法输出"),
        "resolved_category_v2": resolved_category,
        "resolved_category_label": ALLOWED_LABELS.get(resolved_category, "非法输出"),
        "classification_basis": classification_basis,
        "classification_confidence": classification_confidence,
        "llm_evidence_quote": quote,
        "llm_evidence_segment_id": segment_id,
        "resolved_evidence_segment_id": resolved_segment_id,
        "segment_id_corrected": bool(segment_exact and not submitted_segment_exact),
        "llm_rationale": rationale,
        "allowed_label": allowed,
        "evidence_exact": evidence_exact,
        "segment_exact": segment_exact,
        "quote_explicit_labels": quote_labels,
        "rule_llm_agree": source["rule_hint"] == category if source["rule_hint"] in LABEL_PATTERNS else None,
        "pilot_review_status": review_status,
    }


def batches(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def parsed_items_by_id(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index valid item-shaped outputs and ignore unrelated model prose."""

    indexed: dict[str, dict[str, Any]] = {}
    for item in (response.get("parsed") or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        product_id = clean_text(item.get("product_id"))
        if product_id:
            indexed[product_id] = item
    return indexed


def add_usage(target: Counter, response: dict[str, Any]) -> None:
    for key, value in (response.get("usage") or {}).items():
        if isinstance(value, (int, float)):
            target[key] += value


def safe_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_review_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "product_id",
        "brand",
        "model",
        "legacy_category",
        "report_id",
        "published_at",
        "rule_hint",
        "llm_category_v2",
        "llm_category_label",
        "resolved_category_v2",
        "resolved_category_label",
        "classification_basis",
        "classification_confidence",
        "llm_evidence_quote",
        "evidence_exact",
        "segment_exact",
        "rule_llm_agree",
        "pilot_review_status",
        "source_url",
        "reviewer_label",
        "reviewer_notes",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for item in results:
            writer.writerow({**item, "reviewer_label": "", "reviewer_notes": ""})


def metric_summary(results: list[dict[str, Any]], profile: dict[str, int], usage: Counter) -> dict[str, Any]:
    explicit_results = [item for item in results if item["rule_hint"] in LABEL_PATTERNS]
    agreements = [item for item in explicit_results if item["rule_llm_agree"] is True]
    assigned_results = [item for item in results if item["resolved_category_v2"] not in {"needs_review", "invalid_output"}]
    ready_statuses = {"high_confidence_candidate", "medium_confidence_candidate"}
    return {
        "corpus_profile": profile,
        "sample_products": len(results),
        "llm_category_distribution": dict(Counter(item["llm_category_v2"] for item in results)),
        "resolved_category_distribution": dict(Counter(item["resolved_category_v2"] for item in results)),
        "rule_hint_distribution": dict(Counter(item["rule_hint"] for item in results)),
        "allowed_label_rate": round(sum(bool(item["allowed_label"]) for item in results) / max(1, len(results)), 4),
        "evidence_exact_rate": round(sum(bool(item["evidence_exact"]) for item in results) / max(1, len(results)), 4),
        "segment_exact_rate": round(sum(bool(item["segment_exact"]) for item in results) / max(1, len(results)), 4),
        "high_confidence_candidate_rate": round(sum(item["pilot_review_status"] == "high_confidence_candidate" for item in results) / max(1, len(results)), 4),
        "assigned_products": len(assigned_results),
        "abstained_products": sum(item["resolved_category_v2"] == "needs_review" for item in results),
        "assigned_evidence_exact_rate": round(sum(bool(item["evidence_exact"]) for item in assigned_results) / max(1, len(assigned_results)), 4),
        "assigned_segment_exact_rate": round(sum(bool(item["segment_exact"]) for item in assigned_results) / max(1, len(assigned_results)), 4),
        "assigned_high_confidence_rate": round(sum(item["pilot_review_status"] == "high_confidence_candidate" for item in assigned_results) / max(1, len(assigned_results)), 4),
        "medium_confidence_candidates": sum(item["pilot_review_status"] == "medium_confidence_candidate" for item in results),
        "ready_candidate_rate": round(sum(item["pilot_review_status"] in ready_statuses for item in results) / max(1, len(results)), 4),
        "explicit_rule_llm_agreement_rate": round(len(agreements) / max(1, len(explicit_results)), 4),
        "explicit_rule_comparison_count": len(explicit_results),
        "needs_manual_review": sum(item["pilot_review_status"] not in ready_statuses for item in results),
        "usage": dict(usage),
        "important_note": "This is an evidence/consistency pilot, not a ground-truth accuracy score. Fill reviewer_label before calculating accuracy.",
    }


def write_report(path: Path, run_meta: dict[str, Any], metrics: dict[str, Any], results: list[dict[str, Any]]) -> None:
    distribution = metrics.get("resolved_category_distribution") or {}
    lines = [
        "# 耳机细分类 v2 小样本验证",
        "",
        f"- 运行时间：{run_meta['generated_at']}",
        f"- 模型：{run_meta['model']}",
        f"- Prompt 版本：{run_meta['prompt_version']}",
        f"- 样本粒度：唯一产品，共 {metrics['sample_products']} 款",
        "- 安全边界：本次结果未回写正式产品分类，也未重建正式页面",
        "",
        "## 自动质量检查",
        "",
        f"- 合法分类输出率：{metrics['allowed_label_rate']:.1%}",
        f"- 模型给出明确分类：{metrics['assigned_products']} 款；主动放弃判断：{metrics['abstained_products']} 款",
        f"- 已分类结果的证据原文可回查率：{metrics['assigned_evidence_exact_rate']:.1%}",
        f"- 已分类结果的证据片段定位准确率：{metrics['assigned_segment_exact_rate']:.1%}",
        f"- 已分类结果中可作为高置信候选：{metrics['assigned_high_confidence_rate']:.1%}",
        f"- 结构证据推断的中置信候选：{metrics['medium_confidence_candidates']} 款",
        f"- 高/中置信候选覆盖率：{metrics['ready_candidate_rate']:.1%}",
        f"- 明确规则信号与模型一致率：{metrics['explicit_rule_llm_agreement_rate']:.1%}（{metrics['explicit_rule_comparison_count']} 款）",
        f"- 仍需人工复核：{metrics['needs_manual_review']} 款",
        "",
        "## 模型分类分布",
        "",
    ]
    for key, label in ALLOWED_LABELS.items():
        lines.append(f"- {label}：{distribution.get(key, 0)} 款")
    medium_results = [item for item in results if item["pilot_review_status"] == "medium_confidence_candidate"]
    review_results = [item for item in results if item["pilot_review_status"] == "needs_review"]
    lines.extend(["", "## 中置信结构推断", ""])
    if medium_results:
        for item in medium_results:
            identity = " ".join(filter(None, [item.get("brand", ""), item.get("model", "")]))
            lines.append(
                f"- `{item['product_id']}`（{identity}）：{item['resolved_category_label']}；"
                f"证据：{item['llm_evidence_quote']}"
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## 证据不足 / 待复核产品", ""])
    if review_results:
        for item in review_results:
            identity = " ".join(filter(None, [item.get("brand", ""), item.get("model", "")]))
            reason = item.get("llm_rationale") or "模型未给出可核验的直接证据。"
            lines.append(f"- `{item['product_id']}`（{identity}）：{reason}")
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 如何解读",
            "",
            "上述结果只能证明输出格式、原文证据回查和规则一致性，不能替代人工标注准确率。",
            "请在 manual_review.csv 中填写 reviewer_label；只有完成分层人工复核后，才能决定是否进行全量历史回填。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> Path:
    candidates, profile = build_candidates()
    if args.sample_manifest:
        sample = select_sample_from_manifest(candidates, Path(args.sample_manifest))
    else:
        sample = select_sample(candidates, args.sample_size)
    if not args.sample_manifest and len(sample) < args.sample_size:
        raise RuntimeError(f"Only {len(sample)} eligible products are available for a requested sample of {args.sample_size}")
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir).resolve() / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_json(output_dir / "sample_manifest.json", {"profile": profile, "items": sample})
    if args.dry_run:
        write_review_csv(output_dir / "manual_review.csv", [validate_result(item, None) for item in sample])
        return output_dir

    api_key, api_url, model = model_config(Path(args.env_file))
    raw_dir = output_dir / "raw_batches"
    raw_dir.mkdir(parents=True, exist_ok=True)
    llm_by_id: dict[str, dict[str, Any]] = {}
    usage = Counter()
    for batch_index, batch_items in enumerate(batches(sample, args.batch_size), start=1):
        raw_path = raw_dir / f"batch_{batch_index:03d}.json"
        if raw_path.exists() and args.resume:
            response = json.loads(raw_path.read_text(encoding="utf-8"))
        else:
            response = llm_batch(api_key, api_url, model, batch_items)
            safe_json(raw_path, response)
        batch_results = parsed_items_by_id(response)
        add_usage(usage, response)

        expected_ids = {item["product_id"] for item in batch_items}
        missing_ids = sorted(expected_ids - set(batch_results))
        for retry_index, product_id in enumerate(missing_ids, start=1):
            source_item = next(item for item in batch_items if item["product_id"] == product_id)
            retry_path = raw_dir / f"batch_{batch_index:03d}_retry_{retry_index:02d}.json"
            if retry_path.exists() and args.resume:
                retry_response = json.loads(retry_path.read_text(encoding="utf-8"))
            else:
                retry_response = llm_batch(api_key, api_url, model, [source_item])
                safe_json(retry_path, retry_response)
            batch_results.update(parsed_items_by_id(retry_response))
            add_usage(usage, retry_response)

        llm_by_id.update({key: value for key, value in batch_results.items() if key in expected_ids})
        unresolved = sorted(expected_ids - set(batch_results))
        suffix = f"; unresolved={len(unresolved)}" if unresolved else ""
        print(
            f"[{batch_index}/{(len(sample) + args.batch_size - 1) // args.batch_size}] "
            f"received {len(batch_results)}/{len(batch_items)} products{suffix}",
            flush=True,
        )

    preliminary_results = [validate_result(item, llm_by_id.get(item["product_id"])) for item in sample]
    repair_sources = [
        source
        for source, result in zip(sample, preliminary_results)
        if result["llm_category_v2"] not in {"needs_review", "invalid_output"} and not result["evidence_exact"]
    ]
    for repair_index, repair_items in enumerate(batches(repair_sources, args.batch_size), start=1):
        repair_path = raw_dir / f"validation_repair_{repair_index:03d}.json"
        if repair_path.exists() and args.resume:
            repair_response = json.loads(repair_path.read_text(encoding="utf-8"))
        else:
            repair_response = llm_batch(
                api_key,
                api_url,
                model,
                repair_items,
                system_prompt=EVIDENCE_REPAIR_PROMPT,
            )
            safe_json(repair_path, repair_response)
        llm_by_id.update(parsed_items_by_id(repair_response))
        add_usage(usage, repair_response)
        print(f"[evidence repair {repair_index}] checked {len(repair_items)} products", flush=True)

    results = [validate_result(item, llm_by_id.get(item["product_id"])) for item in sample]
    metrics = metric_summary(results, profile, usage)
    run_meta = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "model": model,
        "api_host": requests.utils.urlparse(api_url).hostname,
        "prompt_version": PROMPT_VERSION,
        "sample_size": len(sample),
        "batch_size": args.batch_size,
        "source_root": str(ROOT),
        "sample_manifest_source": str(Path(args.sample_manifest).resolve()) if args.sample_manifest else "",
        "writes_production_data": False,
    }
    safe_json(output_dir / "results.json", {"run": run_meta, "metrics": metrics, "items": results})
    safe_json(output_dir / "metrics.json", metrics)
    write_review_csv(output_dir / "manual_review.csv", results)
    write_report(output_dir / "report.md", run_meta, metrics, results)
    return output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--sample-manifest", default="", help="reuse product IDs from a previous sample manifest")
    parser.add_argument("--dry-run", action="store_true", help="build the sample only; do not call the LLM")
    parser.add_argument("--resume", action="store_true", help="reuse existing raw batch JSON in the same run directory")
    return parser.parse_args()


if __name__ == "__main__":
    output = run(parse_args())
    print(f"Pilot artifacts: {output}")
