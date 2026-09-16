"""情报网站 MVP 核心框架包。

core 子包只放"与具体情报源无关"的通用代码：
- models: 统一数据模型（dataclass）
- base_source: 情报源抽象基类
- pipeline: 抓取 -> 解析 -> 结构化抽取 -> 落盘 的统一编排逻辑
- extract: 详情页结构化信息抽取的各个子模块（卖点、部件、技术参数、图片分级）

新增一个情报网站时，只需要在 sources/ 下新增一个子模块实现 BaseSource，
不需要改动 core 里的任何代码。
"""
