"""Case-image OCR using PP-OCR models (PaddleOCR lineage).

Primary runtime is RapidOCR on ONNX Runtime — same PP-OCR detectors/recognizers
as PaddleOCR, with the models shipped inside the package so nothing is downloaded.
``rapidocr`` 3.x supports Python 3.13; an existing ``rapidocr_onnxruntime`` install
(Python 3.12 and older) keeps working. Optional native paddleocr remains as a
subprocess fallback. Set BID_CASE_OCR=off to disable.
"""
from __future__ import annotations

import importlib.util
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path, PurePosixPath

from PIL import Image

MAX_IMAGES = 60
MAX_SIDE = 2800
OCR_TIMEOUT_SEC = 120
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.webp'}
_WORKER = Path(__file__).with_name('case_ocr_worker.py')
_lock = threading.RLock()
_rapid = None
_rapid_error = None
_backend = None


def enabled() -> bool:
    value = os.environ.get('BID_CASE_OCR', 'on').strip().lower()
    return value not in {'0', 'off', 'false', 'no'}


RAPID_MODULES = ('rapidocr', 'rapidocr_onnxruntime')


def _rapid_module() -> str | None:
    for name in RAPID_MODULES:
        try:
            if importlib.util.find_spec(name) is not None:
                return name
        except (ImportError, ValueError):
            continue
    return None


def _has_rapidocr() -> bool:
    return _rapid_module() is not None


def _has_paddleocr() -> bool:
    try:
        import paddleocr  # noqa: F401
        return True
    except Exception:
        return False


def available() -> bool:
    if not enabled():
        return False
    return _has_rapidocr() or _has_paddleocr()


def unavailable_reason() -> str:
    if not enabled():
        return '案例 OCR 已关闭（BID_CASE_OCR=off）'
    if available():
        return ''
    return '本机未安装案例 OCR；请在 .venv 中执行：pip install -r requirements-ocr.txt'


def is_image_name(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in IMAGE_SUFFIXES


def _prepare_image(raw: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(raw))
    image.load()
    if image.mode != 'RGB':
        image = image.convert('RGB')
    width, height = image.size
    longest = max(width, height)
    if longest > MAX_SIDE:
        scale = MAX_SIDE / float(longest)
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.LANCZOS)
    return image


def _rapid_engine():
    global _rapid, _rapid_error
    with _lock:
        if _rapid is not None or _rapid_error is not None:
            return _rapid
        try:
            module = _rapid_module()
            if module is None:
                raise ImportError('RapidOCR is not installed')
            engine_class = __import__(module, fromlist=['RapidOCR']).RapidOCR
            # Keep model-loading chatter out of server logs; rapidocr 3.x resets the level from its config.
            logging.getLogger('RapidOCR').setLevel(logging.WARNING)
            _rapid = engine_class(params={'Global.log_level': 'warning'}) if module == 'rapidocr' else engine_class()
        except Exception as exc:
            _rapid_error = exc
            _rapid = None
        return _rapid


def _recognize_rapid(raw: bytes) -> str:
    engine = _rapid_engine()
    if engine is None:
        return ''
    try:
        import numpy as np
        image = _prepare_image(raw)
        return _rapid_text(engine(np.array(image)))
    except Exception:
        return ''


def _rapid_text(output) -> str:
    """rapidocr 3.x returns an object with ``txts``; rapidocr_onnxruntime returns (rows, elapse)."""
    texts = getattr(output, 'txts', None)
    if texts is None and isinstance(output, tuple) and output:
        texts = [row[1] for row in output[0] or [] if isinstance(row, (list, tuple)) and len(row) > 1]
    return '\n'.join(text.strip() for text in texts or [] if isinstance(text, str) and text.strip()).strip()


def _recognize_paddle_subprocess(raw: bytes) -> str:
    try:
        png = io.BytesIO()
        _prepare_image(raw).save(png, format='PNG')
        payload = png.getvalue()
    except Exception:
        return ''
    with _lock:
        with tempfile.TemporaryDirectory(prefix='bid-ocr-') as folder:
            path = Path(folder) / 'page.png'
            path.write_bytes(payload)
            try:
                completed = subprocess.run(
                    [sys.executable, str(_WORKER), str(path)],
                    capture_output=True, text=True, timeout=OCR_TIMEOUT_SEC, check=False,
                )
            except Exception:
                return ''
    if not completed.stdout.strip():
        return ''
    try:
        body = json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception:
        return ''
    text = body.get('text') if isinstance(body, dict) else ''
    return text.strip() if isinstance(text, str) else ''


def recognize_image(raw: bytes) -> str:
    """Return OCR text for one image, or '' when disabled / empty / failure."""
    global _backend
    if not raw or not enabled() or not available():
        return ''
    if _has_rapidocr():
        text = _recognize_rapid(raw)
        if text or not _has_paddleocr():
            _backend = _rapid_module()
            return text
    if _has_paddleocr():
        _backend = 'paddleocr'
        return _recognize_paddle_subprocess(raw)
    return ''


def pdf_page_image(raw: bytes, page_index: int, scale: float = 2.0):
    import pypdfium2 as pdfium
    # PDFium is process-global and not thread-safe.
    with _lock, pdfium.PdfDocument(raw) as pdf:
        page = pdf[page_index]
        try:
            width, height = page.get_size()
            if not (width > 0 and height > 0):
                raise ValueError('PDF 页面尺寸无效')
            bitmap = page.render(scale=min(scale, MAX_SIDE / max(width, height)))
            try:
                return bitmap.to_pil().copy()
            finally:
                bitmap.close()
        finally:
            page.close()


def recognize_pdf_page(raw: bytes, page_index: int, scale: float = 2.0) -> str:
    if not raw or not enabled() or page_index < 0 or not available():
        return ''
    try:
        with pdf_page_image(raw, page_index, scale) as image:
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
        return recognize_image(buffer.getvalue())
    except Exception:
        return ''


def media_budget(remaining: int | None = None) -> int:
    if remaining is None:
        return MAX_IMAGES
    return max(0, min(MAX_IMAGES, remaining))
