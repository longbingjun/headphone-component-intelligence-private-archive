"""渠道层：CSV 导入 + 京东直连 + ZOL 报价溯源。"""

from sources.base import SourceDescriptor

CHANNEL_SOURCE = SourceDescriptor("channel_multi", "channel", "电商渠道（ZOL/京东/CSV）", active=True)
