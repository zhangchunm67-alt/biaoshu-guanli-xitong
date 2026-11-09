"""Procurement forms stay original while identity evidence still fills normally."""
import base64
import io
from pathlib import Path
import sys
import tempfile
import unittest

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
import framework_service
from server import Store, SUBJECT


class PreservedFrameworkFormsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bid-preserved-forms-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data', seed=False)
        self.addCleanup(self.store.close)
        for category in self.store.all('category'):
            category['enabled'] = category['id'] in {'authorization', 'legal_identity'}
            self.store.put('category', category)
        self.store.put('category', dict(id='text_template', name='文书模板', layer='special',
            industry_id='', aliases=['投标函', '承诺函', '授权委托书'], enabled=True, sort_order=100))
        self.templates = {}
        for kind, name in [('bid', '投标函模板'), ('promise', '承诺函模板'),
                           ('authorization', '法定代表人授权委托书模板')]:
            self.templates[kind] = self.store.material(dict(name=name, category_id='text_template',
                subject=SUBJECT, mode='text', valid_until='长期',
                content='SUPPLIER-TEMPLATE-' + kind + '：不得重复加入采购原表。'))
        self.images = []
        for name, category, color in [('张三（法定代表人）身份证', 'legal_identity', 'navy'),
                                      ('李四（委托代理人）身份证', 'authorization', 'red')]:
            image = io.BytesIO()
            Image.new('RGB', (120, 80), color).save(image, format='PNG')
            self.images.append(self.store.material(dict(name=name, category_id=category, subject=SUBJECT,
                mode='page', valid_until='长期', files=[dict(name=category + '.png',
                    data=base64.b64encode(image.getvalue()).decode())])))
        category = self.store.require('category', 'legal_identity')
        category['enabled'] = False
        self.store.put('category', category)
        self.store.db.commit()

    def upload(self, document, name='保留采购文书'):
        data = io.BytesIO()
        document.save(data)
        return dict(name=name, filename='forms.docx', data=base64.b64encode(data.getvalue()).decode())

    def forms(self, *, empty=False, second=False, procurement_source=False):
        document = Document()
        if procurement_source:
            document.add_paragraph('测试服务采购项目')
            document.add_paragraph('第五章 投标文件格式')
        labels = ['投标函', '承诺函', '法定代表人授权委托书']
        for index, label in enumerate(labels, 1):
            document.add_heading(('附件' + str(index) + '：' if procurement_source else '') + label, 1)
            if empty:
                continue
            if index == 1:
                document.add_paragraph('BID-ORIGINAL：我方已研究采购文件，按原要求响应。')
            elif index == 2:
                document.add_paragraph('PROMISE-ORIGINAL：我方承诺如实提供本项目资料。')
                table = document.add_table(rows=2, cols=2)
                table.style = 'Table Grid'
                table.cell(0, 0).text = '采购方承诺事项'
                table.cell(0, 1).text = '签章栏'
                table.cell(1, 0).text = 'ORIGINAL-TABLE-KEEP'
                table.cell(1, 1).text = '________'
            else:
                document.add_paragraph('本人张三系测试单位法定代表人，现委托李四，为本项目代理人。')
                signature = document.add_paragraph()
                signature.add_run('SIGNATURE-KEEP：法定代表人（签字）：________').bold = True
                document.add_paragraph('日期：________')
        document.add_heading('技术方案', 1)
        document.add_paragraph('NEXT-FORM-KEEP：请说明本项目实施安排。')
        if second:
            document.add_heading('法定代表人授权委托书', 1)
            document.add_paragraph('本人王五系另一单位法定代表人，现委托赵六，为本项目代理人。')
            document.add_paragraph('SECOND-FORM-END：日期：________')
        return document

    def project(self, document, *, procurement=True):
        project = self.store.create_project(self.upload(document))
        if procurement:
            stored = self.store.require('project', project['id'])
            framework_id = 'framework_' + project['id']
            stored.update(framework_id=framework_id, framework_artifact_id='word')
            self.store.put('framework', dict(id=framework_id, projects={'word': project['id']},
                project_id=project['id'], review_version=3, requirements_confirmed=True))
            self.store.put('project', stored)
            self.store.db.commit()
        return self.store.prepare_text_materials(project['id'], text_first=True)

    def text_rows(self, project):
        return [row for row in project['mappings'] if row['category_id'] == 'text_template']

    def assert_preserved(self, rows, count=3):
        self.assertEqual(count, len(rows), rows)
        for row in rows:
            self.assertEqual(('skip', [], 'ready', True),
                (row['action'], row['material_ids'], row['status'], row.get('preserve_original')), row)

    def attach(self, project, locator=None, ids=None):
        row = next(row for row in project['mappings'] if row['category_id'] == 'authorization')
        update = dict(id=row['id'], action='fill', material_ids=ids if ids is not None else
                      [item['id'] for item in reversed(self.images)])
        if locator is not None:
            update['locator_id'] = locator
        project = self.store.update_mappings(project['id'], {'mappings': [update]})
        return project, next(row for row in project['mappings'] if row['id'] == update['id'])

    def export(self, project):
        exported = self.store.export(project['id'], {'mode': 'draft'})
        return exported, Document(self.store.path('exports/' + exported['id'] + '.docx'))

    def paragraph_content(self, document):
        return [(p.text, [(r.text, r.bold, r.italic, r.underline) for r in p.runs])
                for p in document.paragraphs if p.text.strip()]

    def test_real_framework_confirmation_keeps_originals_and_exports_both_ids(self):
        framework = framework_service.create(self.store, self.upload(self.forms(procurement_source=True)))
        framework = framework_service.revise(self.store, framework['id'], dict(revision=framework['revision'],
            items=framework['requirements'], confirmed=True, notes_acknowledged=True,
            acknowledged_removed_ids=[]), 'requirements')
        project = framework_service.confirm(self.store, framework['id'], dict(revision=framework['revision']))
        project = self.store.prepare_text_materials(project['id'], text_first=True)
        self.assert_preserved(self.text_rows(project))
        project, image = self.attach(project)
        self.assertEqual('ready', image['status'], image['issues'])
        self.assertTrue(image['locator_id'].startswith('auth-body:'))
        source = self.store.path(self.store.require('project', project['id'])['template_storage'])
        original_bytes = source.read_bytes()
        before = Document(source)
        exported, after = self.export(project)
        self.assertEqual((1, 0, 0), (exported['filled_count'], exported['missing_count'], exported['skipped_count']))
        self.assertEqual(2, len(after.inline_shapes))
        after_content = [item for item in self.paragraph_content(after)[1:]
                         if item[0] not in {'授权人身份证', '代理人身份证'}]
        self.assertEqual(self.paragraph_content(before), after_content)
        self.assertEqual([table._tbl.xml for table in before.tables], [table._tbl.xml for table in after.tables])
        self.assertEqual(original_bytes, source.read_bytes())
        text = '\n'.join(p.text for p in after.paragraphs)
        self.assertNotIn('SUPPLIER-TEMPLATE-', text)
        self.assertNotIn('【人工跳过：', text)
        self.assertNotIn('【待补：', text)
        nodes = list(after.element.body)
        signature = next(i for i, node in enumerate(nodes) if 'SIGNATURE-KEEP' in engine._text(node))
        following = next(i for i, node in enumerate(nodes) if 'NEXT-FORM-KEEP' in engine._text(node))
        pictures = [i for i, node in enumerate(nodes) if list(node.iter(qn('w:drawing')))]
        self.assertTrue(all(signature < i < following for i in pictures))

    def test_empty_form_sections_also_need_no_extra_text_template(self):
        category = self.store.require('category', 'authorization')
        category['enabled'] = False
        self.store.put('category', category)
        project = self.project(self.forms(empty=True))
        self.assert_preserved(self.text_rows(project))
        project = self.store.update_mappings(project['id'], {'mappings': [
            dict(id=row['id'], confirmed=True) for row in self.text_rows(project)]})
        exported, result = self.export(project)
        self.assertEqual((0, 0, 0), (exported['filled_count'], exported['missing_count'], exported['skipped_count']))
        self.assertEqual([p.text for p in self.forms(empty=True).paragraphs],
                         [p.text for p in result.paragraphs][1:])

    def test_legacy_missing_or_selected_rows_refresh_and_stale_fill_are_normalized(self):
        project = self.project(self.forms())
        project, image = self.attach(project)
        stored = self.store.require('project', project['id'])
        rows = self.text_rows(stored)
        for index, row in enumerate(rows):
            row.update(action='missing' if index == 0 else 'fill',
                material_ids=[] if index == 0 else [self.templates['bid']['id']],
                status='missing' if index == 0 else 'ready', confirmed=True, note='保留人工备注')
            row.pop('preserve_original', None)
        self.store.put('project', stored)
        self.store.db.commit()
        refreshed = self.store.public_project(self.store.require('project', project['id']))
        self.assert_preserved(self.text_rows(refreshed))
        self.assertTrue(all(row['note'] == '保留人工备注' for row in self.text_rows(refreshed)))
        stale = [dict(id=row['id'], action='fill', material_ids=[self.templates['bid']['id']], confirmed=False)
                 for row in rows]
        saved = self.store.update_mappings(project['id'], {'mappings': stale})
        self.assert_preserved(self.text_rows(saved))
        exported, result = self.export(saved)
        self.assertEqual(1, exported['filled_count'])
        self.assertEqual(0, exported['missing_count'])
        self.assertEqual(0, exported['skipped_count'])
        self.assertEqual(2, len(result.inline_shapes))
        self.assertNotIn('SUPPLIER-TEMPLATE-', '\n'.join(p.text for p in result.paragraphs))

    def test_original_authorization_keeps_both_role_and_name_validation(self):
        project = self.project(self.forms())
        project, image = self.attach(project, ids=[self.images[0]['id']])
        self.assertEqual('blocked', image['status'])
        self.assertTrue(any('一份授权人身份证和一份代理人身份证' in issue for issue in image['issues']))
        material = self.store.require('material', self.images[0]['id'])
        material['name'] = '王五（法定代表人）身份证'
        self.store.put('material', material)
        self.store.db.commit()
        project, image = self.attach(project)
        self.assertEqual('blocked', image['status'])
        self.assertTrue(any('姓名不一致' in issue for issue in image['issues']))
        with self.assertRaises(ValueError):
            self.export(project)

    def test_duplicate_authorization_forms_do_not_cross_link_identities(self):
        project = self.project(self.forms(second=True))
        texts = self.text_rows(project)
        self.assert_preserved(texts, 4)
        forms = [row for row in texts if '授权' in row['title']]
        image = next(row for row in project['mappings'] if row['category_id'] == 'authorization')
        self.assertEqual('', image['locator_id'])
        project, image = self.attach(project, forms[0]['locator_id'])
        self.assertEqual('auth-body:' + forms[0]['locator_id'], image['locator_id'])
        self.assertEqual('ready', image['status'], image['issues'])
        exported, result = self.export(project)
        nodes = list(result.element.body)
        boundary = next(i for i, node in enumerate(nodes) if 'NEXT-FORM-KEEP' in engine._text(node))
        pictures = [i for i, node in enumerate(nodes) if list(node.iter(qn('w:drawing')))]
        self.assertEqual(2, len(result.inline_shapes))
        self.assertTrue(all(index < boundary for index in pictures))
        project, image = self.attach(project, forms[1]['locator_id'])
        self.assertEqual('auth-body:' + forms[1]['locator_id'], image['locator_id'])
        self.assertEqual('blocked', image['status'])
        self.assertTrue(any('姓名不一致' in issue for issue in image['issues']))

    def test_explicit_agent_attachment_stays_put_with_another_blank_authorization(self):
        document = self.forms()
        next(p for p in document.paragraphs if p.text == '技术方案').insert_paragraph_before(
            '附：委托代理人身份证复印件（正反面）')
        document.add_heading('法定代表人授权委托书', 1)
        project = self.project(document)
        self.assert_preserved(self.text_rows(project), 4)
        original = next(row for row in project['mappings'] if row['category_id'] == 'authorization')
        self.assertTrue(original['locator_id'].startswith('at:authorization:'), original)
        project, image = self.attach(project, ids=[self.images[1]['id']])
        self.assertEqual(original['locator_id'], image['locator_id'])
        self.assertEqual('ready', image['status'], image['issues'])
        exported, result = self.export(project)
        self.assertEqual((1, 0, 0), (exported['filled_count'], exported['missing_count'], exported['skipped_count']))
        self.assertEqual(1, len(result.inline_shapes))
        nodes = list(result.element.body)
        slot = next(i for i, node in enumerate(nodes) if '附：委托代理人身份证复印件' in engine._text(node))
        boundary = next(i for i, node in enumerate(nodes) if engine._text(node) == '技术方案')
        picture = next(i for i, node in enumerate(nodes) if list(node.iter(qn('w:drawing'))))
        self.assertLess(slot, picture)
        self.assertLess(picture, boundary)

    def test_toc_and_explicit_material_placeholder_are_not_preserved_forms(self):
        document = Document()
        document.styles.add_style('TOC 1', WD_STYLE_TYPE.PARAGRAPH)
        document.add_paragraph('目录')
        document.add_paragraph('投标函\t1', 'TOC 1')
        document.add_paragraph('承诺函\t2', 'TOC 1')
        document.add_heading('投标函', 1)
        document.add_paragraph('采购投标函正文。')
        document.add_heading('其他材料', 1)
        document.add_paragraph('{{承诺函}}')
        source = self.root / 'toc-and-placeholder.docx'
        document.save(source)
        categories = [self.store.require('category', 'text_template')]
        scanned = engine.scan_document(source, categories, [], ['text_template'], preserve_forms=True)
        by_id = {heading['id']: heading for heading in scanned['headings']}
        rows = scanned['mappings']
        self.assertEqual(2, len(rows), rows)
        original = next(row for row in rows if by_id[row['locator_id']]['kind'] != 'placeholder')
        placeholder = next(row for row in rows if by_id[row['locator_id']]['kind'] == 'placeholder')
        self.assert_preserved([original], 1)
        self.assertFalse(placeholder.get('preserve_original'))
        self.assertNotEqual('skip', placeholder['action'])
        self.assertFalse(any('\t' in row['title'] for row in rows))

    def test_imported_template_without_framework_still_accepts_text_material(self):
        category = self.store.require('category', 'authorization')
        category['enabled'] = False
        self.store.put('category', category)
        project = self.project(self.forms(), procurement=False)
        self.assertFalse(project.get('framework_id'))
        rows = self.text_rows(project)
        self.assertEqual(3, len(rows))
        self.assertFalse(any(row.get('preserve_original') for row in rows))
        updates = []
        for row in rows:
            kind = 'authorization' if '授权' in row['title'] else 'promise' if '承诺' in row['title'] else 'bid'
            updates.append(dict(id=row['id'], action='fill', material_ids=[self.templates[kind]['id']]))
        project = self.store.update_mappings(project['id'], {'mappings': updates})
        exported, result = self.export(project)
        self.assertEqual(3, exported['filled_count'])
        text = '\n'.join(p.text for p in result.paragraphs)
        for kind in self.templates:
            self.assertEqual(1, text.count('SUPPLIER-TEMPLATE-' + kind))


if __name__ == '__main__':
    unittest.main()
