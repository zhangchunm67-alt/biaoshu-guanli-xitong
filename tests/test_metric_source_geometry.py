"""Reject hostile table dimensions before expanding virtual grid columns."""
from pathlib import Path
import sys
import unittest

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/bid-response-framework/scripts'))
from metric_source import table_rows, table_heading, MAX_GRID_COLUMNS

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def table(spans, before='0', after='0'):
    root = etree.Element('{'+W+'}tbl', nsmap={'w': W})
    row = etree.SubElement(root, '{'+W+'}tr')
    props = etree.SubElement(row, '{'+W+'}trPr')
    for name, value in [('gridBefore', before), ('gridAfter', after)]:
        etree.SubElement(props, '{'+W+'}'+name).set('{'+W+'}val', value)
    for width in spans:
        cell = etree.SubElement(row, '{'+W+'}tc')
        props = etree.SubElement(cell, '{'+W+'}tcPr')
        etree.SubElement(props, '{'+W+'}gridSpan').set('{'+W+'}val', width)
        paragraph = etree.SubElement(cell, '{'+W+'}p')
        run = etree.SubElement(paragraph, '{'+W+'}r')
        etree.SubElement(run, '{'+W+'}t').text = '技术要求'
    return root


class MetricGeometryTests(unittest.TestCase):
    def test_boundary_retains_legal_cells(self):
        rows = table_rows(table([str(MAX_GRID_COLUMNS-1), '1']))
        self.assertEqual(2, len(rows[0][0]))
        self.assertEqual(MAX_GRID_COLUMNS, len(table_heading(rows)[1]))

    def test_each_dimension_and_row_total_have_a_hard_limit(self):
        for spans, before, after in [(['4097'], '0', '0'), (['4096', '1'], '0', '0'),
                                    (['1'], '4096', '0'), (['1'], '0', '4096'),
                                    (['1'], '4097', '0'), (['1'], '0', '4097')]:
            with self.subTest(spans=spans, before=before, after=after):
                with self.assertRaisesRegex(ValueError, '表格列数异常'):
                    table_rows(table(spans, before, after))

    def test_negative_zero_and_noninteger_spans_are_rejected(self):
        for value in ('0', '-1', '1.5', '1_0', 'NaN', ''):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, '表格列数异常'):
                    table_rows(table([value]))

    def test_word_integer_lexical_variants_keep_the_same_geometry(self):
        self.assertEqual(table_rows(table(['1']))[0][:2],
                         table_rows(table(['+0001'], '-0', '+0'))[0][:2])

    def test_grid_declaration_also_has_a_hard_limit(self):
        root = table(['1'])
        grid = etree.SubElement(root, '{'+W+'}tblGrid')
        for _ in range(MAX_GRID_COLUMNS+1):
            etree.SubElement(grid, '{'+W+'}gridCol')
        with self.assertRaisesRegex(ValueError, '表格列数异常'):
            table_rows(root)


if __name__ == '__main__':
    unittest.main()
