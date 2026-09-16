"""情报源抽象基类。

新增一个情报网站时，只需要继承 BaseSource 并实现:
  - fetch_list(limit)  -> 返回若干条"列表条目"（轻量字典即可，不强制结构）
  - parse_detail(item) -> 把列表条目解析/补全成 TeardownReport 或 VideoItem

core/pipeline.py 只依赖这两个方法和 source_id 属性，不关心具体网站的
HTML 结构、RSS 字段名等细节，这些都封装在各自的 sources/<name>/ 实现里。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Union

from core.models import TeardownReport, VideoItem


class BaseSource(ABC):
    """所有情报源必须实现的统一接口。"""

    #: 情报源唯一标识，比如 "audio52"，会写入每条数据的 source_id 字段。
    source_id: str = "base"

    #: 人类可读的名称，用于日志/报告展示。
    display_name: str = "Base Source"

    @abstractmethod
    def fetch_list(self, limit: int = 30) -> Iterable[dict]:
        """获取"待处理条目"列表（不要求已经是完整数据模型）。

        返回的每个 dict 至少应该包含 url / title，具体字段由子类自行约定，
        并在 parse_detail 里消费。
        """
        raise NotImplementedError

    @abstractmethod
    def parse_detail(self, item: dict) -> Union[TeardownReport, VideoItem, None]:
        """把一个列表条目解析成统一的数据模型对象。

        返回 None 表示该条目应该被跳过（比如不属于本情报源关心的范畴）。
        """
        raise NotImplementedError
