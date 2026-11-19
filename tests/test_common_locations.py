"""Common material requests, append-only attachment areas and legacy upgrades."""
import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
from location_preview import LocationPreview
from server import Store


class CommonLocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bid-common-test-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.docx'
        self.output = self.root / 'output.docx'
        self.categories = copy.deepcopy(engine.DEFAULT_CATEGORIES)

    def tearDown(self):
        self.temp.cleanup()

    def material(self, category, **extra):
        return dict(id=category, category_id=category, name='法人身份证' if category == 'legal_identity' else category,
                    subject=engine.EXPECTED_SUBJECT, mode='text', content='已确认的' + category + '材料',
                    files=[], years=[], valid_until='长期', **extra)

    def scan(self, doc, materials=()):
        doc.save(self.source)
        return engine.scan_document(self.source, self.categories, list(materials))

    def test_punctuated_and_annotated_material_list_is_filled_at_each_entry(self):
        doc = Document()
        doc.add_paragraph('公司简介；')
        doc.add_paragraph('营业执照副本复印件（加盖公章）；')
        materials = [self.material(c) for c in ('business_license', 'company_profile')]
        original_text = [p.text for p in doc.paragraphs]
        scanned = self.scan(doc, materials)
        rows = [dict(r, confirmed=True) for r in scanned['mappings'] if r['status'] == 'ready']
        self.assertEqual(2, len(rows))
        self.assertTrue(all(r['location_match']['method'] == 'entry' for r in rows))
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        engine.export_document(self.source, self.output, rows, materials, categories=self.categories)
        result = [p.text for p in Document(self.output).paragraphs]
        self.assertNotIn('通用材料附件（请商务核对）', result)
        for title,cid in zip(original_text,['company_profile','business_license']):
            self.assertEqual('已确认的'+cid+'材料',result[result.index(title)+1])
        engine.export_document(self.source, self.output, rows, materials, categories=self.categories)
        self.assertEqual(1, [p.text for p in Document(self.output).paragraphs].count('已确认的business_license材料'))
        self.assertEqual(before, hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_id_attachment_request_does_not_claim_proof_body_completed(self):
        doc = Document()
        doc.add_paragraph('投标人须提供营业执照复印件、法人身份证复印件并加盖公章。')
        material = self.material('legal_identity')
        scanned = self.scan(doc, [material])
        row = next(r for r in scanned['mappings'] if r['category_id'] == 'legal_identity')
        self.assertEqual('ready', row['status'])
        self.assertIn('身份证附件不替代证明或授权正文，空白信息和签章须核对。', row['warnings'])
        self.assertIn('证明正文另行核对', row['title'])
        material['valid_until'] = '2001-01-01'
        checked = engine.validate_mappings([row], [material], scanned['headings'], self.categories)[0]
        self.assertEqual('blocked', checked['status'])

    def test_scoring_title_is_not_an_attachment_target(self):
        doc = Document()
        doc.add_heading('第三章 评标办法', 1)
        doc.add_heading('营业执照', 2)
        doc.add_paragraph('投标人应提供营业执照复印件。')
        scanned = self.scan(doc, [self.material('business_license')])
        row = next(r for r in scanned['mappings'] if r['category_id'] == 'business_license')
        self.assertEqual('ca:business_license', row['locator_id'])
        self.assertEqual('appendix', row['location_match']['status'])

    def test_legacy_appendix_digest_locator_still_previews(self):
        # Scoring-section request has no reliable body slot → synthetic appendix.
        doc = Document()
        doc.add_heading('第三章 评标办法', 1)
        doc.add_heading('近三年财务审计报告', 2)
        doc.add_paragraph('投标人应提供近三年财务审计报告。')
        scanned = self.scan(doc)
        row = next(r for r in scanned['mappings'] if r['category_id'] == 'audit_report')
        self.assertEqual('ca:audit_report', row['locator_id'])
        preview = LocationPreview()
        try:
            with patch.object(preview, '_build', return_value={'preview_id': 'test', 'page_count': 2}):
                result = preview.describe({
                    'template': self.source,
                    'template_hash': hashlib.sha256(self.source.read_bytes()).hexdigest(),
                    'template_name': 'source.docx',
                    'locator_id': 'ca:audit_report:deadbeefdeadbeef',
                    'action': 'fill', 'materials': [], 'categories': self.categories,
                })
            self.assertIn('文末另起一页新增材料区', result['description'])
            self.assertEqual([], result['markers'])
        finally:
            preview.close()

    def test_sync_remaps_legacy_appendix_digest_locator(self):
        store = Store(self.root / 'data', seed=False)
        try:
            doc = Document()
            doc.add_heading('第三章 评标办法', 1)
            doc.add_heading('近三年财务审计报告', 2)
            doc.add_paragraph('投标人应提供近三年财务审计报告。')
            path = store.root / 'templates/template.docx'
            path.parent.mkdir(parents=True, exist_ok=True)
            doc.save(path)
            scanned = engine.scan_document(path, store.all('category'), [])
            row = next(r for r in scanned['mappings'] if r['category_id'] == 'audit_report')
            self.assertEqual('ca:audit_report', row['locator_id'])
            stale = 'ca:audit_report:1f7a840bb564a086'
            row.update(locator_id=stale, confirmed=True, location_match={
                'status': 'appendix', 'reason': 'legacy', 'candidates': []})
            project = dict(
                id='legacy-appendix', template_storage='templates/template.docx',
                template_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                mappings=[row],
                headings=[{'id': stale, 'title': '文末新增材料区 · 近三年财务审计报告',
                           'kind': 'common_appendix', 'category_ids': ['audit_report']}],
                special_category_ids=[], common_locator_version=12)
            upgraded = store.sync_common_locations(project)
            self.assertEqual('ca:audit_report', upgraded['mappings'][0]['locator_id'])
            self.assertTrue(upgraded['mappings'][0]['confirmed'])
            self.assertTrue(any(h['id'] == 'ca:audit_report' for h in upgraded['headings']))
            self.assertFalse(any(h['id'] == stale for h in upgraded['headings']))
            self.assertEqual(engine.COMMON_LOCATOR_VERSION, upgraded['common_locator_version'])
        finally:
            store.close()

    def test_real_response_form_preferred_over_scoring_section(self):
        doc = Document()
        doc.add_heading('第三章 评标办法', 1)
        doc.add_heading('营业执照', 2)
        doc.add_heading('第六章 投标文件格式', 1)
        doc.add_heading('营业执照副本（加盖公章）', 2)
        scanned = self.scan(doc, [self.material('business_license')])
        rows = [r for r in scanned['mappings'] if r['category_id'] == 'business_license']
        self.assertEqual(1, len(rows))
        self.assertEqual('营业执照副本（加盖公章）', rows[0]['title'])
        self.assertTrue(rows[0]['locator_id'].startswith('p:'))

    def test_tax_locator_upgrade_preserves_materials_and_manual_locations(self):
        store = Store(self.root / 'upgrade', seed=False)
        self.addCleanup(store.close)
        path = store.root / 'templates/legacy.docx'
        doc = Document()
        doc.add_heading('4.4企业税收及社保缴纳凭证', 2)
        doc.add_paragraph('提供开标日前6个月任意一个月的税收和社会保障资金缴纳凭证。')
        doc.add_heading('候选位置', 2)
        doc.save(path)
        project = dict(id='legacy-tax', template_storage='templates/legacy.docx',
                       template_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                       special_category_ids=[], common_locator_version=15, mappings=[])
        scanned = store.scan_project_template(project)
        target = next(r for r in scanned['mappings'] if r['category_id'] == 'tax_social')
        self.assertEqual('matched', target['location_match']['status'])
        manual = next(h['id'] for h in scanned['headings'] if h['title'] == '候选位置')
        fallback = {'id': 'ca:tax_social', 'kind': 'common_appendix', 'category_ids': ['tax_social']}
        saved = {**target, 'id': 'saved-tax', 'locator_id': fallback['id'],
                 'location_match': {'status': 'appendix'}, 'action': 'fill',
                 'material_ids': ['selected-proof'], 'note': '已人工选材料', 'confirmed': True}
        audit = next(r for r in scanned['mappings'] if r['category_id'] == 'audit_report')
        project.update(headings=[*scanned['headings'], fallback], mappings=[saved, audit])
        manual_project = copy.deepcopy(project)
        upgraded = store.sync_common_locations(project)
        row = next(r for r in upgraded['mappings'] if r['category_id'] == 'tax_social')
        self.assertEqual((target['locator_id'], ['selected-proof'], '已人工选材料', False),
                         (row['locator_id'], row['material_ids'], row['note'], row['confirmed']))
        self.assertEqual(audit, next(r for r in upgraded['mappings'] if r['category_id'] == 'audit_report'))
        self.assertNotIn(fallback['id'], {h['id'] for h in upgraded['headings']})
        manual_project['mappings'][0].update(locator_id=manual, location_match={'status': 'manual'})
        upgraded = store.sync_common_locations(manual_project)
        row, = [r for r in upgraded['mappings'] if r['category_id'] == 'tax_social']
        self.assertEqual((manual, ['selected-proof'], True),
                         (row['locator_id'], row['material_ids'], row['confirmed']))

    def test_negative_request_and_auditor_license_do_not_select_bidder_license(self):
        doc = Document()
        doc.add_paragraph('本项目无需提供营业执照。')
        doc.add_paragraph('审计报告应包含会计师事务所营业执照。')
        scanned = self.scan(doc, [self.material('business_license')])
        row = next(r for r in scanned['mappings'] if r['category_id'] == 'business_license')
        self.assertFalse(row['locator_id'])
        self.assertEqual('missing', row['status'])

    def test_request_in_table_is_not_filled_into_scoring_cell(self):
        doc = Document()
        table=doc.add_table(rows=2, cols=2)
        table.cell(0,0).text='评分项目'
        table.cell(1,0).text='须提供营业执照复印件'
        scanned = self.scan(doc, [self.material('business_license')])
        row = next(r for r in scanned['mappings'] if r['category_id'] == 'business_license')
        self.assertEqual('ca:business_license', row['locator_id'])

    def test_project_managers_id_does_not_select_legal_representatives_id(self):
        doc = Document()
        doc.add_paragraph('请提供项目负责人身份证复印件。')
        scanned = self.scan(doc, [self.material('legal_identity')])
        row = next(r for r in scanned['mappings'] if r['category_id'] == 'legal_identity')
        self.assertFalse(row['locator_id'])
        self.assertFalse(row['material_ids'])

    def test_appendix_preview_describes_new_area_without_fabricating_original_marker(self):
        doc = Document()
        doc.add_paragraph('请说明企业发展历程，并提供企业简介。')
        scanned = self.scan(doc)
        row = next(r for r in scanned['mappings'] if r['category_id'] == 'company_profile')
        preview = LocationPreview()
        try:
            with patch.object(preview, '_build', return_value={'preview_id': 'test', 'page_count': 3}):
                result = preview.describe({'template': self.source, 'template_hash': hashlib.sha256(self.source.read_bytes()).hexdigest(),
                    'template_name': 'source.docx', 'locator_id': row['locator_id'], 'action': 'fill',
                    'materials': [], 'categories': self.categories})
            self.assertEqual(3, result['initial_page'])
            self.assertEqual([], result['markers'])
            self.assertIn('文末另起一页新增材料区', result['description'])
        finally:
            preview.close()

    def test_legacy_automatic_missing_upgrades_but_manual_skip_is_preserved(self):
        store = Store(self.root / 'data', seed=False)
        try:
            doc = Document()
            doc.add_paragraph('公司简介；')
            path = store.root / 'templates/template.docx'
            doc.save(path)
            scanned = engine.scan_document(path, store.all('category'), [])
            row = next(r for r in scanned['mappings'] if r['category_id'] == 'company_profile')
            row.update(locator_id='', confirmed=True, action='missing')
            project = dict(id='legacy', template_storage='templates/template.docx', template_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                           mappings=[row], headings=[], special_category_ids=[])
            upgraded = store.sync_common_locations(project)
            self.assertTrue(upgraded['mappings'][0]['locator_id'].startswith('at:'))
            self.assertFalse(upgraded['mappings'][0]['confirmed'])
            project.pop('common_locator_version')
            project['mappings'][0].update(locator_id='', action='skip', confirmed=True)
            kept = store.sync_common_locations(project)['mappings'][0]
            self.assertEqual(('skip', True, ''), (kept['action'], kept['confirmed'], kept['locator_id']))
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()
