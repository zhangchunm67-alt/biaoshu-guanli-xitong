"""Regressions found by running a real tender and a hand-made custom outline end to end.

Each fixture mirrors the structure of the real documents without their content.
"""
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.shared import Cm
from PIL import Image

import engine
import framework_service
from server import Store


def upload(document):
    stream = io.BytesIO()
    document.save(stream)
    return base64.b64encode(stream.getvalue()).decode()


def png():
    stream = io.BytesIO()
    Image.new('RGB', (40, 30), 'white').save(stream, 'PNG')
    stream.seek(0)
    return stream


def tender():
    document = Document()
    document.add_heading('第三章 评标办法', 1)
    document.add_paragraph('2、否决投标情形汇总')
    table = document.add_table(rows=4, cols=3)
    for row, cells in zip(table.rows, [
            ('章节', '内容', '条款'),
            ('第一章', '招标公告', '不完全符合“三、投标人资格要求”的投标将被否决'),
            ('第四章', '合同条款', '未能完全响应任一★条款的投标将被否决。'),
            ('第五章', '技术规范书', '未能完全响应任一★条款的投标将被否决。')]):
        for cell, text in zip(row.cells, cells):
            cell.text = text
    document.add_paragraph('评标报告应当如实记载以下内容：')
    document.add_paragraph('（5）否决投标情况说明；')
    document.add_heading('第六章 投标文件格式', 1)
    document.add_paragraph('一、投标函')
    document.add_paragraph('投标函正文，签字盖章。')
    document.add_paragraph('二、技术方案')
    document.add_paragraph('1、拟派团队成员简历表')
    form = document.add_table(rows=2, cols=2)
    form.cell(0, 0).text, form.cell(0, 1).text = '姓  名', '性  别'
    document.add_paragraph('备注：先附简历、再附材料。')
    document.add_paragraph('十五、其他材料（本项目不涉及）')
    document.add_paragraph('附件15-1 完成尽职调查报告承诺函')
    document.add_paragraph('完成尽职调查报告承诺函')
    document.add_paragraph('致：招标人')
    document.add_paragraph('我公司承诺按要求完成尽职调查报告。')
    document.add_paragraph('投标人名称（盖章）：        日期：    年  月  日')
    document.add_paragraph('十六、中介机构评估表（本项目不涉及）')
    return document


def custom_outline():
    document = Document()
    document.add_heading('投标函', 1)
    document.add_heading('资格证明文件', 1)
    document.add_heading('投标人基本情况信息表', 2)
    document.add_heading('技术方案', 1)
    document.add_heading('9.1 项目理解', 2)
    document.add_paragraph('我方对项目背景的理解：围绕品牌传播目标开展投放。')
    document.add_heading('9.9 团队及人员配置', 2)
    document.add_heading('9.9.2 拟派团队成员简历表', 3)
    resume = document.add_table(rows=2, cols=2)
    resume.cell(0, 0).text, resume.cell(0, 1).text = '姓  名张三', '性  别男'
    document.add_picture(png(), width=Cm(2))
    document.add_paragraph('备注：先附简历、再附材料。')
    document.add_heading('9.9.4 社保缴纳证明', 3)
    document.add_picture(png(), width=Cm(2))
    return document


class RealTenderRegressionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name), seed=False)
        self.addCleanup(self.store.close)
        # The automatic calibration job needs Kimi; it is irrelevant to these checks.
        model = patch('outline_workflow.model_call', side_effect=ValueError('model disabled in tests'))
        model.start()
        self.addCleanup(model.stop)
        self.record = framework_service.create(self.store, {
            'name': '回归', 'filename': '采购.docx', 'data': upload(tender()), 'outline_choice': True})

    def labels(self, category):
        return [row['text'] for row in self.record['requirements'] if row['category'] == category]

    def test_rejection_summary_rows_keep_the_rule_not_the_section_name(self):
        redlines = self.labels('redline')
        for junk in ('招标公告', '合同条款', '技术规范书', '（5）否决投标情况说明；'):
            self.assertNotIn(junk, redlines)
        self.assertIn('不完全符合“三、投标人资格要求”的投标将被否决', redlines)
        # A ★ legend points at concrete ★ clauses and is not listed on its own.
        self.assertNotIn('未能完全响应任一★条款的投标将被否决。', redlines)

    def test_forms_marked_not_involved_are_not_required_formats(self):
        labels = [row['label'] for row in self.record['requirements'] if row['category'] == 'format']
        self.assertFalse(any('尽职调查' in label for label in labels), labels)

    def confirmed_project(self):
        record = framework_service.revise(self.store, self.record['id'], {
            'revision': self.record['revision'], 'items': self.record['requirements'], 'confirmed': True,
            'notes_acknowledged': True, 'acknowledged_removed_ids': []}, 'requirements')
        record = framework_service.revise(self.store, record['id'], {
            'revision': record['revision'], 'docx': {'name': '人工目录.docx', 'data': upload(custom_outline())}},
            'outline')
        return framework_service.confirm(self.store, record['id'], {'revision': record['revision']})

    def test_stage_export_keeps_custom_outline_content_without_duplicate_form_notes(self):
        project = self.confirmed_project()
        with self.store.lock:
            overview = self.store.stage_snapshot(project['id'])[0]['stage_overview']
            snapshot = self.store.stage_snapshot(project['id'], overview)
        exported = self.store.export(project['id'], {'mode': 'stage', **overview}, snapshot=snapshot)
        document = Document(self.store.path('exports/' + exported['id'] + '.docx'))
        text = [p.text for p in document.paragraphs]
        self.assertIn('我方对项目背景的理解：围绕品牌传播目标开展投放。', text)
        self.assertTrue(any(t.cell(0, 0).text == '姓  名张三' for t in document.tables))
        self.assertGreaterEqual(len(document.inline_shapes), 2)
        self.assertEqual(1, text.count('备注：先附简历、再附材料。'))
        self.assertTrue(any('已保留自定义目录' in w for w in exported['warnings']))
        status = {row['text']: row['status'] for row in overview['chapters']}
        self.assertEqual('completed', next(v for k, v in status.items() if k.endswith('项目理解')))

    def test_saved_chapter_text_takes_precedence_over_custom_outline_content(self):
        project = self.confirmed_project()
        with self.store.lock:
            framework = self.store.require('framework', project['framework_id'])
            ident = next(row['id'] for row in framework['outline'] if row['text'].endswith('项目理解'))
            current = self.store.require('project', project['id'])
            imported = self.store.imported_technical(current, framework)
            self.assertIn(ident, imported['sections'])
            current['chapter_texts'] = {ident: '人工保存的新正文。'}
            self.assertNotIn(ident, self.store.imported_technical(current, framework)['sections'])

    def test_team_social_security_and_info_form_are_not_company_material_slots(self):
        project = self.confirmed_project()
        with self.store.lock:
            mappings = self.store.public_project(self.store.require('project', project['id']))['mappings']
        titles = {(row['category_id'], row['title']) for row in mappings}
        self.assertFalse(any(cid == 'tax_social' and '9.9.4' in title for cid, title in titles), titles)
        self.assertFalse(any(cid == 'company_profile' for cid, _ in titles), titles)


class RelativeTaxPeriodMessageTests(unittest.TestCase):
    def test_relative_period_explains_why_nothing_was_preselected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'template.docx'
            document = Document()
            document.add_heading('第五章 投标文件格式', 1)
            document.add_heading('企业税收及社保缴纳凭证', 2)
            document.add_paragraph('提供开标日前6个月任意一个月的税收和社会保障资金缴纳凭证。')
            document.save(path)
            categories = [c for c in engine.DEFAULT_CATEGORIES if c['id'] == 'tax_social']
            materials = [dict(id=name, name=name, category_id='tax_social', subject='北京腾宝科技有限公司',
                              mode='text', content='x', files=[], years=[], revision=1, valid_until='长期')
                         for name in ('2026年8月纳税证明', '2026年8月社保缴费凭证')]
            scanned = engine.scan_document(path, categories, materials)
            row = next(r for r in scanned['mappings'] if r['category_id'] == 'tax_social')
            self.assertEqual([], row['material_ids'])
            # The reviewer chose “填入” but has not picked the vouchers yet.
            checked = engine.validate_mappings([dict(row, action='fill')], materials, scanned['headings'], categories)[0]
            self.assertTrue(any('人工选择符合期限' in issue for issue in checked['issues']), checked['issues'])
            self.assertFalse(any('多个材料版本' in issue for issue in checked['issues']))


if __name__ == '__main__':
    unittest.main()
