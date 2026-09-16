"""v2 数据模型：按领域区块 views 存储，不含 content_html。"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Optional


def _as_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {k: _as_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_as_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _as_dict(v) for k, v in obj.items()}
    return obj


@dataclass
class MarketView:
    brand: str = ""
    model: str = ""
    category: str = ""
    launch_date: Optional[str] = None
    price_cny: Optional[float] = None
    price_note: str = ""
    selling_points: list[dict] = field(default_factory=list)
    # 面向使用者的产品收益/体验主张。技术器件与参数事实不进入此列表，
    # 而由 CostView.teardown_inventory 独立承载。
    consumer_claims: list[dict] = field(default_factory=list)
    scenarios: list[str] = field(default_factory=list)
    positioning_summary: str = ""
    launch_date_evidence: Optional[dict] = None
    price_evidence: Optional[dict] = None


@dataclass
class CostView:
    major_parts: list[str] = field(default_factory=list)
    chip_modules: list[dict] = field(default_factory=list)
    bom_table: list[dict] = field(default_factory=list)
    supply_hints: list[dict] = field(default_factory=list)
    packaging_notes: list[str] = field(default_factory=list)
    process_hints: list[dict] = field(default_factory=list)
    # V3 Phase1：总结段「我爱音频网总结」PNG 物料表 URL（专用，区别于 structure.key_image_urls）
    summary_image_urls: list[dict] = field(default_factory=list)
    summary_text: str = ""
    # Evidence-first teardown inventory. Classification is functional and does
    # not contain cost, version, left/right part-number or batch weighting.
    teardown_inventory: list[dict] = field(default_factory=list)


@dataclass
class StructureView:
    form_factor: str = ""
    earbud_type: str = ""
    materials: list[str] = field(default_factory=list)
    ip_rating: Optional[str] = None
    weight_g: Optional[str] = None  # 整机/总重（头戴式等单一形态直接用此字段）
    dimensions: list[str] = field(default_factory=list)
    internal_structure: list[dict] = field(default_factory=list)
    fastener_type: list[dict] = field(default_factory=list)
    sealing_method: list[dict] = field(default_factory=list)
    wear_design: list[dict] = field(default_factory=list)
    key_image_urls: list[dict] = field(default_factory=list)
    # Every original report image, retained for internal MinIO evidence storage.
    report_image_urls: list[dict] = field(default_factory=list)
    assembly_notes: list[str] = field(default_factory=list)
    ip_rating_evidence: Optional[dict] = None
    weight_evidence: Optional[dict] = None
    # TWS/开放式/耳夹式：整机重量之外再拆出充电盒/单只耳机重量
    weight_case_g: Optional[str] = None
    weight_case_evidence: Optional[dict] = None
    weight_earbud_g: Optional[str] = None
    weight_earbud_evidence: Optional[dict] = None


@dataclass
class HardwareView:
    specs: list[dict] = field(default_factory=list)


@dataclass
class SoftwareView:
    bluetooth_version: Optional[str] = None
    codecs: list[dict] = field(default_factory=list)
    multipoint: list[dict] = field(default_factory=list)
    app_name: str = ""
    app_features: list[dict] = field(default_factory=list)
    ota_support: list[dict] = field(default_factory=list)
    latency_notes: list[dict] = field(default_factory=list)
    bluetooth_evidence: Optional[dict] = None


@dataclass
class RoleViews:
    market: MarketView = field(default_factory=MarketView)
    cost: CostView = field(default_factory=CostView)
    structure: StructureView = field(default_factory=StructureView)
    hardware: HardwareView = field(default_factory=HardwareView)
    software: SoftwareView = field(default_factory=SoftwareView)

    def to_dict(self) -> dict:
        return _as_dict(self)


@dataclass
class ReportRecord:
    id: str
    type: str = "report"
    source_layer: str = "technical"  # official|channel|review|technical
    source_id: str = "audio52"
    url: str = ""
    title: str = ""
    brand: str = ""
    model: str = ""
    category: str = ""
    published_at: str = ""
    author: str = ""
    summary: str = ""
    captured_at: str = ""
    data_completeness: float = 0.0
    views: RoleViews = field(default_factory=RoleViews)

    def to_dict(self) -> dict:
        d = _as_dict(self)
        d["views"] = self.views.to_dict()
        return d


@dataclass
class VideoRecord:
    id: str
    type: str = "video"
    source_layer: str = "technical"
    source_id: str = "audio52"
    url: str = ""
    title: str = ""
    product_title: str = ""
    brand: str = ""
    model: str = ""
    category: str = ""
    published_at: str = ""
    publisher: str = ""
    summary: str = ""
    source_site: str = ""
    video_embed_url: str = ""
    captured_at: str = ""
    data_completeness: float = 0.0
    views: RoleViews = field(default_factory=RoleViews)

    def to_dict(self) -> dict:
        d = _as_dict(self)
        d["views"] = self.views.to_dict()
        return d
