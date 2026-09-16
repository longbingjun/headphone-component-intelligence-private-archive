"""图片处理管线："文本密集型 vs 图像密集型" 粗判断 + OCR 队列（框架先行方案）。

设计目标（按用户明确选定的"框架先行"方案实现）：
1. 对文章里的每张图片，做一个轻量、可解释的启发式判断：
   - edge_density：Canny 边缘像素占比。规格表/铭牌截图通常文字笔画密集、
     边缘像素占比高；实物拍摄照片边缘占比相对分散、没有那么"密"。
   - unique_color_ratio：颜色种类的丰富程度（缩放+量化后统计唯一颜色数）。
     文字/表格类图片大多是"白底黑字"，颜色种类很少；实物照片色彩层次丰富，
     颜色种类多。
   - aspect_ratio 作为辅助信号记录下来，供后续调参/人工复核参考。
   这一步目前没有引入更重的模型（比如文本检测网络），属于第一版启发式，
   代码里标注了 TODO，后续可以替换成 OpenCV EAST / DB 文本检测器等更准确的方案。
2. 对判断为 text_dense 的图片，标记 ocr_status="pending"，记录 url / 所在文章 /
   判断依据，形成一个"待接入真实 OCR 引擎"的队列（data/images_queue.json）。
3. 如果当前环境里 pytesseract + tesseract 可执行文件都能正常工作，
   就顺手真的跑一遍 OCR 作为验证（ocr_status 变成 done/failed），
   否则保持 pending，留给下一阶段接入云端 OCR / 多模态大模型。
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass

import requests

from core.models import ImageAsset

try:
    import numpy as np
    import cv2

    _CV_OK = True
except Exception:  # pragma: no cover
    _CV_OK = False

try:
    from PIL import Image

    _PIL_OK = True
except Exception:  # pragma: no cover
    _PIL_OK = False

try:
    import pytesseract

    _PYTESSERACT_IMPORTED = True
except Exception:  # pragma: no cover
    _PYTESSERACT_IMPORTED = False


# ---- 启发式阈值（TODO: 用更大样本人工标注后再调参，目前是首版经验值）----
EDGE_DENSITY_THRESHOLD = 0.045
UNIQUE_COLOR_RATIO_THRESHOLD = 0.12


@dataclass
class _TesseractAvailability:
    checked: bool = False
    available: bool = False
    version: str = ""


_tess_state = _TesseractAvailability()


def tesseract_available() -> bool:
    """惰性检测本机是否装了 tesseract 可执行文件，只检测一次。"""

    if not _PYTESSERACT_IMPORTED:
        return False
    if not _tess_state.checked:
        _tess_state.checked = True
        try:
            version = pytesseract.get_tesseract_version()
            _tess_state.available = True
            _tess_state.version = str(version)
        except Exception:
            _tess_state.available = False
    return _tess_state.available


def _classify_image_bytes(img_bytes: bytes) -> dict:
    """返回粗判断结果 dict，不抛异常（下载/解码失败时返回 unknown）。"""

    result = {
        "width": None,
        "height": None,
        "aspect_ratio": None,
        "edge_density": None,
        "classification": "unknown",
        "classification_reason": "",
    }
    if not (_PIL_OK and _CV_OK):
        result["classification_reason"] = "缺少 Pillow/OpenCV 依赖，跳过图像分析"
        return result

    try:
        pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception as e:
        result["classification_reason"] = f"图片解码失败: {e}"
        return result

    w, h = pil_img.size
    result["width"] = w
    result["height"] = h
    result["aspect_ratio"] = round(w / h, 3) if h else None

    arr = np.array(pil_img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 80, 160)
    edge_density = float(np.count_nonzero(edges)) / float(edges.size)
    result["edge_density"] = round(edge_density, 4)

    small = cv2.resize(arr, (64, 64), interpolation=cv2.INTER_AREA)
    quantized = (small // 32).reshape(-1, 3)
    unique_colors = len({tuple(px) for px in quantized.tolist()})
    unique_ratio = unique_colors / (64 * 64)

    is_text_dense = edge_density > EDGE_DENSITY_THRESHOLD and unique_ratio < UNIQUE_COLOR_RATIO_THRESHOLD
    result["classification"] = "text_dense" if is_text_dense else "image_dense"
    result["classification_reason"] = (
        f"edge_density={edge_density:.4f} (阈值{EDGE_DENSITY_THRESHOLD}), "
        f"unique_color_ratio={unique_ratio:.4f} (阈值{UNIQUE_COLOR_RATIO_THRESHOLD})"
    )
    return result


def _run_ocr(img_bytes: bytes) -> tuple[str, str | None]:
    """尝试跑真实 OCR。返回 (status, text)。"""

    if not tesseract_available():
        return "pending", None
    try:
        pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        text = pytesseract.image_to_string(pil_img, lang="chi_sim+eng")
        text = text.strip()
        return ("done" if text else "done_empty"), text
    except Exception:
        return "failed", None


def build_image_assets(
    raw_images: list[dict],
    session: requests.Session,
    request_delay_sec: float = 0.6,
    download_timeout: int = 10,
    user_agent: str = "",
) -> tuple[list[ImageAsset], list[dict]]:
    """下载并分析文章里的图片，返回 (ImageAsset 列表, 待 OCR 队列条目列表)。

    raw_images: [{"index": 0, "url": "...", "alt": "...", "caption": "..."}, ...]
    队列条目会被 pipeline 汇总进 data/images_queue.json，供后续接入真实 OCR 引擎。
    """

    assets: list[ImageAsset] = []
    queue_entries: list[dict] = []
    # 图片 CDN（阿里云 OSS）开启了防盗链，必须带上站点自身的 Referer 才能正常下载。
    headers = {"Referer": "https://www.52audio.com/"}
    if user_agent:
        headers["User-Agent"] = user_agent

    for raw in raw_images:
        asset = ImageAsset(index=raw["index"], url=raw["url"], alt=raw.get("alt", ""), caption=raw.get("caption", ""))
        img_bytes = None
        try:
            resp = session.get(raw["url"], headers=headers, timeout=download_timeout)
            if resp.status_code == 200:
                img_bytes = resp.content
        except Exception:
            img_bytes = None

        if img_bytes:
            info = _classify_image_bytes(img_bytes)
            asset.width = info["width"]
            asset.height = info["height"]
            asset.aspect_ratio = info["aspect_ratio"]
            asset.edge_density = info["edge_density"]
            asset.classification = info["classification"]
            asset.classification_reason = info["classification_reason"]
        else:
            asset.classification_reason = "图片下载失败，跳过分析"

        if asset.classification == "text_dense":
            if img_bytes:
                status, text = _run_ocr(img_bytes)
                asset.ocr_status = status
                asset.ocr_text = text
                asset.ocr_engine = "tesseract" if status in ("done", "done_empty", "failed") and tesseract_available() else None
            else:
                asset.ocr_status = "pending"

            if asset.ocr_status == "pending":
                queue_entries.append(
                    {
                        "image_url": asset.url,
                        "reason": asset.classification_reason,
                        "alt": asset.alt,
                        "caption": asset.caption,
                    }
                )
        else:
            asset.ocr_status = "not_applicable"

        assets.append(asset)
        if request_delay_sec:
            time.sleep(request_delay_sec)

    return assets, queue_entries
