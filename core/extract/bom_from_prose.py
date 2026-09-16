"""从「我爱音频网总结」段的 prose 文本里抽 BOM 行。

总结段 prose 通常按固定句式组织（实测 281175/280166）：

    内部主要配置方面，充电盒搭载了WEL旭航诚3.7V/600mAh锂电池，采用了
    INJOINIC英集芯IP5528 TWS耳机充电仓管理SoC，...；还采用了Prisemi芯导
    P14C1S过压过流保护IC，保护电路安全。

    耳机内部搭载HJ弘捷新能源3.85V/55mAh软包扣式电池，采用三磁路黄金微晶
    振膜动圈单元，内置两颗MEMS麦克风，主控芯片为Bluetrum中科蓝讯BT8912F
    蓝牙音频SoC。

提取策略：
1. 用「充电盒搭载/充电仓搭载/耳机内部搭载/耳机搭载」等锚点把 prose 切成
   "Case 段" / "Earbud 段" / 未标注段，给每段定一个 side（充电盒 / 耳机 / 全机）。
2. 段内按句号/分号细切，对每个子句尝试抽：
   - 芯片/模组：CHIP_PATTERNS 命中 + CHIP_BRAND_MAP 归一化品牌
   - 电池：(\\d+)\\s*mAh.*电池 | 电池.*(\\d+)\\s*mAh + 厂商前缀
   - 喇叭单元：(\\d+\\.?\\d*)\\s*mm.*(动圈|单元|振膜|双单元) | (动圈|振膜).*单元
   - 麦克风：MEMS麦克风 | 麦克风
3. 输出与现 `cost.bom_table` 同 schema 的行：
   {component, brand, model, qty_hint, side, role, evidence}
   role 用 COMPONENT_LEXICON 的 importance（major/minor）；芯片类标 "主控/蓝牙"。
"""

from __future__ import annotations

import re

from sources.audio52 import lexicon


# 句子切分：句号/问号/叹号/分号
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[。！？!?；])\s*")

# side 锚点：把 prose 切成「充电盒段 / 耳机段 / 其他段」
_SIDE_ANCHORS: list[tuple[str, re.Pattern]] = [
    ("充电盒", re.compile(r"(?:充电盒|充电仓|座舱)\s*(?:搭载|采用|内置|配备|设置|内部)")),
    ("耳机", re.compile(r"耳机\s*(?:内部)?\s*(?:搭载|采用|内置|配备|设置)")),
]

# 子句级 side 提示
_CASE_CLAUSE_RE = re.compile(r"充电盒|充电仓|座舱")
_EARBUD_CLAUSE_RE = re.compile(r"耳机内部|耳机|左耳|右耳|腔体")

# 电池：标称容量 + 厂商前缀（实测样本：「WEL旭航诚3.7V/600mAh锂电池」）
_BATT_CAPACITY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:V\s*/?|/)\s*(\d{2,4})\s*mAh",
    re.I,
)
_BATT_CAPACITY_ONLY_RE = re.compile(r"(\d{2,4})\s*mAh", re.I)
_BATT_VENDOR_RE = re.compile(
    r"(WEL旭航诚|HJ弘捷新能源|弘捷|微电新能源|鹏辉|宁德时代|比亚迪|国能|亿纬|赣锋)"
)
_BATT_KEYWORD_RE = re.compile(r"(锂电池|扣式电池|软包.*电池|钢壳.*电池|电池)")

# 喇叭单元
_SPEAKER_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*mm\s*(?:对称双单元|双单元|动圈单元|动铁单元|单元|振膜)"
)
_SPEAKER_KW_RE = re.compile(r"(动圈单元|动铁单元|双单元|对称双单元|振膜动圈|黄金微晶振膜|钛振膜|生物振膜)")

# 麦克风
_MIC_RE = re.compile(r"(两颗|双|三颗|三)?\s*(?:MEMS\s*)?麦克风", re.I)
_MIC_QTY_MAP = {"两颗": "2", "双": "2", "三颗": "3", "三": "3"}

# 主控芯片显式锚点：「主控芯片为X」
_MAIN_CHIP_RE = re.compile(r"主控芯片\s*为\s*(.+?)(?:[。，；]|$)")


def _split_into_segments(text: str) -> list[tuple[str, str]]:
    """按 side 锚点把 prose 切成 [(side, segment_text), ...]。

    未匹配到锚点的前缀段标 side=""（视为「全机」）。
    """
    if not text:
        return []
    # 找出所有锚点位置
    anchors: list[tuple[int, str]] = []
    for side, pat in _SIDE_ANCHORS:
        for m in pat.finditer(text):
            anchors.append((m.start(), side))
    if not anchors:
        return [("", text)]
    anchors.sort(key=lambda x: x[0])

    segments: list[tuple[str, str]] = []
    # 锚点之前的前缀
    if anchors[0][0] > 0:
        pre = text[: anchors[0][0]].strip()
        if pre:
            segments.append(("", pre))
    for i, (pos, side) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(text)
        seg = text[pos:end].strip()
        if seg:
            segments.append((side, seg))
    return segments


def _resolve_brand(model: str) -> str:
    """根据芯片型号字符串查 CHIP_BRAND_MAP，返回品牌展示名。"""
    if not model:
        return ""
    for prefix, brand in lexicon.CHIP_BRAND_MAP:
        if prefix.lower() in model.lower():
            return brand
    return ""


# 核心型号提取：剥掉品牌前缀，只保留字母数字部分，用于去重 key。
# 例：「INJOINIC英集芯IP5528」→「IP5528」，「Bluetrum中科蓝讯BT8912F」→「BT8912F」
_MODEL_CORE_RE = re.compile(r"[A-Z]{1,4}[\d]+[A-Z0-9]*", re.I)
_BRAND_PREFIXES = (
    "INJOINIC", "英集芯", "Bluetrum", "中科蓝讯", "BES", "恒玄", "ConvenientPower",
    "易冲半导体", "XHSC", "小华半导体", "WINSEMI", "稳先微", "Prisemi", "芯导",
    "Qualcomm", "高通", "Actions", "络达", "Airoha", "JL", "晶晨", "爱科微", "昆腾",
)


def _canonical_model(model: str) -> str:
    """归一化芯片型号用于去重：剥品牌前缀，取第一个「字母+数字」token。"""
    if not model:
        return ""
    s = model
    for prefix in _BRAND_PREFIXES:
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix):]
            break
    m = _MODEL_CORE_RE.search(s)
    return m.group(0).upper() if m else re.sub(r"\s+", "", s).upper()


def _clause_side(side_seg: str, clause: str) -> str:
    """段级 side + 子句级 side 提示，子句优先。"""
    if _CASE_CLAUSE_RE.search(clause):
        return "充电盒"
    if _EARBUD_CLAUSE_RE.search(clause):
        return "耳机"
    return side_seg


def _detect_chip_role(match_text: str, start: int, clause: str) -> str:
    """根据芯片在子句中的上下文判定 role。

    用 ±30 字符窗口判定，避免同一长句里多个芯片互相污染 role。
    """
    win_start = max(0, start - 30)
    win_end = min(len(clause), start + len(match_text) + 30)
    window = clause[win_start:win_end]
    low = window.lower()

    # 显式「主控芯片为X」锚点
    if "主控芯片" in window:
        return "主控/蓝牙"
    # 已知 PMIC 型号
    if re.search(r"IP55(28|26|16)", match_text, re.I):
        return "PMIC/充电仓管理"
    if "充电仓管理" in window or "电源管理芯片" in window or "pmic" in low:
        return "PMIC/充电仓管理"
    if "无线充电" in window:
        return "无线充电IC"
    if "保护" in window and ("ic" in low or "IC" in window):
        return "保护IC"
    if "mcu" in low:
        return "MCU"
    if "加速度传感器" in window or "传感器" in window:
        return "传感器"
    # 默认按型号前缀猜
    if re.match(r"BT\d", match_text, re.I) or re.match(r"BES\d", match_text, re.I):
        return "主控/蓝牙"
    if re.match(r"WSDF|P\d+C", match_text, re.I):
        return "保护IC"
    if re.match(r"HC32L", match_text, re.I):
        return "MCU"
    if re.match(r"CPS", match_text, re.I):
        return "无线充电IC"
    return "主控/蓝牙"


def _extract_chips(segment: str, side_seg: str) -> list[dict]:
    """从段内抽芯片行。返回 BOM 行 dict 列表。"""
    rows: list[dict] = []
    seen_canonical: set[str] = set()
    for clause in _CLAUSE_SPLIT_RE.split(segment):
        if not clause or len(clause) < 4:
            continue
        for pat in lexicon.CHIP_PATTERNS:
            for m in re.finditer(pat, clause, re.I):
                raw = m.group(0).strip()
                brand = _resolve_brand(raw)
                canonical = _canonical_model(raw)
                if not canonical or canonical in seen_canonical:
                    continue
                seen_canonical.add(canonical)
                role = _detect_chip_role(raw, m.start(), clause)
                rows.append(
                    {
                        "component": "芯片/模组",
                        "brand": brand,
                        "model": raw,
                        "qty_hint": "1",
                        "side": _clause_side(side_seg, clause),
                        "role": role,
                        "evidence": {
                            "value": raw,
                            "confidence": 0.85,
                            "source_type": "summary_prose",
                            "source_text": clause.strip()[:300],
                        },
                        "evidence_text": clause.strip()[:300],
                        "confidence": 0.85,
                        "_canonical": canonical,
                    }
                )
    return rows


def _extract_batteries(segment: str, side_seg: str) -> list[dict]:
    """抽电池行：「WEL旭航诚3.7V/600mAh锂电池」「3.85V/55mAh软包扣式电池」。"""
    rows: list[dict] = []
    for clause in _CLAUSE_SPLIT_RE.split(segment):
        if not clause or "电池" not in clause:
            continue
        cap_m = _BATT_CAPACITY_RE.search(clause)
        cap_only_m = _BATT_CAPACITY_ONLY_RE.search(clause) if not cap_m else None
        if not cap_m and not cap_only_m:
            continue
        cap = cap_m.group(2) if cap_m else cap_only_m.group(1)
        volt = cap_m.group(1) if cap_m else ""
        vendor_m = _BATT_VENDOR_RE.search(clause)
        brand = vendor_m.group(1) if vendor_m else ""
        # 形态描述：软包扣式 / 钢壳扣式 / 锂电池
        form = ""
        if "软包" in clause and "扣式" in clause:
            form = "软包扣式"
        elif "钢壳" in clause and "扣式" in clause:
            form = "钢壳扣式"
        elif "锂" in clause:
            form = "锂电池"
        model = f"{volt}V/{cap}mAh{form}" if volt else f"{cap}mAh{form}"
        rows.append(
            {
                "component": "电池",
                "brand": brand,
                "model": model,
                "qty_hint": "1",
                "side": _clause_side(side_seg, clause),
                "role": "major",
                "evidence": {
                    "value": model,
                    "confidence": 0.85,
                    "source_type": "summary_prose",
                    "source_text": clause.strip()[:300],
                },
                "evidence_text": clause.strip()[:300],
                "confidence": 0.85,
            }
        )
    return rows


def _extract_speakers(segment: str, side_seg: str) -> list[dict]:
    """抽喇叭单元行：「11.8mm对称双单元」「三磁路黄金微晶振膜动圈单元」。"""
    rows: list[dict] = []
    for clause in _CLAUSE_SPLIT_RE.split(segment):
        if not clause:
            continue
        size_m = _SPEAKER_RE.search(clause)
        kw_m = _SPEAKER_KW_RE.search(clause)
        if not size_m and not kw_m:
            continue
        size = size_m.group(1) + "mm" if size_m else ""
        form = kw_m.group(1) if kw_m else "动圈单元"
        model = f"{size} {form}".strip()
        rows.append(
            {
                "component": "喇叭单元",
                "brand": "",
                "model": model,
                "qty_hint": "1",
                "side": _clause_side(side_seg, clause) or "耳机",
                "role": "major",
                "evidence": {
                    "value": model,
                    "confidence": 0.8,
                    "source_type": "summary_prose",
                    "source_text": clause.strip()[:300],
                },
                "evidence_text": clause.strip()[:300],
                "confidence": 0.8,
            }
        )
    return rows


def _extract_microphones(segment: str, side_seg: str) -> list[dict]:
    """抽麦克风行：「内置两颗MEMS麦克风」「双MEMS麦克风+加速度传感器」。"""
    rows: list[dict] = []
    seen = False
    for clause in _CLAUSE_SPLIT_RE.split(segment):
        if not clause:
            continue
        m = _MIC_RE.search(clause)
        if not m or seen:
            continue
        qty_word = m.group(1) or ""
        qty = _MIC_QTY_MAP.get(qty_word, "")
        is_mems = "MEMS" in clause.upper()
        model = ("MEMS" if is_mems else "") + "麦克风"
        rows.append(
            {
                "component": "麦克风",
                "brand": "",
                "model": model,
                "qty_hint": qty,
                "side": _clause_side(side_seg, clause) or "耳机",
                "role": "major",
                "evidence": {
                    "value": model,
                    "confidence": 0.78,
                    "source_type": "summary_prose",
                    "source_text": clause.strip()[:300],
                },
                "evidence_text": clause.strip()[:300],
                "confidence": 0.78,
            }
        )
        seen = True  # 同段只收一条麦克风行，避免重复
    return rows


def extract_bom_from_prose(summary_text: str) -> list[dict]:
    """从总结段纯文本抽 BOM 行。

    返回行 schema（与 `views.cost.bom_table` 一致，额外多带 evidence_text /
    confidence 顶层字段，便于下游合并去重；合并入 bom_table 时可只取标准字段）：

        [{
          "component": "芯片/模组" | "电池" | "喇叭单元" | "麦克风",
          "brand": "INJOINIC英集芯",
          "model": "IP5528",
          "qty_hint": "1",
          "side": "充电盒" | "耳机" | "",
          "role": "主控/蓝牙" | "PMIC/充电仓管理" | "major" | ...,
          "evidence": {value, confidence, source_type, source_text},
          "evidence_text": "...",
          "confidence": 0.85,
        }, ...]
    """
    if not summary_text:
        return []

    rows: list[dict] = []
    for side_seg, seg_text in _split_into_segments(summary_text):
        rows.extend(_extract_chips(seg_text, side_seg))
        rows.extend(_extract_batteries(seg_text, side_seg))
        rows.extend(_extract_speakers(seg_text, side_seg))
        rows.extend(_extract_microphones(seg_text, side_seg))

    # 去重：同 (component, canonical_model, side) 只保留 confidence 最高的一条
    deduped: list[dict] = []
    seen: dict[tuple, int] = {}
    for row in rows:
        canon = row.pop("_canonical", "") if row.get("component") == "芯片/模组" else re.sub(
            r"\s+", "", row.get("model", "")
        ).upper()
        key = (row.get("component", ""), canon, row.get("side", ""))
        if key in seen:
            idx = seen[key]
            if row["confidence"] > deduped[idx]["confidence"]:
                deduped[idx] = row
            continue
        seen[key] = len(deduped)
        deduped.append(row)

    return deduped
