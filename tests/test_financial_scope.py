"""Audit-report materials must follow their own response lot, including common years."""
import copy
import io
from pathlib import Path
import tempfile
import unittest

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image

import engine


class FinancialScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.docx'
        self.output = self.root / 'output.docx'
        self.categories = [copy.deepcopy(c) for c in engine.DEFAULT_CATEGORIES if c['id'] == 'audit_report']
        self.materials = []
        self.colors = {2023: (255, 0, 0), 2024: (0, 255, 0), 2025: (0, 0, 255)}
        for year, color in self.colors.items():
            path = self.root / f'{year}.png'
            Image.new('RGB', (40, 30), color).save(path)
            self.materials.append({'id': str(year), 'category_id': 'audit_report',
                'name': f'{year}年度审计报告', 'subject': engine.EXPECTED_SUBJECT,
                'years': [year], 'valid_until': '长期', 'mode': 'page',
                'files': [{'name': path.name, 'path': str(path)}]})

    def fixture(self, *, common=False, table=False, lots=True):
        document = Document()
        if common:
            document.add_paragraph('各包件均须提交2023年度财务审计报告。')
        for index, year in enumerate((2024, 2025), 1):
            document.add_heading(f'包件{index}' if lots else f'{"甲" if index == 1 else "乙"}平台响应', 1)
            if table:
                t = document.add_table(rows=1, cols=2)
                t.cell(0, 0).text = '财务材料要求'
                t.cell(0, 1).text = f'请提供{year}年度财务审计报告。'
                document.add_heading('财务审计报告', 2)
            else:
                document.add_heading(f'{year}年度财务审计报告', 2)
            document.add_paragraph('请在此附对应财务证明材料。')
        document.save(self.source)
        return engine.scan_document(self.source, self.categories, self.materials)

    def test_generated_qualification_uses_the_buyers_explicit_year_and_body_slots(self):
        document = Document()
        for level in (1, 2):
            document.styles.add_style(f'BFHeading{level}', WD_STYLE_TYPE.PARAGRAPH)

        def heading(title, level, ident):
            paragraph = document.add_paragraph(title, f'BFHeading{level}')
            outline = OxmlElement('w:outlineLvl')
            outline.set(qn('w:val'), str(level - 1))
            paragraph._p.get_or_add_pPr().append(outline)
            mark = OxmlElement('w:bookmarkStart')
            mark.set(qn('w:name'), f'BidSection{ident}')
            mark.set(qn('w:id'), str(ident))
            paragraph._p.append(mark)

        heading('4. 资格证明文件', 1, 1)
        heading('4.1 投标人基本情况信息表', 2, 2)
        document.add_paragraph('投标人基本情况信息表')
        document.add_table(rows=1, cols=1).cell(0, 0).text = '营业执照号'
        document.add_paragraph('注：提供最新营业执照，电子版提供原件扫描件。')
        heading('4.3 财务审计报告或银行资信证明', 2, 3)
        document.add_paragraph('提供202X年度财务审计报告，或开标日前6个月内开具的银行资信证明。')
        document.save(self.source)
        categories = [c for c in engine.DEFAULT_CATEGORIES if c['id'] in {'audit_report', 'business_license'}]
        evidence = {'required_years': [2025], 'bank_credit_months': 6, 'source': {'locator': '公告3.3'}}
        result = engine.scan_document(self.source, categories, self.materials, preserve_forms=True,
                                      procurement_audit=evidence)
        mappings = {row['category_id']: row for row in result['mappings']}
        license_row, audit = mappings['business_license'], mappings['audit_report']
        self.assertTrue(license_row['locator_id'].startswith('at:business_license:'))
        self.assertEqual('BidSection2', license_row['chapter_id'])
        self.assertEqual('BidSection3', audit['chapter_id'])
        self.assertEqual([2025], audit['required_years'])
        self.assertEqual(['2025'], audit['material_ids'])
        self.assertFalse(audit['existing_content'])
        self.assertEqual(6, audit['requirement']['bank_credit_months'])
        wrong = {**audit, 'material_ids': ['2024']}
        self.assertEqual('blocked', engine.validate_mappings(
            [wrong], self.materials, result['headings'], categories, preserve_forms=True)[0]['status'])

    def test_distinct_lot_years_preselect_only_their_own_images_and_export_in_place(self):
        scanned = self.fixture()
        rows = scanned['mappings']
        self.assertEqual([[2024], [2025]], [r['required_years'] for r in rows])
        self.assertEqual([['2024'], ['2025']], [r['material_ids'] for r in rows])
        before = self.source.read_bytes()
        engine.export_document(self.source, self.output, [dict(r, confirmed=True) for r in rows],
                               self.materials, 'reviewed', self.categories)
        self.assertEqual(before, self.source.read_bytes())
        result = Document(self.output)
        colors, current = {}, None
        for paragraph in result.paragraphs:
            if paragraph.text.startswith('包件'):
                current = paragraph.text
            for blip in paragraph._p.iter(qn('a:blip')):
                blob = result.part.related_parts[blip.get(qn('r:embed'))].blob
                colors.setdefault(current, []).append(Image.open(io.BytesIO(blob)).convert('RGB').getpixel((0, 0)))
        self.assertEqual({'包件1': [self.colors[2024]], '包件2': [self.colors[2025]]}, colors)
        wrong = dict(rows[0], material_ids=['2025'], action='fill', confirmed=True)
        checked = engine.validate_mappings([wrong], self.materials, scanned['headings'], self.categories)[0]
        self.assertEqual('blocked', checked['status'])
        self.assertTrue(any('年度与目标要求不符' in issue for issue in checked['issues']))

    def test_supplementary_years_do_not_block_complete_required_coverage(self):
        scanned = self.fixture()
        row = dict(scanned['mappings'][0], material_ids=['2023', '2024', '2025'], action='fill', confirmed=True)
        checked = engine.validate_mappings([row], self.materials, scanned['headings'], self.categories)[0]
        self.assertEqual('ready', checked['status'])
        self.assertEqual([2024], checked['required_years'])
        self.assertFalse(checked['issues'])
        self.assertIn('已覆盖要求年度，其他年度报告作为补充材料。', checked['warnings'])
        engine.export_document(self.source, self.output, [checked], self.materials, 'reviewed', self.categories)
        self.assertEqual(3, len(Document(self.output).inline_shapes))

    def test_supplementary_years_cannot_replace_required_year_or_hide_invalid_subject(self):
        scanned = self.fixture()
        row = dict(scanned['mappings'][0], material_ids=['2023', '2025'], action='fill')
        checked = engine.validate_mappings([row], self.materials, scanned['headings'], self.categories)[0]
        self.assertEqual('blocked', checked['status'])
        self.assertIn('缺少要求年度：2024。', checked['issues'])
        materials = copy.deepcopy(self.materials)
        materials[0]['subject'] = '其他公司'
        row['material_ids'] = ['2023', '2024', '2025']
        checked = engine.validate_mappings([row], materials, scanned['headings'], self.categories)[0]
        self.assertEqual('blocked', checked['status'])
        self.assertTrue(any('主体必须核实' in issue for issue in checked['issues']))

    def test_common_year_is_inherited_without_inheriting_other_lot_year(self):
        rows = self.fixture(common=True)['mappings']
        self.assertEqual([[2023, 2024], [2023, 2025]], [r['required_years'] for r in rows])
        self.assertEqual([['2023', '2024'], ['2023', '2025']], [r['material_ids'] for r in rows])

    def test_generic_form_uses_its_own_lot_table_requirement(self):
        rows = self.fixture(table=True)['mappings']
        self.assertEqual([[2024], [2025]], [r['required_years'] for r in rows])
        self.assertEqual([['2024'], ['2025']], [r['material_ids'] for r in rows])

    def test_separate_named_response_sections_do_not_share_years(self):
        rows = self.fixture(lots=False)['mappings']
        self.assertEqual([[2024], [2025]], [r['required_years'] for r in rows])


if __name__ == '__main__':
    unittest.main()
