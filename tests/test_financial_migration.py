"""Version 12 must migrate financial scopes without resetting unrelated reviews."""
import base64
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from docx import Document

import engine
from server import Store


class FinancialMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bid-financial-migration-')
        self.store = Store(Path(self.temp.name), seed=False)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def project(self, years=()):
        document = Document()
        document.add_heading('公司简介', 1)
        for index, year in enumerate(years, 1):
            document.add_heading(f'包件{index}', 1)
            document.add_heading(f'{year}年度财务审计报告', 2)
        raw = io.BytesIO()
        document.save(raw)
        public = self.store.create_project({'name': '版本迁移验收', 'filename': 'template.docx',
            'data': base64.b64encode(raw.getvalue()).decode()})
        project = self.store.require('project', public['id'])
        project['mappings'] = [row for row in project['mappings'] if row.get('locator_id')]
        for row in project['mappings']:
            row.update(action='skip', confirmed=True, note='保留已核对的选择')
        project['mappings'] = engine.validate_mappings(project['mappings'], [], project['headings'],
                                                        self.store.matching_categories(project))
        project.update(common_locator_version=11, special_selection_confirmed=True, status='已确认')
        self.store.put('project', project)
        self.store.db.commit()
        return project

    def test_unrelated_project_preserves_review_and_token_without_rescan(self):
        project = self.project()
        before = copy.deepcopy(project)
        token = self.store.mapping_revision(project)
        with patch.object(self.store, 'scan_project_template', side_effect=AssertionError('No financial rows')):
            result = self.store.public_project(project)
        expected = dict(before, common_locator_version=12)
        self.assertEqual(expected, self.store.require('project', project['id']))
        self.assertEqual(token, result['mapping_revision'])
        self.assertEqual('已确认', result['status'])

    def test_unchanged_financial_requirements_keep_all_confirmations(self):
        project = self.project((2024,))
        before = copy.deepcopy(project)
        token = self.store.mapping_revision(project)
        result = self.store.public_project(project)
        self.assertEqual(dict(before, common_locator_version=12), self.store.require('project', project['id']))
        self.assertEqual(token, result['mapping_revision'])
        self.assertTrue(all(row['confirmed'] for row in result['mappings']))

    def test_scoped_financial_migration_only_invalidates_changed_financial_rows(self):
        project = self.project((2024, 2025))
        combined = project['requirements']['audit_report']
        for heading in project['headings']:
            if 'audit_report' in heading.get('category_ids', []):
                heading['requirement'] = copy.deepcopy(combined)
        for row in project['mappings']:
            if row['category_id'] == 'audit_report':
                row['requirement'] = copy.deepcopy(combined)
                row['required_years'] = [2024, 2025]
        self.store.put('project', project)
        self.store.db.commit()
        before = copy.deepcopy(project)
        result = self.store.public_project(project)
        audit = [row for row in result['mappings'] if row['category_id'] == 'audit_report']
        self.assertEqual([[2024], [2025]], [row['required_years'] for row in audit])
        self.assertTrue(all(not row['confirmed'] for row in audit))
        self.assertEqual('待确认', result['status'])
        previous = {row['id']: row for row in before['mappings']}
        for row in result['mappings']:
            if row['category_id'] != 'audit_report':
                self.assertEqual(previous[row['id']], row)
            for field in ('locator_id', 'material_ids', 'action', 'note'):
                self.assertEqual(previous[row['id']][field], row[field])
        persisted = self.store.require('project', project['id'])
        with patch.object(self.store, 'scan_project_template', side_effect=AssertionError('Migration repeated')):
            self.store.public_project(persisted)

    def test_archived_version_11_project_is_unchanged_even_by_direct_sync_calls(self):
        project = self.project((2024, 2025))
        project['framework_id'] = 'framework_archive'
        self.store.put('framework', {'id': 'framework_archive', 'projects': {'word': 'current'}})
        self.store.put('project', project)
        self.store.db.commit()
        before = copy.deepcopy(project)
        with patch.object(self.store, 'scan_project_template', side_effect=AssertionError('Archived project rescanned')):
            self.store.sync_common_locations(project)
            self.store.sync_special_locations(project)
            self.store.sync_material_matches(project)
            self.store.public_project(project)
        self.assertEqual(before, project)
        self.assertEqual(before, self.store.require('project', project['id']))


if __name__ == '__main__':
    unittest.main()
