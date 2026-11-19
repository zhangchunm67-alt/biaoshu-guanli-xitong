"""AI redline review: model result validation, persistence and invalidation."""
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

import framework_service as service
import redline_ai
import redline_scan
from server import Store


class RedlineAiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bid-redline-ai-')
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / 'data')
        self.addCleanup(self.store.close)
        document = Document()
        for line in ['设备维保服务采购项目', '第一章 合格供应商资格要求',
                     '供应商须提供2023年1月1日以来至少两个同类合同。',
                     '未提供营业执照复印件的投标将被否决。',
                     '供应商须在两小时内响应报修。', '第五章 响应文件格式',
                     '附件1 报价函', '我方报价为____，供应商签字盖章：____。',
                     '附件2 人员表']:
            document.add_paragraph(line)
        table = document.add_table(rows=2, cols=3)
        for cell, value in zip(table.rows[0].cells, ['姓名', '资格证书', '证明页码']):
            cell.text = value
        document.add_paragraph('注：逐人填写并签章。')
        stream = io.BytesIO()
        document.save(stream)
        self.framework = service.create(self.store, dict(
            name='AI 红线核对', filename='采购.docx',
            data=base64.b64encode(stream.getvalue()).decode()))
        self.framework = service.revise(self.store, self.framework['id'], dict(
            revision=self.framework['revision'], items=self.framework['requirements'],
            confirmed=True, notes_acknowledged=True, acknowledged_removed_ids=[]), 'requirements')
        self.project = service.confirm(self.store, self.framework['id'], {
            'revision': self.framework['revision']})
        self.items = redline_scan.describe(self.store, self.project['id'])['items']
        self.assertTrue(self.items)

    def write_chapters(self, text='我方已提供营业执照复印件，并承诺两小时内响应报修。'):
        project = self.store.require('project', self.project['id'])
        project['chapter_texts'] = {row['id']: text for row in self.framework['outline']}
        project['chapter_revision'] = project.get('chapter_revision', 0) + 1
        self.store.put('project', project)
        self.store.db.commit()

    def test_review_persists_validated_items_and_auto_acknowledges_safe_passes(self):
        self.write_chapters()
        ids = [item['id'] for item in self.items]
        chapter_id = self.framework['outline'][0]['id']
        result = {
            'items': [
                {'id': ids[0], 'verdict': 'satisfied', 'reason': '正文已响应营业执照要求。',
                 'evidence': '我方已提供营业执照复印件。', 'chapter_ids': [chapter_id],
                 'suggestion': ''},
                {'id': 'not-a-redline', 'verdict': 'risk', 'reason': '未知条目', 'evidence': '',
                 'chapter_ids': [], 'suggestion': ''},
                {'id': ids[0], 'verdict': 'risk', 'reason': '重复条目', 'evidence': '',
                 'chapter_ids': [], 'suggestion': ''},
            ]
        }
        with patch('writing_model.complete_json', return_value=result) as call:
            scan = redline_ai.review(self.store, self.project['id'])
        payload = call.call_args.args[1]
        self.assertTrue(payload['redlines'])
        self.assertTrue(payload['chapters'])
        self.assertEqual(ids[0], payload['redlines'][0]['id'])
        self.assertIn(ids[0], scan['ai_review'])
        # 重复结果取最保守结论。
        self.assertEqual('risk', scan['ai_review'][ids[0]]['verdict'])
        self.assertEqual('重复条目', scan['ai_review'][ids[0]]['reason'])
        self.assertTrue(scan['ai_reviewed_at'])
        self.assertFalse(scan['complete'], 'AI 判定有风险时不得自动过关')
        self.assertNotIn('not-a-redline', scan['ai_review'])
        # 单条 satisfied 结果保留可核对的正文引用；无规则风险时可自动过关。
        second = {'items': [
            {'id': ident, 'verdict': 'satisfied', 'reason': '正文已响应营业执照要求。',
             'evidence': '我方已提供营业执照复印件。', 'chapter_ids': [chapter_id],
             'suggestion': '递交前核对原件。'} for ident in ids]}
        # 材料栏目就绪，避免规则层 risk 挡住自动过关。
        project = self.store.require('project', self.project['id'])
        for row in project.get('mappings') or []:
            if row.get('category_id') == 'business_license':
                row.update(action='fill', status='ready', material_ids=row.get('material_ids') or ['mat-x'])
        self.store.put('project', project)
        self.store.db.commit()
        with patch('writing_model.complete_json', return_value=second):
            scan = redline_ai.review(self.store, self.project['id'])
        self.assertEqual('satisfied', scan['ai_review'][ids[0]]['verdict'])
        self.assertEqual([chapter_id], scan['ai_review'][ids[0]]['chapter_ids'])
        self.assertEqual('我方已提供营业执照复印件。', scan['ai_review'][ids[0]]['evidence'])
        self.assertTrue(scan['ai_review'][ids[0]]['evidence_verified'])
        self.assertEqual('递交前核对原件。', scan['ai_review'][ids[0]]['suggestion'])
        self.assertTrue(scan['complete'], 'AI 通过且规则无风险时应自动确认')

    def test_satisfied_without_evidence_is_downgraded_to_unclear(self):
        self.write_chapters()
        ids = [item['id'] for item in self.items]
        with patch('writing_model.complete_json', return_value={'items': [
                {'id': ident, 'verdict': 'satisfied', 'reason': '正文已响应。',
                 'evidence': '', 'chapter_ids': [], 'suggestion': ''} for ident in ids]}):
            scan = redline_ai.review(self.store, self.project['id'])
        for ident in ids:
            self.assertEqual('unclear', scan['ai_review'][ident]['verdict'])
            self.assertIn('未给出对应正文引用', scan['ai_review'][ident]['reason'])
            self.assertFalse(scan['ai_review'][ident]['evidence_verified'])

    def test_satisfied_evidence_is_checked_against_saved_chapter_text(self):
        self.write_chapters()
        ids = [item['id'] for item in self.items]
        with patch('writing_model.complete_json', return_value={'items': [
                {'id': ident, 'verdict': 'satisfied', 'reason': '正文已响应。',
                 'evidence': '我方已提供安全生产许可证。', 'chapter_ids': [], 'suggestion': ''}
                for ident in ids]}):
            scan = redline_ai.review(self.store, self.project['id'])
        for ident in ids:
            self.assertEqual('satisfied', scan['ai_review'][ident]['verdict'])
            self.assertFalse(scan['ai_review'][ident]['evidence_verified'])

    def test_result_is_discarded_when_inputs_change_during_model_call(self):
        self.write_chapters()
        ids = [item['id'] for item in self.items]

        def mutate(*_args, **_kwargs):
            with self.store.lock:
                project = self.store.require('project', self.project['id'])
                project['chapter_texts'] = {key: '模型调用期间修改的正文'
                                            for key in project['chapter_texts']}
                self.store.put('project', project)
                self.store.db.commit()
            return {'items': [{'id': ident, 'verdict': 'satisfied', 'reason': '已响应。',
                               'evidence': '模型调用期间修改的正文', 'chapter_ids': [],
                               'suggestion': ''} for ident in ids]}

        with patch('writing_model.complete_json', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, '已变化'):
                redline_ai.review(self.store, self.project['id'])
        project = self.store.require('project', self.project['id'])
        self.assertNotIn('redline_ai', project)

    def test_review_requires_adopted_chapter_text(self):
        with patch('writing_model.complete_json') as call:
            with self.assertRaisesRegex(ValueError, '生成并采纳投标正文'):
                redline_ai.review(self.store, self.project['id'])
        call.assert_not_called()

    def test_ai_review_expires_when_chapter_text_changes(self):
        self.write_chapters()
        ids = [item['id'] for item in self.items]
        with patch('writing_model.complete_json', return_value={'items': [
                {'id': ident, 'verdict': 'unclear', 'reason': '需人工核对。',
                 'chapter_ids': [], 'suggestion': ''} for ident in ids]}):
            first = redline_ai.review(self.store, self.project['id'])
        self.assertIn('ai_review', first)
        self.write_chapters('修改后的正文，但仍承诺两小时响应。')
        again = redline_scan.describe(self.store, self.project['id'])
        self.assertNotIn('ai_review', again)
        self.assertTrue(again.get('ai_review_stale'))

    def test_legacy_ai_review_without_evidence_is_treated_as_stale(self):
        self.write_chapters()
        ids = [item['id'] for item in self.items]
        with patch('writing_model.complete_json', return_value={'items': [
                {'id': ident, 'verdict': 'unclear', 'reason': '需人工核对。', 'evidence': '',
                 'chapter_ids': [], 'suggestion': ''} for ident in ids]}):
            redline_ai.review(self.store, self.project['id'])
        project = self.store.require('project', self.project['id'])
        project['redline_ai'].pop('version', None)
        self.store.put('project', project)
        self.store.db.commit()
        scan = redline_scan.describe(self.store, self.project['id'])
        self.assertNotIn('ai_review', scan)
        self.assertTrue(scan.get('ai_review_stale'))


if __name__ == '__main__':
    unittest.main()
