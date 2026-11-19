"""Local redline checklist after drafting: material risks and acknowledgment."""
import base64
import copy
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
            self.assertTrue(item['manual_check'])
            self.assertFalse(item['auto_checked'])
        plain = redline_scan.scan([{'id': 'plain', 'category': 'redline',
                                    'label': '一般', 'text': '投标文件不符合要求的，投标无效。'}],
                                  {}, [], [])['items'][0]
        self.assertFalse(plain['manual_check'])
        self.assertTrue(plain['hints'])

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

    def test_heading_covered_by_written_descendant_is_not_flagged_empty(self):
        requirements = [{'id': 'r4', 'category': 'redline', 'label': '响应',
                         'text': '未在两小时内响应报修的，按废标处理。'}]
        outline = [{'id': 'S1', 'text': '一、商务文件', 'level': 1, 'requirement_ids': ['r4']},
                   {'id': 'S11', 'text': '商务要求响应', 'level': 2, 'requirement_ids': []}]
        covered = redline_scan.scan(requirements, {'S11': '两小时内响应。'}, [], outline)['items'][0]
        self.assertEqual('attention', covered['status'])
        self.assertFalse(any('尚无正文' in note for note in covered['findings']))
        # 子章节正文变化会让该条确认依据失效。
        changed = redline_scan.scan(requirements, {'S11': '四小时内响应。'}, [], outline)['items'][0]
        self.assertNotEqual(covered['basis'], changed['basis'])
        empty = redline_scan.scan(requirements, {'S1': '', 'S11': ''}, [], outline)['items'][0]
        self.assertEqual('risk', empty['status'])
        self.assertTrue(any('尚无正文' in note for note in empty['findings']))

    def test_material_revision_changes_basis_and_input_token(self):
        requirements = [{'id': 'r', 'category': 'redline', 'label': '执照',
                         'text': '未附营业执照的，投标无效。'}]
        mappings = [{'id': 'm', 'category_id': 'business_license', 'action': 'fill',
                     'status': 'ready', 'material_ids': ['mat']}]
        first = redline_scan.scan(requirements, {}, mappings, [], {'mat': (1, ('f1',))})
        second = redline_scan.scan(requirements, {}, mappings, [], {'mat': (2, ('f2',))})
        self.assertNotEqual(first['items'][0]['basis'], second['items'][0]['basis'])
        self.assertNotEqual(first['input_token'], second['input_token'])

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

    def test_legacy_acknowledgment_is_migrated_only_when_inputs_unchanged(self):
        self.store.prepare_text_materials(self.project['id'], text_first=True)
        project = self.store.require('project', self.project['id'])
        result = redline_scan.describe(self.store, project['id'])
        self.assertTrue(result['items'])
        legacy_token = redline_scan._legacy_input_token(
            self.framework['requirements'], project.get('chapter_texts', {}),
            project.get('mappings', []))
        ids = [item['id'] for item in result['items']]
        project['redline_review'] = {
            'input_token': legacy_token, 'acknowledged_ids': ids,
            'chapter_revision': project.get('chapter_revision', 0)}
        self.store.put('project', project)
        self.store.db.commit()
        migrated = redline_scan.describe(self.store, project['id'])
        self.assertTrue(migrated['complete'])
        self.assertEqual(sorted(ids), sorted(migrated['acknowledged_ids']))
        # 旧 token 对不上时不得沿用旧确认。
        project['redline_review'] = {'input_token': 'stale', 'acknowledged_ids': ids}
        self.store.put('project', project)
        self.store.db.commit()
        stale = redline_scan.describe(self.store, project['id'])
        self.assertFalse(stale['complete'])
        with self.assertRaisesRegex(ValueError, '重新扫描'):
            redline_scan.acknowledge(self.store, project['id'], {
                'input_token': 'stale', 'acknowledged_ids': []})

    def test_framework_edit_keeps_unchanged_redline_acknowledgments(self):
        first = redline_scan.describe(self.store, self.project['id'])
        redline_scan.acknowledge(self.store, self.project['id'], {
            'acknowledged': {item['id']: item['basis'] for item in first['items']}})
        items = copy.deepcopy(self.framework['requirements'])
        target = next(row for row in items if row['category'] != 'redline')
        target['text'] = (target.get('text') or '') + '（复核）'
        revised = service.revise(self.store, self.framework['id'], {
            'revision': self.framework['revision'], 'items': items, 'confirmed': True,
            'notes_acknowledged': True, 'acknowledged_removed_ids': []}, 'requirements')
        project = service.confirm(self.store, revised['id'], {'revision': revised['revision']})
        self.assertNotEqual(self.project['id'], project['id'])
        again = redline_scan.describe(self.store, project['id'])
        old = {item['id']: item for item in first['items']}
        matched = [item for item in again['items']
                   if old.get(item['id'], {}).get('basis') == item['basis']]
        self.assertTrue(matched, '至少应有一条红线依据未变')
        for item in matched:
            self.assertTrue(item['acknowledged'], item)

    def test_material_file_change_expires_acknowledgment(self):
        self.store.prepare_text_materials(self.project['id'], text_first=True)
        project = self.store.require('project', self.project['id'])
        target = next(row for row in project['mappings'] if row['category_id'] == 'business_license')
        self.store.put('material', {
            'id': 'mat-license', 'category_id': 'business_license', 'name': '营业执照',
            'subject': '', 'version': '', 'years': [], 'valid_until': '', 'mode': 'page',
            'content': '', 'description': '', 'revision': 1, 'file_ids': ['file-old']})
        target['material_ids'] = ['mat-license']
        target['action'] = 'fill'
        target['status'] = 'ready'
        self.store.put('project', project)
        self.store.db.commit()
        first = redline_scan.describe(self.store, project['id'])
        saved = redline_scan.acknowledge(self.store, project['id'], {
            'acknowledged': {item['id']: item['basis'] for item in first['items']}})
        self.assertTrue(saved['complete'])
        material = self.store.require('material', 'mat-license')
        material['revision'] = 2
        material['file_ids'] = ['file-new']
        self.store.put('material', material)
        self.store.db.commit()
        again = redline_scan.describe(self.store, project['id'])
        self.assertFalse(again['complete'])
        self.assertTrue(any(item['ack_expired'] for item in again['items']))

    def test_store_ack_and_invalidate_on_chapter_change(self):
        self.store.prepare_text_materials(self.project['id'], text_first=True)
        project = self.store.require('project', self.project['id'])
        for row in project['mappings']:
            if row.get('category_id') == 'business_license':
                row.update(action='missing', status='missing', material_ids=[], confirmed=False)
        self.store.put('project', project)
        self.store.db.commit()
        result = redline_scan.describe(self.store, project['id'])
        self.assertTrue(result['items'])
        self.assertFalse(result['complete'])
        # 初稿与已核对版本均要求红线过关；未过关时硬拦导出。
        with self.assertRaisesRegex(ValueError, '废标红线'):
            self.store.prepare_export(project['id'], {'mode': 'draft'})
        with self.assertRaisesRegex(ValueError, '废标红线'):
            self.store.prepare_export(project['id'], {'mode': 'reviewed'})
        stale = {item['id']: 'stale' for item in result['items']}
        with self.assertRaisesRegex(ValueError, '重新核对'):
            redline_scan.acknowledge(self.store, project['id'], {'acknowledged': stale})
        saved = redline_scan.acknowledge(self.store, project['id'], {
            'acknowledged': {item['id']: item['basis'] for item in result['items']}})
        self.assertTrue(saved['complete'])
        # 规则层仍有材料风险时，即使已勾选也不能导出。
        with self.assertRaisesRegex(ValueError, '风险|材料|空章'):
            redline_scan.require_complete(self.store, self.store.require('project', project['id']))
        # 材料就绪后，确认可进入导出摘要。
        project = self.store.require('project', project['id'])
        for row in project['mappings']:
            if row.get('category_id') == 'business_license':
                row.update(action='fill', status='ready', material_ids=['mat-ready'])
        self.store.put('project', project)
        self.store.db.commit()
        ready = redline_scan.describe(self.store, project['id'])
        saved = redline_scan.acknowledge(self.store, project['id'], {
            'acknowledged': {item['id']: item['basis'] for item in ready['items']}})
        # 无 AI 且无章节正文时：有红线则导出仍要求 AI；此处先取 enforce=False 摘要。
        summary = redline_scan.require_complete(
            self.store, self.store.require('project', project['id']), enforce=False)
        self.assertEqual(saved['acknowledged_ids'], summary['acknowledged_ids'])
        self.assertTrue(summary['items'])
        self.assertTrue(summary['input_token'])
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

    def test_draft_and_reviewed_both_reject_pending_redlines(self):
        project = self.store.prepare_text_materials(self.project['id'], text_first=True)
        summary = redline_scan.require_complete(self.store, project, enforce=False)
        self.assertFalse(summary['complete'])
        self.assertGreater(summary['pending_count'], 0)
        self.assertTrue(summary['items'])
        with self.assertRaisesRegex(ValueError, '废标红线'):
            redline_scan.require_complete(self.store, self.store.require('project', project['id']), enforce=True)
        with self.assertRaisesRegex(ValueError, '废标红线'):
            self.store.prepare_export(project['id'], {'mode': 'draft'})

    def test_draft_export_blocks_when_redlines_pending(self):
        self.store.prepare_text_materials(self.project['id'], text_first=True)
        project = self.store.require('project', self.project['id'])
        for row in project['mappings']:
            if row.get('category_id') == 'business_license':
                row.update(action='missing', status='missing', material_ids=[], confirmed=False)
        self.store.put('project', project)
        self.store.db.commit()
        with self.assertRaisesRegex(ValueError, '废标红线'):
            self.store.export(project['id'], {'mode': 'draft'})

    def test_ai_satisfied_auto_acknowledges_non_risk_items(self):
        framework = self.store.require('framework', self.framework['id'])
        requirements = [{'id': 'r-auto', 'category': 'redline', 'label': '响应时效',
                         'text': '未在两小时内响应报修的，按废标处理。'}]
        outline = [{'id': 'S1', 'text': '服务响应', 'level': 1, 'requirement_ids': ['r-auto']}]
        framework['requirements'] = [
            row for row in framework['requirements'] if row.get('category') != 'redline'
        ] + requirements
        framework['outline'] = outline
        self.store.put('framework', framework)
        project = self.store.require('project', self.project['id'])
        project['chapter_texts'] = {'S1': '我方承诺两小时内响应报修。'}
        project['chapter_revision'] = 1
        scanned = redline_scan.scan(requirements, project['chapter_texts'], project.get('mappings') or [], outline)
        project['redline_ai'] = {
            'version': 2,
            'input_token': scanned['input_token'],
            'items': {'r-auto': {
                'verdict': 'satisfied', 'reason': '正文已承诺。',
                'evidence': '我方承诺两小时内响应报修。', 'evidence_verified': True,
                'chapter_ids': ['S1'], 'suggestion': ''}},
            'reviewed_at': '2026-01-01T00:00:00+00:00',
        }
        self.store.put('project', project)
        self.store.db.commit()
        described = redline_scan.describe(self.store, project['id'])
        self.assertTrue(described['complete'], described)
        self.assertIn('r-auto', described['acknowledged_ids'])
        saved = self.store.require('project', project['id']).get('redline_review') or {}
        self.assertEqual({}, saved)
        self.assertTrue(described['items'][0]['auto_acknowledged'])

    def test_stale_confirmation_revision_cannot_replace_current_decision(self):
        from errors import ConflictError

        key = self.project['id']
        current = redline_scan.describe(self.store, key)
        row = current['items'][0]
        redline_scan.acknowledge(self.store, key, {
            'review_revision': current['review_revision'], 'acknowledged': {row['id']: row['basis']},
        })
        with self.assertRaises(ConflictError):
            redline_scan.acknowledge(self.store, key, {
                'review_revision': current['review_revision'], 'acknowledged': {},
            })
        self.assertIn(row['id'], redline_scan.describe(self.store, key)['acknowledged_ids'])

    def test_only_rule_checked_non_manual_items_qualify_for_automatic_ack(self):
        good = dict(id='r', status='attention', manual_check=False, auto_checked=True, basis='basis')
        verdict = {'r': {'verdict': 'satisfied', 'evidence_verified': True}}
        for flags in ({'manual_check': True}, {'auto_checked': False}, {'status': 'risk'}):
            with self.subTest(flags=flags):
                self.assertEqual({}, redline_scan.auto_pass_ids({'items': [dict(good, **flags)]}, verdict))
        self.assertEqual({'r': 'basis'}, redline_scan.auto_pass_ids({'items': [good]}, verdict))

if __name__ == '__main__':
    unittest.main()
