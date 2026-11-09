"""Explicit response-book organization and buyer feedback table regressions."""
from pathlib import Path
import sys
import tempfile
import unittest

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/bid-response-framework/scripts'
sys.path.insert(0, str(SCRIPTS))
from plan_framework import Source, generate
from prescribed_response_books import book_structure, named_form_ranges
from build_framework import build


def tables(document):
    return [[[cell.text for cell in row.cells] for row in table.rows] for table in document.tables]


class PrescribedBooksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fixture(self, *, separated=True, feedback=True):
        d = Document()
        for line in ['采购项目 ： 校园维护服务', '第一部分 投标须知', '4、投标文件组成',
                     '1）技术标投标内容（独立成册，独立封装；不得包含商务报价）' if separated else '1）技术标投标内容',
                     '第一部分：企业基本情况', 'a) 营业执照复印件（加盖公章）', 'b) 投标承诺书（盖章）',
                     '第二部分：技术材料', '根据本招标文件第三部分技术要求，提供技术标响应相关材料',
                     '2）商务标投标内容（独立成册，独立封装）', '根据商务要求提供报价',
                     '5、投标文件的递交', '按规定递交。', '第三部分 招标内容及要求',
                     '一、技术要求', '请供应商逐项反馈，并附参数证明。', '1.基本服务能力']:
            d.add_paragraph(line)
        t = d.add_table(rows=3, cols=4)
        for c, value in zip(t.rows[0].cells, ['类别', '指标', '说明', '供应商反馈' if feedback else '采购方说明']):
            c.text = value
        t.cell(1, 0).merge(t.cell(2, 0)).text = '巡检'
        t.cell(1, 2).text = '列出两个校区的值班安排。'
        t.cell(2, 2).text = '附应急人员证书。'
        d.add_paragraph('2.特色服务能力')
        d.add_paragraph('1）应急维护')
        for text in ['到场时间及可用备件', '续页：修复时间与验收记录']:
            continuation = d.add_table(rows=1, cols=4)
            continuation.cell(0, 2).text = text
            d.add_paragraph('')
        for line in ['注：反馈须与所附证明一致。', '二、商务报价要求',
                     '请按附件：商务报价单填写商务报价（标明税率）。', '报价有效期为180天。',
                     '第四部分 相关表格', 'l 投标承诺书', 'l 保密承诺书',
                     'l 法定代表人授权书', '投标承诺书', '（本承诺书装订于投标文件首页）',
                     '我方承诺所附资料真实完整，履行采购要求并承担相应法律责任。', '投标人（盖章）：____ 日期：____',
                     '法定代表人授权书', '我方授权____代表本公司签署本项目文件，授权范围及期间如下：____。',
                     '法定代表人（签字）：____ 被授权人：____', '（以下粘贴身份证复印件）',
                     '执业资质证书等复印件']:
            d.add_paragraph(line)
        source = self.root / 'source.docx'
        d.save(source)
        return source

    def test_explicit_books_keep_feedback_continuations_forms_and_missing_attachment(self):
        source = self.fixture()
        plan = generate(Source(source))
        self.assertEqual('prescribed-response-books', plan['strategy'])
        self.assertEqual('校园维护服务', plan['project'])
        heads = [b for b in plan['blocks'] if b['kind'] == 'heading']
        self.assertEqual(['技术标', '商务标'], [b['text'] for b in heads if b['level'] == 1])
        self.assertEqual('投标承诺书', heads[1]['text'])
        self.assertLess(next(i for i, b in enumerate(heads) if b['text'] == '法定代表人授权书'),
                        next(i for i, b in enumerate(heads) if b['text'] == '第二部分：技术材料'))
        self.assertIn('保密承诺书', [b['text'] for b in heads])
        self.assertIn('执业资质证书等复印件', [b['text'] for b in heads])
        feedback = next(b for b in heads if b['text'] == '1）应急维护')
        self.assertTrue(feedback['locked_format'])
        self.assertEqual(4, feedback['level'])
        output = self.root / 'built.docx'
        build(source, plan, output)
        result = Document(output)
        self.assertEqual(tables(Document(source)), tables(result))
        text = '\n'.join(p.text for p in result.paragraphs)
        self.assertIn('报价有效期为180天。', text)
        self.assertIn('注：反馈须与所附证明一致。', text)
        self.assertIn('法定代表人（签字）：____ 被授权人：____', text)
        self.assertIn('商务报价表附件未包含在本文件中', text)
        self.assertEqual(['投标承诺书', '法定代表人授权书'], [f['title'] for f in named_form_ranges(Source(source))])

    def test_rejects_unprescribed_books_or_nonresponse_tables(self):
        self.assertIsNone(book_structure(Source(self.fixture(separated=False))))
        self.assertIsNone(book_structure(Source(self.fixture(feedback=False))))

    def test_qualification_book_and_price_form_stay_in_their_own_books(self):
        source = self.fixture()
        doc = Document(source)
        boundary = next(p for p in doc.paragraphs if p.text == '5、投标文件的递交')
        boundary.insert_paragraph_before('3）资格标投标内容（独立成册，独立封装）')
        boundary.insert_paragraph_before('a) 资格审查申请书（加盖公章）')
        doc.add_paragraph('报价承诺书')
        doc.add_paragraph('本公司承诺最终含税报价为人民币____元，该金额包含全部服务成本。')
        doc.add_paragraph('投标人（签章）____')
        doc.save(source)
        plan = generate(Source(source))
        grouped = {}
        current = None
        for block in plan['blocks']:
            if block['kind'] == 'heading' and block['level'] == 1:
                current = block['text']
                grouped[current] = []
            if current:
                grouped[current].append(block)
        def titles(book):
            return [b['text'] for b in grouped[book] if b['kind'] == 'heading']
        self.assertIn('报价承诺书', titles('商务标'))
        self.assertNotIn('报价承诺书', titles('技术标'))
        self.assertNotIn('商务报价要求', titles('资格标'))
        self.assertIn('资格审查申请书', titles('资格标'))
        self.assertIn('法定代表人授权书', titles('资格标'))
        self.assertIn('投标承诺书', titles('技术标'))

    def test_identity_caption_keeps_following_original_box_and_signature(self):
        source = self.fixture()
        doc = Document(source)
        caption = next(p for p in doc.paragraphs if p.text == '（以下粘贴身份证复印件）')
        caption.text = '身份证复印件'
        table = doc.add_table(rows=2, cols=1)
        table.cell(0, 0).text = '身份证正面粘贴框'
        table.cell(1, 0).text = '身份证反面粘贴框'
        caption._p.addnext(table._tbl)
        doc.save(source)
        plan = generate(Source(source))
        output = self.root / 'identity.docx'
        build(source, plan, output)
        self.assertIn(tables(Document(source))[-1], tables(Document(output)))

    def test_direct_parenthesized_technical_items_do_not_skip_heading_level(self):
        source = self.fixture()
        doc = Document(source)
        for p in list(doc.paragraphs):
            if p.text in ('1.基本服务能力', '2.特色服务能力'):
                p._p.getparent().remove(p._p)
        doc.save(source)
        heads = [b for b in generate(Source(source))['blocks'] if b['kind'] == 'heading']
        target = next(b for b in heads if b['text'] == '1）应急维护')
        self.assertEqual(3, target['level'])

    def test_fuxing_original_fourteen_feedback_tables_are_all_preserved(self):
        source = SCRIPTS.parents[3] / '标书下载/25年标书/25年复星联合招标项目/【招标文件】复星联合健康保险股份有限公司营销推广类服务商名录项目.docx'
        if not source.exists():
            self.skipTest('buyer regression source is not distributed with checkout')
        plan = generate(Source(source))
        self.assertEqual('prescribed-response-books', plan['strategy'])
        self.assertEqual('营销推广类服务商名录项目', plan['project'])
        self.assertEqual(3, len(plan['forms']))
        result = self.root / 'result.docx'
        build(source, plan, result)
        original, built = Document(source), Document(result)
        self.assertEqual(14, len(original.tables))
        self.assertEqual(tables(original), tables(built))
        heads = [b for b in plan['blocks'] if b['kind'] == 'heading']
        self.assertEqual(['技术标', '商务标'], [b['text'] for b in heads if b['level'] == 1])
        self.assertEqual(9, len([b for b in heads if b['level'] == 4]))
        self.assertIn('保密承诺书', [b['text'] for b in heads])
        self.assertIn('执业资质证书等复印件', [b['text'] for b in heads])
        self.assertTrue(any('附件未包含' in n['text'] for n in plan['review']))


if __name__ == '__main__':
    unittest.main()
