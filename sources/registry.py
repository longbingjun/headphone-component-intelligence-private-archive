"""信源注册表：技术层（52audio）+ 渠道/官方/评测占位。"""

from __future__ import annotations

from sources.base import SourceDescriptor
from sources.channel.jd_import import CHANNEL_SOURCE
from sources.channel.jd_client import fetch_jd_price, search_jd  # noqa: F401
from sources.channel.zol_client import fetch_zol_prices, search_zol_products  # noqa: F401
from sources.official import OFFICIAL_SOURCE
from sources.review import REVIEW_SOURCE

_REGISTRY: dict[str, SourceDescriptor] = {
    "audio52": SourceDescriptor("audio52", "technical", "我爱音频网 52audio 拆解", active=True),
    CHANNEL_SOURCE.source_id: CHANNEL_SOURCE,
    OFFICIAL_SOURCE.source_id: OFFICIAL_SOURCE,
    REVIEW_SOURCE.source_id: REVIEW_SOURCE,
}


def get(source_id: str) -> SourceDescriptor | None:
    return _REGISTRY.get(source_id)


def all_descriptors() -> list[SourceDescriptor]:
    return list(_REGISTRY.values())


def by_layer(layer: str) -> list[SourceDescriptor]:
    return [d for d in _REGISTRY.values() if d.source_layer == layer and d.active]
