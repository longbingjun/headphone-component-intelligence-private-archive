"""卖点/特色候选句抽取。

方法（启发式，量级不追求精确，能抽出候选句子并标注即可）：
1. 用简单规则把正文切成句子；
2. 用 jieba 分词，统计每句命中"卖点关键词库"（外部传入）的个数和具体词；
3. 用 snownlp 给每句打一个情感分（0~1，越高越正向/越像营销话术）；
4. 综合得分 = 命中关键词数 * 2 + 情感分，取 top N 作为候选卖点，
   并保留 matched_keywords 和 sentiment 供人工复核。

这个模块不关心关键词库内容从哪来，只接收一个 list[str]，
真正的关键词表放在 sources/audio52/lexicon.py，方便按站点/品类调整。
"""

from __future__ import annotations

from core.extract.text_utils import split_sentences
from core.models import SellingPoint

try:
    from snownlp import SnowNLP

    _SNOWNLP_OK = True
except Exception:  # pragma: no cover - 极端环境下 snownlp 不可用时优雅降级
    _SNOWNLP_OK = False


def _sentiment_score(text: str) -> float | None:
    if not _SNOWNLP_OK:
        return None
    try:
        return round(float(SnowNLP(text).sentiments), 3)
    except Exception:
        return None


def _sentiment_label(score: float | None) -> str:
    if score is None:
        return "neutral"
    if score >= 0.65:
        return "positive"
    if score <= 0.35:
        return "negative"
    return "neutral"


def extract_selling_points(
    plain_text: str,
    keyword_lexicon: list[str],
    top_n: int = 8,
) -> list[SellingPoint]:
    """从纯文本里抽取候选卖点句。"""

    sentences = split_sentences(plain_text)
    if not sentences:
        return []

    scored: list[tuple[float, SellingPoint]] = []
    seen: set[str] = set()
    for sent in sentences:
        if sent in seen:
            continue
        seen.add(sent)
        matched = [kw for kw in keyword_lexicon if kw in sent]
        if not matched:
            continue
        sentiment = _sentiment_score(sent)
        score = len(matched) * 2 + (sentiment or 0.5)
        scored.append(
            (
                score,
                SellingPoint(
                    text=sent,
                    matched_keywords=matched,
                    sentiment=sentiment,
                    sentiment_label=_sentiment_label(sentiment),
                ),
            )
        )

    scored.sort(key=lambda x: x[0], reverse=True)
    return [sp for _, sp in scored[:top_n]]
