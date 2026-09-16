"""产品结构理解：从标题层级 + 图片说明 + 正文描述里识别"主要部件/次要部件"。

52audio 拆解报告的典型写作套路是：
  <h4>四、XXX部件</h4>
  <figure><img ...></figure>
  <p>描述这张图/这个部件的文字...</p>

所以识别策略是：
1. 顺序扫描 Block（heading/paragraph/image），维护"当前小节标题"和"最近一张图片下标"；
2. 对每个 paragraph，用部件关键词库（外部传入）做子串匹配；
3. 命中就记一条 ComponentMention，携带所在小节标题和最近图片下标，方便详情页把
   文字描述和图片对应起来；
4. 标题本身命中关键词，也作为一次"高权重"证据（很多分节标题直接就是部件名，
   例如"三、喇叭单元解析"）。

部件的 major/minor 归类同样由外部关键词库决定，不写死在这里，
方便以后针对不同品类（耳机 vs 音箱）调整。
"""

from __future__ import annotations

from core.extract.text_utils import Block, parse_content_blocks
from core.models import ComponentInfo, ComponentMention


def extract_components(
    content_html: str,
    component_lexicon: dict[str, dict],
) -> tuple[list[ComponentInfo], list[ComponentInfo]]:
    """返回 (主要部件列表, 次要部件列表)。

    component_lexicon 形如：
        {
          "喇叭单元": {"importance": "major", "aliases": ["喇叭单元", "动圈单元", "动铁单元", "扬声器"]},
          "耳塞套": {"importance": "minor", "aliases": ["耳塞套", "耳帽", "硅胶套"]},
          ...
        }
    """

    blocks: list[Block] = parse_content_blocks(content_html)
    found: dict[str, ComponentInfo] = {}

    current_heading: str | None = None
    last_image_index: int | None = None

    def get_or_create(name: str, importance: str) -> ComponentInfo:
        if name not in found:
            found[name] = ComponentInfo(name=name, importance=importance, mentions=[])
        return found[name]

    for block in blocks:
        if block.kind == "heading":
            current_heading = block.text
            # 标题本身直接命中部件名，也算一次证据（不带正文片段，只做标记）
            for name, meta in component_lexicon.items():
                aliases = meta.get("aliases", [name])
                if any(alias in block.text for alias in aliases):
                    comp = get_or_create(name, meta.get("importance", "minor"))
                    comp.mentions.append(
                        ComponentMention(text=block.text, heading=current_heading, image_index=None)
                    )
        elif block.kind == "image":
            last_image_index = block.img_index
        elif block.kind == "paragraph":
            for name, meta in component_lexicon.items():
                aliases = meta.get("aliases", [name])
                if any(alias in block.text for alias in aliases):
                    comp = get_or_create(name, meta.get("importance", "minor"))
                    comp.mentions.append(
                        ComponentMention(
                            text=block.text,
                            heading=current_heading,
                            image_index=last_image_index,
                        )
                    )

    major = [c for c in found.values() if c.importance == "major"]
    minor = [c for c in found.values() if c.importance != "major"]
    major.sort(key=lambda c: len(c.mentions), reverse=True)
    minor.sort(key=lambda c: len(c.mentions), reverse=True)
    return major, minor
