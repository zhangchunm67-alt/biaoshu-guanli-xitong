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

    def test_ai_redline_review_uses_kimi_and_sees_custom_outline_content(self):
        import redline_ai
        import redline_scan
        project = self.confirmed_project()
        captured = {}

        def kimi(system, content, schema, model=None, **_):
            captured.update(model=model, content=content)
            return {'items': []}

        with patch('writing_model.complete_json', side_effect=kimi):
            redline_ai.review(self.store, project['id'])  # no saved body text: imported content alone suffices
        self.assertEqual('moonshotai/kimi-k3.1-pro', captured['model'])
        joined = '\n'.join(row['text'] for row in captured['content']['chapters'])
        self.assertIn('我方对项目背景的理解', joined)
        self.assertIn('本章含自定义目录原有图片或表格', joined)
        with self.store.lock:
            current = self.store.require('project', project['id'])
            framework = self.store.require('framework', current['framework_id'])
            result = redline_scan.evaluate(current, framework)
            # The input token still only follows saved text, so earlier confirmations stay valid.
            plain = redline_scan.scan(framework['requirements'], current.get('chapter_texts', {}),
                                      current['mappings'], framework['outline'])
        self.assertEqual(plain['input_token'], result['input_token'])

    def test_team_social_security_and_info_form_are_not_company_material_slots(self):
        project = self.confirmed_project()
        with self.store.lock:
            mappings = self.store.public_project(self.store.require('project', project['id']))['mappings']
        titles = {(row['category_id'], row['title']) for row in mappings}
        self.assertFalse(any(cid == 'tax_social' and '9.9.4' in title for cid, title in titles), titles)
        self.assertFalse(any(cid == 'company_profile' for cid, _ in titles), titles)


class RedlineKimiLoopTests(unittest.TestCase):
    """Step 4: saved redlines are checked by Kimi against the AI draft, which Kimi rewrites until met."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name), seed=False)
        self.addCleanup(self.store.close)
        for target, value in [('outline_workflow.model_call', ValueError('model disabled')),
                              ('writing_model.configuration', None)]:
            stub = (patch(target, side_effect=value) if value is not None else patch(target, return_value={
                'ready': True, 'status': 'ready', 'model': 'mock-writer', 'message': '已配置'}))
            stub.start()
            self.addCleanup(stub.stop)
        submit = patch.object(self.store.writing.pool, 'submit', side_effect=lambda fn, *a: fn(*a))
        submit.start()
        self.addCleanup(submit.stop)
        document = Document()
        document.add_heading('第二章 投标人须知', 1)
        document.add_paragraph('投标有效期不足150天的，投标将被否决。')
        document.add_heading('第六章 投标文件格式', 1)
        document.add_paragraph('一、技术方案')
        document.add_paragraph('【据实编制】')
        record = framework_service.create(self.store, {'name': '循环', 'filename': '采购.docx',
                                                       'data': upload(document), 'outline_choice': True})
        self.redline = next(r for r in record['requirements'] if r['category'] == 'redline')
        record = framework_service.revise(self.store, record['id'], {
            'revision': record['revision'], 'items': record['requirements'], 'confirmed': True,
            'notes_acknowledged': True, 'acknowledged_removed_ids': []}, 'requirements')
        record = framework_service.revise(self.store, record['id'], {
            'revision': record['revision'], 'text': '1. 技术方案\n1.1 服务保障'}, 'outline')
        self.project = framework_service.confirm(self.store, record['id'], {'revision': record['revision']})
        self.chapter = next(r['id'] for r in self.store.require('framework', record['id'])['outline']
                            if r['text'].endswith('服务保障'))
        # Step 3 done: text materials reviewed (none apply), so step 4 may start.
        with self.store.lock:
            project = self.store.prepare_text_materials(self.project['id'], text_first=True)
            self.store.update_mappings(self.project['id'], {
                'mapping_revision': project['mapping_revision'],
                'mappings': [{'id': m['id'], 'confirmed': True} for m in project['mappings']]})
            current = self.store.require('project', self.project['id'])
            current['text_review_confirmed'] = True
            self.store.put('project', current)
            self.store.db.commit()

    def request(self, **extra):
        project = self.store.require('project', self.project['id'])
        pack = self.store.get('writing_cases', project['id']) or {'revision': 0}
        return dict(mapping_revision=self.store.mapping_revision(project), revision=project.get('chapter_revision', 0),
                    case_revision=pack['revision'], **extra)

    def draft(self, context, model=None):
        return dict(chapters=[dict(chapter_id=row['id'], text='本节说明服务保障安排与响应流程。',
                                   requirement_ids=row.get('requirement_ids', []),
                                   source_refs=[context['case_sources'][0]['id']], material_refs=[],
                                   missing_items=[], conflicts=[]) for row in context['chapters']], notes=[])

    def test_unmet_redline_is_rewritten_by_kimi_and_rechecked(self):
        self.store.writing.upload(self.project['id'], self.request(files=[{
            'name': '案例.txt', 'data': base64.b64encode('历史项目按时提供服务保障。'.encode()).decode()}]))
        rid, calls = self.redline['id'], []

        def kimi(system, content, schema, model=None, **_):
            calls.append((model, 'repair' if '修正员' in system else 'review'))
            if '修正员' in system:
                chapter = next(row for row in content['chapters'] if row['id'] == self.chapter)
                return {'items': [dict(chapter_id=self.chapter, issue_ids=[rid], source_ids=[], summary='补充承诺',
                                       text=chapter['text'] + '\n我方承诺投标有效期为150天。')]}
            text = next((row['text'] for row in content['chapters'] if row['id'] == self.chapter), '')
            if '150天' in text:
                return {'items': [dict(id=rid, verdict='satisfied', reason='已承诺', evidence='我方承诺投标有效期为150天。',
                                       chapter_ids=[self.chapter], suggestion='')]}
            return {'items': [dict(id=rid, verdict='risk', reason='未承诺有效期', evidence='',
                                   chapter_ids=[self.chapter], suggestion='补充有效期承诺')]}

        with patch('writing_model.complete_json', side_effect=kimi), \
                patch('writing_model.generate', side_effect=self.draft):
            self.store.writing.start(self.project['id'], self.request(chat=True))
        job = self.store.writing.describe(self.project['id'])['job']
        self.assertEqual('completed', job['status'], job.get('error'))
        kimi_id = 'moonshotai/kimi-k3.1-pro'
        self.assertEqual([(kimi_id, 'review'), (kimi_id, 'repair'), (kimi_id, 'review')], calls)
        row = next(r for r in job['chapters'] if r['chapter_id'] == self.chapter)
        self.assertIn('我方承诺投标有效期为150天', row['text'])
        self.assertEqual([rid], row['redline_revisions'][0]['issue_ids'])

    def test_repair_stops_after_two_rounds(self):
        self.store.writing.upload(self.project['id'], self.request(files=[{
            'name': '案例.txt', 'data': base64.b64encode('历史项目按时提供服务保障。'.encode()).decode()}]))
        rid, calls = self.redline['id'], []

        def stubborn(system, content, schema, model=None, **_):
            calls.append('repair' if '修正员' in system else 'review')
            if '修正员' in system:
                chapter = next(row for row in content['chapters'] if row['id'] == self.chapter)
                return {'items': [dict(chapter_id=self.chapter, issue_ids=[rid], source_ids=[], summary='再改',
                                       text=chapter['text'] + '\n我方将按要求响应。')]}
            return {'items': [dict(id=rid, verdict='risk', reason='仍未满足', evidence='',
                                   chapter_ids=[self.chapter], suggestion='补充')]}

        with patch('writing_model.complete_json', side_effect=stubborn), \
                patch('writing_model.generate', side_effect=self.draft):
            self.store.writing.start(self.project['id'], self.request(chat=True))
        job = self.store.writing.describe(self.project['id'])['job']
        self.assertEqual('completed', job['status'], job.get('error'))
        self.assertEqual(['review', 'repair', 'review', 'repair', 'review'], calls)
        self.assertTrue(job['result']['pending_count'] >= 1)


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
