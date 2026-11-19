import base64
import io
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

import engine
import framework_service
from server import Store


def _docx_upload(doc):
    stream = io.BytesIO()
    doc.save(stream)
    return base64.b64encode(stream.getvalue()).decode()


class OutlineWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name), seed=False)
        self.addCleanup(self.store.close)
        model_patch = patch('outline_workflow.model_call', side_effect=self.model)
        self.mock_model = model_patch.start()
        self.addCleanup(model_patch.stop)
        document = Document()
        document.add_heading('第六章 投标文件格式', 1)
        document.add_paragraph('一、投标函')
        document.add_paragraph('采购原表要求签字盖章。')
        document.add_paragraph('二、技术方案')
        document.add_paragraph('【据实编制】')
        document.add_paragraph('三、分项报价表')
        table = document.add_table(2, 2)
        table.cell(0, 0).text = '投标报价'
        table.cell(0, 1).text = '含税单价'
        document.add_paragraph('四、其他材料（本项目不涉及）')
        document.add_paragraph('1、无需提供的下级材料')
        document.add_paragraph('五、授权说明（如适用）')
        document.add_paragraph('六、补充声明函')
        document.add_paragraph('本标包不适用。')
        self.tender = _docx_upload(document)

    def model(self, system, content, schema):
        value = json.loads(content)
        if 'units' in value:
            self.assertIn('“本项目不涉及”“本项目不适用”', system)
            excluded = [row for row in value['units'] if row.get('not_applicable')]
            self.assertEqual(['四、其他材料（本项目不涉及）', '1、无需提供的下级材料',
                              '六、补充声明函', '本标包不适用。'], [row['text'] for row in excluded])
            self.assertFalse(any(row.get('prescribed_numbered_heading') for row in excluded))
            return {'items': [{'source_index': row['index'], 'level': 2 if row['text'].startswith('1、') else 1}
                              for row in value['units'] if re.match(r'^[一二三四五1]、', row['text'])]}
        row = value['rows'][0]
        return {'items': [{'requirement_id': row['id'], 'suggestion': '请逐项核对采购要求。'}]}

    def intake(self, name):
        record = framework_service.create(self.store, {'name': name, 'filename': '采购.docx',
            'data': self.tender, 'outline_choice': True})
        self.assertEqual([], record['artifacts'])
        self.assertEqual([], record['outline'])
        return framework_service.revise(self.store, record['id'], {'revision': record['revision'],
            'items': record['requirements'], 'confirmed': True, 'notes_acknowledged': True}, 'requirements')

    def wait_job(self, key, kind):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with self.store.lock:
                result = self.store.outline_jobs.describe(key, kind)
            if result['status'] in {'failed', 'completed'}:
                return result
            time.sleep(.02)
        self.fail('Outline job did not finish')

    def test_custom_outline_is_authoritative_calibration_and_stage_snapshot(self):
        record = self.intake('自定义测试')
        key = record['id']
        record = framework_service.revise(self.store, key, {'revision': record['revision'],
            'text': '1. 投标函\n2. 技术方案\n2.1 自定义子节\n3. 授权说明（如适用）'}, 'outline')
        summary = next(row for row in framework_service.list_frameworks(self.store) if row['id'] == key)
        self.assertEqual('custom', summary['outline_mode'])
        self.assertFalse(summary['projects'].get('word'))
        self.assertEqual(['三、分项报价表'], [item['text'] for item in record['outline_missing']])
        self.assertEqual(2, len(record['outline_inapplicable']))
        # 自定义目录以上传内容为准：未收录的采购原表只提示，不再阻断确认。
        project = framework_service.confirm(self.store, key, {'revision': record['revision']})
        self.assertTrue(project.get('id'))
        self.assertEqual(project['id'], self.store.require('framework', key)['projects']['word'])
        overview = self.store.stage_snapshot(project['id'])[0]['stage_overview']
        self.assertEqual(['三、分项报价表'], overview['missing_original_forms'])
        with self.assertRaisesRegex(ValueError, '重新打开阶段预览'):
            self.store.prepare_export(project['id'], {'mode': 'stage', 'mapping_revision': overview['mapping_revision']})
        exported = self.store.export(project['id'], {'mode': 'stage', **overview})
        document = Document(self.store.path('exports/' + exported['id'] + '.docx'))
        self.assertIn('禁止投标', document.paragraphs[0].text)
        # 上传目录没有“分项报价表”，因此导出也不应擅自补入该采购原表。
        self.assertFalse(any(table.cell(0, 1).text == '含税单价' for table in document.tables))
        self.assertEqual(['1. 投标函', '2. 技术方案', '2.1 自定义子节', '3. 授权说明（如适用）'],
                         [p.text for p in document.paragraphs if p.style.name.startswith('BidFlowHeading') or
                          p.style.style_id.startswith('BFHeading')])
        result = self.wait_job(key, 'calibration')
        self.assertEqual('completed', result['status'], result.get('error'))
        self.assertTrue(any(item['suggestion'] for item in result['rows']))
        excluded = next(item for item in result['rows'] if item.get('not_applicable'))
        self.assertEqual([], excluded['automatic'])
        self.assertEqual('', excluded['suggestion'])

    def test_automatic_uses_source_numbering_and_allows_one_subtree_edit(self):
        record = self.intake('自动测试')
        key = record['id']
        previous_record = self.store.require('framework', key)
        previous_record['_auto_outline'] = {'items': [{'source_index': 1, 'level': 1, 'text': '旧目录'}]}
        previous_record['_outline_job'] = {'status': 'completed'}
        framework_service.save_record(self.store, previous_record)
        self.store.db.commit()
        self.assertEqual('idle', self.store.outline_jobs.describe(key, 'automatic')['status'])
        with self.assertRaisesRegex(ValueError, '请先完成 AI 目录识别'):
            framework_service.revise(self.store, key, {'revision': record['revision'], 'automatic': True}, 'outline')
        self.store.outline_jobs.start(key, 'automatic')
        self.assertEqual('completed', self.wait_job(key, 'automatic')['status'])
        self.assertEqual(1, self.mock_model.call_count)
        record = framework_service.revise(self.store, key, {'revision': record['revision'], 'automatic': True}, 'outline')
        self.assertEqual(['一、投标函', '二、技术方案', '三、分项报价表', '五、授权说明（如适用）'],
                         [item['text'] for item in record['outline']])
        self.assertEqual([], record['outline_missing'])
        edited = list(record['outline'])
        edited.insert(2, {'text': '2.1 自定义技术小节', 'level': 2})
        revised = framework_service.revise(self.store, key, {'revision': record['revision'], 'items': edited}, 'outline')
        self.assertEqual([(r['id'], r['text']) for r in record['outline']],
                         [(r['id'], r['text']) for r in revised['outline'] if r['level'] == 1])

    def test_project_case_is_optional_and_reusable(self):
        record = self.intake('案例测试')
        key = record['id']
        self.store.outline_jobs.start(key, 'automatic')
        self.assertEqual('completed', self.wait_job(key, 'automatic')['status'])
        record = framework_service.revise(self.store, key, {'revision': record['revision'], 'automatic': True}, 'outline')
        project = framework_service.confirm(self.store, key, {'revision': record['revision']})
        files = [{'name': '案例.txt', 'data': base64.b64encode('案例证据仅供测试。'.encode()).decode()}]
        body = {'case_revision': 0, 'mapping_revision': project['mapping_revision'],
                'files': files, 'save_to_library': {'name': '项目复用案例'}}
        with self.assertRaises(PermissionError):
            self.store.writing.upload(project['id'], body, remote=True)
        result = self.store.writing.upload(project['id'], body)
        ident = result['saved_library_case_id']
        self.assertEqual('项目复用案例', self.store.require('library_case', ident)['name'])
        self.assertEqual('案例.txt', self.store.require('library_case', ident)['files'][0]['name'])
        selection = self.store.contributions.package([ident])
        self.assertEqual(1, len(selection))
        self.assertIn('项目复用案例', selection[0]['name'])
        following = self.intake('跨项目复用测试')
        other = following['id']
        self.store.outline_jobs.start(other, 'automatic')
        self.assertEqual('completed', self.wait_job(other, 'automatic')['status'])
        following = framework_service.revise(self.store, other, {
            'revision': following['revision'], 'automatic': True}, 'outline')
        second = framework_service.confirm(self.store, other, {'revision': following['revision']})
        reused = self.store.writing.upload(second['id'], {'case_revision': 0,
            'mapping_revision': second['mapping_revision'], 'library_case_ids': [ident]})
        self.assertEqual([ident], reused['library_case_ids'])
        self.assertIn('案例证据仅供测试', reused['case_sources'][0]['text'])

    def test_skip_survives_rescan_and_resume_remains_available(self):
        document = Document()
        document.add_heading('一、营业执照', 1)
        template = {'name': '材料章测试', 'filename': '模板.docx',
                    'data': _docx_upload(document), 'material_conflicts': True}
        project = self.store.create_project(template)
        mapping = next(row for row in project['mappings'] if row['category_id'] == 'business_license')
        key, ident = project['id'], mapping['id']
        project = self.store.update_mappings(key, {'mappings': [{'id': ident, 'action': 'skip'}],
                                                   'review_ids': [ident]})
        self.assertEqual('skip', next(row for row in project['mappings'] if row['id'] == ident)['action'])
        project = self.store.rescan(key)
        self.assertEqual('skip', next(row for row in project['mappings'] if row['id'] == ident)['action'])
        project = self.store.update_mappings(key, {'mappings': [{'id': ident, 'action': 'missing'}]})
        self.assertEqual('missing', next(row for row in project['mappings'] if row['id'] == ident)['action'])


class MaterialConflictTests(unittest.TestCase):
    def test_same_chapter_duplicate_requires_decision_and_cross_chapter_reuse_is_allowed(self):
        headings = [
            {'id': 'h1', 'chapter_id': 'BidSection1', 'title': '位置一', 'category_ids': []},
            {'id': 'h2', 'chapter_id': 'BidSection1', 'title': '位置二', 'category_ids': []},
            {'id': 'h3', 'chapter_id': 'BidSection2', 'title': '另一章', 'category_ids': []},
        ]
        mappings = [{'id': 'm' + str(n), 'category_id': 'company_profile', 'layer': 'common',
                     'action': 'fill', 'material_ids': ['material'], 'locator_id': 'h' + str(n),
                     'conflict_review': True} for n in (1, 2, 3)]
        materials = [{'id': 'material', 'mode': 'text', 'content': '统一的案例文字', 'files': []}]
        with patch('engine._check_material', return_value=([], [])):
            results = engine.validate_mappings(mappings, materials, headings)
            self.assertEqual(['ready', 'blocked', 'ready'], [r['status'] for r in results])
            self.assertEqual('duplicate', results[1]['content_conflict'])
            mappings[1]['existing_handling'] = 'keep'
            results = engine.validate_mappings(mappings, materials, headings)
            self.assertTrue(results[1]['retains_content'])
            self.assertEqual('skipped', results[1]['status'])


if __name__ == '__main__':
    unittest.main()
