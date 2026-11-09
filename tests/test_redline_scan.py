"""Local redline checklist after drafting: material risks and acknowledgment."""
import base64
import io
import tempfile
import unittest
from pathlib import Path

from docx import Document

import framework_service as service
import redline_scan
from server import Store


class RedlineScanUnitTests(unittest.TestCase):
    def test_scan_marks_missing_license_material_as_risk(self):
        requirements = [{
            'id': 'r1', 'category': 'redline', 'label': '未附营业执照废标',
            'text': '未按要求提供营业执照复印件的，投标无效。',
        }]
        mappings = [{
            'id': 'm1', 'category_id': 'business_license', 'action': 'missing',
            'status': 'missing', 'material_ids': [],
        }]
        result = redline_scan.scan(requirements, {}, mappings, outline=[])
        self.assertEqual(1, len(result['items']))
        self.assertEqual('risk', result['items'][0]['status'])
        self.assertTrue(any('营业执照' in note for note in result['items'][0]['findings']))
        pending = redline_scan.apply_ack(result, [])
        self.assertFalse(pending['complete'])
        done = redline_scan.apply_ack(result, ['r1'])
        self.assertTrue(done['complete'])

    def test_empty_linked_chapter_is_flagged(self):
        requirements = [{
            'id': 'r2', 'category': 'redline', 'label': '响应时限',
            'text': '未在两小时内响应报修的，按废标处理。',
        }]
        outline = [{'id': 'BidSection1', 'text': '服务响应', 'requirement_ids': ['r2']}]
        result = redline_scan.scan(requirements, {}, [], outline)
        self.assertEqual('risk', result['items'][0]['status'])
        self.assertTrue(any('尚无正文' in note for note in result['items'][0]['findings']))


class RedlineScanStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bid-redline-')
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
            name='红线扫描', filename='采购.docx',
            data=base64.b64encode(stream.getvalue()).decode()))
        self.framework = service.revise(self.store, self.framework['id'], dict(
            revision=self.framework['revision'], items=self.framework['requirements'],
            confirmed=True, notes_acknowledged=True, acknowledged_removed_ids=[]), 'requirements')
        self.project = service.confirm(self.store, self.framework['id'], {
            'revision': self.framework['revision']})

    def test_store_ack_and_invalidate_on_chapter_change(self):
        project = self.store.prepare_text_materials(self.project['id'], text_first=True)
        for row in project['mappings']:
            if row.get('category_id') == 'business_license':
                row.update(action='missing', status='missing', material_ids=[], confirmed=False)
        self.store.put('project', project)
        self.store.db.commit()
        result = redline_scan.describe(self.store, project['id'])
        self.assertTrue(result['items'])
        self.assertFalse(result['complete'])
        saved = redline_scan.acknowledge(self.store, project['id'], {
            'mapping_revision': result['mapping_revision'],
            'chapter_revision': result['chapter_revision'],
            'input_token': result['input_token'],
            'acknowledged_ids': [item['id'] for item in result['items']],
        })
        self.assertTrue(saved['complete'])
        chapter = next(row['id'] for row in self.framework['outline'])
        project = self.store.require('project', self.project['id'])
        project['chapter_texts'] = {chapter: '已补充正文，触发红线输入变化。'}
        project['chapter_revision'] = project.get('chapter_revision', 0) + 1
        self.store.put('project', project)
        self.store.db.commit()
        again = redline_scan.describe(self.store, project['id'])
        self.assertNotEqual(result['input_token'], again['input_token'])
        self.assertFalse(again['complete'])


if __name__ == '__main__':
    unittest.main()
