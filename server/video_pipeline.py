"""Subtitle-first teardown-video processing without audio transcription.

The module is deliberately independent from the queue and object storage.  A
worker gives it one temporary video file and it emits an auditable artifact
directory.  Creator subtitle tracks are preferred; hard-subtitle OCR is used
only when no usable track exists.
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable


@dataclass
class SubtitleSegment:
    id: int
    start: float
    end: float
    raw_text: str
    confidence: float = 1.0
    samples: int = 1


def normalize_text(text: str) -> str:
    return re.sub(r"[\s，。！？；：、,.!?;:'\"‘’“”·—_-]+", "", text).lower()


def text_is_same(left: str, right: str) -> bool:
    left_norm, right_norm = normalize_text(left), normalize_text(right)
    if not left_norm or not right_norm:
        return False
    if left_norm in right_norm or right_norm in left_norm:
        return min(len(left_norm), len(right_norm)) / max(len(left_norm), len(right_norm)) >= 0.68
    return SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.76


def format_time(seconds: float) -> str:
    minutes, secs = divmod(max(0.0, seconds), 60)
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours:02d}:{minutes:02d}:{secs:05.2f}"


def _timestamp(value: str) -> float:
    parts = value.strip().replace(",", ".").split(":")
    if len(parts) == 2:
        parts.insert(0, "0")
    if len(parts) != 3:
        raise ValueError(f"unsupported subtitle timestamp: {value!r}")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _clean_caption(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{\\[^}]+}", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return " ".join(line.strip() for line in text.splitlines() if line.strip()).strip()


def _merge_adjacent(segments: list[SubtitleSegment]) -> list[SubtitleSegment]:
    merged: list[SubtitleSegment] = []
    for segment in sorted(segments, key=lambda item: (item.start, item.end)):
        if not normalize_text(segment.raw_text):
            continue
        if merged and segment.start <= merged[-1].end + 0.25 and text_is_same(
            merged[-1].raw_text, segment.raw_text
        ):
            previous = merged[-1]
            previous.end = max(previous.end, segment.end)
            if len(normalize_text(segment.raw_text)) > len(normalize_text(previous.raw_text)):
                previous.raw_text = segment.raw_text
            previous.samples += segment.samples
        else:
            merged.append(segment)
    for index, segment in enumerate(merged, 1):
        segment.id = index
    return merged


def parse_subtitle_file(path: Path) -> list[SubtitleSegment]:
    """Parse creator-provided VTT/SRT or Bilibili JSON captions."""

    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        body = payload.get("body", payload) if isinstance(payload, dict) else payload
        if not isinstance(body, list):
            raise ValueError(f"unsupported subtitle JSON: {path}")
        segments = [
            SubtitleSegment(
                index,
                float(item.get("from", item.get("start", 0))),
                float(item.get("to", item.get("end", 0))),
                _clean_caption(str(item.get("content", item.get("text", "")))),
            )
            for index, item in enumerate(body, 1)
            if isinstance(item, dict)
        ]
        return _merge_adjacent(segments)

    content = path.read_text(encoding="utf-8-sig", errors="replace")
    time_pattern = re.compile(
        r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[,.]\d{3})\s*-->\s*"
        r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[,.]\d{3})[^\r\n]*"
    )
    matches = list(time_pattern.finditer(content))
    segments: list[SubtitleSegment] = []
    for index, match in enumerate(matches, 1):
        body_start = match.end()
        body_end = matches[index].start() if index < len(matches) else len(content)
        body = re.sub(r"\r?\n\s*\d+\s*$", "", content[body_start:body_end].strip())
        segments.append(
            SubtitleSegment(
                index,
                _timestamp(match.group("start")),
                _timestamp(match.group("end")),
                _clean_caption(body),
            )
        )
    if not segments:
        raise ValueError(f"no timed captions found in {path}")
    return _merge_adjacent(segments)


def _segment_payload(segments: list[SubtitleSegment]) -> list[dict[str, Any]]:
    return [
        asdict(segment)
        | {"start_time": format_time(segment.start), "end_time": format_time(segment.end)}
        for segment in segments
    ]


def write_segments(segments: list[SubtitleSegment], output_dir: Path) -> None:
    payload = _segment_payload(segments)
    (output_dir / "subtitle_segments.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Kept during the migration so older publishers can consume new artifacts.
    (output_dir / "ocr_segments.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (output_dir / "subtitle_segments.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(payload[0]) if payload else ["id"])
        writer.writeheader()
        writer.writerows(payload)


def scan_hard_subtitles(
    video: Path,
    *,
    sample_interval: float,
    crop: tuple[float, float, float, float] = (0.15, 0.875, 0.78, 0.955),
    ocr_factory: Callable[[], Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[SubtitleSegment], dict[str, Any]]:
    """Read a bottom subtitle band at a fixed interval and merge OCR repeats."""

    import cv2
    import psutil
    from rapidocr_onnxruntime import RapidOCR

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {video}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps
    every_n = max(1, round(fps * sample_interval))
    ocr = (ocr_factory or RapidOCR)()
    process = psutil.Process()
    peak_rss = process.memory_info().rss
    active: dict[str, Any] | None = None
    segments: list[SubtitleSegment] = []
    frame_index = -1
    sampled = 0
    ocr_seconds = 0.0
    left, top, right, bottom = crop

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frame_index += 1
            if frame_index % every_n:
                continue
            timestamp = frame_index / fps
            height, width = frame.shape[:2]
            roi = frame[int(height * top) : int(height * bottom), int(width * left) : int(width * right)]
            if roi.size == 0:
                raise ValueError("hard-subtitle crop is outside the video frame")
            target_width = 1280
            target_height = max(32, int(roi.shape[0] * target_width / roi.shape[1]))
            roi = cv2.resize(roi, (target_width, target_height))
            started = time.perf_counter()
            result, _ = ocr(roi, use_det=False, use_cls=False, use_rec=True)
            ocr_seconds += time.perf_counter() - started
            sampled += 1
            peak_rss = max(peak_rss, process.memory_info().rss)

            text, confidence = "", 0.0
            if result and isinstance(result[0], (list, tuple)) and len(result[0]) >= 2:
                text, confidence = str(result[0][0]).strip(), float(result[0][1])
            if confidence < 0.60 or len(normalize_text(text)) < 2:
                text = ""

            if text:
                if active and text_is_same(str(active["text"]), text):
                    active["end"] = timestamp + sample_interval
                    active["samples"] += 1
                    active["misses"] = 0
                    if confidence > active["confidence"] or len(normalize_text(text)) > len(
                        normalize_text(str(active["text"]))
                    ):
                        active["text"], active["confidence"] = text, confidence
                else:
                    if active:
                        segments.append(
                            SubtitleSegment(
                                len(segments) + 1,
                                active["start"],
                                active["end"],
                                active["text"],
                                active["confidence"],
                                active["samples"],
                            )
                        )
                    active = {
                        "start": timestamp,
                        "end": timestamp + sample_interval,
                        "text": text,
                        "confidence": confidence,
                        "samples": 1,
                        "misses": 0,
                    }
            elif active:
                active["misses"] += 1
                if active["misses"] <= 1:
                    active["end"] = timestamp + sample_interval
                else:
                    segments.append(
                        SubtitleSegment(
                            len(segments) + 1,
                            active["start"],
                            active["end"],
                            active["text"],
                            active["confidence"],
                            active["samples"],
                        )
                    )
                    active = None
            if progress and sampled % 200 == 0:
                progress(f"OCR {timestamp:.1f}/{duration:.1f}s; segments={len(segments)}")
    finally:
        capture.release()

    if active:
        segments.append(
            SubtitleSegment(
                len(segments) + 1,
                active["start"],
                active["end"],
                active["text"],
                active["confidence"],
                active["samples"],
            )
        )
    segments = [item for item in segments if item.samples >= 2 or item.confidence >= 0.92]
    segments = _merge_adjacent(segments)
    return segments, {
        "fps": fps,
        "duration_seconds": duration,
        "frame_count": frame_count,
        "sample_interval_seconds": sample_interval,
        "ocr_samples": sampled,
        "ocr_compute_seconds": round(ocr_seconds, 2),
        "peak_rss_mb": round(peak_rss / 1024 / 1024, 1),
    }


FACT_KEYWORDS = tuple(
    "电压 电流 功率 容量 芯片 型号 麦克风 处理器 蓝牙 电池 扬声器 单元 材质 工艺 "
    "结构 尺寸 重量 防水 编码 协议 传感器 接口 续航 充电 制造商 生产商 供应商 "
    "包装 配件 外观 设计 佩戴 兼容 连接 音效".split()
)

FACT_SECTIONS = {
    "market",
    "unboxing_packaging",
    "unboxing_charging_case",
    "unboxing_earbuds",
    "specification",
    "bom",
}

_HEURISTIC_OPERATION_ONLY_RE = re.compile(
    r"^(?:打开|取出|拆掉|拆开|断开|卸掉|撕掉|进入|下面进入|首先拆解|"
    r"盖板内侧|支架正面|支架背面|座舱底部|腔体内部|外壳内侧|耳机后腔|"
    r"耳机充电盒|耳机主板电路|充电盒主板电路)[～~。！! ]*$"
)
_HEURISTIC_OPERATION_PREFIX_RE = re.compile(r"^(?:打开|取出|拆掉|拆开|断开|卸掉|撕掉)")
_HEURISTIC_STRONG_FACT_RE = re.compile(
    r"(?:型号|丝印|生产厂|生产商|制造商|供应商|来自|额定|标称|容量|电压|"
    r"功率|尺寸|重量|续航|防水|材质|振膜|蓝牙音频\s*(?:SoC|SOC)|"
    r"电源管理\s*(?:SoC|SOC|IC)|麦克风|扬声器|喇叭|电池|霍尔|连接器)",
    re.IGNORECASE,
)


def is_fact_candidate(segment: SubtitleSegment) -> bool:
    return bool(re.search(r"\d", segment.raw_text) or any(word in segment.raw_text for word in FACT_KEYWORDS))


def heuristic_events(segments: list[SubtitleSegment]) -> list[dict[str, Any]]:
    """Conservative, source-only fallback used when the LLM is unavailable.

    The fallback retains the narrative phase so its output can feed the same
    product-detail sections as report extraction.  It intentionally drops
    bare disassembly actions: a frame showing that a cover was removed is not
    useful procurement evidence unless the same caption also states a
    component, material, model or parameter.
    """

    phase = "unboxing_packaging"
    events: list[dict[str, Any]] = []
    for segment in sorted(segments, key=lambda item: (item.start, item.end, item.id)):
        text = segment.raw_text.strip()
        if re.search(r"(?:下面)?进入拆解|内部配置信息|首先拆解", text):
            phase = "bom"
        elif phase != "bom" and re.search(r"耳机整体外观|耳机采用了?耳(?:夹|挂|塞)", text):
            phase = "unboxing_earbuds"
        elif phase == "unboxing_packaging" and re.fullmatch(r"耳机充电盒[。！! ]*", text):
            phase = "unboxing_charging_case"

        if not is_fact_candidate(segment):
            continue
        if _HEURISTIC_OPERATION_ONLY_RE.search(text) and not _HEURISTIC_STRONG_FACT_RE.search(text):
            continue
        if _HEURISTIC_OPERATION_PREFIX_RE.search(text) and not (
            re.search(r"\d|型号|丝印|来自|生产厂|材质|额定|标称|容量|电压|功率", text)
            or _bom_model_tokens(text)
        ):
            continue

        section = phase
        if (
            segment.id <= 3
            and not re.search(r"包装|盒盖|内部物品|配件", text)
            and re.search(r"(?:产品名|耳机|型号)", text)
        ):
            section = "market"
        importance = 4 if _HEURISTIC_STRONG_FACT_RE.search(text) else 3
        if _bom_model_tokens(text) or re.search(r"生产厂|生产商|制造商|供应商|来自", text):
            importance = 5
        events.append(
            {
                "segment_id": segment.id,
                "corrected_text": text,
                "fact": text,
                "importance": importance,
                "needs_review": segment.confidence < 0.85,
                "section": section,
                "selection_reason": "local_source_only_fallback",
            }
        )
    return events


def _nearby_context(
    candidates: list[SubtitleSegment], source: SubtitleSegment, radius: int = 2
) -> list[SubtitleSegment]:
    """Return a short, contiguous subtitle window for split label evidence."""

    ordered = sorted(candidates, key=lambda item: (item.start, item.end, item.id))
    position = next((index for index, item in enumerate(ordered) if item.id == source.id), -1)
    if position < 0:
        return [source]
    left = position
    while left > 0 and position - left < radius:
        previous, current = ordered[left - 1], ordered[left]
        if current.start - previous.end > 5.0:
            break
        left -= 1
    right = position
    while right + 1 < len(ordered) and right - position < radius:
        current, following = ordered[right], ordered[right + 1]
        if following.start - current.end > 5.0:
            break
        right += 1
    return ordered[left : right + 1]


_BOM_MODEL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-]{4,})(?=[A-Za-z0-9-]*[A-Za-z])"
    r"(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{3,}(?![A-Za-z0-9])"
)
_BOM_CONTINUATION_RE = re.compile(
    r"^(?:型号|丝印|制造商|生产商|供应商|额定|标称|充电限制|工作频率|"
    r"生产厂|来自|源自|据.+来自|用于|负责|采用|容量|电压|电流|功率|阻抗|尺寸|"
    r"标.*电压|同时支持|支持|在电池|是一款|是(?:四|双|单)通道|最大|超高|"
    r"立体声|和立体声|内置.+放大器)"
)
_BOM_PARAMETER_TOKEN_RE = re.compile(
    r"^\d+(?:\.\d+)?(?:mAh|Ah|mWh|Wh|mV|V|mm|Hz|kHz|MHz|GHz|mW|W)$",
    re.IGNORECASE,
)


def _bom_model_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _BOM_MODEL_TOKEN_RE.finditer(text):
        value = match.group(0).strip("-_")
        if "cortex" in value.casefold() or _BOM_PARAMETER_TOKEN_RE.fullmatch(value):
            continue
        tokens.append(value)
    return tokens


def _bom_family(text: str) -> str:
    """Return a coarse component family used only to join split captions.

    This is intentionally narrower than the final BOM taxonomy.  Its job is to
    keep a label, model and parameter lines together without crossing into the
    next component described by the presenter.
    """

    compact = normalize_text(text)
    if re.search(r"过压|过流|保护ic|充电器前端保护|电池过压", compact, re.IGNORECASE):
        return "battery_protection"
    if re.search(r"麦克风放大器|接收器放大器", compact):
        return ""
    if re.search(r"无线音频(?:传输|接收|收发)|无线音频.*ic", compact, re.IGNORECASE):
        return "wireless_audio"
    if re.search(r"音频芯片|音频编解码", compact, re.IGNORECASE):
        return "audio_codec"
    if re.search(r"(?:存储器|储存器)", compact):
        return "memory"
    if re.search(r"(?:mcu|微控制器)", compact, re.IGNORECASE):
        return "mcu"
    if re.search(r"蓝牙音频\s*(?:soc|主控|芯片)", compact, re.IGNORECASE):
        return "bluetooth_audio_soc"
    if re.search(r"电源管理\s*(?:soc|ic|芯片)|可编程升压|升压ic", compact, re.IGNORECASE):
        return "power_management"
    if re.search(r"霍尔(?:元件|传感器)", compact):
        return "sensor"
    if "稳压器" in compact:
        return "regulator"
    if re.search(r"扬声器|喇叭", compact):
        return "speaker"
    if "麦克风" in compact:
        return "microphone"
    if "电池" in compact:
        return "battery"
    return ""


def _bom_identity_only(text: str) -> bool:
    compact = text.strip()
    return bool(
        _bom_model_tokens(compact)
        and re.search(r"^(?:这是|型号|丝印)|(?:制造商|生产商|供应商)", compact)
    )


def _bom_continuation(text: str) -> bool:
    return bool(_BOM_CONTINUATION_RE.search(text.strip()))


def _bom_context_text(candidates: list[SubtitleSegment], source: SubtitleSegment) -> str:
    """Join captions only within one component-description block.

    A fixed symmetric radius incorrectly joins adjacent components (for
    example a storage IC, wireless IC and ear-cup description).  The block
    below is anchored by a coarse component family and admits only same-family
    captions or clearly labelled continuations such as model, voltage and
    frequency lines.
    """

    ordered = sorted(candidates, key=lambda item: (item.start, item.end, item.id))
    position = next((index for index, item in enumerate(ordered) if item.id == source.id), -1)
    if position < 0:
        return source.raw_text.strip()

    family = _bom_family(source.raw_text)
    anchor = position
    if not family and _bom_identity_only(source.raw_text) and position + 1 < len(ordered):
        following = ordered[position + 1]
        if following.start - source.end <= 5.0:
            family = _bom_family(following.raw_text)
    if not family and (_bom_continuation(source.raw_text) or _bom_identity_only(source.raw_text)):
        for index in range(position - 1, max(-1, position - 6), -1):
            current, following = ordered[index], ordered[index + 1]
            if following.start - current.end > 5.0:
                break
            prior_family = _bom_family(current.raw_text)
            if prior_family:
                family, anchor = prior_family, index
                break
            if not (_bom_continuation(current.raw_text) or _bom_identity_only(current.raw_text)):
                break
    if not family:
        return source.raw_text.strip()

    # Find the first line in this component block.  A component line that
    # already contains a model is a strong boundary; only a directly preceding
    # identity-only line may belong to it (e.g. "恩智浦 LPC804" + "32位 MCU").
    left = anchor
    anchor_has_model = bool(_bom_model_tokens(ordered[anchor].raw_text))
    if not anchor_has_model:
        while left > 0 and anchor - left < 5:
            previous, current = ordered[left - 1], ordered[left]
            if current.start - previous.end > 5.0:
                break
            previous_family = _bom_family(previous.raw_text)
            if previous_family and previous_family != family:
                break
            if previous_family == family or _bom_identity_only(previous.raw_text):
                left -= 1
                continue
            break

    right = max(position, anchor)
    while right + 1 < len(ordered) and right - anchor < 6:
        current, following = ordered[right], ordered[right + 1]
        if following.start - current.end > 5.0:
            break
        following_family = _bom_family(following.raw_text)
        if following_family and following_family != family:
            break
        identity_only = _bom_identity_only(following.raw_text)
        if identity_only:
            next_family = ""
            if right + 2 < len(ordered):
                next_item = ordered[right + 2]
                if next_item.start - following.end <= 5.0:
                    next_family = _bom_family(next_item.raw_text)
            if next_family and next_family != family:
                break
        if following_family == family or _bom_continuation(following.raw_text) or identity_only:
            right += 1
            continue
        break

    return "；".join(
        dict.fromkeys(item.raw_text.strip() for item in ordered[left : right + 1] if item.raw_text.strip())
    )


def _json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        # Some OpenAI-compatible gateways wrap valid JSON in short prose.
        # Parse only the first JSON value instead of greedily slicing from the
        # first to the last brace, which breaks when a second object follows.
        first = cleaned.find("{")
        if first < 0:
            raise ValueError("model response did not contain a JSON object")
        value, _ = json.JSONDecoder().raw_decode(cleaned[first:])
    if not isinstance(value, dict):
        raise ValueError("model response root must be a JSON object")
    return value


def _message_text(message: Any) -> str:
    """Read text from standard and gateway-specific chat response fields."""

    candidates = [getattr(message, "content", None)]
    extra = getattr(message, "model_extra", None)
    for name in ("reasoning_content", "output_text", "reasoning", "analysis"):
        candidates.append(getattr(message, name, None))
        if isinstance(extra, dict):
            candidates.append(extra.get(name))
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def validate_events(
    events: list[dict[str, Any]], candidates: list[SubtitleSegment]
) -> list[dict[str, Any]]:
    """Constrain model output to source segments and flag numeric corrections."""

    by_id = {segment.id: segment for segment in candidates}
    ordered_sources = sorted(candidates, key=lambda segment: (segment.start, segment.end, segment.id))

    def narrative_side(source: SubtitleSegment, section: str) -> tuple[str, str]:
        if section != "bom":
            return "", ""
        side = ""
        evidence = ""
        for candidate in ordered_sources:
            if candidate.start > source.start or candidate.id == source.id:
                break
            text = candidate.raw_text.strip()
            if re.search(r"(?:首先)?拆解耳机部分|首先拆解耳机", text):
                side, evidence = "耳机", text
            elif re.search(r"拆解充电盒|充电盒拆解", text):
                side, evidence = "充电盒", text
        return side, evidence
    validated: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for item in events:
        content = ""
        try:
            segment_id = int(item.get("segment_id"))
        except (TypeError, ValueError):
            continue
        source = by_id.get(segment_id)
        if source is None:
            continue
        corrected = str(item.get("corrected_text") or source.raw_text).strip()
        fact = str(item.get("fact") or corrected).strip()
        if not fact:
            continue
        identity = (segment_id, normalize_text(fact))
        if identity in seen:
            continue
        seen.add(identity)
        try:
            importance = max(1, min(5, int(item.get("importance", 3))))
        except (TypeError, ValueError):
            importance = 3
        review_value = item.get("needs_review", False)
        needs_review = review_value is True or str(review_value).strip().lower() in {
            "1",
            "true",
            "yes",
        }
        source_numbers = re.findall(r"\d+(?:[.,]\d+)?", source.raw_text)
        corrected_numbers = re.findall(r"\d+(?:[.,]\d+)?", corrected)
        if source_numbers != corrected_numbers:
            needs_review = True
        section = str(item.get("section") or "").strip()
        if section not in FACT_SECTIONS:
            section = "bom" if item.get("bom_items") else "specification"
        side, side_evidence = narrative_side(source, section)
        validated.append(
            {
                **item,
                "segment_id": segment_id,
                "corrected_text": corrected,
                "fact": fact,
                "importance": importance,
                "needs_review": needs_review,
                "section": section,
                "narrative_side": side,
                "narrative_side_evidence": side_evidence,
                "bom_context_text": _bom_context_text(candidates, source),
                "selection_reason": str(item.get("selection_reason") or "model_selected"),
            }
        )
    return validated


def extract_facts(segments: list[SubtitleSegment], output_dir: Path) -> tuple[list[dict[str, Any]], str]:
    api_key = os.getenv("APP_VIDEO_MODEL_TOKEN") or os.getenv("APP_TEXT_MODEL_TOKEN")
    base_url = os.getenv("APP_VIDEO_MODEL_BASE_URL") or os.getenv("APP_TEXT_MODEL_BASE_URL")
    model = os.getenv("APP_VIDEO_MODEL_NAME") or os.getenv("APP_TEXT_MODEL_NAME")
    candidates = [segment for segment in segments if is_fact_candidate(segment)]
    if not (api_key and base_url and model):
        return validate_events(heuristic_events(segments), segments), "local_heuristic_no_api"

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=0)
    events: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    fallback_by_id = {item["segment_id"]: item for item in heuristic_events(segments)}
    gateway_failed = False
    # Keep each batch small enough that reasoning-capable compatible models
    # still have room to emit their final JSON. With 32 segments and a 2500
    # token ceiling, the configured gateway can finish during hidden reasoning
    # and return an empty message body, silently forcing heuristic fallback.
    batch_size = 8
    for offset in range(0, len(candidates), batch_size):
        batch = candidates[offset : offset + batch_size]
        if gateway_failed:
            fallback = [fallback_by_id[item.id] for item in batch if item.id in fallback_by_id]
            events.extend(fallback)
            audit.append(
                {
                    "batch_offset": offset,
                    "status": "fallback_after_gateway_failure",
                    "events": len(fallback),
                }
            )
            continue
        source = [
            {
                "segment_id": item.id,
                "start": round(item.start, 2),
                "end": round(item.end, 2),
                "raw_text": item.raw_text,
                "nearby_context": [
                    {"segment_id": nearby.id, "raw_text": nearby.raw_text}
                    for nearby in _nearby_context(segments, item)
                    if nearby.id != item.id
                ],
                "subtitle_confidence": round(item.confidence, 3),
            }
            for item in batch
        ]
        prompt = (
            "你在分析耳机拆解视频字幕。只筛选值得保存画面证据的产品事实：关键参数、"
            "芯片/器件型号、材料工艺、结构设计、测量结论。不要选择寒暄、转场和纯操作描述。"
            "可以纠正明显 OCR 错字和断句，但不得猜测数字、单位或型号；不确定时保留原文并设"
            " needs_review=true。若原字幕明确披露了BOM器件，同时给出bom_items；生产商、型号和"
            "参数值必须逐字存在于当前raw_text或其nearby_context中，且相邻字幕必须明显属于同一器件，"
            "不能依靠外部知识补全。只返回 JSON："
            '{"events":[{"segment_id":1,"corrected_text":"", "fact":"",'
            '"importance":1,"needs_review":false,"selection_reason":"",'
            '"section":"market|unboxing_packaging|unboxing_charging_case|unboxing_earbuds|specification|bom",'
            '"topic":"佩戴体验|音质体验|通话体验|续航充电|连接与智能|耐用防护|外观设计|",'
            '"bom_items":[{"component":"电池","manufacturer":"","model":"",'
            '"side":"充电盒","qty_hint":"1","parameters":[{"label":"容量",'
            '"value":"500mAh"}],"confidence":0.9}]}]}。输入：'
            + json.dumps(source, ensure_ascii=False)
        )
        content = ""
        response = None
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0.1,
                max_tokens=5000,
                messages=[
                    {"role": "system", "content": "你是保守、可追溯的拆解视频事实提取器。"},
                    {"role": "user", "content": prompt},
                ],
            )
            content = _message_text(response.choices[0].message)
            parsed = _json_object(content)
            events.extend(item for item in parsed.get("events", []) if isinstance(item, dict))
            audit.append({"batch_offset": offset, "status": "ok", "response": content})
        except Exception as exc:
            gateway_failed = True
            fallback = [fallback_by_id[item.id] for item in batch if item.id in fallback_by_id]
            events.extend(fallback)
            audit.append(
                {
                    "batch_offset": offset,
                    "status": "fallback",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                    "response": content[:4000],
                    "finish_reason": (
                        response.choices[0].finish_reason if response and response.choices else None
                    ),
                    "events": len(fallback),
                }
            )
    (output_dir / "llm_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    successful_batches = sum(item.get("status") == "ok" for item in audit)
    return (
        validate_events(events, segments),
        f"openai_compatible:{model};successful_batches={successful_batches}/{len(audit)}",
    )


def detect_summary_start(segments: list[SubtitleSegment], duration: float) -> dict[str, Any]:
    strong = [r"附上.*(?:已知|核心|参数|信息)", r"(?:最后|下面).*(?:总结|回顾)", r"总结(?:一下|如下)"]
    weak = [r"(?:内部)?主要配置(?:上|方面)", r"以上就是.*拆解", r"最后来看"]
    allowed = duration * 0.55
    strong_hits, weak_hits = [], []
    for segment in segments:
        if segment.start < allowed:
            continue
        for pattern, target in [(item, strong_hits) for item in strong] + [(item, weak_hits) for item in weak]:
            if re.search(pattern, segment.raw_text):
                target.append(
                    {
                        "segment_id": segment.id,
                        "time": segment.start,
                        "timecode": format_time(segment.start),
                        "text": segment.raw_text,
                        "pattern": pattern,
                    }
                )
                break
    chosen = strong_hits[0] if strong_hits else None
    if chosen is None and weak_hits:
        outros = [item["time"] for item in weak_hits if re.search(r"以上就是", item["text"])]
        chosen = next(
            (
                item
                for item in weak_hits
                if re.search(r"主要配置(?:上|方面)|最后来看", item["text"])
                and any(outro > item["time"] for outro in outros)
            ),
            None,
        )
    return {
        "method": "late_video_linguistic_cues",
        "duration_seconds": duration,
        "summary_detected": chosen is not None,
        "summary_start": chosen["time"] if chosen else None,
        "summary_start_time": chosen["timecode"] if chosen else None,
        "evidence": chosen,
        "strong_hits": strong_hits,
        "weak_hits": weak_hits,
    }


def frame_quality(frame: Any) -> float:
    """Laplacian variance on the non-subtitle image area; higher is sharper."""

    import cv2

    height, width = frame.shape[:2]
    main = frame[int(height * 0.05) : int(height * 0.84), int(width * 0.05) : int(width * 0.95)]
    gray = cv2.cvtColor(main, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def extract_keyframes(
    video: Path,
    segments: list[SubtitleSegment],
    events: list[dict[str, Any]],
    output_dir: Path,
) -> list[dict[str, Any]]:
    import cv2
    import imagehash
    from PIL import Image

    by_id = {segment.id: segment for segment in segments}
    frame_dir = output_dir / "keyframes"
    frame_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video))
    hashes: list[tuple[float, Any, str]] = []
    saved_count = 0
    enriched: list[dict[str, Any]] = []
    try:
        for event in events:
            try:
                segment = by_id[int(event["segment_id"])]
            except (KeyError, TypeError, ValueError):
                continue
            center = (segment.start + segment.end) / 2
            candidates = []
            for timestamp in sorted(
                {max(segment.start, center - 0.4), center, min(segment.end, center + 0.4)}
            ):
                capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
                ok, frame = capture.read()
                if ok:
                    candidates.append((frame_quality(frame), timestamp, frame))
            if not candidates:
                continue
            quality, chosen_time, frame = max(candidates, key=lambda item: item[0])
            height = frame.shape[0]
            rgb = cv2.cvtColor(frame[: int(height * 0.85), :], cv2.COLOR_BGR2RGB)
            perceptual_hash = imagehash.phash(Image.fromarray(rgb).resize((640, 306)))
            frame_name = next(
                (
                    old_name
                    for old_time, old_hash, old_name in hashes
                    if abs(chosen_time - old_time) <= 15 and perceptual_hash - old_hash <= 6
                ),
                "",
            )
            if not frame_name:
                saved_count += 1
                frame_name = f"kf_{saved_count:03d}_{int(chosen_time):04d}s.jpg"
                if not cv2.imwrite(
                    str(frame_dir / frame_name), frame, [cv2.IMWRITE_JPEG_QUALITY, 93]
                ):
                    raise RuntimeError(f"failed to encode keyframe: {frame_name}")
                hashes.append((chosen_time, perceptual_hash, frame_name))
            enriched.append(
                event
                | {
                    "start": segment.start,
                    "end": segment.end,
                    "start_time": format_time(segment.start),
                    "end_time": format_time(segment.end),
                    "raw_text": segment.raw_text,
                    "ocr_confidence": round(segment.confidence, 4),
                    "keyframe": f"keyframes/{frame_name}",
                    "keyframe_time": chosen_time,
                    "frame_quality": round(quality, 2),
                }
            )
    finally:
        capture.release()
    (output_dir / "facts.json").write_text(
        json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return enriched


def video_duration(video: Path) -> float:
    import cv2

    capture = cv2.VideoCapture(str(video))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 1.0
        return capture.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    finally:
        capture.release()


def run_pipeline(
    video: Path,
    output_dir: Path,
    *,
    subtitle_path: Path | None = None,
    reuse_segments_path: Path | None = None,
    reuse_events_path: Path | None = None,
    sample_interval: float = 0.5,
    include_summary: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    duration = video_duration(video)
    if reuse_segments_path and reuse_segments_path.is_file():
        payload = json.loads(reuse_segments_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("reused subtitle segments must be a JSON list")
        segments = [
            SubtitleSegment(
                int(item["id"]),
                float(item["start"]),
                float(item["end"]),
                str(item["raw_text"]),
                float(item.get("confidence", 1.0)),
                int(item.get("samples", 1)),
            )
            for item in payload
            if isinstance(item, dict)
        ]
        method = "hard_subtitle_ocr"
        scan_stats = {"reused_subtitle_segments": str(reuse_segments_path)}
    elif subtitle_path and subtitle_path.is_file():
        segments = parse_subtitle_file(subtitle_path)
        method = "creator_subtitle_track"
        scan_stats: dict[str, Any] = {"subtitle_track": subtitle_path.name}
    else:
        segments, scan_stats = scan_hard_subtitles(
            video, sample_interval=sample_interval, progress=print
        )
        method = "hard_subtitle_ocr"
    if not segments:
        raise RuntimeError("no usable subtitle text was extracted")
    write_segments(segments, output_dir)
    if reuse_events_path and reuse_events_path.is_file():
        reused = json.loads(reuse_events_path.read_text(encoding="utf-8"))
        if not isinstance(reused, list):
            raise ValueError("reused fact events must be a JSON list")
        # Human-curated event files contain only source segment ids and optional
        # presentation metadata. Re-validate them against the OCR/subtitle
        # source so an edited review file cannot inject unsupported text.
        events = validate_events(
            [item for item in reused if isinstance(item, dict)], segments
        )
        extractor = f"reused_events:{reuse_events_path.name}"
    else:
        events, extractor = extract_facts(segments, output_dir)
    phases = detect_summary_start(segments, duration)
    (output_dir / "phase_boundaries.json").write_text(
        json.dumps(phases, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    excluded: list[dict[str, Any]] = []
    cutoff = phases.get("summary_start")
    if cutoff is not None and not include_summary:
        by_id = {segment.id: segment for segment in segments}
        kept = []
        for event in events:
            segment = by_id.get(int(event.get("segment_id", -1)))
            if segment and segment.start >= float(cutoff):
                excluded.append(event | {"excluded_reason": "summary_or_recap_phase"})
            else:
                kept.append(event)
        events = kept
    (output_dir / "events_excluded_as_summary.json").write_text(
        json.dumps(excluded, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    facts = extract_keyframes(video, segments, events, output_dir)
    stats = scan_stats | {
        "subtitle_method": method,
        "subtitle_segments": len(segments),
        "fact_extractor": extractor,
        "selected_facts": len(facts),
        "unique_keyframes": len({item["keyframe"] for item in facts}),
        "summary_detected": phases["summary_detected"],
        "summary_start_time": phases["summary_start_time"],
        "summary_facts_excluded": len(excluded),
        "duration_seconds": round(duration, 2),
        "video_size_mb": round(video.stat().st_size / 1024 / 1024, 2),
        "wall_seconds": round(time.perf_counter() - started, 2),
        "audio_transcription_used": False,
    }
    (output_dir / "run_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return stats
