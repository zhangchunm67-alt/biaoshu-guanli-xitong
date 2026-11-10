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
        pending = redline_scan.apply_ack(result, {})
        self.assertFalse(pending['complete'])
        done = redline_scan.apply_ack(result, {'r1': result['items'][0]['basis']})
        self.assertTrue(done['complete'])
        stale = redline_scan.apply_ack(result, {'r1': 'old-basis'})
        self.assertFalse(stale['complete'])
        self.assertTrue(stale['items'][0]['ack_expired'])

    def test_tax_violation_wording_is_not_a_tax_certificate_requirement(self):
        requirements = [{'id': 'r3', 'category': 'redline', 'label': '信用',
                         'text': '被列入重大税收违法失信主体名单的，投标无效。'}]
        mappings = [{'id': 'm', 'category_id': 'tax_social', 'action': 'missing', 'status': 'missing'}]
        item = redline_scan.scan(requirements, {}, mappings, [])['items'][0]
        self.assertEqual('attention', item['status'])
        self.assertEqual([], item['material_category_ids'])
        requirements[0]['text'] = '未提供近六个月依法缴纳税收证明的，投标无效。'
        item = redline_scan.scan(requirements, {}, mappings, [])['items'][0]
        self.assertEqual('risk', item['status'])

    def test_non_material_redlines_get_specific_hints(self):
        rows = [('d', '未按要求缴纳投标保证金的，投标无效。', '保证金'),
                ('s', '投标文件未按要求签字盖章的，按废标处理。', '签字、盖章'),
                ('p', '报价超过最高限价的，投标无效。', '最高限价'),
                ('v', '投标有效期不足 90 天的，投标无效。', '有效期')]
        requirements = [{'id': i, 'category': 'redline', 'label': i, 'text': t} for i, t, _ in rows]
        items = redline_scan.scan(requirements, {}, [], [])['items']
        for item, (_, _, word) in zip(items, rows):
            self.assertTrue(any(word in hint for hint in item['hints']), item)
            self.assertFalse(item['auto_checked'])

    def test_ack_only_expires_for_redline_whose_basis_changed(self):
        requirements = [{'id': 'a', 'category': 'redline', 'label': '响应', 'text': '两小时内响应，否则废标。'},
                        {'id': 'b', 'category': 'redline', 'label': '执照', 'text': '未附营业执照的，投标无效。'}]
        outline = [{'id': 'S1', 'text': '服务响应', 'requirement_ids': ['a']},
                   {'id': 'S2', 'text': '公司介绍', 'requirement_ids': []}]
        mappings = [{'id': 'm', 'category_id': 'business_license', 'action': 'fill', 'status': 'ready',
                     'material_ids': ['lic']}]
        texts = {'S1': '两小时响应。', 'S2': '公司简介'}
        first = redline_scan.scan(requirements, texts, mappings, outline)
        acks = {item['id']: item['basis'] for item in first['items']}
        unrelated = redline_scan.scan(requirements, dict(texts, S2='公司简介（修改错别字）'), mappings, outline)
        self.assertTrue(redline_scan.apply_ack(unrelated, acks)['complete'])
        linked = redline_scan.scan(requirements, dict(texts, S1='一小时响应。'), mappings, outline)
        state = redline_scan.apply_ack(linked, acks)
        self.assertEqual(['b'], state['acknowledged_ids'])
        material = redline_scan.scan(requirements, texts, [dict(mappings[0], material_ids=['other'])], outline)
        self.assertEqual(['a'], redline_scan.apply_ack(material, acks)['acknowledged_ids'])

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
        with self.assertRaisesRegex(ValueError, '废标红线'):
            self.store.prepare_export(project['id'], {'mode': 'draft'})
        stale = {item['id']: 'stale' for item in result['items']}
        with self.assertRaisesRegex(ValueError, '重新核对'):
            redline_scan.acknowledge(self.store, project['id'], {'acknowledged': stale})
        saved = redline_scan.acknowledge(self.store, project['id'], {
            'acknowledged': {item['id']: item['basis'] for item in result['items']}})
        self.assertTrue(saved['complete'])
        # 服务端导出校验认可已保存的确认。
        redline_scan.require_complete(self.store, self.store.require('project', project['id']))
        # 旧版页面的 input_token 方式仍可用。
        legacy = redline_scan.acknowledge(self.store, project['id'], {
            'input_token': saved['input_token'], 'acknowledged_ids': saved['acknowledged_ids']})
        self.assertTrue(legacy['complete'])
        linked = [row for row in self.framework['outline']
                  if any(i in (row.get('requirement_ids') or []) for i in saved['acknowledged_ids'])
                  and not row.get('locked_format')]
        target = (linked or self.framework['outline'])[0]['id']
        project = self.store.require('project', self.project['id'])
        project['chapter_texts'] = {target: '已补充正文，触发红线输入变化。'}
        project['chapter_revision'] = project.get('chapter_revision', 0) + 1
        self.store.put('project', project)
        self.store.db.commit()
        again = redline_scan.describe(self.store, project['id'])
        self.assertNotEqual(result['input_token'], again['input_token'])
        if linked:
            self.assertFalse(again['complete'])
            self.assertTrue(any(item['ack_expired'] for item in again['items']))

if __name__ == '__main__':
    unittest.main()
