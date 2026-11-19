"""目录校验：原文序号乱序、重复、跳号、混用自动编号、层级错位、附件序号与同级重名。"""
import base64
import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'skills/bid-response-framework/scripts'))
import framework_service as service
from outline_check import check_outline, decimal_headings, format_report, parse_number, read_docx_outline
from server import Store


def rows(*items):
    return [dict(id=f'S{i}', text=text, level=level) for i, (text, level) in enumerate(items, 1)]


def codes(result):
    return [issue['code'] for issue in result['issues']]


class OutlineCheckUnitTests(unittest.TestCase):
    def test_decimal_display_replaces_mixed_prefixes_at_each_level(self):
        outline = rows(('一、商务部分', 1), ('3.1 一、投标一览表', 2),
                       ('（一）分项报价', 3), ('二、技术方案', 1),
                       ('9.5 项目理解', 2), ('附件3 报价说明', 2))
        self.assertEqual([
            ('1.', '商务部分'), ('1.1', '投标一览表'), ('1.1.1', '分项报价'),
            ('2.', '技术方案'), ('2.1', '项目理解'), ('2.2', '附件3 报价说明'),
        ], list(decimal_headings(outline)))

    def test_clean_outline_passes(self):
        result = check_outline(rows(('营业执照', 1), ('公司介绍', 1), ('服务方案', 1), ('实施计划', 2),
                                    ('附件1 报价函', 1), ('附件3 承诺书', 1)))
        self.assertEqual([], result['issues'])
        self.assertIn('通过', format_report(result))

    def test_disordered_duplicate_and_late_start_sequence_gets_renumber_fix(self):
        entries = rows(('商务部分', 1), ('三、报价函', 2), ('一、法定代表人身份证明', 2), ('一、授权委托书', 2))
        result = check_outline(entries)
        self.assertEqual(['sequence'], codes(result))
        issue = result['issues'][0]
        self.assertIn('从 3 开始', issue['message'])
        self.assertIn('重复 1', issue['message'])
        self.assertIn('顺序颠倒', issue['message'])
        self.assertEqual({'S2': '一、报价函', 'S3': '二、法定代表人身份证明', 'S4': '三、授权委托书'},
                         {f['id']: f['text'] for f in issue['fixes']})

    def test_gap_and_styles_keep_their_own_format(self):
        result = check_outline(rows(('格式', 1), ('（一）投标函', 2), ('（三）偏离表', 2)))
        self.assertEqual([{'id': 'S3', 'text': '（二）偏离表'}], result['issues'][0]['fixes'])
        self.assertIn('跳号', result['issues'][0]['message'])
        chapters = check_outline(rows(('第一章 总则', 1), ('第三章 报价', 1)))
        self.assertEqual([{'id': 'S2', 'text': '第二章 报价'}], chapters['issues'][0]['fixes'])
        decimal = check_outline(rows(('方案', 1), ('3.1 服务内容', 2), ('3.3 质量保证', 2)))
        self.assertEqual([{'id': 'S3', 'text': '3.2 质量保证'}], decimal['issues'][0]['fixes'])

    def test_mixed_buyer_numbers_and_word_auto_numbering(self):
        result = check_outline(rows(('第五章 投标文件格式', 1), ('服务方案', 1), ('公司介绍', 1)))
        self.assertEqual(['mixed_auto'], codes(result))
        self.assertEqual([{'id': 'S1', 'text': '投标文件格式'}], result['issues'][0]['fixes'])

    def test_safe_fixes_are_applied_to_plan_before_rendering(self):
        plan = {'blocks': [
            {'kind': 'heading', 'bookmark': 'S1', 'text': '商务部分', 'level': 1},
            {'kind': 'heading', 'bookmark': 'S2', 'text': '三、报价函', 'level': 2},
            {'kind': 'heading', 'bookmark': 'S3', 'text': '一、身份证明', 'level': 2},
            {'kind': 'heading', 'bookmark': 'S4', 'text': '一、授权委托书', 'level': 2},
        ]}
        fixed = service.apply_outline_fixes(copy.deepcopy(plan))
        self.assertEqual(['商务部分', '一、报价函', '二、身份证明', '三、授权委托书'],
                         [block['text'] for block in fixed['blocks']])

    def test_title_key_ignores_numbering_for_chapter_migration(self):
        self.assertEqual(service._title_key('一、公司介绍'), service._title_key('公司介绍'))
        self.assertEqual(service._title_key('1. 服务方案'), service._title_key('服务方案'))

    def test_safe_fixes_remove_manual_numbers_when_mixed_with_auto_numbering(self):
        plan = {'blocks': [
            {'kind': 'heading', 'bookmark': 'S1', 'text': '第五章 投标文件格式', 'level': 1},
            {'kind': 'heading', 'bookmark': 'S2', 'text': '服务方案', 'level': 1},
        ]}
        fixed = service.apply_outline_fixes(copy.deepcopy(plan))
        self.assertEqual(['投标文件格式', '服务方案'], [block['text'] for block in fixed['blocks']])
        # （一）、附件N 不会关掉 Word 自动编号，不算混用。
        self.assertEqual([], check_outline(rows(('（一）投标函', 1), ('（二）报价表', 1)))['issues'])

    def test_mixed_styles_and_child_same_style_as_parent(self):
        self.assertIn('mixed_style', codes(check_outline(rows(('格式', 1), ('（一）投标函', 2), ('（1）报价表', 2)))))
        result = check_outline(rows(('一、商务部分', 1), ('二、报价函', 2), ('二、技术部分', 1)))
        self.assertIn('level_style', codes(result))

    def test_attachment_order_is_flagged_without_renumbering(self):
        result = check_outline(rows(('附件', 1), ('附件3 承诺书', 2), ('附件1 报价表', 2), ('附件1 报价表副本', 2)))
        issue = next(i for i in result['issues'] if i['code'] == 'attachment')
        self.assertIn('顺序颠倒', issue['message'])
        self.assertIn('重复 1', issue['message'])
        self.assertEqual([], issue['fixes'])

    def test_duplicate_titles_after_removing_numbers(self):
        result = check_outline(rows(('一、公司介绍', 1), ('二、公司介绍', 1)))
        self.assertIn('duplicate_title', codes(result))

    def test_years_amounts_and_bare_numbers_are_not_sequence_numbers(self):
        for title in ['2023年度审计报告', '3.5万元以下项目', '3.5 万元以下项目', '100%响应', '一、']:
            self.assertIsNone(parse_number(title), title)
        self.assertEqual([], check_outline(rows(('业绩', 1), ('2023年业绩', 2), ('2024年业绩', 2)))['issues'])
        self.assertEqual(('1.', '月度宣传推广情况报告机制'),
                         next(decimal_headings(rows(('9.10.1 月度宣传推广情况报告机制', 1)))))

    def test_reads_headings_from_generated_word(self):
        document = Document()
        for level in (1, 2):
            document.styles.add_style(f'BFHeading{level}', 1)
        for number, (text, level) in enumerate([('商务部分', 1), ('二、报价函', 2), ('一、授权书', 2)], 1):
            paragraph = document.add_paragraph(text, f'BFHeading{level}')
            mark = OxmlElement('w:bookmarkStart')
            mark.set(qn('w:id'), str(number))
            mark.set(qn('w:name'), f'BidSection{number}')
            paragraph._p.append(mark)
        document.add_paragraph('正文')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'f.docx'
            document.save(path)
            entries = read_docx_outline(path)
        self.assertEqual(['BidSection1', 'BidSection2', 'BidSection3'], [e['id'] for e in entries])
        self.assertEqual(['sequence'], codes(check_outline(entries)))


class OutlineCheckServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.close)
        doc = Document()
        for line in ['设备维保服务采购项目', '第一章 合格供应商资格要求',
                     '供应商须提供2023年1月1日以来至少两个同类合同。',
                     '供应商须在两小时内响应报修。', '第五章 响应文件格式',
                     '附件1 报价函', '我方报价为____，供应商签字盖章：____。', '附件2 人员表']:
            doc.add_paragraph(line)
        table = doc.add_table(rows=2, cols=3)
        for cell, value in zip(table.rows[0].cells, ['姓名', '资格证书', '证明页码']):
            cell.text = value
        doc.add_paragraph('注：逐人填写并签章。')
        raw = io.BytesIO()
        doc.save(raw)
        record = service.create(self.store, dict(name='目录校验', filename='采购.docx',
                                                 data=base64.b64encode(raw.getvalue()).decode()))
        self.record = service.revise(self.store, record['id'], dict(
            revision=record['revision'], items=record['requirements'], confirmed=True,
            notes_acknowledged=True, acknowledged_removed_ids=[]), 'requirements')

    def test_messy_numbers_are_fixed_automatically_without_confirmation(self):
        self.assertIn('outline_check', self.record)
        outline_rows = copy.deepcopy(self.record['outline'])
        top = [r for r in outline_rows if r['level'] == 1]
        self.assertGreaterEqual(len(top), 3)
        top[0]['text'] = '三、' + top[0]['text']
        top[1]['text'] = '一、' + top[1]['text']
        top[2]['text'] = '一、' + top[2]['text']
        messy = service.revise(self.store, self.record['id'],
                               {'revision': self.record['revision'], 'items': outline_rows}, 'outline')
        # 可安全修正的序号问题已在生成目录时自动处理，不再要求人工勾选。
        self.assertNotIn('sequence', codes(messy['outline_check']))
        project = service.confirm(self.store, messy['id'], {'revision': messy['revision']})
        self.assertEqual(messy['id'], project['framework_id'])


if __name__ == '__main__':
    unittest.main()
