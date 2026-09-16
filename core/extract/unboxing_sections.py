"""从 52audio 拆解正文 HTML 提取开箱三区：包装 / 充电盒 / 耳机（图文对齐）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.extract.text_utils import Block, parse_content_blocks

_MODULE_PACKAGING = "packaging"
_MODULE_CHARGING_CASE = "charging_case"
_MODULE_EARBUDS = "earbuds"

_HEADING_UNBOX = re.compile(r"开箱|包装|配件一览")
_HEADING_TEARDOWN = re.compile(r"拆解|内部结构|主板")
_HEADING_CASE = re.compile(r"充电盒")
_HEADING_EARBUD = re.compile(r"耳机")
_HEADING_SUMMARY = re.compile(r"总结|物料清单")

_PACKAGING_KW = (
    "包装盒",
    "包装",
    "开箱",
    "配件",
    "说明书",
    "耳塞",
    "会员卡",
    "天地盖",
    "随机标配",
)
_CASE_KW = (
    "充电盒",
    "座舱",
    "仓盖",
    "充电仓",
    "仓体",
    "无线充电",
    "Type-C",
    "指示灯",
    "转轴",
)
_EARBUD_KW = (
    "耳机",
    "入耳",
    "耳柄",
    "出音嘴",
    "拾音孔",
    "压感",
    "L/R",
    "佩戴",
)

_ACCESSORY_RE = re.compile(
    r"(?:\u5305\u62ec|\u542b\u6709|\u6807\u914d|\u9644\u5e26|\u53d6\u51fa\u5185\u90e8\u6240\u6709\u7269\u54c1[，,]\u5305\u62ec\u4e86)([^\u3002\uff1b]+?)(?:\u7b49|\u3002)"
)
_ACCESSORY_SKIP = re.compile(r"^(?:了|的|以及|和|与|及|总共|能够|可以|不同|用户|需求|规格|一副|三副)")
_INTRO_END = re.compile(r"下面就来看看|详细拆解|拆解报告")


@dataclass
class ImageItem:
    url: str
    alt: str = ""
    caption: str = ""
    module: str = ""
    phase: str = ""  # intro | unboxing | teardown | summary

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "alt": self.alt,
            "caption": self.caption,
            "module": self.module,
            "phase": self.phase,
        }


@dataclass
class ModuleSection:
    module: str
    description: str = ""
    images: list[ImageItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    accessories: list[str] = field(default_factory=list)
    bom_side: str = ""  # 充电盒 | 耳机

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "description": self.description,
            "images": [i.to_dict() for i in self.images],
            "notes": self.notes,
            "accessories": self.accessories,
            "bom_side": self.bom_side,
        }


def _score_module(text: str) -> str:
    t = text or ""
    pkg = sum(1 for kw in _PACKAGING_KW if kw in t)
    case = sum(1 for kw in _CASE_KW if kw in t)
    bud = sum(1 for kw in _EARBUD_KW if kw in t)
    # 充电盒座舱里的耳机触点，偏充电盒
    if "座舱" in t and "耳机" in t and "充电触点" in t:
        case += 2
        bud -= 1
    if pkg >= case and pkg >= bud and pkg > 0:
        return _MODULE_PACKAGING
    if case >= bud and case > 0:
        return _MODULE_CHARGING_CASE
    if bud > 0:
        return _MODULE_EARBUDS
    return ""


def _phase_from_heading(heading: str) -> str:
    h = heading or ""
    if _HEADING_SUMMARY.search(h):
        return "summary"
    if _HEADING_CASE.search(h) and _HEADING_TEARDOWN.search(h):
        return "teardown"
    if _HEADING_EARBUD.search(h) and _HEADING_TEARDOWN.search(h):
        return "teardown"
    if _HEADING_TEARDOWN.search(h):
        return "teardown"
    if _HEADING_UNBOX.search(h):
        return "unboxing"
    return ""


def _module_from_heading(heading: str, phase: str) -> str:
    h = heading or ""
    if _HEADING_CASE.search(h):
        return _MODULE_CHARGING_CASE
    if _HEADING_EARBUD.search(h) and "开箱" not in h:
        return _MODULE_EARBUDS
    if phase == "unboxing" and _HEADING_UNBOX.search(h):
        return _MODULE_PACKAGING
    return ""


def _parse_accessories(text: str) -> list[str]:
    out: list[str] = []
    for m in _ACCESSORY_RE.finditer(text):
        chunk = m.group(1)
        parts = re.split(r"[、，,和及]", chunk)
        for p in parts:
            p = p.strip()
            if len(p) < 2 or _ACCESSORY_SKIP.search(p):
                continue
            if p not in out:
                out.append(p)
    return out


def _pick_appearance_images(images: list[ImageItem], limit: int = 12) -> list[dict]:
    """开箱段配图（phase=unboxing），用于外观展示。"""
    picked = [i.to_dict() for i in images if i.phase == "unboxing"]
    return picked[:limit]


def _intro_selling_points(blocks: list[Block]) -> list[dict]:
    """开箱段之前的导语，常含官方卖点（音质/佩戴/降噪）。"""
    from core.views.role_extract import _tag_selling_point, make_evidence

    points: list[dict] = []
    for b in blocks:
        if b.kind == "heading" and _HEADING_UNBOX.search(b.text):
            break
        if b.kind != "paragraph":
            continue
        if _INTRO_END.search(b.text):
            break
        if len(b.text) < 40:
            continue
        tag = _tag_selling_point(b.text)
        if tag == "其他" and not any(k in b.text for k in ("音质", "降噪", "佩戴", "续航", "星闪", "蓝牙")):
            continue
        points.append(
            {
                "text": b.text,
                "tag": tag,
                "evidence": make_evidence(b.text, b.text, source_type="intro", confidence=0.72),
            }
        )
    return points[:6]


def extract_unboxing_sections(content_html: str) -> dict:
    """
    返回:
    {
      intro_selling_points: [...],
      packaging: { module, description, images, notes, accessories },
      charging_case: {...},
      earbuds: {...},
      gaps: [...]   # 未能从 52audio 正文获得的字段说明
    }
    """
    blocks = parse_content_blocks(content_html)
    intro_points = _intro_selling_points(blocks)

    sections: dict[str, ModuleSection] = {
        _MODULE_PACKAGING: ModuleSection(module=_MODULE_PACKAGING, bom_side=""),
        _MODULE_CHARGING_CASE: ModuleSection(module=_MODULE_CHARGING_CASE, bom_side="充电盒"),
        _MODULE_EARBUDS: ModuleSection(module=_MODULE_EARBUDS, bom_side="耳机"),
    }

    phase = "intro"
    active_module = _MODULE_PACKAGING
    unboxing_stage = ""  # packaging | case | earbuds（仅 unboxing 段内）
    last_para = ""

    for b in blocks:
        if b.kind == "heading":
            hp = _phase_from_heading(b.text)
            hm = _module_from_heading(b.text, hp or phase)
            if hp:
                phase = hp
                if hp == "unboxing":
                    unboxing_stage = "packaging"
                    active_module = _MODULE_PACKAGING
                    last_para = ""
            if hm:
                active_module = hm
            elif phase == "unboxing" and _HEADING_UNBOX.search(b.text):
                active_module = _MODULE_PACKAGING
                unboxing_stage = "packaging"
                last_para = ""
            continue

        if b.kind == "paragraph":
            last_para = b.text
            if phase == "summary":
                continue
            if phase == "unboxing":
                if unboxing_stage == "packaging" and any(k in b.text for k in _CASE_KW[:3]):
                    unboxing_stage = "case"
                elif unboxing_stage in ("packaging", "case") and any(
                    k in b.text for k in ("耳机整体外观", "耳机外侧", "耳机内侧", "柄状的入耳")
                ):
                    unboxing_stage = "earbuds"
                stage_map = {
                    "packaging": _MODULE_PACKAGING,
                    "case": _MODULE_CHARGING_CASE,
                    "earbuds": _MODULE_EARBUDS,
                }
                active_module = stage_map.get(unboxing_stage, _MODULE_PACKAGING)
            mod = _score_module(b.text) or active_module
            if phase == "unboxing":
                active_module = mod if _score_module(b.text) else active_module
            sec = sections[mod]
            if b.text not in sec.notes:
                sec.notes.append(b.text)
            for acc in _parse_accessories(b.text):
                if acc not in sec.accessories and mod == _MODULE_PACKAGING:
                    sections[_MODULE_PACKAGING].accessories.append(acc)
            # 用首段外观描述作 module summary
            if not sec.description and len(b.text) >= 20:
                if mod == active_module or phase == "unboxing":
                    sec.description = b.text
            continue

        if b.kind == "image" and b.img_url:
            if phase == "summary":
                continue
            if re.search(r"logo|qrcode|二维码", b.img_alt + b.text + last_para, re.I):
                continue
            mod = active_module
            if phase == "unboxing":
                mod = active_module
            elif phase == "teardown":
                mod = _score_module(f"{last_para} {b.img_alt} {b.text}") or active_module
                if _HEADING_CASE.search(last_para) or any(k in last_para for k in _CASE_KW[:4]):
                    mod = _MODULE_CHARGING_CASE
                elif any(k in last_para for k in ("耳机", "耳柄", "出音嘴")):
                    mod = _MODULE_EARBUDS
            else:
                mod = _score_module(f"{last_para} {b.img_alt} {b.text}") or active_module
            caption = last_para or b.text or b.img_alt
            item = ImageItem(
                url=b.img_url.strip(),
                alt=b.img_alt,
                caption=caption,
                module=mod,
                phase=phase,
            )
            sec = sections[mod]
            if not any(i.url == item.url for i in sec.images):
                sec.images.append(item)

    gaps: list[str] = []
    if not sections[_MODULE_PACKAGING].images:
        gaps.append("包装区：正文未匹配到包装图片（可能文章结构特殊）")
    if not sections[_MODULE_CHARGING_CASE].images:
        gaps.append("充电盒：无配图（可能仅有文字描述）")
    if not sections[_MODULE_EARBUDS].images:
        gaps.append("耳机：无配图")
    if not sections[_MODULE_PACKAGING].accessories:
        gaps.append("包装配件清单：未能从「包括…等」句式解析出零件列表")
    if not intro_points:
        gaps.append("导语卖点：开箱前无足够长的功能介绍段落")

    result = {
        "intro_selling_points": intro_points,
        "packaging": sections[_MODULE_PACKAGING].to_dict(),
        "charging_case": sections[_MODULE_CHARGING_CASE].to_dict(),
        "earbuds": sections[_MODULE_EARBUDS].to_dict(),
        "gaps": gaps,
    }
    for key, mod in (
        ("packaging", _MODULE_PACKAGING),
        ("charging_case", _MODULE_CHARGING_CASE),
        ("earbuds", _MODULE_EARBUDS),
    ):
        imgs = sections[mod].images
        result[key]["appearance_images"] = _pick_appearance_images(imgs)
        result[key]["teardown_image_count"] = sum(1 for i in imgs if i.phase == "teardown")
    return result
