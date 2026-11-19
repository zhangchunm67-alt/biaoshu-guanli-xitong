"""Subprocess entry for one-shot PaddleOCR; isolates native crashes from the server."""
from __future__ import annotations

import json
import sys


def main(argv=None):
    args = list(argv or sys.argv[1:])
    if len(args) != 1:
        print(json.dumps({'ok': False, 'error': 'usage: case_ocr_worker.py <image-path>', 'text': ''}, ensure_ascii=False))
        return 2
    path = args[0]
    try:
        from paddleocr import PaddleOCR
        import numpy as np
        from PIL import Image
        image = Image.open(path)
        image.load()
        if image.mode != 'RGB':
            image = image.convert('RGB')
        engine = PaddleOCR(use_angle_cls=True, lang='ch', show_log=False, use_gpu=False, enable_mkldnn=False)
        result = engine.ocr(np.array(image), cls=True)
        lines = []
        for item in (result[0] or []) if result else []:
            if not item or len(item) < 2:
                continue
            payload = item[1]
            text = payload[0] if isinstance(payload, (list, tuple)) else payload
            if isinstance(text, str) and text.strip():
                lines.append(text.strip())
        print(json.dumps({'ok': True, 'text': '\n'.join(lines), 'error': ''}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'ok': False, 'text': '', 'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
