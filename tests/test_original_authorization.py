"""Keep a buyer's authorization form and attach IDs without another body template."""
import base64
import io
import unittest
from docx import Document
from docx.oxml.ns import qn
import test_authorization_sequence as sequence_fixture


class OriginalAuthorizationTests(unittest.TestCase):
    setUp = sequence_fixture.AuthorizationSequenceTests.setUp
    tearDown = sequence_fixture.AuthorizationSequenceTests.tearDown

    def original_project(self, named=False, second=False):
        d = Document()
        d.add_heading('法定代表人授权委托书', 1)
        d.add_paragraph('本人张三系测试单位法定代表人，现委托李四，为本项目代理人。' if named else
                        '本人___（法人名字）系___（公司名称）的法定代表人，现委托___（被授权人名字）为我方代理人。', 'Heading 1')
        for text in ['委托期限：', '代理人无转委托权。', '投标人：（公司名称，盖单位章）',
                     '法定代表人：（签字或盖章）', '身份证号码：', '委托代理人：（签字或盖章）',
                     '身份证号码：', '日期：']:
            d.add_paragraph(text, 'Heading 1')
        d.add_heading('承诺与声明', 1)
        d.add_paragraph('NEXT-FORM-KEEP')
        if second:
            d.add_heading('法定代表人授权委托书', 1)
            d.add_paragraph('本人王五系另一单位法定代表人，现委托赵六，为本项目代理人。')
        b = io.BytesIO(); d.save(b)
        p = self.store.create_project(dict(name='采购原表保留', filename='original.docx', data=base64.b64encode(b.getvalue()).decode()))
        p = self.store.prepare_text_materials(p['id'], text_first=True)
        text_rows = [r for r in p['mappings'] if r['category_id'] == 'text_template']
        p = self.store.update_mappings(p['id'], {'mappings': [dict(id=r['id'], action='skip', material_ids=[]) for r in text_rows]})
        return p, text_rows[0]

    def attach(self, p, text):
        row = next(r for r in p['mappings'] if r['category_id'] == 'authorization')
        p = self.store.update_mappings(p['id'], {'mappings': [dict(id=row['id'], locator_id=text['locator_id'],
            action='fill', material_ids=[m['id'] for m in reversed(self.images)])]})
        return p, next(r for r in p['mappings'] if r['id'] == row['id'])

    def test_preserve_form_and_export_ids_after_date_before_next_form(self):
        p, text = self.original_project()
        p, auth = self.attach(p, text)
        self.assertEqual('ready', auth['status'], auth['issues'])
        self.assertTrue(next(r for r in p['mappings'] if r['id'] == text['id'])['preserve_original'])
        source = self.store.path(self.store.require('project', p['id'])['template_storage'])
        before = source.read_bytes()
        snapshot = self.store.preview_snapshot(p['id'], dict(mapping_id=auth['id'], locator_id=text['locator_id'], action='fill'))
        self.assertEqual('auth-body:' + text['locator_id'], snapshot['locator_id'])
        self.assertIsNone(snapshot['authorization_body'])
        for _ in range(2):
            out = self.store.export(p['id'], {'mode': 'draft'})
            d = Document(self.store.path('exports/' + out['id'] + '.docx'))
            nodes = list(d.element.body)
            txt = [''.join(t.text or '' for t in n.iter(qn('w:t'))) for n in nodes]
            date = next(i for i, t in enumerate(txt) if t == '日期：')
            boundary = next(i for i, t in enumerate(txt) if t == '承诺与声明')
            images = [i for i, n in enumerate(nodes) if list(n.iter(qn('w:drawing')))]
            self.assertEqual(2, len(images))
            self.assertTrue(all(date < i < boundary for i in images), txt)
            self.assertEqual(1, [p.text for p in d.paragraphs].count('法定代表人授权委托书'))
            self.assertNotIn('AUTH-BODY-END', '\n'.join(txt))
            self.assertNotIn('人工跳过：法定代表人授权委托书', '\n'.join(txt))
        self.assertEqual(before, source.read_bytes())

    def test_original_names_and_both_roles_still_checked(self):
        p, text = self.original_project(named=True)
        material = self.store.require('material', self.images[0]['id'])
        material['name'] = '王五（法定代表人）身份证'
        self.store.put('material', material); self.store.db.commit()
        p, auth = self.attach(p, text)
        self.assertEqual('blocked', auth['status'])
        self.assertTrue(any('姓名不一致' in x for x in auth['issues']))
        with self.assertRaises(ValueError):
            self.store.export(p['id'], {'mode': 'draft'})

    def test_legacy_heading_refresh_keeps_choices_and_uses_selected_form(self):
        p, text = self.original_project(second=True)
        p, auth = self.attach(p, text)
        self.assertEqual('ready', auth['status'], auth['issues'])
        stored = self.store.require('project', p['id'])
        for h in stored['headings']:
            h.pop('authorization_original_body_present', None)
        saved = next(r for r in stored['mappings'] if r['id'] == auth['id'])
        saved.update(locator_id=text['locator_id'], note='保留人工备注')
        ids = list(saved['material_ids'])
        self.store.put('project', stored); self.store.db.commit()
        fresh = self.store.public_project(self.store.require('project', p['id']))
        row = next(r for r in fresh['mappings'] if r['id'] == auth['id'])
        self.assertEqual('auth-body:' + text['locator_id'], row['locator_id'])
        self.assertEqual('ready', row['status'], row['issues'])
        self.assertEqual(ids, row['material_ids'])
        self.assertEqual('保留人工备注', row['note'])


if __name__ == '__main__':
    unittest.main()
