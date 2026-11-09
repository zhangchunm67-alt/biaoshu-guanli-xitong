"""Regression tests for a procurement that requires separate submissions."""
from concurrent.futures import ThreadPoolExecutor
import base64
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chapter_drafts
import framework_service
import server


class FrameworkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = server.Store(self.directory.name)
        self.addCleanup(self.store.close)

    def tender(self):
        document = Document()
        for line in [
            '项目名称：测试采购', '子项目一：甲平台', '子项目二：乙平台',
            '两个子项目互不绑定，分别评审。',
            '各子项目须分别提交服务方案。方案须说明实施计划、团队安排。',
            '各子项目须分别提交含税报价单。', '项目预算最高限价 10 万元。',
        ]:
            document.add_paragraph(line)
        raw = io.BytesIO()
        document.save(raw)
        return {'name': '独立响应测试', 'filename': '本次采购.docx',
                'data': base64.b64encode(raw.getvalue()).decode()}

    def test_single_working_document_uses_both_lots_and_fixed_license(self):
        request = self.tender()
        framework = framework_service.create(self.store, request)
        key = framework['id']
        self.assertEqual(key, framework_service.create(self.store, request)['id'])
        self.assertEqual(list(framework_service.GROUPS), [m['key'] for m in framework['metrics']])
        report, mime, _ = framework_service.asset(self.store, key, 'html')
        self.assertTrue(mime.startswith('text/html'))
        self.assertIn('本次生成的目录', report.read_text(encoding='utf-8'))
        self.assertEqual(['word', 'html'], [a['kind'] for a in framework['artifacts']])
        with self.assertRaisesRegex(ValueError, '先保存并确认'):
            framework_service.confirm(self.store, key, {'artifact_id': 'word', 'revision': framework['revision']})
        framework = framework_service.revise(self.store, key,
            {'revision': framework['revision'], 'items': framework['requirements'], 'confirmed': True,
             'notes_acknowledged': True, 'acknowledged_removed_ids': []}, 'requirements')
        project = framework_service.confirm(self.store, key, {'artifact_id': 'word', 'revision': framework['revision']})
        repeated = framework_service.confirm(self.store, key, {'artifact_id': 'word', 'revision': framework['revision']})
        self.assertEqual(project['id'], repeated['id'])
        reviewed = self.store.prepare_text_materials(project['id'])
        self.assertEqual(['business_license'], [m['category_id'] for m in reviewed['mappings']])
        self.assertEqual('missing', reviewed['mappings'][0]['status'])
        with self.assertRaises(ValueError):
            self.store.prepare_export(project['id'], {'mode': 'reviewed'})
        product = self.store.export(project['id'], {'mode': 'draft'})
        self.assertEqual(1, product['missing_count'])
        with self.assertRaisesRegex(ValueError, '保留原件'):
            framework_service.remove(self.store, key)

    def make_old_records(self):
        request = self.tender()
        fw = framework_service.create(self.store, request)
        key = fw['id']
        record = self.store.require('framework', key)
        root = self.store.path(Path('frameworks') / key)
        report_dir = root / 'legacy'
        report_dir.mkdir()
        # Generate the former checklist and four real independent documents using the unchanged skill.
        word = report_dir / '原编制清单.docx'
        framework_service.run_skill('generate.py', root / record['source_storage'], word)
        record.pop('layout')
        record.pop('legacy_projects')
        record['storage']['word'] = str(word.relative_to(root))
        old_projects = []
        for i, file in enumerate(sorted((report_dir / '原编制清单_独立响应文件').glob('*.docx')), 1):
            aid = 'part-' + str(i)
            record['artifacts'].append({'id': aid, 'kind': 'part', 'name': file.name})
            record['storage'][aid] = str(file.relative_to(root))
            if '报价' in file.name:
                continue
            old_record = dict(record, storage={'word': record['storage'][aid]})
            p = framework_service.new_project(self.store, old_record)
            p.update(framework_artifact_id=aid, name=file.stem, chapter_revision=3,
                     chapter_texts={'BidSection1': '已核对：' + file.stem})
            self.store.put('project', p)
            old_projects.append(p)
            record['projects'][aid] = p['id']
            record['project_id'] = p['id']
        self.store.put('framework', record)
        self.store.db.commit()
        return record, old_projects

    def test_upgrade_recovers_both_drafts_once_and_keeps_originals(self):
        record, old_projects = self.make_old_records()
        key = record['id']
        fw = framework_service.describe(self.store, key)
        self.assertEqual(['word', 'html'], [a['id'] for a in fw['artifacts']])
        self.assertEqual(len(old_projects), len(fw['legacy_projects']))
        main = self.store.require('project', fw['project_id'])
        self.assertEqual(2, len(main['chapter_texts']))
        for old, entry in zip(old_projects, fw['legacy_projects']):
            self.assertEqual(old, self.store.require('project', old['id']))
            target = entry['chapter_map']['BidSection1']
            self.assertEqual(old['chapter_texts']['BidSection1'], main['chapter_texts'][target])
            self.assertTrue(framework_service.asset(self.store, key, entry['artifact_id'])[0].is_file())
        chapter_drafts.save_chapters(self.store, main['id'], {'revision': 1,
            'chapters': [{'id': target, 'text': '升级后在整本中的修改'}]})
        self.assertEqual(main['id'], framework_service.confirm(self.store, key, {'revision': fw['revision']})['id'])
        self.assertEqual('升级后在整本中的修改', self.store.require('project', main['id'])['chapter_texts'][target])
        self.assertEqual([main['id']], [p['id'] for p in self.store.bootstrap()['projects']])

    def test_parallel_first_confirmation_creates_only_one_working_project(self):
        request = self.tender()
        with ThreadPoolExecutor(max_workers=2) as pool:
            frameworks = list(pool.map(lambda _: framework_service.create(self.store, request), range(2)))
            confirmed = framework_service.revise(self.store, frameworks[0]['id'],
                {'revision': frameworks[0]['revision'], 'items': frameworks[0]['requirements'],
                 'confirmed': True, 'notes_acknowledged': True, 'acknowledged_removed_ids': []}, 'requirements')
            projects = list(pool.map(lambda row: framework_service.confirm(self.store, row['id'], {'revision': confirmed['revision']}), frameworks))
        self.assertEqual(frameworks[0]['id'], frameworks[1]['id'])
        self.assertEqual(projects[0]['id'], projects[1]['id'])
        self.assertEqual(1, len(self.store.all('project')))

    def test_changed_legacy_source_preserves_old_draft_and_does_not_publish_checklist(self):
        record, old_projects = self.make_old_records()
        root = self.store.path(Path('frameworks') / record['id'])
        (root / record['source_storage']).write_bytes(b'changed')
        fw = framework_service.describe(self.store, record['id'])
        self.assertTrue(fw['migration_error'])
        self.assertFalse(fw['artifacts'])
        self.assertEqual(2, len(fw['legacy_projects']))
        self.assertEqual(old_projects[0], self.store.require('project', old_projects[0]['id']))
        with self.assertRaisesRegex(ValueError, '原件'):
            framework_service.confirm(self.store, record['id'], {})

    def test_listing_legacy_split_projects_does_not_upgrade_or_hide_their_drafts(self):
        record, old_projects = self.make_old_records()
        before = (self.store.all('framework'), self.store.all('project'))
        folder = self.store.path(Path('frameworks') / record['id'])
        files = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        with patch.object(framework_service, 'describe', side_effect=AssertionError('listing cannot upgrade')), \
             patch.object(framework_service, 'build_edition', side_effect=AssertionError('listing cannot build')), \
             patch.object(self.store, 'put', side_effect=AssertionError('listing cannot write')):
            listed = self.store.bootstrap()['projects']
        self.assertEqual({p['id'] for p in old_projects}, {p['id'] for p in listed})
        self.assertEqual(before, (self.store.all('framework'), self.store.all('project')))
        self.assertEqual(files, {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()})

    def test_unknown_procurement_layout_explains_recovery_and_removes_partial_files(self):
        doc = Document()
        doc.add_paragraph('采购项目邀请函。')
        data = io.BytesIO()
        doc.save(data)
        request = {'name': '特殊格式', 'filename': '采购.docx',
                   'data': base64.b64encode(data.getvalue()).decode()}
        with self.assertRaisesRegex(ValueError, '已有模板'):
            framework_service.create(self.store, request)
        self.assertEqual([], self.store.all('framework'))
        self.assertEqual([], list(self.store.path('frameworks').iterdir()))

    def test_unreadable_upload_leaves_no_framework_or_project(self):
        request = {'name': '无效文件', 'filename': '采购.docx',
                   'data': base64.b64encode(b'This is not a Word file').decode()}
        with self.assertRaises(ValueError):
            framework_service.create(self.store, request)
        self.assertEqual([], self.store.all('framework'))
        self.assertEqual([], self.store.all('project'))
