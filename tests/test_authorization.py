import base64
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from docx.oxml.ns import qn
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine


class AuthorizationImagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source, self.output = self.root / 'input.docx', self.root / 'output.docx'
        self.categories = copy.deepcopy(engine.DEFAULT_CATEGORIES)
        self.materials = []
        for cid, who in [('legal_identity', '甲某'), ('authorization', '乙某')]:
            role = '法定代表人' if cid == 'legal_identity' else '委托代理人'
            files = []
            for side in ('人像面', '国徽面'):
                p = self.root / (who + side + '.png')
                Image.new('RGB', (300, 180), 'white').save(p)
                files.append(dict(name=p.name, path=str(p), rotation=0))
            self.materials.append(dict(id=cid, name=who + '（' + role + '）身份证',
                category_id=cid, subject=engine.EXPECTED_SUBJECT, mode='side_by_side',
                files=files, years=[], valid_until='2046-02-04', content=''))

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, document):
        document.save(self.source)
        scan = engine.scan_document(self.source, self.categories, self.materials)
        rows = [r for r in scan['mappings'] if r['category_id'] == 'authorization']
        self.assertEqual(1, len(rows))
        row = dict(rows[0], action='fill', material_ids=['legal_identity', 'authorization'], confirmed=True)
        return row, scan

    def checked(self, row, scan, materials=None):
        return engine.validate_mappings([row], materials or self.materials, scan['headings'], self.categories)[0]

    def form(self):
        doc = Document()
        doc.add_heading('第六章 投标文件格式', 1)
        doc.add_paragraph('法定代表人授权委托书')
        doc.add_paragraph('其他目录项')
        doc.add_heading('附件5：法定代表人授权书格式', 2)
        doc.add_heading('法定代表人授权委托书', 3)
        p = doc.add_paragraph('本授权书声明：注册于（国家或地区的名称）的（公司名称）的在下面签字的')
        p.add_run('（法定代表人姓名、').bold = True
        p.add_run('职务）代表本公司授权（单位名称）的（被授权人的姓名、职务）为合法代理人，就（项目名称/项目编号）处理竞价事宜。')
        doc.add_paragraph('本授权书于   年  月  日签字生效，特此声明。')
        doc.add_paragraph('单位名称：（盖公章）')
        doc.add_paragraph('日期：')
        doc.add_paragraph('附件6：资格证明文件')
        doc.add_paragraph('下面原文不得改变')
        return doc

    def test_matching_entry_receives_only_selected_images_without_generating_body(self):
        doc = Document()
        doc.add_paragraph('法人代表授权书（加盖公章）；')
        row, scan = self.prepare(doc)
        source_bytes = self.source.read_bytes()
        row = self.checked(row, scan)
        self.assertEqual('ready', row['status'], row['issues'])
        self.assertTrue(row['locator_id'].startswith('at:'))
        report = engine.export_document(self.source, self.output, [row], self.materials, categories=self.categories)
        result = Document(self.output)
        text = '\n'.join(p.text for p in result.paragraphs)
        self.assertNotIn('本人甲某', text)
        self.assertNotIn('现委托乙某', text)
        self.assertEqual(4, len(result.inline_shapes))
        self.assertEqual(4, report['results'][0]['file_count'])
        self.assertNotIn('authorization', report['results'][0])
        self.assertEqual(source_bytes, self.source.read_bytes())
        with ZipFile(self.output) as z:
            self.assertIsNone(z.testzip())

    def test_blank_form_keeps_all_text_and_formatting_with_images_before_next_attachment(self):
        doc = self.form()
        row, scan = self.prepare(doc)
        row['material_ids'] = ['authorization']
        row = self.checked(row, scan)
        self.assertEqual('ready', row['status'], row['issues'])
        self.assertTrue(row['locator_id'].startswith('p:'))
        engine.export_document(self.source, self.output, [row], self.materials, categories=self.categories, mode='reviewed')
        result = Document(self.output)
        original = [p.text for p in doc.paragraphs if p.text.strip()]
        self.assertEqual(original, [p.text for p in result.paragraphs if p.text.strip()])
        original_runs = [[(r.text, r.bold) for r in p.runs] for p in doc.paragraphs if p.text.strip()]
        self.assertEqual(original_runs, [[(r.text, r.bold) for r in p.runs] for p in result.paragraphs if p.text.strip()])
        self.assertEqual(2, len(result.inline_shapes))
        children = list(result.element.body)
        image_index = next(i for i, e in enumerate(children) if list(e.iter(qn('w:drawing'))))
        following_index = next(i for i, e in enumerate(children) if '附件6' in engine._text(e))
        self.assertLess(image_index, following_index)

    def test_images_do_not_require_both_roles_or_personal_fields(self):
        doc = Document()
        doc.add_paragraph('请提供法定代表人授权委托书。')
        row, scan = self.prepare(doc)
        for ids in [['authorization'], ['legal_identity'], ['legal_identity', 'authorization']]:
            checked = self.checked(dict(row, material_ids=ids), scan)
            self.assertEqual('ready', checked['status'], checked['issues'])
        self.assertEqual('blocked', self.checked(dict(row, material_ids=[]), scan)['status'])

    def test_retired_settings_are_discarded_and_require_one_new_confirmation(self):
        row, scan = self.prepare(self.form())
        row['authorization'] = dict(enabled=True, signed_on='', project_name='不得写入')
        row['authorization_preview'] = {'body_lines': ['不得写入']}
        with self.assertRaisesRegex(ValueError, '重新核对'):
            engine.export_document(self.source, self.output, [row], self.materials, categories=self.categories)
        checked = self.checked(row, scan)
        self.assertEqual('ready', checked['status'], checked['issues'])
        self.assertFalse(checked['confirmed'])
        self.assertNotIn('authorization', checked)
        self.assertNotIn('authorization_preview', checked)
        checked['confirmed'] = True
        engine.export_document(self.source, self.output, [checked], self.materials, categories=self.categories)
        self.assertNotIn('不得写入', '\n'.join(p.text for p in Document(self.output).paragraphs))

    def test_expired_missing_or_text_materials_still_block(self):
        row, scan = self.prepare(self.form())
        for change in [{'valid_until': '2000-01-01'}, {'files': []}, {'mode': 'text', 'content': '正文'}]:
            materials = copy.deepcopy(self.materials)
            materials[1].update(change)
            self.assertEqual('blocked', self.checked(row, scan, materials)['status'])

    def test_existing_images_and_conflicting_agent_still_block(self):
        doc = self.form()
        doc.paragraphs[6].add_run().add_picture(self.materials[0]['files'][0]['path'])
        row, scan = self.prepare(doc)
        self.assertTrue(any('已含证件图片' in e for e in self.checked(row, scan)['issues']))
        doc = Document()
        doc.add_heading('法定代表人授权委托书', 1)
        doc.add_paragraph('本人甲某系北京腾宝科技有限公司的法定代表人，现委托丙某，为我方代理人。')
        row, scan = self.prepare(doc)
        self.assertTrue(any('丙某' in e and '乙某' in e for e in self.checked(row, scan)['issues']))

    def legal_row(self, doc):
        doc.save(self.source)
        scan = engine.scan_document(self.source, self.categories, self.materials)
        rows = [r for r in scan['mappings'] if r['category_id'] == 'legal_identity']
        self.assertEqual(1, len(rows))
        return dict(rows[0], action='fill', material_ids=['legal_identity'], confirmed=True), scan

    def certificate(self, body):
        doc = Document()
        doc.add_heading('法定代表人身份证明', 1)
        doc.add_paragraph(body)
        doc.add_paragraph('附：法定代表人身份证正反面复印件')
        doc.add_table(rows=1, cols=2).style = 'Table Grid'
        doc.add_paragraph('投标人：（盖章）')
        return doc

    def test_agent_named_without_punctuation_is_compared(self):
        doc = Document()
        doc.add_heading('法定代表人授权委托书', 1)
        doc.add_paragraph('本人甲某系北京腾宝科技有限公司的法定代表人，现委托丙某为我方代理人。')
        row, scan = self.prepare(doc)
        row['material_ids'] = ['authorization']
        issues = self.checked(row, scan)['issues']
        self.assertTrue(any('丙某' in e and '乙某' in e for e in issues), issues)

    def test_legal_representative_id_must_match_certificate_and_authorization(self):
        row, scan = self.legal_row(self.certificate('兹证明丁某系北京腾宝科技有限公司的法定代表人。'))
        checked = self.checked(row, scan)
        self.assertEqual('blocked', checked['status'])
        self.assertTrue(any('原文法定代表人为丁某' in e and '甲某' in e for e in checked['issues']), checked['issues'])
        row, scan = self.legal_row(self.certificate('兹证明甲某系北京腾宝科技有限公司的法定代表人。'))
        self.assertEqual('ready', self.checked(row, scan)['status'])
        # The legal representative's ID placed in an authorization form uses that form's body.
        doc = Document()
        doc.add_heading('法定代表人授权委托书', 1)
        doc.add_paragraph('本人丁某系北京腾宝科技有限公司的法定代表人，现委托乙某为我方代理人。')
        row, scan = self.prepare(doc)
        row['material_ids'] = ['legal_identity']
        issues = self.checked(row, scan)['issues']
        self.assertTrue(any('丁某' in e and '甲某' in e for e in issues), issues)

    def test_unnamed_material_warns_and_blank_template_is_not_compared(self):
        materials = copy.deepcopy(self.materials)
        materials[0]['name'] = '身份证正反面附件'
        row, scan = self.legal_row(self.certificate('兹证明丁某系北京腾宝科技有限公司的法定代表人。'))
        checked = self.checked(row, scan, materials)
        self.assertEqual('ready', checked['status'], checked['issues'])
        self.assertTrue(any('未能从材料名称识别证件人员' in w and '丁某' in w for w in checked['warnings']))
        row, scan = self.legal_row(self.certificate('兹证明（姓名）系（投标人名称）的法定代表人。'))
        checked = self.checked(row, scan)
        self.assertEqual('ready', checked['status'], checked['issues'])
        self.assertFalse(any('请核对是否为本人' in w for w in checked['warnings']))

    def test_store_backfills_names_for_projects_saved_before_the_check(self):
        import base64
        from server import Store
        store = Store(self.root / 'names', seed=False)
        try:
            material = self.materials[0]
            body = {k: v for k, v in material.items() if k not in {'id', 'files'}}
            body['files'] = [dict(name=f['name'], data=base64.b64encode(Path(f['path']).read_bytes()).decode())
                             for f in material['files']]
            mid = store.material(body)['id']
            self.certificate('兹证明丁某系北京腾宝科技有限公司的法定代表人。').save(self.source)
            project = store.create_project(dict(name='旧项目', filename='t.docx',
                                                data=base64.b64encode(self.source.read_bytes()).decode()))
            raw = store.require('project', project['id'])
            row = next(r for r in raw['mappings'] if r['category_id'] == 'legal_identity')
            row.update(action='fill', material_ids=[mid], confirmed=True, status='ready', issues=[])
            for heading in raw['headings']:
                heading.pop('legal_text', None)  # Saved before headings carried names.
            raw.pop('sync_version')
            store.put('project', raw)
            store.db.commit()
            store.migrate_projects()  # Runs at startup.
            migrated = next(r for r in store.require('project', project['id'])['mappings'] if r['id'] == row['id'])
            self.assertEqual('blocked', migrated['status'])
            self.assertFalse(migrated['confirmed'])
            self.assertTrue(any('丁某' in e for e in migrated['issues']))
        finally:
            store.close()

    def test_store_migrates_old_project_and_ignores_cached_client_fields(self):
        from server import Store
        store = Store(self.root / 'store', seed=False)
        try:
            ids = []
            for material in self.materials:
                body = {k: v for k, v in material.items() if k not in {'id', 'files'}}
                body['files'] = [dict(name=f['name'], data=base64.b64encode(Path(f['path']).read_bytes()).decode()) for f in material['files']]
                ids.append(store.material(body)['id'])
            self.form().save(self.source)
            project = store.create_project(dict(name='图片填充', filename='template.docx', data=base64.b64encode(self.source.read_bytes()).decode()))
            raw = store.require('project', project['id'])
            row = next(r for r in raw['mappings'] if r['category_id'] == 'authorization')
            row.update(action='fill', material_ids=ids, confirmed=True, note='保留备注',
                       authorization={'enabled': True}, authorization_preview={'body_lines': ['旧正文']})
            locator, mapping_id = row['locator_id'], row['id']
            raw.pop('sync_version')  # Saved by the earlier text-generation version.
            store.put('project', raw)
            store.db.commit()
            store.migrate_projects()  # Runs at startup.
            reopened = store.public_project(store.require('project', project['id']))
            migrated = next(r for r in reopened['mappings'] if r['id'] == mapping_id)
            self.assertEqual('ready', migrated['status'], migrated['issues'])
            self.assertFalse(migrated['confirmed'])
            self.assertEqual(ids, migrated['material_ids'])
            self.assertEqual(locator, migrated['locator_id'])
            self.assertEqual('保留备注', migrated['note'])
            self.assertNotIn('authorization', migrated)
            saved = store.update_mappings(project['id'], {'mappings': [dict(migrated, confirmed=True, authorization={'enabled': True, 'signed_on': ''})]})
            result = next(r for r in saved['mappings'] if r['id'] == mapping_id)
            self.assertTrue(result['confirmed'])
            self.assertNotIn('authorization', result)
            persisted = next(r for r in store.require('project', project['id'])['mappings'] if r['id'] == mapping_id)
            self.assertNotIn('authorization', persisted)
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()
