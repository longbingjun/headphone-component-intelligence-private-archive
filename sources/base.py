"""V4 四层信源元数据（与 core.base_source 爬虫接口并存）。

core.base_source：v1/v2 爬虫 fetch_list/parse_detail 接口
sources.registry：信源层注册 + 融合优先级
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SourceLayer = Literal["official", "channel", "review", "technical"]

LAYER_PRIORITY: dict[str, int] = {
    "technical": 4,
    "official": 3,
    "channel": 2,
    "review": 1,
}


@dataclass
class SourceDescriptor:
    source_id: str
    source_layer: SourceLayer
    display_name: str
    active: bool = True

    def layer_priority(self) -> int:
        return LAYER_PRIORITY.get(self.source_layer, 0)
