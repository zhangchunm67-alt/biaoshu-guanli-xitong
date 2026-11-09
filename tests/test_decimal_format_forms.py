"""Explicit decimal format chapters retain real tables and never split prose."""
from pathlib import Path
import sys
import tempfile
import unittest

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/bid-response-framework/scripts'
sys.path.insert(0, str(SCRIPTS))
from plan_framework import Source, generate
from decimal_format_forms import format_ranges
from build_framework import build


def tables(document):
    return [[[cell.text for cell in row.cells] for row in table.rows] for table in document.tables]


class DecimalFormatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fixture(self):
        d = Document()
        for line in ['项目名称：校园信息服务', '第一部分 采购邀请', '请按采购文件准备响应材料。',
                     '第二部分 项目需求', '1、信息发布', '按时发布公告并记录送达情况。',
                     '2、信息监测', '监测范围：包括新闻网站、论坛、博客等来源，发现情况须在一小时内反馈。',
                     '第三部分谈判响应文件格式', '1报价', '1.1报价函',
                     '我方接受报价有效期约定。供应商（盖章）：____', '1.2 报价一览表',
                     '报价模板详见【附件一】报价清单，严禁删减原表。', '项目响应方案',
                     '请根据第二部分项目需求制定方案。', '3.响应偏离表']:
            d.add_paragraph(line)
        deviation = d.add_table(rows=3, cols=4)
        for c, value in zip(deviation.rows[0].cells, ['序号', '采购要求', '响应情况', '偏离说明']):
            c.text = value
        deviation.cell(1, 0).merge(deviation.cell(2, 0)).text = '1'
        d.add_paragraph('无偏离时填写“无偏离”，不得删表。')
        d.add_paragraph('供应商（盖章）：____')
        d.add_paragraph('4.授权书')
        d.add_paragraph('本公司授权____代表办理本项目响应事宜。')
        d.add_paragraph('身份证复印件')
        identity = d.add_table(rows=2, cols=1)
        identity.cell(0, 0).text = '身份证正面'
        identity.cell(1, 0).text = '身份证反面'
        d.add_paragraph('法定代表人（签字）：____')
        d.add_paragraph('5.公司说明函')
        d.add_paragraph('一、基本情况')
        company = d.add_table(rows=2, cols=3)
        for c, value in zip(company.rows[0].cells, ['核验事项', '是', '否']):
            c.text = value
        company.cell(1, 0).text = '是否具备服务团队？'
        d.add_paragraph('请按实际情况勾选。')
        d.add_paragraph('二、公司情况介绍')
        d.add_paragraph('请说明组织结构及人员分布。')
        d.add_paragraph('6.历史业绩情况')
        d.add_paragraph('一、近2年历史业绩统计表')
        history = d.add_table(rows=3, cols=2)
        history.cell(0, 0).text = '项目类别'
        history.cell(0, 1).text = '数量'
        history.cell(1, 0).text = '5万元以下'
        history.cell(2, 0).text = '合计'
        for line in ['无项目经验填写0；请附对应证明。', '7.其他材料',
                     '1.社保权益记录', '2.独家授权委托书（如有）', '评分办法', '评分规则另见附件二。']:
            d.add_paragraph(line)
        source = self.root / 'source.docx'
        d.save(source)
        return source

    def test_mixed_numbering_keeps_forms_and_comma_prose_intact(self):
        source = self.fixture()
        plan = generate(Source(source))
        self.assertEqual('decimal-format-forms', plan['strategy'])
        heads = [b for b in plan['blocks'] if b['kind'] == 'heading']
        self.assertEqual(['1报价', '项目响应方案', '3.响应偏离表', '4.授权书',
                          '5.公司说明函', '6.历史业绩情况', '7.其他材料', '营业执照'],
                         [b['text'] for b in heads if b['level'] == 1])
        self.assertIn('信息监测', [b['text'] for b in heads])
        self.assertFalse(any(b['text'] in ('新闻网站', '论坛', '博客') for b in heads))
        self.assertTrue(all('source_start' in b and 'source_end' in b
                            for b in heads if b.get('origin') != 'user_policy'))
        output = self.root / 'result.docx'
        build(source, plan, output)
        result = Document(output)
        self.assertEqual(tables(Document(source)), tables(result))
        text = '\n'.join(p.text for p in result.paragraphs)
        self.assertIn('监测范围：包括新闻网站、论坛、博客等来源，发现情况须在一小时内反馈。', text)
        self.assertIn('无偏离时填写“无偏离”，不得删表。', text)
        self.assertIn('法定代表人（签字）：____', text)
        self.assertIn('当前主文件未包含该原表', text)
        self.assertNotIn('评分规则另见附件二。', text)

    def test_no_format_chapter_is_not_a_basis_for_promoting_requirement_lists(self):
        source = self.fixture()
        d = Document(source)
        p = next(p for p in d.paragraphs if p.text == '第三部分谈判响应文件格式')
        p.text = '第三部分 采购人内部说明'
        d.save(source)
        self.assertEqual([], format_ranges(Source(source)))

    def test_formal_run_supersedes_a_prior_catalogue(self):
        source = self.fixture()
        d = Document(source)
        first = next(p for p in d.paragraphs if p.text == '1报价')
        for title in ['1报价', '2.项目响应方案', '3.响应偏离表', '4.授权书',
                      '5.公司说明函', '6.历史业绩情况', '7.其他材料']:
            first.insert_paragraph_before(title)
        d.save(source)
        plan = generate(Source(source))
        self.assertEqual(8, len([b for b in plan['blocks'] if b['kind'] == 'heading' and b['level'] == 1]))
        output = self.root / 'result.docx'
        build(source, plan, output)
        self.assertEqual(tables(Document(source)), tables(Document(output)))

    def test_procurement_regression_keeps_all_five_original_tables(self):
        source = SCRIPTS.parents[3] / '标书下载/25年标书/25年招商基金公关项目/招商基金管理有限公司公关代理类服务采购文件.docx'
        if not source.exists():
            self.skipTest('buyer regression source is not distributed with checkout')
        plan = generate(Source(source))
        self.assertEqual('decimal-format-forms', plan['strategy'])
        output = self.root / 'result.docx'
        build(source, plan, output)
        original, result = Document(source), Document(output)
        self.assertEqual(5, len(original.tables))
        self.assertEqual(tables(original), tables(result))
        headings = [b['text'] for b in plan['blocks'] if b['kind'] == 'heading']
        self.assertIn('舆情管理', headings)
        self.assertFalse(any(title in {'新闻网站', '论坛', '博客', '社交媒体'} for title in headings))
        self.assertEqual(9, len([b for b in plan['blocks'] if b['kind'] == 'heading' and b['level'] == 1]))
        paragraphs = '\n'.join(p.text for p in result.paragraphs)
        self.assertEqual('前置回复确认函（采购原格式）', headings[0])
        self.assertEqual('prior_reply', plan['forms'][0]['submission_scope'])
        for label in ['□标段一：稿件撰写', '□标段二：媒体代理', '□标段三：自媒体投放',
                      '□标段四：公关活动', '□标段五：舆情管理', '法定代表人或授权代表（签字）：',
                      '供应商名称（公章）：']:
            self.assertIn(label, paragraphs)
        self.assertIn('经过我司内部评估，我司将参与以下标段', paragraphs)
        self.assertIn('无尚无合作的，填写“无”', paragraphs)
        self.assertIn('小数点后6位', paragraphs)
        self.assertIn('50万/□100万/□150万/□300万', paragraphs)
        self.assertIn('单标段报价单请完整报价', paragraphs)


if __name__ == '__main__':
    unittest.main()
