"""Composition checklists preserve image attachments and explicit exemptions."""
import base64
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

from docx import Document
from docx.shared import Inches

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/bid-response-framework/scripts'
sys.path.insert(0, str(SCRIPTS))
from plan_framework import Source, generate
from composition_checklist_forms import structure
from build_framework import build


def media(path):
    with ZipFile(path) as package:
        return {name: package.read(name) for name in package.namelist() if name.startswith('word/media/')}


class CompositionChecklistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fixture(self):
        doc = Document()
        for line in ['项目名称：设备维护询价项目', '一、报价须知', '二、项目概述、内容及要求',
                     '1.项目概述', '办公设备维护服务。', '三、报价文件的编制', '2.报价文件的组成',
                     '报价文件包括资质部分和商务部分。', '2.1 资质部分（需加盖报价人公章）',
                     '（1）营业执照', '（2）制造商合法授权书（本项目无需提供）', '（3）近2年财务报表',
                     '2.2 商务部分', '报价单（报价含增值税，请列明税率）', '3.报价',
                     '3.1 报价包含全部服务费用。', '四、报价文件的递交',
                     '由法定代表人授权的代理人签字的报价文件中须同时提交法定代表人授权委托书。',
                     '资质部分和商务部分分别装订、密封。', '附件：报价确认书', '报价确认书',
                     '我方已阅读询价书并将按采购要求准备响应文件。', '报价人（公章）：____ 日期：____']:
            doc.add_paragraph(line)
        image = self.root / 'attachment.png'
        image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXxQAAAAASUVORK5CYII='))
        paragraph = doc.add_paragraph('附件2：报价明细单')
        paragraph.add_run().add_picture(str(image), width=Inches(1))
        source = self.root / 'source.docx'
        doc.save(source)
        return source

    def test_explicit_composition_keeps_image_and_excludes_exempt_material(self):
        source = self.fixture()
        plan = generate(Source(source))
        self.assertEqual('composition-checklist-forms', plan['strategy'])
        headings = [b for b in plan['blocks'] if b['kind'] == 'heading']
        self.assertEqual(['qualification', 'commercial'], [b['book_role'] for b in headings if b['level'] == 1])
        self.assertNotIn('制造商合法授权书', [b['text'] for b in headings])
        self.assertIn('法定代表人授权委托书（授权代理人签署时适用）', [b['text'] for b in headings])
        image_form = next(f for f in plan['forms'] if f['has_images'])
        self.assertEqual(image_form['source_start'], image_form['content_start'])
        output = self.root / 'result.docx'
        build(source, plan, output)
        self.assertEqual(1, len(media(source)))
        self.assertEqual(media(source), media(output))
        text = '\n'.join(p.text for p in Document(output).paragraphs)
        self.assertIn('未进行 OCR', text)
        self.assertIn('需人工核对', text)
        self.assertIn('不适用（采购原文）：制造商合法授权书（本项目无需提供）', text)
        self.assertIn('我方已阅读询价书并将按采购要求准备响应文件。', text)
        self.assertIn('3.1 报价包含全部服务费用。', text)

    def test_composition_label_is_required(self):
        source = self.fixture()
        doc = Document(source)
        next(p for p in doc.paragraphs if p.text == '2.报价文件的组成').text = '2.说明事项'
        doc.save(source)
        self.assertIsNone(structure(Source(source)))

    def test_real_image_attachment_regression_retains_media_without_ocr_claim(self):
        source = SCRIPTS.parents[3] / '标书下载/25年标书/26年 嘉实基金投标/腾宝科技/招标文件/嘉实基金互联网-社区推广项目询价书（2026）.docx'
        if not source.exists():
            self.skipTest('buyer regression source is not distributed with checkout')
        plan = generate(Source(source))
        self.assertEqual('composition-checklist-forms', plan['strategy'])
        output = self.root / 'result.docx'
        build(source, plan, output)
        self.assertEqual(1, len(media(source)))
        self.assertEqual(media(source), media(output))
        headings = [b for b in plan['blocks'] if b['kind'] == 'heading']
        self.assertEqual(['资质部分', '商务部分'], [b['text'] for b in headings if b['level'] == 1])
        self.assertFalse(any('制造商' in b['text'] for b in headings))
        self.assertIn('报价确认书', [b['text'] for b in headings])
        self.assertIn('报价明细单', [b['text'] for b in headings])
        text = '\n'.join(p.text for p in Document(output).paragraphs)
        self.assertIn('未进行 OCR', text)
        self.assertIn('我司承担询价文件规定的责任和义务。', text)
        self.assertIn('2个工作日内', text)
        self.assertIn('报价人在报价时应基于项目特点', text)


    def test_composition_checklist_can_end_at_document_boundary(self):
        doc = Document()
        for line in ['设备采购项目', '2. 报价文件组成', '2.1 资质部分',
                     '(1) 营业执照复印件', '2.2 商务部分', '报价说明（含全部税费）']:
            doc.add_paragraph(line)
        source = self.root / 'composition-at-end.docx'
        doc.save(source)
        plan = generate(Source(source))
        self.assertEqual('composition-checklist-forms', plan['strategy'])
        self.assertEqual(['qualification', 'commercial'], [b['book_role'] for b in plan['blocks']
                                                           if b['kind'] == 'heading' and b['level'] == 1])
        output = self.root / 'composition-result.docx'
        build(source, plan, output)
        text = '\n'.join(p.text for p in Document(output).paragraphs)
        self.assertIn('营业执照复印件', text)
        self.assertIn('报价说明（含全部税费）', text)
        self.assertIn('【待补】按本节采购要求提供报价', text)


if __name__ == '__main__':
    unittest.main()
