"""Validation reuse stays inside one operation; matching never parses UI text."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from docx import Document
from PIL import Image

import engine
from material_rules import ValidationIssues, has_only_partial_issues


class ValidationOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bid-validation-')
        self.root = Path(self.temp.name)
        self.template = self.root / 'template.docx'
        self.picture = self.root / 'evidence.png'
        Image.new('RGB', (100, 80), 'white').save(self.picture)

    def tearDown(self):
        self.temp.cleanup()

    def material(self, category, name, **updates):
        result = dict(id='evidence', category_id=category, name=name,
                      subject=engine.EXPECTED_SUBJECT, mode='page', years=[2025],
                      valid_until='长期', files=[dict(path=str(self.picture), name='evidence.png', rotation=0)])
        result.update(updates)
        return result

    def scan(self, title, material):
        document = Document()
        document.add_heading(title, 1)
        document.save(self.template)
        categories = [copy.deepcopy(c) for c in engine.DEFAULT_CATEGORIES if c['id'] == material['category_id']]
        return engine.scan_document(self.template, categories, [material]), categories

    def test_scan_opens_each_unchanged_image_once_but_next_operation_rechecks(self):
        material = self.material('audit_report', '2025年审计报告')
        original_open = Image.open
        with patch.object(engine.Image, 'open', wraps=original_open) as opened:
            scanned, categories = self.scan('2025-2026年财务审计报告', material)
        self.assertEqual(1, opened.call_count)
        self.assertEqual(['evidence'], scanned['mappings'][0]['material_ids'])
        self.assertIn('missing_years', scanned['mappings'][0]['issue_types'])
        with patch.object(engine.Image, 'open', wraps=original_open) as opened:
            engine.validate_mappings(scanned['mappings'], [material], scanned['headings'], categories)
            engine.validate_mappings(scanned['mappings'], [material], scanned['headings'], categories)
        self.assertEqual(2, opened.call_count)

    def test_context_detects_file_change_and_final_export_revalidates(self):
        material = self.material('business_license', '营业执照')
        scanned, categories = self.scan('营业执照', material)
        context = engine.ValidationContext()
        checked = engine.validate_mappings(scanned['mappings'], [material], scanned['headings'], categories, context=context)
        self.assertEqual('ready', checked[0]['status'])
        self.picture.write_bytes(b'not an image anymore')
        checked = engine.validate_mappings(scanned['mappings'], [material], scanned['headings'], categories, context=context)
        self.assertEqual('blocked', checked[0]['status'])
        with self.assertRaisesRegex(ValueError, '图片损坏'):
            engine.export_document(self.template, self.root / 'output.docx', scanned['mappings'], [material], categories=categories)
        self.assertFalse((self.root / 'output.docx').exists())

    def test_rewording_partial_issues_does_not_change_preselection(self):
        original_append = ValidationIssues.append

        def reword(issues, message, issue_type='invalid'):
            original_append(issues, '覆盖范围尚不完整。' if issue_type != 'invalid' else message, issue_type)

        with patch.object(ValidationIssues, 'append', reword):
            report = self.material('audit_report', '2025年审计报告')
            scanned, _ = self.scan('2025-2026年财务审计报告', report)
            row = scanned['mappings'][0]
            self.assertEqual(['evidence'], row['material_ids'])
            self.assertEqual(['覆盖范围尚不完整。'], row['issues'])
            self.assertTrue(has_only_partial_issues(row))
            tax = self.material('tax_social', '2025年1月纳税证明')
            scanned, _ = self.scan('2025年1月至2月税收和社会保障资金缴纳凭证', tax)
            row = scanned['mappings'][0]
            self.assertEqual(['evidence'], row['material_ids'])
            self.assertEqual({'missing_months', 'missing_evidence_kind'}, set(row['issue_types']))
            self.assertTrue(has_only_partial_issues(row))

    def test_blocking_issue_cannot_become_partial_by_using_old_chinese_prefix(self):
        original_append = ValidationIssues.append

        def reword(issues, message, issue_type='invalid'):
            original_append(issues, '缺少要求年度：测试文案。' if issue_type == 'invalid' else message, issue_type)

        with patch.object(ValidationIssues, 'append', reword):
            material = self.material('business_license', '营业执照', subject='其他公司')
            scanned, categories = self.scan('营业执照', material)
            self.assertEqual([], scanned['mappings'][0]['material_ids'])
            row = dict(scanned['mappings'][0], action='fill', material_ids=['evidence'])
            checked = engine.validate_mappings([row], [material], scanned['headings'], categories)[0]
            self.assertFalse(has_only_partial_issues(checked))
            self.assertIn('invalid', checked['issue_types'])

    def test_shared_metadata_keeps_archive_and_use_policies_distinct(self):
        material = self.material('B01', '历史案例', subject='合作方', valid_until='2000-01-01')
        feedback = engine.material_form_feedback(material, 1, special=True)
        self.assertEqual({}, feedback['errors'])
        self.assertEqual('历史证明已过期，可以归档；使用前须核对。', feedback['warnings']['valid_until'])
        common = engine.material_form_feedback(material, 1)
        self.assertIn('subject', common['errors'])
        self.assertIn('valid_until', common['errors'])
        qualification = dict(material, category_id='B03')
        errors, _ = engine._check_material(qualification, 'B03', [], {}, special=True)
        self.assertTrue(any('已过期' in message for message in errors))
        errors, warnings = engine._check_material(material, 'B01', [], {}, special=True)
        self.assertEqual([], errors)
        self.assertTrue(any('历史业绩' in message for message in warnings))


if __name__ == '__main__':
    unittest.main()
