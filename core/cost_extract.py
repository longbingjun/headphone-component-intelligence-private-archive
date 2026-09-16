"""成本工程师字段抽取：从 views.cost/structure/software 提取矩阵与快照列。"""

from __future__ import annotations

from typing import Any


def _ev_text(item: Any) -> str:
    if isinstance(item, dict):
        ev = item.get("evidence")
        if isinstance(ev, dict):
            return ev.get("source_text") or ev.get("value") or ""
        return item.get("source_text") or item.get("source_ref") or item.get("value") or ""
    return ""


def _format_bom_row(row: dict) -> str:
    brand = (row.get("brand") or "").strip()
    model = (row.get("model") or "").strip()
    comp = (row.get("component") or "").strip()
    if brand or model:
        return f"{brand} {model}".strip()
    return comp


def _row_matches(row: dict, *keywords: str, field: str = "both") -> bool:
    role = (row.get("role") or "")
    comp = (row.get("component") or "")
    side = (row.get("side") or "")
    if field == "role":
        text = role
    elif field == "component":
        text = comp
    elif field == "side":
        text = side
    else:
        text = role + comp + side
    return any(k in text for k in keywords)


def extract_cost_fields(views: dict, *, row_fallback: dict | None = None) -> dict[str, dict]:
    """从单条 report views 提取成本矩阵列，返回 {col_key: {value, evidence, source_layer}}。"""
    cost = views.get("cost") or {}
    structure = views.get("structure") or {}
    hardware = views.get("hardware") or {}
    software = views.get("software") or {}
    bom = cost.get("bom_table") or []
    chips = cost.get("chip_modules") or []
    specs = hardware.get("specs") or []
    row_fallback = row_fallback or {}

    def cell(val: str, ev: str = "", layer: str = "technical") -> dict:
        return {"value": val or "", "evidence": ev or "", "source_layer": layer}

    out: dict[str, dict] = {}

    # 主控芯片
    main_chip, main_ev = "", ""
    for c in chips:
        comp = (c.get("component") or c.get("part") or "")
        role = (c.get("role") or "")
        if _row_matches(c, "主控", "MCU", "SoC", "蓝牙", field="both") or any(
            k in comp for k in ("主控", "MCU", "SoC", "蓝牙")
        ):
            main_chip = c.get("model") or comp
            main_ev = _ev_text(c)
            break
    if not main_chip:
        for b in bom:
            if _row_matches(b, "主控", "蓝牙", "SoC", "MCU"):
                main_chip = _format_bom_row(b)
                main_ev = _ev_text(b)
                break
    if not main_chip and chips:
        main_chip = chips[0].get("model") or chips[0].get("part") or ""
        main_ev = _ev_text(chips[0])
    if not main_chip and row_fallback.get("major_chips"):
        main_chip = row_fallback["major_chips"][0]
    out["main_chip"] = cell(main_chip, main_ev)

    # PMIC
    pmic, pmic_ev = "", ""
    for b in bom:
        if _row_matches(b, "PMIC", "充电仓管理", "电源管理", "PMU", "保护IC", "过压", "充电管理"):
            pmic = _format_bom_row(b)
            pmic_ev = _ev_text(b)
            break
    if not pmic:
        for c in chips:
            if _row_matches(c, "PMIC", "充电仓管理", "电源管理", "PMU", "保护IC", "过压"):
                pmic = c.get("model") or c.get("component") or ""
                pmic_ev = _ev_text(c)
                break
    out["pmic"] = cell(pmic, pmic_ev)

    # 电池：按 side 分耳机/充电盒
    bat_ear, bat_ear_ev = "", ""
    bat_case, bat_case_ev = "", ""
    for b in bom:
        if "电池" not in (b.get("component") or ""):
            continue
        side = (b.get("side") or "")
        formatted = _format_bom_row(b)
        ev = _ev_text(b)
        if side in ("充电盒", "Case", "仓") or _row_matches(b, "充电盒", "充电仓", "座舱", field="side"):
            if not bat_case:
                bat_case, bat_case_ev = formatted, ev
        elif side in ("耳机", "左耳", "右耳", "L", "R") or _row_matches(b, "耳机", "左耳", "右耳", field="side"):
            if not bat_ear:
                bat_ear, bat_ear_ev = formatted, ev
        elif not bat_ear:
            bat_ear, bat_ear_ev = formatted, ev
        elif not bat_case:
            bat_case, bat_case_ev = formatted, ev

    if not bat_ear or not bat_case:
        for s in specs:
            if s.get("param") == "电池容量":
                val = f"{s.get('value', '')} {s.get('unit', '')}".strip()
                ev = s.get("source_ref") or ""
                if not bat_ear:
                    bat_ear, bat_ear_ev = val, ev
                elif not bat_case:
                    bat_case, bat_case_ev = val, ev
                break

    out["battery_ear"] = cell(bat_ear, bat_ear_ev)
    out["battery_case"] = cell(bat_case, bat_case_ev)
    out["battery"] = cell(bat_ear or bat_case, bat_ear_ev or bat_case_ev)

    # 喇叭
    speaker, speaker_ev = "", ""
    for b in bom:
        comp = (b.get("component") or "")
        if any(k in comp for k in ("喇叭", "扬声器", "动圈", "单元", "振膜")):
            speaker = _format_bom_row(b)
            speaker_ev = _ev_text(b)
            break
    out["speaker"] = cell(speaker, speaker_ev)

    # 仓充电接口
    case_chg, case_chg_ev = "", ""
    for s in specs:
        if s.get("param") == "充电接口":
            case_chg = s.get("value") or s.get("model") or ""
            case_chg_ev = s.get("source_ref") or ""
            break
    out["case_charging"] = cell(case_chg, case_chg_ev)

    out["bom_rows"] = cell(str(len(bom)) if bom else "0")
    pkg = cost.get("packaging_notes") or []
    pkg_first = pkg[0] if pkg else None
    out["packaging"] = cell(
        pkg_first if isinstance(pkg_first, str) else (pkg_first.get("value") if isinstance(pkg_first, dict) else ""),
        _ev_text(pkg_first) if pkg_first else "",
    )

    mats = structure.get("materials") or []
    out["materials"] = cell("、".join(m if isinstance(m, str) else (m.get("value") or "") for m in mats))
    ip_ev = structure.get("ip_rating_evidence") or {}
    out["ip_rating"] = cell(
        structure.get("ip_rating") or "",
        ip_ev.get("source_text", "") if isinstance(ip_ev, dict) else "",
    )
    w_ev = structure.get("weight_evidence") or {}
    out["weight_g"] = cell(
        str(structure.get("weight_g") or ""),
        w_ev.get("source_text", "") if isinstance(w_ev, dict) else "",
    )
    wc_ev = structure.get("weight_case_evidence") or {}
    out["weight_case_g"] = cell(
        str(structure.get("weight_case_g") or ""),
        wc_ev.get("source_text", "") if isinstance(wc_ev, dict) else "",
    )
    we_ev = structure.get("weight_earbud_evidence") or {}
    out["weight_earbud_g"] = cell(
        str(structure.get("weight_earbud_g") or ""),
        we_ev.get("source_text", "") if isinstance(we_ev, dict) else "",
    )
    out["form_factor"] = cell(structure.get("form_factor") or "")
    out["earbud_type"] = cell(structure.get("earbud_type") or "")

    bt_ev = software.get("bluetooth_evidence") or {}
    out["bluetooth"] = cell(
        software.get("bluetooth_version") or row_fallback.get("bluetooth") or "",
        bt_ev.get("source_text", "") if isinstance(bt_ev, dict) else "",
    )
    out["bluetooth_version"] = out["bluetooth"]

    codec_vals = []
    for c in software.get("codecs") or row_fallback.get("codecs") or []:
        if isinstance(c, dict):
            v = c.get("value") or c.get("text") or ""
            if v:
                codec_vals.append(str(v))
        elif isinstance(c, str) and c:
            codec_vals.append(c)
    out["codecs"] = cell("、".join(codec_vals))

    bat_mah, bat_mah_ev = "", ""
    for s in specs:
        if s.get("param") == "电池容量":
            bat_mah = f"{s.get('value', '')} {s.get('unit', '')}".strip()
            bat_mah_ev = s.get("source_ref") or ""
            break
    out["battery_mah"] = cell(bat_mah, bat_mah_ev)

    for s in specs:
        if s.get("param") == "充电接口":
            out["charge_interface"] = cell(s.get("value") or "", s.get("source_ref") or "")
            break
    else:
        out["charge_interface"] = cell("")

    return out


def pick_best_report(reports: list[dict]) -> dict | None:
    """选 data_completeness 最高且 bom_table 最长的 technical 报告。"""
    if not reports:
        return None

    def score(r: dict) -> tuple[float, int]:
        dc = float(r.get("data_completeness") or 0)
        bom_len = len((r.get("views") or {}).get("cost", {}).get("bom_table") or [])
        return (dc, bom_len)

    return max(reports, key=score)


def compute_cost_completeness(fields: dict[str, dict]) -> float:
    """成本快照完整度：核心列填充率。"""
    keys = ("main_chip", "pmic", "battery_ear", "battery_case", "speaker", "bom_rows")
    filled = sum(1 for k in keys if (fields.get(k) or {}).get("value"))
    return round(filled / len(keys), 2) if keys else 0.0
