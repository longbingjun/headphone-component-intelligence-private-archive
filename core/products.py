"""产品实体层：品牌/型号归一化与 canonical ID 生成。"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from core.catalog_overrides import combined_brand_aliases
from core.cost_extract import compute_cost_completeness, extract_cost_fields, pick_best_report
from core.bom_taxonomy import component_key
from core.extract.bom_parameters import bom_row_key
from core.extract.consumer_claims import deterministic_candidates, extract_consumer_source, validate_consumer_claims
from core.paths import (
    bom_parameters_enrich_dir,
    channel_enrich_dir,
    consumer_claims_enrich_dir,
    identity_overrides_path,
    launch_enrich_dir,
    official_enrich_dir,
    unboxing_enrich_dir,
    unboxing_summary_enrich_dir,
)
from sources.audio52.lexicon import BRAND_ALIASES, PRODUCT_TYPE_SUFFIXES

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_NON_SKU_MODEL_MARKERS = (
    "拆解汇总", "拆解对比", "还能怎么做", "给你答案", "重点全在", "款产品",
)
_GENERIC_MODELS = {"earbuds", "earphone", "earphones", "headphones", "headset", "buds", "unknown", "brand"}


def _brand_aliases() -> list[tuple[str, list[str]]]:
    return combined_brand_aliases(BRAND_ALIASES)


def normalize_brand(brand: str) -> str:
    """将品牌名归一化为 BRAND_ALIASES 中的展示名。"""
    text = (brand or "").strip()
    if not text:
        return ""

    lower = text.lower()
    best: tuple[int, int, str] | None = None  # (start, -len(alias), display)
    for display, aliases in _brand_aliases():
        for alias in aliases:
            idx = lower.find(alias.lower())
            if idx == -1:
                continue
            candidate = (idx, -len(alias), display)
            if best is None or candidate < best:
                best = candidate
    return best[2] if best else text


def normalize_model(model: str, brand: str = "") -> str:
    """型号归一化：去空白、去掉重复品牌前缀。"""
    text = re.sub(r"\s+", " ", (model or "").strip())
    if not text:
        return ""

    text = re.sub(
        r"^(?:视频拆解|拆解视频|深度拆解|拆解|拆机视频|拆机|视频)\s*[：:丨|·-]*\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    norm_brand = normalize_brand(brand) if brand else ""
    if norm_brand:
        for alias in [norm_brand, *next((a for d, a in _brand_aliases() if d == norm_brand), [])]:
            if alias and text.lower().startswith(alias.lower()):
                text = text[len(alias) :].strip(" -·、，,")
                break
    for suffix in sorted(PRODUCT_TYPE_SUFFIXES, key=len, reverse=True):
        if suffix and text.lower().endswith(suffix.lower()):
            text = text[: -len(suffix)].strip(" -.,，")
            break
    return text or (model or "").strip()


def _slug_part(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    asciiish = normalized.encode("ascii", "ignore").decode("ascii").lower()
    if not asciiish:
        asciiish = re.sub(r"\s+", "-", text.strip().lower())
    slug = _SLUG_RE.sub("-", asciiish).strip("-")
    return slug or "unknown"


def canonical_product_id(brand: str, model: str) -> str:
    """生成稳定的 canonical 产品 ID，格式 `{brand_slug}--{model_slug}`。"""
    norm_brand = normalize_brand(brand)
    norm_model = normalize_model(model, norm_brand)
    brand_slug = _slug_part(norm_brand or "unknown")
    model_slug = _slug_part(norm_model or "unknown")
    return f"{brand_slug}--{model_slug}"


def guess_brand_from_text(text: str) -> str:
    """从标题/型号文本中猜测品牌（用于 video 缺 brand 字段时）。"""
    lower = (text or "").lower()
    best: tuple[int, int, str] | None = None
    for display, aliases in _brand_aliases():
        for alias in aliases:
            idx = lower.find(alias.lower())
            if idx == -1:
                continue
            candidate = (idx, -len(alias), display)
            if best is None or candidate < best:
                best = candidate
    return best[2] if best else ""


def identity_review_reason(brand: str, model: str, title: str = "") -> str:
    """Return a reason when the identity is unsafe for automatic price lookup."""
    normalized_brand = normalize_brand(brand)
    normalized_model = normalize_model(model, normalized_brand)
    combined = f"{title} {normalized_model}".lower()
    if not normalized_brand:
        return "brand_missing"
    if not normalized_model:
        return "model_missing"
    if normalized_model.lower() in _GENERIC_MODELS:
        return "model_generic"
    if any(marker in combined for marker in _NON_SKU_MODEL_MARKERS):
        return "not_a_single_sku"
    return ""


def is_identity_searchable(brand: str, model: str, title: str = "") -> bool:
    return not identity_review_reason(brand, model, title)


def load_official_enrich(canonical_id: str) -> dict | None:
    path = official_enrich_dir() / f"{canonical_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_channel_enrich(canonical_id: str) -> dict | None:
    """读取渠道层 enrich（按 canonical_id）。"""
    path = channel_enrich_dir() / f"{canonical_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_launch_enrich(canonical_id: str) -> dict | None:
    path = launch_enrich_dir() / f"{canonical_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def merge_launch_snapshot(canonical_id: str, first_seen: str, market: dict | None = None) -> dict:
    """Return a display-safe, source-linked launch-date snapshot.

    Article publication dates are intentionally not presented as launch dates.
    They are only used to place unresolved historical products into the
    user-approved legacy bucket.
    """
    evidence = load_launch_enrich(canonical_id) or {}
    date_value = str(evidence.get("launch_date") or "").strip()
    display = str(evidence.get("launch_display") or "").strip()
    if date_value or display:
        return {
            "date": date_value,
            "display": display or date_value,
            "scope": str(evidence.get("launch_scope") or "").strip(),
            "status": str(evidence.get("status") or "verified").strip(),
            "source_url": str(evidence.get("source_url") or "").strip(),
            "evidence": str(evidence.get("evidence") or "").strip(),
            "source_type": str(evidence.get("source_type") or "").strip(),
        }

    # A negative result is also a cache entry: it prevents the scheduled job
    # from repeatedly sending the same original article to the model.  It must
    # still render the user-facing legacy/pending state below, never an empty
    # value that looks like a verified date.
    checked = str(evidence.get("status") or "").strip() == "not_found"

    inferred = str((market or {}).get("launch_date") or "").strip()
    if inferred:
        return {
            "date": inferred,
            "display": inferred,
            "scope": "",
            "status": "reported",
            "source_url": "",
            "evidence": "来源原文已提及上市时间，待补充外部发布时间链接。",
            "source_type": "source_article",
        }

    if first_seen[:4].isdigit() and int(first_seen[:4]) <= 2024:
        return {
            "date": "",
            "display": "2024及以前产品，暂未找到相应信息",
            "scope": "",
            "status": "legacy_unresolved",
            "source_url": "",
            "evidence": "已核对来源原文，未发现可直接佐证的上市时间。" if checked else "",
            "source_type": "",
        }
    return {
        "date": "",
        "display": "上市时间待核验",
        "scope": "",
        "status": "pending",
        "source_url": "",
        "evidence": "已核对来源原文，待补充公开发布时间来源。" if checked else "",
        "source_type": "",
    }


def load_identity_overrides() -> dict[str, dict]:
    """Return reviewed product identities keyed by original 52audio report ID.

    The raw report stays immutable.  Only high-confidence, single-product or
    split-product entries are used by the product builder.
    """
    path = identity_overrides_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    items = payload.get("items") if isinstance(payload, dict) else None
    return items if isinstance(items, dict) else {}


def load_unboxing_enrich(report_id: str) -> dict | None:
    path = unboxing_enrich_dir() / f"{report_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_consumer_claims_enrich(canonical_id: str) -> dict | None:
    path = consumer_claims_enrich_dir() / f"{canonical_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_unboxing_summary_enrich(report_id: str) -> dict | None:
    path = unboxing_summary_enrich_dir() / f"{report_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_bom_parameters_enrich(canonical_id: str) -> dict | None:
    path = bom_parameters_enrich_dir() / f"{canonical_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _attach_bom_parameters(canonical_id: str, rows: list[dict]) -> None:
    cached = load_bom_parameters_enrich(canonical_id) or {}
    items = cached.get("items") if isinstance(cached.get("items"), list) else []
    by_key = {str(item.get("key") or ""): item for item in items if isinstance(item, dict)}
    for row in rows:
        item = by_key.get(bom_row_key(row)) or {}
        parameters = item.get("parameters") if isinstance(item.get("parameters"), list) else []
        if parameters:
            row["parameters"] = parameters
            row["parameter_meta"] = cached.get("meta") or {}


def _bom_model_token(value: object) -> str:
    tokens = re.findall(r"[A-Z]{1,8}\d+[A-Z0-9-]*", str(value or "").upper())
    return tokens[-1] if tokens else re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _compatible_bom_models(left: object, right: object) -> bool:
    a = _bom_model_token(left)
    b = _bom_model_token(right)
    if not a or not b or a == b:
        return True
    def family_pattern(value: str) -> str:
        return "[A-Z0-9-]+".join(re.escape(part) for part in re.split(r"X+", value))

    if "X" in a and re.fullmatch(family_pattern(a), b):
        return True
    if "X" in b and re.fullmatch(family_pattern(b), a):
        return True
    return False


def _aggregate_bom_facts(rows: list[dict]) -> list[dict]:
    """Aggregate repeated descriptions of one identified component.

    Rows with two different concrete models stay separate. Two model-less rows
    also stay separate because they may represent physically distinct parts.
    """
    output: list[dict] = []
    for source in rows:
        row = dict(source)
        component = re.sub(r"\s+", "", str(row.get("component") or ""))
        side = str(row.get("side") or "")
        model = _bom_model_token(row.get("model"))
        target: dict | None = None
        for existing in output:
            if re.sub(r"\s+", "", str(existing.get("component") or "")) != component:
                continue
            if str(existing.get("side") or "") != side:
                continue
            existing_model = _bom_model_token(existing.get("model"))
            if not existing_model and not model:
                continue
            if _compatible_bom_models(existing.get("model"), row.get("model")):
                target = existing
                break
        if target is None:
            evidence_text = str(row.get("fact_text") or "").strip()
            if evidence_text:
                row["evidence_texts"] = [evidence_text]
            output.append(row)
            continue

        old_model = _bom_model_token(target.get("model"))
        new_is_more_specific = bool(model) and (
            not old_model or ("X" in old_model and "X" not in model) or len(model) > len(old_model)
        )
        evidence_texts = list(target.get("evidence_texts") or [])
        incoming_text = str(row.get("fact_text") or "").strip()
        if incoming_text and incoming_text not in evidence_texts:
            evidence_texts.append(incoming_text)
        target["evidence_texts"] = evidence_texts

        images = list(target.get("evidence_images") or [])
        image_keys = {str(image.get("url") or "") for image in images if isinstance(image, dict)}
        for image in row.get("evidence_images") or []:
            if isinstance(image, dict) and str(image.get("url") or "") not in image_keys:
                images.append(image)
                image_keys.add(str(image.get("url") or ""))
        target["evidence_images"] = images

        parameters = list(target.get("parameters") or [])
        parameter_keys = {
            (str(item.get("label") or ""), str(item.get("value") or ""))
            for item in parameters if isinstance(item, dict)
        }
        for item in row.get("parameters") or []:
            key = (str(item.get("label") or ""), str(item.get("value") or ""))
            if isinstance(item, dict) and key not in parameter_keys:
                parameters.append(item)
                parameter_keys.add(key)
        if parameters:
            target["parameters"] = parameters

        if new_is_more_specific:
            for field in ("model", "brand", "fact_text", "evidence", "classification_reason"):
                if row.get(field):
                    target[field] = row[field]
    return output


def _slim_unboxing_module(mod: dict | None, *, max_images: int = 8) -> dict:
    mod = mod or {}
    images = mod.get("appearance_images") or mod.get("images") or []
    if isinstance(images, list) and len(images) > max_images:
        images = images[:max_images]
    return {
        "description": mod.get("description") or "",
        "accessories": mod.get("accessories") or [],
        "appearance_images": images,
        "image_count": len(mod.get("images") or []),
        "teardown_image_count": mod.get("teardown_image_count") or 0,
    }


def merge_unboxing_snapshot(report_ids: list[str]) -> dict | None:
    """从最佳报告的 unboxing enrich 生成产品页摘要。"""
    best: tuple[int, dict] | None = None
    for rid in report_ids:
        raw = load_unboxing_enrich(rid)
        if not raw:
            continue
        pkg = raw.get("packaging") or {}
        score = len(pkg.get("images") or []) + len(pkg.get("accessories") or [])
        if best is None or score > best[0]:
            best = (score, raw)
    if not best:
        return None
    raw = best[1]
    report_id = str(raw.get("report_id") or "")
    summary = load_unboxing_summary_enrich(report_id) or {}
    summary_modules = summary.get("modules") if isinstance(summary.get("modules"), dict) else {}
    gaps = raw.get("gaps") or []
    modules = {
        "packaging": _slim_unboxing_module(raw.get("packaging")),
        "charging_case": _slim_unboxing_module(raw.get("charging_case")),
        "earbuds": _slim_unboxing_module(raw.get("earbuds")),
    }
    for key, module in modules.items():
        module["display_bullets"] = list(summary_modules.get(key) or [])
        if summary.get("meta"):
            module["summary_meta"] = summary["meta"]
    filled = sum(1 for m in modules.values() if m.get("appearance_images"))
    return {
        "report_id": raw.get("report_id"),
        "packaging": modules["packaging"],
        "charging_case": modules["charging_case"],
        "earbuds": modules["earbuds"],
        "gaps": gaps,
        "completeness": round(filled / 3, 2),
    }


def _views_cost_score(views: dict) -> int:
    cost = views.get("cost") or {}
    return len(cost.get("bom_table") or []) + 2 * len(cost.get("chip_modules") or [])


def _views_market_score(views: dict) -> int:
    market = views.get("market") or {}
    return len(market.get("selling_points") or []) + len(market.get("scenarios") or [])


_CONCRETE_BOM_COMPONENT_RE = re.compile(
    r"芯片|SoC|MCU|微控制器|处理器|电池|扬声器|喇叭|动圈|动铁|麦克风|传感器|"
    r"主板|PCBA|PCB|FPC|天线|连接器|连接线|排线|插座|接口|线圈|磁铁|马达|"
    r"LED|指示灯|按键|触控板|触控芯片|支架|转轴|铰链|网罩|声学网|耳套|外壳",
    re.I,
)


def _is_concrete_bom_fallback(row: dict) -> bool:
    """Keep only evidence-backed physical/electronic coarse-BOM rows."""
    component = str(row.get("component") or "").strip()
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    source_text = str(
        evidence.get("text") or evidence.get("source_text") or row.get("evidence_text") or ""
    ).strip()
    if not component or not source_text:
        return False
    if any(token in source_text for token in ("包装盒", "包装清单", "外包装")):
        return False
    if component in {"降噪系统", "触控/按键", "功能", "佩戴体验", "音质"}:
        return False
    if len(source_text) > 220 and not (row.get("model") or row.get("brand")):
        return False
    return bool(_CONCRETE_BOM_COMPONENT_RE.search(component))


def merge_market_snapshot(
    *,
    canonical_id: str,
    report_ids: list[str],
    video_ids: list[str],
    reports_by_id: dict[str, dict],
    videos_by_id: dict[str, dict] | None = None,
) -> dict | None:
    """生成市场快照：拆解报告优先；仅在没有报告时使用视频。"""
    report_candidates: list[tuple[int, dict, str, str, dict]] = []
    for rid in report_ids:
        r = reports_by_id.get(rid)
        if not r:
            continue
        views = r.get("views") or {}
        score = _views_market_score(views)
        # 报告存在时即作为主信源；即使旧 views 很稀疏，也不能被视频条数覆盖。
        report_candidates.append((score, views, "report", rid, r))

    candidates = report_candidates
    if not candidates and videos_by_id:
        for vid in video_ids:
            v = videos_by_id.get(vid)
            if not v:
                continue
            views = v.get("views") or {}
            score = _views_market_score(views)
            if score:
                candidates.append((score, views, "video", vid, v))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[0], reverse=True)
    _, views, source_type, source_id, source_record = candidates[0]
    market = views.get("market") or {}
    selling_points = list(market.get("selling_points") or [])[:8]
    consumer_claims: list[dict] = []
    claims_meta: dict = {}

    # 已审核的模型 enrich 优先；缓存必须仍指向当前关联报告。
    cached = load_consumer_claims_enrich(canonical_id) if source_type == "report" else None
    if cached and str(cached.get("source_report_id") or "") == source_id:
        consumer_claims = list(cached.get("consumer_claims") or [])[:7]
        claims_meta = dict(cached.get("meta") or {})
    elif source_type == "report" and source_record.get("content_html"):
        paragraphs = extract_consumer_source(str(source_record.get("content_html") or ""))
        consumer_claims, rejected = validate_consumer_claims(
            deterministic_candidates(paragraphs),
            paragraphs,
            list(((views.get("cost") or {}).get("teardown_inventory") or [])),
        )
        claims_meta = {
            "method": "deterministic_fallback",
            "source_report_id": source_id,
            "rejected_count": len(rejected),
        }
    elif source_type == "video":
        # 只有产品没有拆解报告时，才允许视频市场事实进入产品页。
        consumer_claims = selling_points

    if consumer_claims:
        selling_points = consumer_claims
    claim_scenarios = [str(claim.get("scenario") or "").strip() for claim in consumer_claims]
    scenarios = list(dict.fromkeys(claim_scenarios if consumer_claims else list(market.get("scenarios") or [])))
    scenarios = [item for item in scenarios if item and item != "游戏"][:4]
    if consumer_claims:
        categories = list(dict.fromkeys(str(claim.get("category") or "").strip() for claim in consumer_claims))
        categories = [item for item in categories if item]
        positioning_summary = f"消费者卖点主要集中在{'、'.join(categories)}。" if categories else ""
    else:
        positioning_summary = market.get("positioning_summary") or ""

    if not (selling_points or scenarios or positioning_summary):
        return None

    return {
        "consumer_claims": consumer_claims,
        "consumer_claims_meta": claims_meta,
        "selling_points": selling_points,
        "scenarios": scenarios,
        "positioning_summary": positioning_summary,
        "launch_date": market.get("launch_date"),
        "best_report_id": source_id if source_type == "report" else None,
        "best_video_id": source_id if source_type == "video" else None,
    }


def merge_cost_snapshot(
    *,
    canonical_id: str,
    report_ids: list[str],
    video_ids: list[str],
    reports_by_id: dict[str, dict],
    videos_by_id: dict[str, dict] | None = None,
    market_price: float | None = None,
) -> dict:
    """从产品关联报告/视频中融合成本快照与 BOM。"""
    reports = [reports_by_id[rid] for rid in report_ids if rid in reports_by_id]
    best = pick_best_report(reports) or (reports[0] if reports else None)
    views = (best or {}).get("views") or {}
    best_video_id = ""

    # 拆解报告是该产品的完整主信源；视频仅在无报告时作为兜底，不能按条数反超。
    if not best and videos_by_id:
        videos = [videos_by_id[vid] for vid in video_ids if vid in videos_by_id]
        if videos:
            best_video = max(videos, key=lambda v: _views_cost_score(v.get("views") or {}))
            vviews = best_video.get("views") or {}
            if _views_cost_score(vviews) > _views_cost_score(views):
                views = vviews
                best_video_id = best_video.get("id", "")
                best = None  # 成本主信源切到视频

    cost = views.get("cost") or {}
    structure = views.get("structure") or {}
    bom_table = list(cost.get("bom_table") or [])
    teardown_inventory = list(cost.get("teardown_inventory") or [])
    # Fine-grained teardown rows are preferred, but older reports may state a
    # battery or chip only in the article summary. Merge unseen summary rows
    # instead of dropping them merely because a few fine-grained rows exist.
    inventory_source: list[dict] = []
    seen_inventory: set[tuple[str, ...]] = set()
    seen_evidence: set[str] = set()
    detailed_families: list[tuple[str, str, str]] = []

    def inventory_key(row: dict) -> tuple[str, ...]:
        model = re.sub(r"[^A-Z0-9]+", "", str(row.get("model") or "").upper())
        if model:
            return ("model", model)
        return (
            "component",
            re.sub(r"\s+", "", str(row.get("component") or "")),
            str(row.get("side") or ""),
        )

    def inventory_evidence_key(row: dict) -> str:
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        text = str(
            row.get("fact_text")
            or evidence.get("text")
            or evidence.get("source_text")
            or row.get("evidence_text")
            or ""
        )
        return re.sub(r"\s+", "", text)

    def model_token(value: object) -> str:
        tokens = re.findall(r"[A-Z]{1,8}\d+[A-Z0-9-]*", str(value or "").upper())
        return tokens[-1] if tokens else re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())

    def duplicates_detailed_family(row: dict) -> bool:
        family = component_key(row.get("component"), inventory_evidence_key(row))
        side = str(row.get("side") or "")
        model = model_token(row.get("model"))
        physical_families = {
            "battery", "speaker_driver", "microphone", "pcb", "antenna", "connector",
            "indicator", "button", "enclosure", "structural_support",
        }
        for existing_family, existing_side, existing_model in detailed_families:
            side_matches = not side or not existing_side or side == existing_side
            if side_matches and family == existing_family and family in physical_families:
                return True
            if family == "generic_chip_module" and model and existing_model:
                if model.startswith(existing_model) or existing_model.startswith(model):
                    return True
        return False

    for source_index, row in enumerate([*teardown_inventory, *bom_table]):
        if not isinstance(row, dict):
            continue
        key = inventory_key(row)
        if key in seen_inventory:
            continue
        evidence_key = inventory_evidence_key(row)
        if source_index < len(teardown_inventory):
            seen_inventory.add(key)
            if evidence_key:
                seen_evidence.add(evidence_key)
            detailed_families.append(
                (
                    component_key(row.get("component"), evidence_key),
                    str(row.get("side") or ""),
                    model_token(row.get("model")),
                )
            )
            inventory_source.append(row)
            continue
        if evidence_key and evidence_key in seen_evidence:
            continue
        if duplicates_detailed_family(row):
            continue
        if not _is_concrete_bom_fallback(row):
            continue
        normalized = dict(row)
        role = str(normalized.get("classification") or normalized.get("role") or "")
        role = {
            "保护IC": "key",
            "PMIC/充电仓管理": "core",
            "传感器": "key",
        }.get(role, role)
        if role not in {"core", "key", "structure", "auxiliary", "unidentified_marking", "review"}:
            role = "auxiliary"
        family = component_key(normalized.get("component"), evidence_key)
        if family in {"enclosure", "structural_support"}:
            role = "structure"
        normalized["classification"] = role
        normalized["role"] = role
        evidence = normalized.get("evidence") if isinstance(normalized.get("evidence"), dict) else {}
        normalized.setdefault(
            "fact_text",
            str(evidence.get("text") or evidence.get("source_text") or normalized.get("evidence_text") or ""),
        )
        normalized.setdefault("evidence_images", [])
        seen_inventory.add(key)
        if evidence_key:
            seen_evidence.add(evidence_key)
        inventory_source.append(normalized)
    technical_facts = [
        row for row in inventory_source
        if row.get("role") not in {"unidentified_marking", "review"}
        and row.get("classification") not in {"unidentified_marking", "review"}
    ]
    _attach_bom_parameters(canonical_id, technical_facts)
    technical_facts = _aggregate_bom_facts(technical_facts)
    data_quality_review_queue = [
        row for row in inventory_source
        if row.get("role") in {"unidentified_marking", "review"}
        or row.get("classification") in {"unidentified_marking", "review"}
    ]
    summary_image_urls = list(cost.get("summary_image_urls") or [])
    report_image_urls = list(structure.get("report_image_urls") or [])
    summary_text = cost.get("summary_text") or ""

    row_fallback: dict = {}
    if best:
        chips = cost.get("chip_modules") or []
        row_fallback["major_chips"] = [c.get("model") for c in chips if c.get("model")]
        sw = views.get("software") or {}
        row_fallback["bluetooth"] = sw.get("bluetooth_version")

    fields = extract_cost_fields(views, row_fallback=row_fallback) if best else {}

    channel = load_channel_enrich(canonical_id)
    official = load_official_enrich(canonical_id)
    price_cny = None
    price_layer = None
    price_source_label = ""
    price_source_url = ""
    price_evidence = ""
    price_currency = "CNY"
    price_kind = ""
    price_kind_labels = {
        "official": "官方价",
        "reference": "第三方渠道参考价",
        "promo": "促销价",
        "used": "二手价",
        "inventory": "库存价",
        "channel_event": "渠道活动价",
        "channel": "渠道价",
    }
    # 对比页的价格口径固定为品牌官方售价/建议零售价。渠道价通常是
    # 实时促销或成交价，不能覆盖已核验的官方定价。
    if official and official.get("msrp_cny") is not None:
        price_cny = official.get("msrp_cny")
        price_layer = "official"
        price_kind = str(official.get("price_kind") or "official")
        official_labels = {
            "official_product_page": "官网价",
            "brand_official_news": "官方新闻价",
            "brand_official_social_post": "官方社区价",
            "brand_announcement_report": "发布报道价",
            "channel_price": "渠道价格",
            "official_flagship_store": "官方旗舰店价",
            "zol_reference_price": "中关村在线参考价",
        }
        price_source_label = str(official.get("price_label") or "").strip() or price_kind_labels.get(
            price_kind,
            official_labels.get(str(official.get("price_evidence_kind") or ""), "官方价"),
        )
        price_source_url = str(
            official.get("price_source_url")
            or official.get("official_url")
            or official.get("vmall_url")
            or ""
        )
        price_evidence = str(official.get("price_evidence") or "")
    elif official and official.get("price_amount") is not None:
        # Some overseas brands only publish a non-CNY price. Keep it visible
        # and traceable instead of silently discarding a useful launch price.
        price_cny = official.get("price_amount")
        price_currency = str(official.get("price_currency") or "CNY").upper()
        price_kind = str(official.get("price_kind") or "official")
        price_layer = "official"
        price_source_label = str(official.get("price_label") or "").strip() or price_kind_labels.get(price_kind, "官方价")
        price_source_url = str(official.get("price_source_url") or official.get("official_url") or official.get("vmall_url") or "")
        price_evidence = str(official.get("price_evidence") or "")
    elif channel and channel.get("price_cny") is not None:
        price_cny = channel.get("price_cny")
        price_layer = "channel"
        price_currency = str(channel.get("price_currency") or "CNY").upper()
        price_kind = str(channel.get("price_kind") or "channel")
        price_source_label = str(channel.get("price_label") or "").strip() or price_kind_labels.get(price_kind, "渠道价")
        price_source_url = str(channel.get("channel_url") or "")
        price_evidence = str(channel.get("price_note") or "")
    elif channel and channel.get("price_amount") is not None:
        price_cny = channel.get("price_amount")
        price_layer = "channel"
        price_currency = str(channel.get("price_currency") or "CNY").upper()
        price_kind = str(channel.get("price_kind") or "channel")
        price_source_label = str(channel.get("price_label") or "").strip() or price_kind_labels.get(price_kind, "渠道价")
        price_source_url = str(channel.get("channel_url") or "")
        price_evidence = str(channel.get("price_note") or "")
    elif market_price is not None:
        price_cny = market_price
        price_layer = "technical"
        price_source_label = "技术文章价"
        price_kind = "technical"

    layer_refs: dict[str, list[str]] = {
        "technical": [f"report:{r['id']}" for r in reports] if reports else [f"video:{vid}" for vid in video_ids],
        "channel": [f"channel:{canonical_id}"] if channel else [],
        "official": [f"official:{canonical_id}"] if official else [],
        "unboxing": [f"unboxing:{rid}" for rid in report_ids if load_unboxing_enrich(rid)],
        "review": [],
    }

    cost_snapshot = {
        "main_chip": (fields.get("main_chip") or {}).get("value"),
        "pmic_case": (fields.get("pmic") or {}).get("value"),
        "battery_ear": (fields.get("battery_ear") or {}).get("value"),
        "battery_case": (fields.get("battery_case") or {}).get("value"),
        "speaker": (fields.get("speaker") or {}).get("value"),
        "materials": (fields.get("materials") or {}).get("value"),
        "weight_g": (fields.get("weight_g") or {}).get("value"),
        "weight_case_g": (fields.get("weight_case_g") or {}).get("value"),
        "weight_earbud_g": (fields.get("weight_earbud_g") or {}).get("value"),
        "ip_rating": (fields.get("ip_rating") or {}).get("value"),
        "bluetooth": (fields.get("bluetooth") or {}).get("value"),
        "bom_row_count": len(technical_facts),
        "price_cny": price_cny,
        "price_currency": price_currency,
        "price_kind": price_kind,
        "price_layer": price_layer,
        "price_source_label": price_source_label,
        "price_source_url": price_source_url,
        "price_evidence": price_evidence,
        "price_disclaimer": str((channel or {}).get("price_disclaimer") or ""),
        "channel_url": (channel or {}).get("channel_url"),
        "sales_hint": (channel or {}).get("sales_hint"),
        "data_completeness": compute_cost_completeness(fields) if fields else 0.0,
        "best_report_id": (best or {}).get("id") if best else None,
        "best_video_id": best_video_id or None,
    }

    return {
        "cost_snapshot": cost_snapshot,
        "cost_fields": fields,
        "bom_table": bom_table,
        "teardown_inventory": teardown_inventory,
        "technical_facts": technical_facts,
        "data_quality_review_queue": data_quality_review_queue,
        "summary_image_urls": summary_image_urls,
        "report_image_urls": report_image_urls,
        "summary_text": summary_text,
        "layer_refs": layer_refs,
    }
