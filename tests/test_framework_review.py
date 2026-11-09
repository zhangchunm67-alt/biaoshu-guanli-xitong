"""Business-level persistence and safety checks for editable procurement reviews."""
import base64
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from docx import Document
from lxml import html

import chapter_drafts
import framework_review
import framework_service as service
from server import Store


class FrameworkReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.close)
        doc = Document()
        for line in ['设备维保服务采购项目', '第一章 合格供应商资格要求',
                     '供应商须提供2023年1月1日以来至少两个同类合同。',
                     '供应商须在两小时内响应报修。', '第五章 响应文件格式',
                     '附件1 报价函', '我方报价为____，供应商签字盖章：____。',
                     '附件2 人员表']:
            doc.add_paragraph(line)
        t = doc.add_table(rows=2, cols=3)
        for cell, value in zip(t.rows[0].cells, ['姓名', '资格证书', '证明页码']):
            cell.text = value
        doc.add_paragraph('注：逐人填写并签章。')
        raw = io.BytesIO()
        doc.save(raw)
        self.framework = service.create(self.store, dict(name='审阅测试', filename='采购.docx',
            data=base64.b64encode(raw.getvalue()).decode()))

    def save_requirements(self, record, items=None, confirmed=True, **extra):
        body = dict(revision=record['revision'],
                    items=record['requirements'] if items is None else items, confirmed=confirmed)
        if confirmed:
            body.setdefault('notes_acknowledged', True)
            body.setdefault('acknowledged_removed_ids', [])
        body.update(extra)
        return service.revise(self.store, record['id'], body, 'requirements')

    def test_source_evidence_cannot_be_overwritten_and_manual_edits_reach_html_word(self):
        before = self.framework
        with self.assertRaisesRegex(ValueError, '先保存并确认'):
            service.confirm(self.store, before['id'], {})
        items = copy.deepcopy(before['requirements'])
        row = next(r for r in items if r['category'] == 'qualification')
        original = row['references'][0]['text']
        row['label'] = '已核对的同类合同'
        row['text'] = '2023年起至少两个同类合同；核对采购原文。'
        row['references'][0]['text'] = '伪造的原文'
        after = self.save_requirements(before, items)
        saved = next(r for r in after['requirements'] if r['id'] == row['id'])
        self.assertEqual(original, saved['references'][0]['text'])
        self.assertEqual('manual', saved['origin'])
        report = service.asset(self.store, after['id'], 'html')[0]
        self.assertIn(row['text'], report.read_text())
        doc = Document(service.asset(self.store, after['id'], 'word')[0])
        self.assertIn(row['text'], '\n'.join(p.text for p in doc.paragraphs))
        page = html.parse(str(report))
        for metric in after['metrics']:
            self.assertEqual(metric['count'], len(page.xpath('//*[@id=$x]/details', x=metric['key'])))
        with self.assertRaisesRegex(ValueError, '其他窗口'):
            self.save_requirements(before, items)
        reopened = service.describe(self.store, after['id'])
        self.assertEqual(after, reopened)

    def test_rename_move_add_delete_keeps_fixed_forms_and_existing_drafts(self):
        record = self.save_requirements(self.framework)
        project = service.confirm(self.store, record['id'], {'revision': record['revision']})
        previous = copy.deepcopy(self.store.require('project', project['id']))
        old_word = self.store.path(previous['template_storage']).read_bytes()
        first = next(r['id'] for r in record['outline'] if r['locked_format'])
        chapter_drafts.save_chapters(self.store, project['id'],
            {'revision': 0, 'chapters': [{'id': first, 'text': '已核实正文保留'}]})
        rows = copy.deepcopy(record['outline'])
        next(r for r in rows if r['id'] == first)['text'] = '采购指定报价函（已核对）'
        rows.append({'id': '', 'text': '补充说明', 'level': 1, 'requirement_ids': []})
        revised = service.revise(self.store, record['id'], {'revision': record['revision'], 'items': rows}, 'outline')
        self.assertFalse(revised['projects'])
        with self.assertRaisesRegex(ValueError, '保留原件'):
            service.remove(self.store, record['id'])
        with self.assertRaisesRegex(ValueError, '目录已更新'):
            service.confirm(self.store, record['id'], {'revision': record['revision']})
        self.assertEqual(old_word, self.store.path(previous['template_storage']).read_bytes())
        self.assertEqual(project['id'], revised['legacy_projects'][-1]['id'])
        with self.assertRaisesRegex(ValueError, '已归档'):
            chapter_drafts.save_chapters(self.store, project['id'],
                {'revision': 1, 'chapters': [{'id': first, 'text': '旧窗口不能覆盖归档'}]})
        new = service.confirm(self.store, record['id'], {'revision': revised['revision']})
        self.assertNotEqual(project['id'], new['id'])
        self.assertEqual('已核实正文保留', self.store.require('project', new['id'])['chapter_texts'][first])
        doc = Document(service.asset(self.store, record['id'], 'word')[0])
        self.assertEqual(['姓名', '资格证书', '证明页码'], [c.text for c in doc.tables[0].rows[0].cells])
        self.assertIn('注：逐人填写并签章。', [p.text for p in doc.paragraphs])
        rows = [r for r in revised['outline'] if r['text'] != '补充说明']
        deleted = service.revise(self.store, record['id'], {'revision': revised['revision'], 'items': rows}, 'outline')
        self.assertNotIn('补充说明', [r['text'] for r in deleted['outline']])

    def test_reject_orphan_required_form_invalid_level_and_failed_build_atomically(self):
        record = self.save_requirements(self.framework)
        original = service.asset(self.store, record['id'], 'word')[0].read_bytes()
        rows = copy.deepcopy(record['outline'])
        removed = next(r for r in rows if r['locked_format'])
        with self.assertRaisesRegex(ValueError, '原表'):
            service.revise(self.store, record['id'], {'revision': record['revision'],
                'items': [r for r in rows if r['id'] != removed['id']]}, 'outline')
        rows[0]['level'] = 3
        with self.assertRaisesRegex(ValueError, '跨级'):
            service.revise(self.store, record['id'], {'revision': record['revision'], 'items': rows}, 'outline')
        rows[0]['level'] = 1
        rows[0]['text'] = '改名'
        with patch.object(service, 'run_skill', side_effect=ValueError('构建失败')):
            with self.assertRaisesRegex(ValueError, '构建失败'):
                service.revise(self.store, record['id'], {'revision': record['revision'], 'items': rows}, 'outline')
        self.assertEqual(record, service.describe(self.store, record['id']))
        self.assertEqual(original, service.asset(self.store, record['id'], 'word')[0].read_bytes())
        self.assertFalse(list(Path(self.temp.name).rglob('.revision-*')))

    def test_confirmation_during_build_is_archived_not_lost(self):
        record = self.save_requirements(self.framework)
        rows = copy.deepcopy(record['outline'])
        rows[0]['text'] += '核对'
        original_build = service.build_edition
        created = []
        def confirm_then_build(*args, **kwargs):
            created.append(service.confirm(self.store, record['id'], {'revision': record['revision']}))
            return original_build(*args, **kwargs)
        with patch.object(service, 'build_edition', side_effect=confirm_then_build):
            revised = service.revise(self.store, record['id'],
                {'revision': record['revision'], 'items': rows}, 'outline')
        self.assertEqual(created[0]['id'], revised['legacy_projects'][-1]['id'])
        self.assertFalse(revised['projects'])
        self.assertIsNotNone(self.store.get('project', created[0]['id']))

    def test_removing_requirement_keeps_custom_subtree_under_same_parent(self):
        req = {'id': 'r', 'category': 'score', 'text': '方案30分', 'scope': '', 'references': []}
        plan = framework_review.bind_requirements({'blocks': [{'kind': 'heading', 'text': '原章', 'level': 1}]}, [req])
        rows = framework_review.outline(plan)
        parent = rows[-1]['id']
        rows.append({'id': '', 'text': '人工新增子章', 'level': 2, 'requirement_ids': []})
        plan = framework_review.edit_outline(plan, rows, [req])
        updated = framework_review.bind_requirements(plan, [])
        outline = framework_review.outline(updated)
        self.assertEqual(parent, outline[-2]['id'])
        self.assertEqual('人工新增子章', outline[-1]['text'])
        self.assertEqual(2, outline[-1]['level'])

    def test_directory_edit_preserves_coverage_without_manual_associations(self):
        record = self.save_requirements(self.framework)
        rows = [{k: r[k] for k in ('id', 'text', 'level')} for r in record['outline']]
        locked = next(r for r in record['outline'] if r['locked_format'])
        next(r for r in rows if r['id'] == locked['id'])['text'] += '（已核对）'
        revised = service.revise(self.store, record['id'],
            {'revision': record['revision'], 'items': rows}, 'outline')
        before = {r['id']: r['requirement_ids'] for r in record['outline']}
        after = {r['id']: r['requirement_ids'] for r in revised['outline']}
        self.assertEqual(before, after)
        self.assertEqual({r['id'] for r in record['requirements']},
                         {i for ids in after.values() for i in ids})
        with self.assertRaisesRegex(ValueError, '原表'):
            service.revise(self.store, record['id'], {'revision': revised['revision'],
                'items': [r for r in rows if r['id'] != locked['id']]}, 'outline')

    def test_required_response_deletion_explains_what_must_be_kept(self):
        req = {'id': 'r', 'category': 'score', 'text': '方案30分', 'scope': '', 'references': []}
        plan = framework_review.bind_requirements({'blocks': [
            {'kind': 'heading', 'text': '原章', 'level': 1}]}, [req])
        rows = [{k: r[k] for k in ('id', 'text', 'level')}
                for r in framework_review.outline(plan) if not r['requirement_ids']]
        with self.assertRaisesRegex(ValueError, '方案30分.*请保留'):
            framework_review.edit_outline(plan, rows, [req])

    def test_confirmation_requires_notes_and_removed_column_acks(self):
        record = self.framework
        stored = self.store.require('framework', record['id'])
        stored['notes'] = ['采购附件中的资格证明仍需人工核对。']
        self.store.put('framework', stored)
        self.store.db.commit()
        record = service.describe(self.store, record['id'])
        with self.assertRaisesRegex(ValueError, '待核对事项'):
            self.save_requirements(record, confirmed=True, notes_acknowledged=False,
                                   acknowledged_removed_ids=[])
        qualification = next(row for row in record['requirements'] if row['category'] == 'qualification')
        kept = [row for row in record['requirements'] if row['id'] != qualification['id']]
        with self.assertRaisesRegex(ValueError, '删除的资格/格式要求'):
            self.save_requirements(record, items=kept, confirmed=True, notes_acknowledged=True,
                                   acknowledged_removed_ids=[])
        confirmed = self.save_requirements(record, items=kept, confirmed=True, notes_acknowledged=True,
                                           acknowledged_removed_ids=[qualification['id']])
        self.assertTrue(confirmed['requirements_confirmed'])
        self.assertNotIn(qualification['id'], {row['id'] for row in confirmed['requirements']})


class PrescribedBookBindingTests(unittest.TestCase):
    def test_unmapped_requirements_stay_in_required_books_without_price_leak(self):
        plan = {'blocks': [
            {'kind': 'heading', 'text': '技术标', 'level': 1, 'book_role': 'technical'},
            {'kind': 'heading', 'text': '技术原表', 'level': 2},
            {'kind': 'heading', 'text': '商务标', 'level': 1, 'book_role': 'commercial'},
            {'kind': 'heading', 'text': '报价原表', 'level': 2}]}
        reqs = [{'id': key, 'category': category, 'text': text, 'scope': '', 'references': []}
                for key, category, text in [('q', 'qualification', '供应商须具备经营资质，不接受联合体报价。'),
                    ('s', 'spec', '响应时间不得超过两小时。'),
                    ('p', 'spec', '报价须含税，报价有效期为365天。')]]
        generated = framework_review.bind_requirements(plan, reqs)
        outline = framework_review.outline(generated)
        self.assertEqual(['技术标', '商务标'], [r['text'] for r in outline if r['level'] == 1])
        current = None
        locations = {}
        for section in framework_review.sections(generated)[1]:
            heading = section[0]
            if heading['level'] == 1:
                current = heading['text']
            for ident in heading.get('requirement_ids', []):
                locations[ident] = current
            if current == '技术标':
                self.assertNotIn('报价须含税', '\n'.join(b.get('text', '') for b in section))
        self.assertEqual({'q': '技术标', 's': '技术标', 'p': '商务标'}, locations)


if __name__ == '__main__':
    unittest.main()
