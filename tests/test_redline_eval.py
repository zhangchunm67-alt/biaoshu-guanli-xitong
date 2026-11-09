"""Redline precision/recall harness: matching rules, annotation sheets and the corpus run."""
import contextlib
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from docx import Document
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import eval_redline as harness
from eval_redline import Gold, Prediction, evaluate, metrics


def quiet(function, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args)


class MatchingTests(unittest.TestCase):
    def test_normalization_ignores_numbering_spacing_and_punctuation(self):
        self.assertEqual(harness.norm('投标文件未按规定加盖单位公章'), harness.norm('（1） 投标文件未按规定加盖单位公章；'))
        self.assertEqual(1.0, harness.coverage(harness.norm('投标报价超过最高限价'),
                                               harness.norm('2、投标报价超过最高限价的，投标无效。')))
        self.assertLess(harness.coverage(harness.norm('投标报价超过最高限价'),
                                         harness.norm('投标文件未按规定密封')), harness.THRESHOLD)

    def test_each_system_item_counts_once_with_typed_errors(self):
        golds = [Gold('G1', '投标文件未按规定加盖单位公章'), Gold('G2', '投标报价超过最高限价'),
                 Gold('G3', '投标截止时间后送达的投标文件不予受理'),
                 Gold('G4', '未按要求提交投标保证金', '不适用'), Gold('G5', '联合体协议未载明分工', '存疑')]
        predictions = [
            Prediction('p1', '（1）投标文件未按规定加盖单位公章；（2）投标报价超过最高限价'),
            Prediction('p2', '投标报价超过最高限价的，投标无效。'),
            Prediction('p3', '投标文件未按规定加盖单位公章的'),
            Prediction('p4', '未按要求提交投标保证金'),
            Prediction('p5', '联合体协议未载明分工的，投标无效'),
            Prediction('p6', '评标委员会应当对否决投标的情况作出书面说明'),
            Prediction('p7', '截止时间后送达'),
        ]
        counts, details = evaluate(golds, predictions)
        pairs = {(row['gold'].ident, row['prediction'].ident) for row in details if row['kind'] == 'TP'}
        self.assertEqual({('G1', 'p3'), ('G2', 'p2')}, pairs)
        kinds = {row['prediction'].ident: row['kind'] for row in details if row['prediction'] and row['kind'] != 'TP'}
        self.assertEqual({'p1': 'DUP', 'p4': 'FP_NA', 'p5': 'IGN', 'p6': 'FP', 'p7': 'FRAG'}, kinds)
        result = metrics(counts)
        self.assertEqual((3, 6, 2, 1, 3, 1), (result['金标条数'], result['系统条数'], result['命中'],
                                              result['漏报'], result['误报'], result['重复']))
        self.assertAlmostEqual(2 / 6, result['精确率'])
        self.assertAlmostEqual(3 / 6, result['宽松精确率'])
        self.assertAlmostEqual(2 / 3, result['召回率'])

    def test_one_item_merging_two_clauses_misses_one_strictly(self):
        golds = [Gold('G1', '投标文件未按规定密封'), Gold('G2', '投标文件未按规定标记')]
        counts, details = evaluate(golds, [Prediction('p1', '投标文件未按规定密封或者投标文件未按规定标记的，不予受理。')])
        result = metrics(counts)
        self.assertEqual((0.5, 1.0), (result['召回率'], result['宽松召回率']))
        self.assertEqual(['与其他条款合并为一条'], [row['reason'] for row in details if row['kind'] == 'FN'])

    def test_missed_clause_reports_where_it_went(self):
        golds = [Gold('G1', '供应商须具有有效的营业执照'), Gold('G2', '投标有效期不少于九十天'),
                 Gold('G3', '投标文件须胶装成册')]
        others = [Prediction('q1', '供应商须具有有效的营业执照。', 'qualification')]
        _, details = evaluate(golds, [], others=others, units=['1 ｜ 投标有效期 ｜ 不少于九十天'])
        reasons = {row['gold'].ident: row['reason'] for row in details}
        self.assertEqual('归入强制资格要求', reasons['G1'])
        self.assertEqual('未识别', reasons['G2'])
        self.assertTrue(reasons['G3'].startswith('未读取'))


class AnnotationSheetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='redline-eval-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write_csv(self, path, rows):
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['编号', '原文条款', '状态'])
            writer.writerows(rows)

    def test_gold_sheet_rejects_unknown_status_and_merges_repeated_clauses(self):
        path = self.root / 'gold.csv'
        self.write_csv(path, [['G1', '投标报价超过最高限价', ''], ['G2', ' （2）投标报价超过最高限价；', '有效'],
                              ['G3', '投标报价超过最高限价', '存疑'], ['', '', '']])
        golds, merged = harness.read_gold(path)
        self.assertEqual((['有效', '存疑'], 1), ([g.status for g in golds], merged))
        self.write_csv(path, [['G1', '投标报价超过最高限价', '有效的']])
        with self.assertRaisesRegex(ValueError, '第2行状态'):
            harness.read_gold(path)

    def tender(self, folder, lines):
        folder.mkdir(parents=True)
        document = Document()
        for line in lines:
            document.add_paragraph(line)
        document.save(folder / '招标文件.docx')
        return folder / '招标文件.docx'

    def test_blind_workbook_then_corpus_run_writes_reproducible_report(self):
        clauses = ['投标文件未按规定密封', '投标文件未加盖单位公章', '投标报价超过最高限价']
        source = self.tender(self.root / 'corpus/D01', ['第二章 投标人须知', '有下列情形之一的，其投标将被否决：',
                                                        *[f'（{i}）{c}；' for i, c in enumerate(clauses, 1)],
                                                        '投标人须在两小时内响应报修。'])
        self.tender(self.root / 'corpus/D02', ['投标截止时间后送达的投标文件，招标人不予受理。'])
        sheet = self.root / 'corpus/D01/红线标注.xlsx'
        quiet(harness.main, ['init', str(source), str(sheet)])
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                harness.main(['init', str(source), str(sheet)])
        book = load_workbook(sheet)
        self.assertEqual(['红线标注', '原文段落', '标注说明'], book.sheetnames)
        texts = [row[4] for row in book['原文段落'].iter_rows(min_row=2, values_only=True)]
        self.assertIn('（1）投标文件未按规定密封；', texts)
        for clause in clauses:
            book['红线标注'].append(['', clause, '有效', '否决/无效', '一律'])
        book['红线标注'].append(['', '投标人须在两小时内响应报修', '有效', '否决/无效', '可能'])
        book.save(sheet)
        self.write_csv(self.root / 'corpus/D02/红线标注.csv', [['G1', '投标截止时间后送达的投标文件，招标人不予受理', '有效']])
        (self.root / 'corpus/manifest.csv').write_text('目录,集合\nD01,test\nD02,dev\n', encoding='utf-8-sig')
        out = self.root / 'report'
        result, _ = quiet(harness.main, ['run', str(self.root / 'corpus'), '--split', 'test', '--out', str(out)])
        self.assertEqual(['D01'], [d['name'] for d in result['documents']])
        summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
        micro = summary['micro']
        self.assertEqual((4, 3, 1), (micro['金标条数'], micro['命中'], micro['漏报']))
        self.assertEqual(micro['系统条数'], micro['命中'] + micro['误报'] + micro['重复'])
        self.assertEqual('test', summary['split'])
        self.assertEqual(64, len(summary['documents'][0]['招标文件SHA256']))
        self.assertEqual(1.0, summary['breakdown']['确定性']['一律']['召回率'])
        with (out / 'details.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(['漏报'], [r['判定'] for r in rows if r['金标条款'] == '投标人须在两小时内响应报修'])
        self.assertIn('严格·微平均', (out / 'report.md').read_text(encoding='utf-8'))
        full, _ = quiet(harness.main, ['run', str(self.root / 'corpus'), '--out', str(self.root / 'all')])
        self.assertEqual(['D01', 'D02'], [d['name'] for d in full['documents']])

    def test_agreement_scores_second_annotator_against_first(self):
        folder = self.root / 'corpus/D01'
        folder.mkdir(parents=True)
        self.write_csv(folder / '红线标注_A.csv', [['A1', '投标文件未按规定密封', '有效'],
                                                ['A2', '投标报价超过最高限价', '有效'], ['A3', '未提交投标保证金', '有效']])
        self.write_csv(folder / '红线标注_B.csv', [['B1', '（1）投标文件未按规定密封', '有效'],
                                                ['B2', '投标报价超过最高限价的', '有效'], ['B3', '投标人不足三家', '有效'],
                                                ['B4', '未提交投标保证金', '存疑']])
        result, out = quiet(harness.main, ['agree', str(self.root / 'corpus'), '--a', '红线标注_A', '--b', '红线标注_B'])
        self.assertAlmostEqual(2 / 3, result['F1'])
        self.assertTrue((out / 'details.csv').is_file())


if __name__ == '__main__':
    unittest.main()
