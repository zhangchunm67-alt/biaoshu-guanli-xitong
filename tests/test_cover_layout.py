"""Standard cover page follows the three-zone bidding-document layout."""
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document
from lxml import etree as ET

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / 'bid-response-framework' / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from build_framework import build  # noqa: E402
from plan_framework import add_standard_cover, extract_package_name, extract_project_id  # noqa: E402

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class CoverLayoutTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='cover-layout-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.root, ignore_errors=True))

    def test_extractors_read_package_and_project_id(self):
        text = '项目名称：示例\n标包名称（如有）：直播服务包\n项目编号：0747-2660SCCZC017\n'
        self.assertEqual(extract_project_id(text), '0747-2660SCCZC017')
        self.assertEqual(extract_package_name(text), '直播服务包')
        self.assertEqual(extract_package_name('无标包字段'), '/')

    def test_standard_cover_renders_three_zones(self):
        source = self.root / 'source.docx'
        document = Document()
        document.add_paragraph('采购项目')
        document.add_paragraph('投标文件格式')
        document.add_paragraph('一、投标函')
        document.add_paragraph('致：招标人')
        document.save(source)
        plan = {
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'project': '新华保险互联网业务直播服务采购项目',
            'blocks': [],
            'review': [],
        }

        class FakePlan:
            def __init__(self):
                self.project = plan['project']
                self.blocks = plan['blocks']

            def add(self, kind, text=None, ref=None, **kw):
                data = {'kind': kind, **kw}
                if text is not None:
                    data['text'] = text
                if ref is not None:
                    data['source_index'] = ref
                self.blocks.append(data)

        add_standard_cover(
            FakePlan(),
            project='新华保险互联网业务直播服务采购项目',
            doc_title='投标文件',
            project_id='0747-2660SCCZC017',
            package='/',
        )
        plan['blocks'].extend([
            {'kind': 'break'},
            {'kind': 'toc'},
            {'kind': 'break'},
            {'kind': 'heading', 'text': '投标函', 'level': 1, 'source_index': 2},
            {'kind': 'copy', 'start': 3, 'end': 3},
        ])
        output = self.root / 'out.docx'
        build(source, plan, output)

        with zipfile.ZipFile(output) as archive:
            body = ET.fromstring(archive.read('word/document.xml')).find(f'{W}body')
            styles = {node.get(f'{W}styleId') for node in ET.fromstring(archive.read('word/styles.xml'))}
        self.assertTrue({'BFCover', 'BFCoverMeta', 'BFCoverBadge', 'BFCoverField', 'BFCoverSpace'} <= styles)

        rows = []
        for child in list(body):
            if child.tag != f'{W}p':
                continue
            text = ''.join(node.text or '' for node in child.iter(f'{W}t'))
            style = child.find(f'{W}pPr/{W}pStyle')
            style_id = style.get(f'{W}val') if style is not None else None
            if style_id == 'BFCover' and text == '目录':
                break
            if text or style_id == 'BFCoverSpace':
                rows.append((style_id, text))

        self.assertEqual(rows[0], ('BFCoverBadge', '正本/副本'))
        self.assertIn(('BFCoverMeta', '新华保险互联网业务直播服务采购项目'), rows)
        self.assertIn(('BFCoverMeta', '标包名称（如有）：/'), rows)
        self.assertIn(('BFCoverMeta', '项目编号：0747-2660SCCZC017'), rows)
        self.assertIn(('BFCover', '投标文件'), rows)
        field_texts = [text for style, text in rows if style == 'BFCoverField']
        self.assertEqual(field_texts, ['投标人名称：________', '投标人地址：________', '日期：________'])
        self.assertGreaterEqual(sum(1 for style, _ in rows if style == 'BFCoverSpace'), 10)


if __name__ == '__main__':
    unittest.main()
