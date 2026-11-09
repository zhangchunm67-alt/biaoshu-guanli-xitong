"""The fixed license section reaches editable plans, Word and HTML without false requirements."""
import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from docx import Document
from lxml import html

import framework_service as service
from server import Store

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/bid-response-framework/scripts'
sys.path.insert(0, str(SCRIPTS))
from plan_framework import Source, generate
from required_sections import ensure_business_license


class BusinessLicenseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / '采购.docx'
        doc = Document()
        for line in ['内容服务项目采购邀请函', '特邀请贵公司参加采购。',
                     '报价人应准备投标文件，包括公司介绍、历史项目、方案、报价。']:
            doc.add_paragraph(line)
        doc.save(self.source)

    def test_automatic_and_saved_plan_have_sibling_before_company_without_false_qualification(self):
        store = Store(self.root / 'data')
        self.addCleanup(store.close)
        record = service.create(store, {'name': '目录规则测试', 'filename': self.source.name,
            'data': base64.b64encode(self.source.read_bytes()).decode()})
        before = copy.deepcopy(record['requirements'])
        record = service.revise(store, record['id'], {'revision': record['revision'],
            'items': before, 'confirmed': True, 'notes_acknowledged': True,
            'acknowledged_removed_ids': []}, 'requirements')
        before = copy.deepcopy(record['requirements'])
        rows = record['outline']
        company = next(i for i, r in enumerate(rows) if r['text'] == '公司介绍')
        self.assertEqual('营业执照', rows[company - 1]['text'])
        self.assertEqual(rows[company]['level'], rows[company - 1]['level'])
        self.assertEqual([], rows[company - 1]['requirement_ids'])
        # Deleting the fixed item must not remove it from the generated outputs.
        old_ids = {r['text']: r['id'] for r in rows if r['text'] != '营业执照'}
        revised = service.revise(store, record['id'], {'revision': record['revision'],
            'items': [r for r in rows if r['text'] != '营业执照']}, 'outline')
        self.assertEqual(before, revised['requirements'])
        self.assertEqual(old_ids, {r['text']: r['id'] for r in revised['outline'] if r['text'] != '营业执照'})
        self.assertEqual(1, sum(r['text'] == '营业执照' for r in revised['outline']))
        page = html.parse(str(service.asset(store, record['id'], 'html')[0]))
        titles = page.xpath('//ul[@class="outline"]/li/text()')
        self.assertEqual('营业执照', titles[titles.index('公司介绍') - 1])
        doc = Document(service.asset(store, record['id'], 'word')[0])
        headings = [p.text for p in doc.paragraphs if p._p.xpath('.//w:bookmarkStart')]
        self.assertEqual(1, sum(t.endswith('营业执照') for t in headings))
        index = next(i for i, t in enumerate(headings) if t.endswith('公司介绍'))
        self.assertTrue(headings[index - 1].endswith('营业执照'))

    def test_existing_supplier_license_is_preserved_but_auditor_license_does_not_replace_it(self):
        for title in ['营业执照', '企业营业执照副本', '营业执照复印件（加盖公章）']:
            plan = {'blocks': [{'kind': 'heading', 'text': title, 'level': 1},
                               {'kind': 'copy', 'start': 3, 'end': 5}]}
            before = copy.deepcopy(plan)
            ensure_business_license(plan)
            self.assertEqual(before, plan)
        plan = {'blocks': [{'kind': 'heading', 'text': '会计师事务所营业执照', 'level': 1}]}
        ensure_business_license(plan)
        self.assertIn('营业执照', [b.get('text') for b in plan['blocks']])

    def test_manual_plan_cli_preserves_bookmarks_and_adds_license_to_word_and_html(self):
        plan = generate(Source(self.source))
        blocks = plan['blocks']
        start = next(i for i, b in enumerate(blocks) if b.get('origin') == 'user_policy')
        del blocks[start:start + 2]
        counter = 0
        for b in blocks:
            if b['kind'] == 'heading':
                counter += 1
                b['bookmark'] = f'BidSection{counter}'
        original = json.dumps(plan, ensure_ascii=False)
        path = self.root / 'manual.json'
        path.write_text(original)
        output = self.root / '框架.docx'
        subprocess.run([sys.executable, '-B', str(SCRIPTS / 'generate.py'), str(self.source),
                        str(output), '--plan', str(path)], check=True, capture_output=True)
        self.assertEqual(original, path.read_text())
        self.assertIn('【待附】营业执照复印件或扫描件。', [p.text for p in Document(output).paragraphs])
        page = html.parse(str(output.with_name('框架_目录与五类要求.html')))
        self.assertIn('营业执照', page.xpath('//ul[@class="outline"]/li/text()'))


if __name__ == '__main__':
    unittest.main()
