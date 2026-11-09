"""Source-driven form preservation and stable navigation regressions."""
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

from docx import Document
from lxml import etree

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/bid-response-framework/scripts'
sys.path.insert(0, str(SCRIPTS))
from plan_framework import Source, generate
from formal_attachment_forms import attachment_ranges
from build_framework import build

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
NS = {'w': W[1:-1]}


def table_signature(table):
    return tuple(tuple(c.text for c in row.cells) for row in table.rows)


class FormalAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def source(self):
        d = Document()
        d.add_paragraph('采购项目：设备维护服务项目')
        d.add_paragraph('供应商须遵守法律、法规，具有独立承担民事责任的能力。')
        d.add_paragraph('第三章 评审办法')
        score = d.add_table(rows=2, cols=4)
        for c, text in zip(score.rows[0].cells, ['评分项', '评分内容', '分值', '应答文件中的装订页码']):
            c.text = text
        for c, text in zip(score.rows[1].cells, ['服务方案', '针对三个服务点分别列出人员与时限。', '30', '']):
            c.text = text
        d.add_paragraph('第五章 响应文件格式')
        # A catalogue contains the same numbers and one grouped material list.
        for text in ['附件1 报价函', '附件2 分项报价表', '附件3 资格证明文件',
                     '3-1 营业执照复印件', '3-2 负责人授权书', '附件4 人员一览表']:
            d.add_paragraph(text)
        d.add_paragraph('附件1 报价函')
        d.add_paragraph('我方就本项目报价，税率为____，金额为____。')
        d.add_paragraph('供应商（盖章）：____；代表（签字）：____')
        d.add_paragraph('附件2 分项报价表')
        table = d.add_table(rows=4, cols=5)
        for c, text in zip(table.rows[0].cells, ['服务项目', '单位', '不含税单价', '含税小计', '备注']):
            c.text = text
        table.cell(1, 0).merge(table.cell(2, 0)).text = '维护服务'
        table.cell(1, 1).text = '月'
        table.cell(2, 1).text = '次'
        table.cell(3, 0).merge(table.cell(3, 2)).text = '合计'
        d.add_paragraph('注：报价须包括三处现场及全部税费；本表签字盖章。')
        d.add_paragraph('附件3-1 营业执照复印件')
        d.add_paragraph('附营业执照复印件并加盖公章。')
        d.add_paragraph('附件3-2 负责人授权书（仅授权代表签署时适用）')
        d.add_paragraph('授权人姓名____；被授权人姓名____。')
        ids = d.add_table(rows=2, cols=1)
        ids.cell(0, 0).text = '身份证正面'
        ids.cell(1, 0).text = '身份证反面'
        d.add_paragraph('附件4 人员一览表')
        people = d.add_table(rows=2, cols=3)
        for c, text in zip(people.rows[0].cells, ['姓名', '任职岗位', '证明材料']):
            c.text = text
        d.add_paragraph('注：一人一行，均提供相应证明。')
        source = self.root / '采购原件.docx'
        d.save(source)
        return source

    def test_actual_form_bodies_keep_sequence_merged_cells_and_notes(self):
        source = self.source()
        plan = generate(Source(source))
        self.assertEqual('formal-attachment-forms', plan['strategy'])
        headings = [b for b in plan['blocks'] if b['kind'] == 'heading']
        self.assertEqual(['1', '2', '3', '3-1', '3-2', '4'], [b['form_id'] for b in headings])
        self.assertEqual([1, 1, 1, 2, 2, 1], [b['level'] for b in headings])
        self.assertTrue(all(b['locked_format'] for b in headings))
        self.assertNotIn('法规', [b['text'] for b in headings])
        self.assertEqual(['1', '2', '3-1', '3-2', '4'], [r['code'] for r in attachment_ranges(Source(source))])
        output = self.root / '框架.docx'
        build(source, plan, output)
        original, result = Document(source), Document(output)
        self.assertEqual([table_signature(t) for t in original.tables], [table_signature(t) for t in result.tables])
        texts = [p.text for p in result.paragraphs]
        self.assertIn('注：报价须包括三处现场及全部税费；本表签字盖章。', texts)
        self.assertIn('注：一人一行，均提供相应证明。', texts)
        with ZipFile(source) as z:
            before = etree.fromstring(z.read('word/document.xml')).xpath('//w:tbl[2]', namespaces=NS)[0]
        with ZipFile(output) as z:
            after = etree.fromstring(z.read('word/document.xml')).xpath('//w:tbl[2]', namespaces=NS)[0]
        for xpath in ['.//w:gridSpan/@w:val', './/w:vMerge/@w:val', './w:tblGrid/w:gridCol/@w:w']:
            self.assertEqual(before.xpath(xpath, namespaces=NS), after.xpath(xpath, namespaces=NS))

    def test_reordered_bookmarks_are_preserved_in_toc_and_body(self):
        source = self.source()
        plan = generate(Source(source))
        headings = [b for b in plan['blocks'] if b['kind'] == 'heading']
        ids = ['BidSection19', 'BidSection3', 'BidSection41', 'BidSection7', 'BidSection8', 'BidSection100']
        for heading, bookmark in zip(headings, ids):
            heading['bookmark'] = bookmark
        output = self.root / '带固定书签.docx'
        build(source, plan, output)
        with ZipFile(output) as z:
            tree = etree.fromstring(z.read('word/document.xml'))
        self.assertEqual(ids, tree.xpath('//w:bookmarkStart/@w:name', namespaces=NS))
        self.assertEqual(ids, tree.xpath('//w:hyperlink/@w:anchor', namespaces=NS))
        self.assertEqual([f' PAGEREF {name} \\h ' for name in ids],
                         tree.xpath('//w:fldSimple/@w:instr', namespaces=NS))
        headings[1]['bookmark'] = ids[0]
        with self.assertRaisesRegex(ValueError, '书签重复'):
            build(source, plan, self.root / '重复书签.docx')

    def test_fixed_copy_rejects_cell_edits(self):
        source = self.source()
        plan = generate(Source(source))
        block = next(b for b in plan['blocks'] if b['kind'] == 'copy' and b.get('preserve_format'))
        block['cells'] = [{'row': 0, 'col': 0, 'text': '未经允许的修改'}]
        with self.assertRaisesRegex(ValueError, '固定原表'):
            build(source, plan, self.root / '不得生成.docx')

    def test_child_only_attachment_numbers_create_complete_parent_chain(self):
        source = self.root / '子附件.docx'
        doc = Document()
        for line in ['第五章 响应文件格式', '附件1-1-1 资格声明', '我方声明资格证明真实完整。',
                     '附件2-1 授权声明', '本人授权代表办理投标。']:
            doc.add_paragraph(line)
        doc.save(source)
        plan = generate(Source(source))
        heads = [b for b in plan['blocks'] if b['kind'] == 'heading']
        forms = [b for b in heads if 'form_id' in b]
        self.assertEqual(['1', '1-1', '1-1-1', '2', '2-1'], [b['form_id'] for b in forms])
        self.assertEqual([1, 2, 3, 1, 2], [b['level'] for b in forms])
        self.assertIn('营业执照', [b['text'] for b in heads])
        self.assertNotIn('附件1 附件1', [b['text'] for b in heads])
        build(source, plan, self.root / '子附件框架.docx')

    def test_3948_preserves_every_form_table_and_official_score_tables(self):
        source = SCRIPTS.parents[3] / '包件2：直播视频类服务' / '招案2026-3948_中银国际证券股份有限公司资管业务互联网营销服务选型入围项目采购邀请文件（发售稿）1008 (1).docx'
        if not source.exists():
            self.skipTest('real buyer regression file is not distributed with source checkout')
        plan = generate(Source(source))
        output = self.root / '3948.docx'
        build(source, plan, output)
        original = Document(source)
        result = Document(output)
        expected = [table_signature(original.tables[i]) for i in [1, 2, *range(5, 19)]]
        self.assertEqual(expected, [table_signature(t) for t in result.tables])
        headings = [b['text'] for b in plan['blocks'] if b['kind'] == 'heading']
        self.assertFalse(any(title in {'法规', '遵守法律'} for title in headings))
        self.assertEqual('附件1 磋商报价函（统一格式）', headings[0])
        self.assertEqual('中银国际证券股份有限公司资管业务互联网营销服务选型入围项目', plan['project'])
        self.assertEqual(22, len(headings))
        self.assertTrue(any('仅' in str(note) or '适用' in str(note) for note in plan['review']))

    def test_reused_attachment_numbers_keep_each_lot_and_discard_each_catalogue(self):
        for separate_chapters in (False, True):
            with self.subTest(separate_chapters=separate_chapters):
                doc = Document()
                doc.add_paragraph('渠道采购项目')
                if not separate_chapters:
                    doc.add_paragraph('第五章 响应文件格式')
                for lot in ('包件一：甲服务', '包件二：乙服务'):
                    doc.add_paragraph(lot)
                    if separate_chapters:
                        doc.add_paragraph('第五章 响应文件格式')
                    for title in ('附件1 报价函', '附件2 人员表'):
                        doc.add_paragraph(title)
                    doc.add_paragraph('以下是本包件的正式格式。')
                    for number, title in ((1, '报价函'), (2, '人员表')):
                        doc.add_paragraph(f'附件{number} {title}')
                        doc.add_paragraph(lot + '专用原表，请按原表填写并盖章。')
                        table = doc.add_table(rows=2, cols=2)
                        table.cell(0, 0).text, table.cell(0, 1).text = lot, title
                        table.cell(1, 0).text = '供应商填写：'
                source = self.root / f'lots-{separate_chapters}.docx'
                doc.save(source)
                plan = generate(Source(source))
                self.assertEqual('formal-attachment-forms', plan['strategy'])
                self.assertEqual(['包件一', '包件一', '包件二', '包件二'], [f['scope'] for f in plan['forms']])
                headings = [b for b in plan['blocks'] if b['kind'] == 'heading']
                self.assertEqual(['包件一：甲服务', '包件二：乙服务', '营业执照'],
                                 [b['text'] for b in headings if b['level'] == 1])
                output = self.root / f'lots-result-{separate_chapters}.docx'
                build(source, plan, output)
                self.assertEqual([table_signature(t) for t in doc.tables],
                                 [table_signature(t) for t in Document(output).tables])
                self.assertNotIn('以下是本包件的正式格式。', '\n'.join(p.text for p in Document(output).paragraphs))

    def test_different_format_chapters_can_reuse_their_chapter_and_attachment_numbers(self):
        doc = Document()
        for chapter in ('第五章 技术响应文件格式', '第五章 商务响应文件格式'):
            doc.add_paragraph(chapter)
            for number, title in ((1, '报价函'), (2, '人员表')):
                doc.add_paragraph(f'附件{number} {title}')
                table = doc.add_table(rows=1, cols=2)
                table.cell(0, 0).text, table.cell(0, 1).text = chapter, title
        source = self.root / 'separate-format-chapters.docx'
        doc.save(source)
        plan = generate(Source(source))
        self.assertEqual(4, len(plan['forms']))
        output = self.root / 'separate-format-chapters-result.docx'
        build(source, plan, output)
        self.assertEqual([table_signature(t) for t in doc.tables],
                         [table_signature(t) for t in Document(output).tables])


if __name__ == '__main__':
    unittest.main()
