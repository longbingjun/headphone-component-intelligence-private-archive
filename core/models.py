"""统一数据模型定义。

所有情报源（sources/xxx）在解析详情页之后，都必须把结果组装成本文件里定义的
dataclass，这样核心流程与 Astro 发布数据生成器（scripts/prepare_web_data.py）
就完全不需要关心某条数据具体来自哪个网站。

设计取舍：
- 用标准库 dataclass 而不是 pydantic，减少依赖，方便在没有网络的 CI 环境里跑通。
- 所有字段都以"能安全序列化成 JSON"为前提（str / int / float / bool / list / dict）。
- 允许字段缺省为 None 或空列表：真实抓取到的文章不一定每个字段都能抽取出来，
  宁可留空也不要瞎猜。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Optional


def _as_dict(obj: Any) -> Any:
    """把 dataclass（含嵌套 dataclass/list）递归转换成可以 json.dump 的普通结构。"""
    if dataclasses.is_dataclass(obj):
        return {k: _as_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_as_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _as_dict(v) for k, v in obj.items()}
    return obj


@dataclass
class SellingPoint:
    """从正文中抽取出来的"卖点/特色"候选句。"""

    text: str
    matched_keywords: list[str] = field(default_factory=list)
    sentiment: Optional[float] = None  # 0~1，越接近1越正向（snownlp）
    sentiment_label: str = "neutral"  # positive / neutral / negative


@dataclass
class ComponentMention:
    """某个部件在正文中的一处相关描述片段。"""

    text: str
    heading: Optional[str] = None  # 该片段所属的 H2/H3/H4 小节标题（如果能定位到）
    image_index: Optional[int] = None  # 该片段附近关联的图片序号（在 images 列表里的下标）


@dataclass
class ComponentInfo:
    """识别出来的一个部件（喇叭单元/电池/主板等）。"""

    name: str
    importance: str = "minor"  # major / minor
    mentions: list[ComponentMention] = field(default_factory=list)


@dataclass
class ImageAsset:
    """文章中的一张图片，及其"文本密集型 vs 图像密集型"判断结果与 OCR 状态。"""

    index: int
    url: str
    alt: str = ""
    caption: str = ""
    # 启发式判断依据
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: Optional[float] = None
    edge_density: Optional[float] = None
    classification: str = "unknown"  # text_dense / image_dense / unknown
    classification_reason: str = ""
    # OCR 相关（框架先行：先建立队列，真正的引擎在下一阶段接入）
    ocr_status: str = "not_applicable"  # pending / done / skipped / not_applicable / failed
    ocr_engine: Optional[str] = None
    ocr_text: Optional[str] = None


@dataclass
class TechSpecs:
    """技术数据/规格信息。来源可能是正文文字，也可能是产品标签图片（OCR 队列）。"""

    charging_method: list[str] = field(default_factory=list)
    charging_port: list[str] = field(default_factory=list)
    manual_notes: list[str] = field(default_factory=list)
    product_markings: list[str] = field(default_factory=list)
    raw_candidates: list[str] = field(default_factory=list)  # 未能归类、但疑似技术参数的原句


@dataclass
class TeardownReport:
    """一篇"拆解报告"（或拆解报告形态的文章）。"""

    id: str
    source_id: str
    type: str  # "report"
    url: str
    title: str
    summary: str
    date: str  # ISO 8601, e.g. 2026-07-01
    author: str = ""
    brand: str = ""
    model: str = ""
    category: str = "其他音频设备"
    raw_categories: list[str] = field(default_factory=list)
    content_html: str = ""
    cover_image: str = ""
    images: list[ImageAsset] = field(default_factory=list)
    selling_points: list[SellingPoint] = field(default_factory=list)
    components_major: list[ComponentInfo] = field(default_factory=list)
    components_minor: list[ComponentInfo] = field(default_factory=list)
    tech_specs: TechSpecs = field(default_factory=TechSpecs)
    has_video_embed: bool = False
    video_embed_urls: list[str] = field(default_factory=list)
    crawled_at: str = ""
    first_seen_at: str = ""  # 首次被本系统抓到的时间，用于未来的"时间线/变更历史"功能

    def to_dict(self) -> dict:
        return _as_dict(self)


@dataclass
class VideoItem:
    """一条"拆解视频"记录（今天的范围较小的次级子页面）。"""

    id: str
    source_id: str
    type: str  # "video"
    url: str
    title: str
    product_title: str
    publisher: str = ""
    date: str = ""
    source_site: str = ""  # 视频实际托管平台，如 bilibili / youtube
    video_embed_url: str = ""
    summary: str = ""
    brand: str = ""
    model: str = ""
    category: str = "其他音频设备"
    raw_categories: list[str] = field(default_factory=list)
    crawled_at: str = ""
    first_seen_at: str = ""

    def to_dict(self) -> dict:
        return _as_dict(self)
