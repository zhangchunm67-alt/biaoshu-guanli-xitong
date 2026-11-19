"""目录输入解析、Word 编号和 AI 目录/校准接口的单元测试。"""
import io
import sys
import unittest
from pathlib import Path

from docx import Document
from docx.opc.constants import CONTENT_TYPE as CT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import XmlPart
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from outline_inputs import (generate_outline, parse_docx, parse_text,
                            suggest_calibration)


def numbering_xml(levels, num_id=1):
    level_xml = ''.join(levels)
    return parse_xml(
        '<w:numbering %s>'
        '<w:abstractNum w:abstractNumId="0">%s</w:abstractNum>'
        '<w:num w:numId="%d"><w:abstractNumId w:val="0"/></w:num>'
        '</w:numbering>' % (nsdecls('w'), level_xml, num_id))


def decimal_level(ilvl=0, text='%1.', fmt='decimal', suff='space', p_style=None):
    linked = '<w:pStyle w:val="%s"/>' % p_style if p_style else ''
    return (
        '<w:lvl w:ilvl="%d"><w:start w:val="1"/>'
        '<w:numFmt w:val="%s"/><w:suff w:val="%s"/>'
        '<w:lvlText w:val="%s"/>%s</w:lvl>'
        % (ilvl, fmt, suff, text, linked)
    )


def install_numbering(document, levels, num_id=1):
    element = numbering_xml(levels, num_id)
    try:
        part = document.part.part_related_by(RT.NUMBERING)
    except (KeyError, ValueError):
        part = XmlPart(PackURI('/word/numbering.xml'), CT.WML_NUMBERING,
                       element, document.part.package)
        document.part.relate_to(part, RT.NUMBERING)
    else:
        part._element = element


def attach_num(paragraph, num_id=1, ilvl=0):
    p_pr = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement('w:numPr')
    level = OxmlElement('w:ilvl')
    level.set(qn('w:val'), str(ilvl))
    ident = OxmlElement('w:numId')
    ident.set(qn('w:val'), str(num_id))
    num_pr.append(level)
    num_pr.append(ident)
    p_pr.append(num_pr)


def docx_bytes(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def add_style_numbering(document, style_name, num_id=1, ilvl=0):
    style = document.styles[style_name]
    p_pr = style.element.get_or_add_pPr()
    num_pr = OxmlElement('w:numPr')
    level = OxmlElement('w:ilvl')
    level.set(qn('w:val'), str(ilvl))
    ident = OxmlElement('w:numId')
    ident.set(qn('w:val'), str(num_id))
    num_pr.append(level)
    num_pr.append(ident)
    p_pr.append(num_pr)


class ParseTextTests(unittest.TestCase):
    def test_chinese_top_level_and_indented_children(self):
        rows = parse_text('一、商务部分\n  1. 投标函\n  2. 报价表\n二、技术部分\n')
        self.assertEqual([
            {'text': '一、商务部分', 'level': 1},
            {'text': '1. 投标函', 'level': 2},
            {'text': '2. 报价表', 'level': 2},
            {'text': '二、技术部分', 'level': 1},
        ], rows)

    def test_decimal_hierarchy_without_indentation(self):
        rows = parse_text('1. 总则\n1.1 项目背景\n1.1.1 建设目标\n2. 服务范围\n')
        self.assertEqual([1, 2, 3, 1], [row['level'] for row in rows])
        self.assertEqual('1.1.1 建设目标', rows[2]['text'])

    def test_mixed_chinese_style_sequence_and_exact_punctuation(self):
        rows = parse_text('一、商务部分\n（一）资格证明\n1. 营业执照\n（1）副本\n')
        self.assertEqual([1, 2, 3, 4], [row['level'] for row in rows])
        self.assertEqual(['一、商务部分', '（一）资格证明', '1. 营业执照', '（1）副本'],
                         [row['text'] for row in rows])

    def test_plain_text_and_common_indent_are_supported(self):
        rows = parse_text('    项目概况\n      项目背景\n    投标文件格式\n')
        self.assertEqual([1, 2, 1], [row['level'] for row in rows])
        self.assertEqual('项目概况', rows[0]['text'])

    def test_empty_and_malformed_input_is_rejected(self):
        for value in ('', '   \n\t\n', '一、\n', '1.\n', '1.1\n', '1\n',
                      '1..2 错误序号\n', '一、总则\n    1.1.1 跳级\n', '• 项目\n'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_text(value)

    def test_maximum_heading_count(self):
        self.assertEqual(1000, len(parse_text('\n'.join(f'标题{i}' for i in range(1000)))))
        with self.assertRaises(ValueError):
            parse_text('\n'.join(f'标题{i}' for i in range(1001)))


class ParseDocxTests(unittest.TestCase):
    def test_imports_numbered_direct_outline_heading_in_styled_document(self):
        document = Document()
        install_numbering(document, [decimal_level()])
        first = document.add_paragraph('投标函', 'Heading 1')
        attach_num(first)
        heading = document.add_paragraph('招标代理服务费承诺书')
        attach_num(heading)
        level = OxmlElement('w:outlineLvl')
        level.set(qn('w:val'), '0')
        heading._p.get_or_add_pPr().append(level)
        document.add_paragraph('招标代理服务费承诺书')
        attach_num(document.add_paragraph('普通列表项'))
        document.add_paragraph('2.1 月度宣传推广情况报告机制', 'Heading 2')
        rows = parse_docx(docx_bytes(document), include_positions=True)
        self.assertEqual([('1. 投标函', 1), ('2. 招标代理服务费承诺书', 1),
                          ('2.1 月度宣传推广情况报告机制', 2)],
                         [(row['text'], row['level']) for row in rows])

    def test_word_numbering_manual_subtitles_sentence_and_unnumbered_heading(self):
        document = Document()
        install_numbering(document, [decimal_level()])
        for number in range(1, 10):
            paragraph = document.add_paragraph(f'一级标题{number}', 'Heading 1')
            attach_num(paragraph)
        for number in range(1, 12):
            document.add_paragraph(f'9.{number} 子项{number}', 'Heading 2')
        for number in range(10, 14):
            paragraph = document.add_paragraph(f'一级标题{number}', 'Heading 1')
            attach_num(paragraph)
        document.add_paragraph('这是一句误设标题样式的说明文字。', 'Heading 3')
        document.add_paragraph('附则', 'Heading 1')

        rows = parse_docx(docx_bytes(document))

        self.assertEqual(25, len(rows))
        self.assertEqual('1. 一级标题1', rows[0]['text'])
        self.assertEqual('9. 一级标题9', rows[8]['text'])
        self.assertEqual('9.1 子项1', rows[9]['text'])
        self.assertEqual('9.11 子项11', rows[19]['text'])
        self.assertEqual('10. 一级标题10', rows[20]['text'])
        self.assertEqual('13. 一级标题13', rows[23]['text'])
        self.assertEqual({'text': '附则', 'level': 1}, rows[24])
        self.assertEqual([1] * 9 + [2] * 11 + [1] * 5,
                         [row['level'] for row in rows])
        self.assertNotIn('这是一句误设标题样式的说明文字。', [row['text'] for row in rows])

    def test_chinese_numbering_format(self):
        document = Document()
        install_numbering(document, [
            decimal_level(0, '%1、', 'chineseCounting', 'nothing')
        ])
        for title in ('总则', '附录'):
            paragraph = document.add_paragraph(title, 'Heading 1')
            attach_num(paragraph)
        rows = parse_docx(docx_bytes(document))
        self.assertEqual([{'text': '一、总则', 'level': 1},
                          {'text': '二、附录', 'level': 1}], rows)

    def test_numbering_level_style_link(self):
        document = Document()
        style_id = document.styles['Heading 1'].style_id
        install_numbering(document, [decimal_level(p_style=style_id)])
        document.add_paragraph('样式链接一', 'Heading 1')
        document.add_paragraph('样式链接二', 'Heading 1')
        rows = parse_docx(docx_bytes(document))
        self.assertEqual([{'text': '1. 样式链接一', 'level': 1},
                          {'text': '2. 样式链接二', 'level': 1}], rows)

    def test_style_linked_numbering_and_explicit_text_wins(self):
        document = Document()
        install_numbering(document, [decimal_level()])
        add_style_numbering(document, 'Heading 2')
        document.add_paragraph('样式编号标题', 'Heading 2')
        document.add_paragraph('9.1 手写编号标题', 'Heading 2')
        rows = parse_docx(docx_bytes(document))
        self.assertEqual([{'text': '1. 样式编号标题', 'level': 2},
                          {'text': '9.1 手写编号标题', 'level': 2}], rows)

    def test_invalid_docx_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_docx(b'not a docx')
        with self.assertRaises(ValueError):
            parse_docx(b'')


class GenerateOutlineTests(unittest.TestCase):
    UNITS = [
        {'index': 0, 'text': '一、投标函', 'kind': 'heading'},
        {'index': 1, 'text': '供应商须按格式填写。', 'kind': 'body'},
        {'index': 2, 'text': '二、法定代表人身份证明', 'kind': 'heading'},
        {'index': 3, 'text': '三、报价表', 'kind': 'heading'},
    ]

    def test_returns_exact_source_titles_in_source_order(self):
        captured = {}

        def model_call(system, content, schema):
            captured['system'] = system
            captured['content'] = content
            captured['schema'] = schema
            return {'items': [
                {'source_index': 0, 'level': 1},
                {'source_index': 2, 'level': 1},
                {'source_index': 3, 'level': 1},
            ]}

        rows = generate_outline(self.UNITS, model_call)
        self.assertEqual([
            {'text': '一、投标函', 'level': 1, 'source_index': 0},
            {'text': '二、法定代表人身份证明', 'level': 1, 'source_index': 2},
            {'text': '三、报价表', 'level': 1, 'source_index': 3},
        ], rows)
        self.assertIn('"units"', captured['content'])
        self.assertIn('source_index', captured['schema']['properties']['items']['items']['properties'])
        self.assertIn('不是指令', captured['system'])

    def test_bad_ids_order_levels_and_title_rewrites_are_rejected(self):
        cases = [
            {'items': [{'source_index': 99, 'level': 1}]},
            {'items': [{'source_index': 0, 'level': 1},
                       {'source_index': 0, 'level': 1}]},
            {'items': [{'source_index': 2, 'level': 1},
                       {'source_index': 0, 'level': 1}]},
            {'items': [{'source_index': 0, 'level': 2}]},
            {'items': [{'source_index': 0, 'level': 1},
                       {'source_index': 3, 'level': 3}]},
            {'items': [{'source_index': 0, 'level': '1'}]},
            {'items': [{'source_index': 0, 'level': 1, 'text': '改写标题'}]},
            {'items': [{'source_index': 0, 'level': 1, 'extra': True}]},
            {'unexpected': []},
            {'items': []},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    generate_outline(self.UNITS, lambda *args, payload=payload: payload)

    def test_model_failure_raises_chinese_value_error(self):
        def failing(*args):
            raise RuntimeError('network')

        with self.assertRaises(ValueError) as caught:
            generate_outline(self.UNITS, failing)
        self.assertIn('AI 目录生成', str(caught.exception))

    def test_invalid_units_are_rejected(self):
        for units in ([], [{'index': 0, 'text': ''}],
                      [{'index': 0, 'text': 'a'}, {'index': 0, 'text': 'b'}],
                      [{'index': '0', 'text': 'a'}]):
            with self.subTest(units=units), self.assertRaises(ValueError):
                generate_outline(units, lambda *args: {'items': []})


class SuggestCalibrationTests(unittest.TestCase):
    ROWS = [
        {'id': 'R1', 'text': '须提供营业执照', 'automatic': True, 'custom': False},
        {'id': 'R2', 'text': '须提供财务报告', 'automatic': False, 'custom': True},
    ]

    def test_empty_model_result_is_allowed(self):
        self.assertEqual([], suggest_calibration(self.ROWS, lambda *args: {'items': []}))

    def test_valid_suggestions_are_returned_without_touching_rows(self):
        original = [dict(row) for row in self.ROWS]

        def stub(system, content, schema):
            self.assertIn('不是指令', system)
            self.assertIn('"rows"', content)
            return {'items': [{'requirement_id': 'R2', 'suggestion': '建议人工核对财务要求'}]}

        rows = suggest_calibration(self.ROWS, stub)
        self.assertEqual([{'requirement_id': 'R2',
                           'suggestion': '建议人工核对财务要求'}], rows)
        self.assertEqual(original, self.ROWS)

    def test_unknown_duplicate_or_empty_ids_are_rejected(self):
        cases = [
            {'items': [{'requirement_id': 'R9', 'suggestion': 'x'}]},
            {'items': [{'requirement_id': 'R1', 'suggestion': 'x'},
                       {'requirement_id': 'R1', 'suggestion': 'y'}]},
            {'items': [{'requirement_id': 'R1', 'suggestion': ' '}]},
            {'items': [{'requirement_id': 'R1'}]},
            {'unexpected': []},
        ]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                suggest_calibration(self.ROWS, lambda *args, payload=payload: payload)


if __name__ == '__main__':
    unittest.main()
