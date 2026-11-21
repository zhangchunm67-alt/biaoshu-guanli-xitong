"""Case OCR backend selection and output handling for both RapidOCR package generations."""
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import case_ocr


class CaseOcrTests(unittest.TestCase):
    def test_reads_rapidocr3_objects_and_legacy_tuples(self):
        self.assertEqual('合同编号\n服务周期', case_ocr._rapid_text(SimpleNamespace(txts=(' 合同编号 ', '服务周期', ''))))
        self.assertEqual('', case_ocr._rapid_text(SimpleNamespace(txts=None)))
        legacy = ([[[0, 0], '合同金额', 0.98], [[1, 1], '  ', 0.5]], 0.2)
        self.assertEqual('合同金额', case_ocr._rapid_text(legacy))
        self.assertEqual('', case_ocr._rapid_text((None, 0.1)))

    def test_prefers_rapidocr3_and_falls_back_to_legacy_package(self):
        def finder(present):
            return lambda name: object() if name in present else None
        with patch('case_ocr.importlib.util.find_spec', side_effect=finder({'rapidocr', 'rapidocr_onnxruntime'})):
            self.assertEqual('rapidocr', case_ocr._rapid_module())
        with patch('case_ocr.importlib.util.find_spec', side_effect=finder({'rapidocr_onnxruntime'})):
            self.assertEqual('rapidocr_onnxruntime', case_ocr._rapid_module())
        with patch('case_ocr.importlib.util.find_spec', side_effect=finder(set())):
            self.assertIsNone(case_ocr._rapid_module())
            self.assertFalse(case_ocr._has_rapidocr())

    def test_requirements_install_a_python_313_compatible_package(self):
        text = (Path(case_ocr.__file__).with_name('requirements-ocr.txt')).read_text(encoding='utf-8')
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')]
        self.assertIn('rapidocr>=3.10,<4', lines)
        self.assertFalse(any(line.startswith('rapidocr-onnxruntime') for line in lines))

    @unittest.skipUnless(case_ocr._rapid_module() == 'rapidocr', 'rapidocr 3.x is not installed')
    def test_installed_engine_reads_chinese_text_offline(self):
        from PIL import Image, ImageDraw, ImageFont
        fonts = [p for p in ('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
                             '/System/Library/Fonts/PingFang.ttc') if Path(p).exists()]
        if not fonts:
            self.skipTest('no CJK font available to draw the sample')
        image = Image.new('RGB', (700, 120), 'white')
        ImageDraw.Draw(image).text((20, 30), '扫描页合同金额', fill='black', font=ImageFont.truetype(fonts[0], 48))
        buffer = io.BytesIO(); image.save(buffer, format='PNG')
        with patch.dict('os.environ', {'BID_CASE_OCR': 'on'}):
            self.assertEqual('扫描页合同金额', case_ocr.recognize_image(buffer.getvalue()))


if __name__ == '__main__':
    unittest.main()
