"""Original form fields must remain linked or become explicit cached text."""
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/bid-response-framework/scripts'
sys.path.insert(0, str(SCRIPTS))
from build_framework import build

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


def element(tag, **attrs):
    node = OxmlElement('w:' + tag)
    for key, value in attrs.items():
        node.set(qn('w:' + key), str(value))
    return node


def target(paragraph, name):
    paragraph._p.append(element('bookmarkStart', id=1, name=name))
    paragraph.add_run('原公司名称')
    paragraph._p.append(element('bookmarkEnd', id=1))


def references(paragraph, name):
    simple = element('fldSimple', instr=f' REF {name} \\h ')
    run = element('r')
    text = element('t')
    text.text = '原公司名称'
    run.append(text)
    simple.append(run)
    paragraph._p.append(simple)
    # Word commonly splits a complex field's instruction across text runs.
    for tag, attrs, text in [
        ('fldChar', {'fldCharType': 'begin'}, None),
        ('instrText', {}, ' PAGE'),
        ('instrText', {}, f'REF "{name}" \\h '),
        ('fldChar', {'fldCharType': 'separate'}, None),
        ('t', {}, '原页码'),
        ('fldChar', {'fldCharType': 'end'}, None),
    ]:
        node = element(tag, **attrs)
        node.text = text
        paragraph.add_run()._r.append(node)
    link = element('hyperlink', anchor=name)
    run = element('r')
    text = element('t')
    text.text = '原内部链接'
    run.append(text)
    link.append(run)
    paragraph._p.append(link)


class SourceReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def generate(self, doc, blocks):
        source = self.root / '原件.docx'
        output = self.root / '框架.docx'
        doc.save(source)
        build(source, {'blocks': [{'kind': 'toc'}, *blocks]}, output)
        with ZipFile(output) as archive:
            return etree.fromstring(archive.read('word/document.xml'))

    def assert_targets_valid(self, tree):
        names = tree.xpath('//w:bookmarkStart/@w:name', namespaces=NS)
        ids = tree.xpath('//w:bookmarkStart/@w:id', namespaces=NS)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertCountEqual(ids, tree.xpath('//w:bookmarkEnd/@w:id', namespaces=NS))
        for anchor in tree.xpath('//w:hyperlink/@w:anchor', namespaces=NS):
            self.assertIn(anchor, names)
        return names

    def test_original_bookmark_and_split_fields_avoid_section_collision(self):
        doc = Document()
        table = doc.add_table(rows=2, cols=1)
        target(table.cell(0, 0).paragraphs[0], 'BidSection1')
        references(table.cell(1, 0).paragraphs[0], 'BidSection1')
        tree = self.generate(doc, [
            {'kind': 'heading', 'text': '报价表', 'bookmark': 'BidSection1'},
            {'kind': 'copy', 'start': 0, 'preserve_format': True},
        ])
        names = self.assert_targets_valid(tree)
        self.assertEqual(['BidSection1', 'BFSource1'], names)
        self.assertEqual([' PAGEREF BidSection1 \\h ', ' REF BFSource1 \\h '],
                         tree.xpath('//w:fldSimple/@w:instr', namespaces=NS))
        self.assertEqual(' PAGEREF "BFSource1" \\h ',
                         ''.join(tree.xpath('//w:tbl//w:instrText/text()', namespaces=NS)))
        self.assertNotIn('待核对', ''.join(tree.xpath('//w:t/text()', namespaces=NS)))

    def test_repeated_form_copy_points_at_its_own_original_target(self):
        doc = Document()
        table = doc.add_table(rows=2, cols=1)
        target(table.cell(0, 0).paragraphs[0], 'Company')
        references(table.cell(1, 0).paragraphs[0], 'Company')
        tree = self.generate(doc, [
            {'kind': 'heading', 'text': '报价表'},
            {'kind': 'copy', 'start': 0, 'preserve_format': True},
            {'kind': 'copy', 'start': 0, 'preserve_format': True},
        ])
        self.assert_targets_valid(tree)
        for table in tree.xpath('//w:tbl[w:tr/w:tc//w:bookmarkStart]', namespaces=NS):
            name = table.xpath('.//w:bookmarkStart/@w:name', namespaces=NS)[0]
            self.assertEqual([f' REF {name} \\h '], table.xpath('.//w:fldSimple/@w:instr', namespaces=NS))
            self.assertEqual([name], table.xpath('.//w:hyperlink/@w:anchor', namespaces=NS))

    def test_cross_paragraph_original_target_and_reference_remain_valid(self):
        doc = Document()
        target(doc.add_paragraph(), 'Company')
        references(doc.add_paragraph(), 'Company')
        tree = self.generate(doc, [
            {'kind': 'heading', 'text': '报价表'},
            {'kind': 'copy', 'start': 0, 'preserve_format': True},
            {'kind': 'copy', 'start': 1, 'preserve_format': True},
        ])
        self.assert_targets_valid(tree)
        self.assertIn(' REF BFSource1 \\h ', tree.xpath('//w:fldSimple/@w:instr', namespaces=NS))

    def test_target_outside_copy_freezes_all_cached_displays_with_notice(self):
        doc = Document()
        target(doc.add_paragraph(), 'Company')
        references(doc.add_paragraph(), 'Company')
        tree = self.generate(doc, [
            {'kind': 'heading', 'text': '报价表'},
            {'kind': 'copy', 'start': 1, 'preserve_format': True},
        ])
        self.assertEqual(['BidSection1'], self.assert_targets_valid(tree))
        self.assertEqual([' PAGEREF BidSection1 \\h '], tree.xpath('//w:fldSimple/@w:instr', namespaces=NS))
        self.assertFalse(any('Company' in value for value in tree.xpath('//w:instrText/text()', namespaces=NS)))
        self.assertEqual(3, len(tree.xpath('//w:fldChar', namespaces=NS)))  # generated TOC only
        text = ''.join(tree.xpath('//w:t/text()', namespaces=NS))
        for original in ('原公司名称', '原页码', '原内部链接'):
            self.assertIn(original, text)
        self.assertEqual(3, text.count('【待核对：原文交叉引用目标'))

    def test_partial_complex_field_cannot_update_a_missing_field_end(self):
        doc = Document()
        paragraph = doc.add_paragraph()
        paragraph.add_run()._r.append(element('fldChar', fldCharType='begin'))
        instruction = element('instrText')
        instruction.text = ' REF MissingCompany '
        paragraph.add_run()._r.append(instruction)
        paragraph.add_run()._r.append(element('fldChar', fldCharType='separate'))
        doc.add_paragraph('原缓存公司名称')
        doc.add_paragraph().add_run()._r.append(element('fldChar', fldCharType='end'))
        tree = self.generate(doc, [
            {'kind': 'heading', 'text': '报价表'},
            {'kind': 'copy', 'start': 0, 'end': 1, 'preserve_format': True},
        ])
        self.assertEqual(3, len(tree.xpath('//w:fldChar', namespaces=NS)))
        text = ''.join(tree.xpath('//w:t/text()', namespaces=NS))
        self.assertIn('原缓存公司名称', text)
        self.assertIn('【待核对：原文交叉引用目标', text)

    def test_hyperlink_field_retargets_internal_bookmark(self):
        doc = Document()
        target(doc.add_paragraph(), 'Company')
        paragraph = doc.add_paragraph()
        for name in ('Company', 'MissingCompany'):
            field = element('fldSimple', instr=f' HYPERLINK \\l "{name}" ')
            run = element('r')
            text = element('t')
            text.text = '原跳转文字'
            run.append(text)
            field.append(run)
            paragraph._p.append(field)
        tree = self.generate(doc, [
            {'kind': 'heading', 'text': '报价表'},
            {'kind': 'copy', 'start': 0, 'end': 1, 'preserve_format': True},
        ])
        self.assert_targets_valid(tree)
        self.assertIn(' HYPERLINK \\l "BFSource1" ', tree.xpath('//w:fldSimple/@w:instr', namespaces=NS))
        visible = ''.join(tree.xpath('//w:t/text()', namespaces=NS))
        self.assertEqual(2, visible.count('原跳转文字'))
        self.assertEqual(1, visible.count('【待核对：原文交叉引用目标'))


if __name__ == '__main__':
    unittest.main()
