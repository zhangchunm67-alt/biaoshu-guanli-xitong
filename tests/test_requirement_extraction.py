"""Procurement-content regressions independent of the generated response outline."""
from collections import Counter
import copy
from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest

from docx import Document
from lxml import html

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/bid-response-framework/scripts'))
from metric_source import read_units
from metric_eligibility import extract as eligibility
from metric_scoring_specs import extract as scoring
from tender_metrics import CATEGORIES, export_review, save_report


class ExtractionRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bid-extraction-')
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / '采购原件.docx'

    def save(self, document):
        document.save(self.source)
        return read_units(self.source)[0]

    def test_explicit_admission_certificate_request_outside_qualification_heading(self):
        doc = Document()
        for text in ['第一章 采购要求', '供应商须提供有效营业执照复印件。',
                     '供应商须提供实施方案和验收交付材料。',
                     '供应商无需提供经营许可证。']:
            doc.add_paragraph(text)
        rows = [r for r in eligibility(self.save(doc)) if r['category'] == 'qualification']
        self.assertEqual(['供应商须提供有效营业执照复印件。'], [r['text'] for r in rows])

    def test_procurement_requirements_heading_keeps_response_obligation(self):
        doc = Document()
        doc.add_paragraph('第一章 采购要求')
        clause = '供应商须在接到通知后2小时内响应，并提供实施计划。'
        doc.add_paragraph(clause)
        rows = [r for r in scoring(self.save(doc)) if r['category'] == 'spec']
        self.assertEqual([clause], [r['text'] for r in rows])

    def test_mandatory_gate_survives_nested_headings_and_relationship_definition(self):
        doc = Document()
        for text in [
            '四、合格供应商的基本资质要求（须同时满足）：',
            '（一）供应商及所报产品/服务的资质要求如下：',
            '1.供应商具有自2023年1月1日以来的同类服务经验，提供至少一个合同复印件。',
            '（二）其他必须满足的要求：',
            '1.供应商不得与存在关联关系的其他供应商同时参加本项目。',
            '关联关系企业包含以下情况：',
            '与本企业法定代表人为同一人的其他企业；',
            '与本企业存在直接控股、管理关系的其他企业。',
            '2.截至递交截止日，供应商未处于采购人禁止准入处罚期内。',
            '3.供应商应未受到联合国制裁，且未被制裁对象控制。',
            '五、项目服务要求',
            '供应商须每周提供三次技术支持服务。',
        ]:
            doc.add_paragraph(text)
        rows = [r for r in eligibility(self.save(doc)) if r['category'] == 'qualification']
        self.assertEqual(4, len(rows))
        self.assertIn('2023年1月1日', rows[0]['text'])
        self.assertIn('至少一个合同', rows[0]['text'])
        self.assertEqual(3, len(rows[1]['related']))
        self.assertTrue(any('禁止准入' in r['text'] for r in rows))
        self.assertTrue(any('制裁' in r['text'] for r in rows))
        self.assertFalse(any('技术支持' in r['text'] for r in rows))

    def test_page_column_and_repeated_factor_names_do_not_hide_award_criteria(self):
        doc = Document()
        doc.add_paragraph('第三部分 评分办法')
        for number, name in [(1, '图文服务'), (2, '视频服务')]:
            doc.add_paragraph(f'包件{number}：{name}')
            table = doc.add_table(rows=1, cols=5)
            for c, value in zip(table.rows[0].cells, ['评分项', '评分项', '评分内容', '分值', '应答文件装订页码']):
                c.text = value
            for values in [
                ['价格', '价格', '价格分按最低报价计算。', '50', ''],
                ['商务部分', '综合实力', '依据场地设备情况评分。\n优：3-5分；一般：0-3分。', '5', ''],
                ['商务部分', '综合实力', '依据财务情况评分。\n优：3-5分；一般：0-3分。', '5', ''],
            ]:
                for c, value in zip(table.add_row().cells, values):
                    c.text = value
        doc.add_paragraph('第四部分 报价要求')
        table = doc.add_table(rows=1, cols=5)
        for c, value in zip(table.rows[0].cells, ['服务内容', '服务说明', '单价', '税率', '权重']):
            c.text = value
        for c, value in zip(table.add_row().cells, ['海报设计', '含改稿', '', '', '40%']):
            c.text = value
        rows = [r for r in scoring(self.save(doc)) if r['category'] == 'score']
        self.assertEqual(6, len(rows))
        self.assertEqual({'包件1 · 图文服务': 3, '包件2 · 视频服务': 3}, Counter(r['unit'].scope for r in rows))
        self.assertFalse(any('海报设计' in r['text'] for r in rows))
        self.assertEqual(2, sum('依据财务情况' in r['text'] for r in rows))

    def test_grade_only_rows_form_one_factor(self):
        doc = Document()
        table = doc.add_table(rows=1, cols=3)
        for c, value in zip(table.rows[0].cells, ['评分因素', '评分标准', '分值']):
            c.text = value
        for values in [['方案质量', '优：完整可执行。', '10'], ['方案质量', '良：基本可执行。', '6']]:
            for c, value in zip(table.add_row().cells, values):
                c.text = value
        rows = [r for r in scoring(self.save(doc)) if r['category'] == 'score']
        self.assertEqual(1, len(rows))
        self.assertEqual(1, len(rows[0]['related']))

    def test_reviewed_content_round_trips_with_original_evidence_and_html_escaping(self):
        doc = Document()
        doc.add_paragraph('供应商须具有独立承担民事责任的能力。')
        self.save(doc)
        first = export_review(self.source)
        again = export_review(self.source)
        self.assertEqual(first['sources'], again['sources'])
        requirement = copy.deepcopy(first['requirements'][0])
        requirement.update(category='qualification', label='<img src=x onerror=alert(1)>',
                           text='经人工核对的准入要求', note='补充核对法人登记证。')
        requirement['references'][0]['text'] = '伪造的原文不得取代真实来源'
        plan = {'source_sha256': sha256(self.source.read_bytes()).hexdigest(),
                'reviewed_requirements': [requirement]}
        reviewed = export_review(self.source, plan)
        self.assertEqual('经人工核对的准入要求', reviewed['requirements'][0]['text'])
        self.assertEqual('供应商须具有独立承担民事责任的能力。', reviewed['requirements'][0]['references'][0]['text'])
        report = Path(self.temp.name) / 'review.html'
        counts = save_report(self.source, report, plan)
        self.assertEqual(1, counts['qualification'])
        self.assertEqual(5, len(counts))
        parsed = html.parse(str(report))
        self.assertFalse(parsed.xpath('//img'))
        self.assertIn('补充核对法人登记证', parsed.getroot().text_content())
        self.assertEqual(5, len(parsed.xpath('//a[contains(@class,"card")]')))

    def test_reviewed_content_cannot_point_to_unknown_source(self):
        doc = Document()
        doc.add_paragraph('供应商须具有独立承担民事责任的能力。')
        self.save(doc)
        row = export_review(self.source)['requirements'][0]
        row['references'][0]['id'] = 'src_unknown'
        with self.assertRaisesRegex(ValueError, '不属于本采购原件'):
            export_review(self.source, {'reviewed_requirements': [row]})

    def test_feedback_table_continuation_retains_one_complete_requirement(self):
        doc = Document()
        doc.add_paragraph('第三部分 技术要求')
        table = doc.add_table(rows=1, cols=4)
        for c, value in zip(table.rows[0].cells, ['服务分类', '评价指标', '说明', '供应商反馈']):
            c.text = value
        for c, value in zip(table.add_row().cells, ['活动策划', '过往案例', '须提供线下活动案例，包括现场', '']):
            c.text = value
        doc.add_paragraph('')
        continued = doc.add_table(rows=0, cols=4)
        for values in [['', '', '人数和行业类型，最多3个案例。', ''],
                       ['', '响应时效', '以小时为单位说明响应时间。', '']]:
            for c, value in zip(continued.add_row().cells, values):
                c.text = value
        rows = [r for r in scoring(self.save(doc)) if r['category'] == 'spec']
        self.assertEqual(2, len(rows))
        self.assertIn('最多3个案例', rows[0]['text'])
        self.assertEqual(1, len(rows[0]['related']))
        self.assertIn('响应时效', rows[1]['label'])
        self.assertFalse(any(r['category'] == 'score' for r in scoring(read_units(self.source)[0])))

    def test_spaced_rejection_bullets_inherit_consequence_and_ignore_bare_title(self):
        doc = Document()
        for line in ['6 、取消投标供应商竞标资格', '有下列任一情况的，将取消该投标供应商的竞标资格：',
                     '1 ) 投标书未按规定密封', '2 ) 投标书未盖单位公章',
                     '3 ) 技术标中包含本项目商务价格信息', '7 、开标与评标']:
            doc.add_paragraph(line)
        rows = [r for r in eligibility(self.save(doc)) if r['category'] == 'redline']
        self.assertEqual(3, len(rows))
        self.assertTrue(all(r.get('related') and '将取消' in r['related'][0].text for r in rows))
        self.assertFalse(any(r['text'] == '6 、取消投标供应商竞标资格' for r in rows))

    def test_submission_quantities_and_signature_conditions_are_kept_together(self):
        doc = Document()
        doc.add_paragraph('第二部分 投标须知')
        clause = '2）投标单位应提供1份正本，正本每一页加盖印章；提供14份副本，副本每份加盖骑缝章。'
        doc.add_paragraph(clause)
        rows = [r for r in scoring(self.save(doc)) if r['category'] == 'spec']
        self.assertTrue(any(r['text'] == clause for r in rows))

    def test_locked_response_matrix_is_a_format_without_attachment_number(self):
        doc = Document()
        doc.add_paragraph('技术服务反馈表')
        table = doc.add_table(rows=1, cols=3)
        for c, value in zip(table.rows[0].cells, ['服务内容', '说明', '供应商反馈']):
            c.text = value
        for c, value in zip(table.add_row().cells, ['客服响应', '须在2小时内响应。', '']):
            c.text = value
        self.save(doc)
        plan = {'blocks': [{'kind': 'heading', 'text': '技术服务反馈表',
                           'source_index': 0, 'source_start': 0, 'source_end': 2, 'locked_format': True}]}
        rows = [r for r in export_review(self.source, plan)['requirements'] if r['category'] == 'format']
        self.assertEqual(1, len(rows))
        self.assertEqual('技术服务反馈表', rows[0]['label'])
        self.assertEqual(0, rows[0]['references'][0]['index'])

    def test_declared_lot_names_bind_numbered_sections_and_reset_at_shared_rules(self):
        doc = Document()
        for line in ['第二部分 项目需求', '标段一：内容策划', '标段二：媒体代理',
                     '各标段要求：', '1、内容策划', '专业知识：了解本行业的产品特点。',
                     '2、媒体代理', '资源要求：应提供媒体资源名单。',
                     '中选原则：', '所有标段分别评审，统一采用综合评分法。']:
            doc.add_paragraph(line)
        units = self.save(doc)
        self.assertEqual('标段一 · 内容策划', next(u.scope for u in units if u.text.startswith('专业知识')))
        self.assertEqual('标段二 · 媒体代理', next(u.scope for u in units if u.text.startswith('资源要求')))
        common = next(u for u in units if u.text.startswith('所有标段'))
        self.assertEqual('', common.scope)
        self.assertNotIn('媒体代理', common.context)
        rows = [r for r in scoring(units) if r['category'] == 'spec']
        self.assertTrue(any(r['text'].startswith('专业知识') for r in rows))
        self.assertFalse(any(r['text'].startswith('标段一：') for r in rows))

    def test_yes_no_supplier_survey_is_not_an_eligibility_gate(self):
        doc = Document()
        doc.add_paragraph('第三部分 响应文件格式')
        doc.add_paragraph('供应商情况调查表')
        table = doc.add_table(rows=1, cols=4)
        for cell, value in zip(table.rows[0].cells, ['序号', '项目', '是', '否']):
            cell.text = value
        for cell, value in zip(table.add_row().cells, ['1', '截至申请日成立时间是否超过3年？', '', '']):
            cell.text = value
        self.assertFalse([r for r in eligibility(self.save(doc)) if r['category'] == 'qualification'])

    def test_short_paragraph_tail_completes_rejection_and_keeps_both_sources(self):
        doc = Document()
        doc.add_paragraph('第三部分 商务要求')
        doc.add_paragraph('报价明显低于市场价格，认定为恶意竞标的，将作为废标处')
        doc.add_paragraph('')
        doc.add_paragraph('理 。')
        rows = [r for r in eligibility(self.save(doc)) if r['category'] == 'redline']
        self.assertEqual(1, len(rows))
        self.assertIn('废标处理', ''.join(rows[0]['text'].split()))
        self.assertEqual(1, len(rows[0]['related']))
        self.assertNotEqual(rows[0]['unit'].locator, rows[0]['related'][0].locator)

    def test_evaluation_sequence_and_technical_gate_do_not_invent_numeric_scores(self):
        doc = Document()
        doc.add_paragraph('开标与评标')
        doc.add_paragraph('评标流程为先对技术标进行评分，后对商务标进行评分。')
        doc.add_paragraph('定标原则：技术合格的情况下，根据综合评分法评选出多家供应商中标。')
        rows = list(scoring(self.save(doc)))
        methods = [r for r in rows if r['category'] == 'spec' and r['label'] == '评审方法与顺序']
        self.assertEqual(2, len(methods))
        self.assertFalse(any(r['category'] == 'score' for r in rows))

    def test_qualification_material_context_preserves_exemptions_and_conditions(self):
        doc = Document()
        for text in [
            '2.1 资质证明材料（须盖章）',
            '（1）营业执照',
            '（2）制造商授权书（本项目无需提供）',
            '（3）如为经销商，须提交有效的经销授权文件。',
            '2.2 商务部分',
            '（1）报价明细表',
        ]:
            doc.add_paragraph(text)
        rows = [r for r in eligibility(self.save(doc)) if r['category'] == 'qualification']
        self.assertEqual(2, len(rows))
        conditional = next(r for r in rows if '经销商' in r['text'])
        self.assertEqual('条件适用时提交', conditional['label'])
        self.assertIn('如为经销商', conditional['text'])
        self.assertEqual(1, len(conditional['related']))
        self.assertFalse(any('制造商' in r['text'] or '报价明细表' in r['text'] for r in rows))


class RealProcurementRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = ROOT.parent / '包件2：直播视频类服务'
        cls.source = next(directory.glob('招案2026-3948*.docx'), None)
        if cls.source is None:
            raise unittest.SkipTest('本机未提供 3948 采购回归原件')
        cls.review = export_review(cls.source)

    def test_3948_five_critical_eligibility_requirements_are_present(self):
        text = '\n'.join(r['text'] for r in self.review['requirements'] if r['category'] == 'qualification')
        for phrase in ['至少一个合同复印件', '信用中国', '重大违法违规', '禁止准入处罚期', '制裁对象']:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_3948_uses_both_official_scoring_tables_not_quote_weights(self):
        rows = [r for r in self.review['requirements'] if r['category'] == 'score']
        self.assertEqual(22, len(rows))
        self.assertEqual(11, sum(r['scope'].startswith('包件1') for r in rows))
        self.assertEqual(11, sum(r['scope'].startswith('包件2') for r in rows))
        self.assertTrue(all(r['references'][0]['locator'].startswith(('表2第', '表3第')) for r in rows))
        self.assertFalse(any('icon' in r['text'] or 'banner' in r['label'] for r in rows))

    def test_3948_format_items_use_actual_attachments_not_catalog(self):
        rows = [r for r in self.review['requirements'] if r['category'] == 'format']
        self.assertEqual(21, len(rows))
        self.assertTrue(all(r['references'][0]['index'] >= 465 for r in rows))
        for phrase in ['磋商报价一览表', '项目人员一览表', '人员简历表', '法定代表人']:
            self.assertTrue(any(phrase in r['label'] for r in rows), phrase)


class ResponseBookRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = ROOT.parent / '标书下载/25年标书/25年复星联合招标项目'
        source = next(directory.glob('【招标文件】*.docx'), None)
        if source is None:
            raise unittest.SkipTest('本机未提供分册与反馈表回归原件')
        cls.review = export_review(source)

    def test_feedback_rows_are_all_read_and_continuation_fragments_share_evidence(self):
        rows = [r for r in self.review['requirements'] if r['category'] == 'spec'
                and any(s['kind'] == 'row' for s in r['references'])]
        self.assertEqual(33, len(rows))
        self.assertEqual(3, sum(len(r['references']) > 1 for r in rows))
        for phrase in ['最多3个案例', 'SCRM', '200人', '30秒', '自然日']:
            self.assertTrue(any(phrase in ''.join(r['text'].split()) for r in rows), phrase)
        self.assertFalse(any(r['category'] == 'score' for r in self.review['requirements']))

    def test_actual_forms_and_feedback_groups_have_format_requirements(self):
        rows = [r for r in self.review['requirements'] if r['category'] == 'format']
        self.assertEqual(13, len(rows))
        for title in ['投标承诺书', '反商业贿赂反舞弊承诺函', '投标人法定代表授权书']:
            self.assertTrue(any(r['label'] == title for r in rows))
        self.assertFalse(any(r['references'][0]['index'] in [228, 229, 230, 231] for r in rows))

    def test_six_explicit_rejection_conditions_and_all_six_entry_gates(self):
        rows = self.review['requirements']
        redlines = [r for r in rows if r['category'] == 'redline'
                    and r['references'][0]['index'] in [105, 107, 109, 111, 113, 115]]
        self.assertEqual(6, len(redlines))
        self.assertTrue(all(len(r['references']) == 2 for r in redlines))
        gates = [r for r in rows if r['category'] == 'qualification'
                 and 56 <= r['references'][0]['index'] <= 61]
        self.assertEqual(6, len(gates))
        self.assertTrue(any('14份副本' in ''.join(r['text'].split()) and '骑缝章' in r['text']
                            for r in rows if r['category'] == 'spec'))

    def test_split_rejection_tail_and_both_evaluation_methods_survive(self):
        rows = self.review['requirements']
        rejection = next(r for r in rows if r['category'] == 'redline'
                         and r['references'][0]['index'] == 222)
        self.assertIn('废标处理', ''.join(rejection['text'].split()))
        self.assertEqual({222, 224}, {s['index'] for s in rejection['references']})
        methods = [r for r in rows if r['category'] == 'spec' and r['label'] == '评审方法与顺序']
        self.assertEqual({125, 129}, {r['references'][0]['index'] for r in methods})


class NamedLotRegressionTests(unittest.TestCase):
    def test_five_named_lots_and_common_rules_keep_their_actual_scope(self):
        source = ROOT.parent / '标书下载/25年标书/25年招商基金公关项目/招商基金管理有限公司公关代理类服务采购文件.docx'
        if not source.is_file():
            self.skipTest('本机未提供五标段公关采购回归原件')
        data = export_review(source)
        specific = [r for r in data['requirements'] if r['category'] == 'spec'
                    and 88 <= r['references'][0]['index'] <= 104]
        self.assertEqual(13, len(specific))
        self.assertEqual({'标段一 · 稿件撰写': 3, '标段二 · 媒体代理': 2,
                          '标段三 · 自媒体投放': 3, '标段四 · 公关活动': 3,
                          '标段五 · 舆情管理': 2}, Counter(r['scope'] for r in specific))
        self.assertTrue(all(s['scope'] == '' for s in data['sources'] if s['index'] in [106, 107, 108]))
        self.assertFalse(any(r['category'] == 'qualification' for r in data['requirements']))
        self.assertTrue(any(r['category'] == 'spec' and '腾讯会议' in r['text']
                            and r['references'][0]['index'] == 20 for r in data['requirements']))


class CompositionChecklistRegressionTests(unittest.TestCase):
    def test_qualification_checklist_schedule_and_submission_rules_keep_source_conditions(self):
        source = ROOT.parent / '标书下载/25年标书/26年 嘉实基金投标/腾宝科技/招标文件/嘉实基金互联网-社区推广项目询价书（2026）.docx'
        if not source.is_file():
            self.skipTest('本机未提供资质组成清单采购回归原件')
        rows = export_review(source)['requirements']
        qualifications = [r for r in rows if r['category'] == 'qualification']
        materials = [r for r in qualifications if 47 <= r['references'][0]['index'] <= 55]
        self.assertEqual({47, 48, 50, 51, 52, 53, 54, 55},
                         {r['references'][0]['index'] for r in materials})
        self.assertTrue(all(any(ref['index'] == 46 for ref in r['references']) for r in materials))
        self.assertFalse(any('无需提供' in r['text'] for r in qualifications))
        schedule = [r for r in qualifications if r['references'][0]['locator'] == '表1第7行']
        self.assertEqual(4, len(schedule))
        for phrase in ['经营范围须包含', '商业信誉良好', '不得参加同一合同', '同类项目经验']:
            self.assertTrue(any(phrase in r['text'] for r in schedule), phrase)
        self.assertTrue(any('联合体报价' in r['text'] and '不接受' in r['text'] for r in qualifications))
        self.assertTrue(any(r['category'] == 'redline' and '作废件处理' in r['text']
                            and r['references'][0]['locator'] == '表1第11行' for r in rows))
        specs = [r for r in rows if r['category'] == 'spec']
        for locator in ['表1第4行', '表1第5行', '表1第12行']:
            self.assertTrue(any(any(ref['locator'] == locator for ref in r['references']) for r in specs), locator)
        self.assertTrue(any('中文译文' in r['text'] and r['references'][0]['index'] == 42 for r in specs))
        self.assertTrue(any('必须通过邮件' in r['text'] and '1月23日' in r['text'] for r in specs))
        envelope = next(r for r in specs if r['label'] == '封套标记与签章')
        self.assertEqual({70, 71, 72}, {ref['index'] for ref in envelope['references']})
        self.assertIn('询价项目名称', envelope['text'])
        self.assertIn('报价人名称', envelope['text'])
        self.assertTrue(any('由法定代表人授权的代理人签字或盖章' in r['text']
                            and '须同时提交法定代表人授权委托书' in r['text'] for r in specs))


if __name__ == '__main__':
    unittest.main()
