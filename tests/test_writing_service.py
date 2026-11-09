"""Project-scoped case writing, review adoption and version conflict integration."""
import base64
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

import chapter_drafts
import engine
from server import Store


class WritingServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.addCleanup(self.store.close)
        config = patch('writing_model.configuration', return_value={
            'ready': True, 'status': 'ready', 'model': 'mock-model', 'message': '已配置'})
        config.start()
        self.addCleanup(config.stop)
        # Run the real job body synchronously so assertions never depend on timing.
        submit = patch.object(self.store.writing.pool, 'submit', side_effect=lambda fn, *a: fn(*a))
        submit.start()
        self.addCleanup(submit.stop)
        self.make_project('project-a')

    def make_project(self, key, only_originals=False):
        framework_id = 'framework-' + key
        template = self.store.path('templates/' + key + '.docx')
        document = Document()
        for style in ('BFHeading1', 'BFBody', 'BFSlot'):
            document.styles.add_style(style, 1)
        rows = [] if only_originals else [
            ('BidSection1', '专项服务方案', 'slot', False),
            ('BidSection2', '专项服务方案', 'slot', False),
        ]
        rows += [('BidSection3', '法定代表人授权委托书', 'slot', False),
                 ('BidSection4', '采购方自带登记表', 'copy', False),
                 ('BidSection5', '响应参数表', 'table', True)]
        blocks, outline = [], []
        for number, (ident, title, kind, locked) in enumerate(rows, 1):
            paragraph = document.add_paragraph(title, 'BFHeading1')
            bookmark = OxmlElement('w:bookmarkStart')
            bookmark.set(qn('w:name'), ident)
            bookmark.set(qn('w:id'), str(number))
            paragraph._p.append(bookmark)
            blocks.append(dict(kind='heading', bookmark=ident))
            blocks.append(dict(kind=kind, text='采购方保留内容'))
            document.add_paragraph('采购方保留内容', 'BFBody')
            if kind == 'slot':
                document.add_paragraph('【待编】编写本节回应。', 'BFSlot')
            else:
                document.add_table(rows=1, cols=1).cell(0, 0).text = '原表不得替换'
            outline.append(dict(id=ident, title=title, requirement_ids=['req-' + ident], locked_format=locked))
        document.save(template)
        material = dict(id='material-' + key, name='公司简介', category_id='company_profile',
                        mode='text', content='公司具备项目现场支持经验。', revision=1, archived=False)
        project = dict(id=key, name='案例写作测试-' + key, framework_id=framework_id,
                       template_storage=str(template.relative_to(self.store.root)),
                       template_hash=hashlib.sha256(template.read_bytes()).hexdigest(),
                       material_flow='text-first', special_selection_confirmed=True,
                       text_review_confirmed=True, special_category_ids=[], chapter_revision=1,
                       chapter_texts={} if only_originals else {'BidSection1': '业务人员已保存的原稿'},
                       mappings=[dict(id='mapping-' + key, category_id='company_profile',
                                      action='fill', status='ready', confirmed=True,
                                      material_ids=[material['id']])])
        source = dict(id='procurement-' + key, text='方案须说明实施安排和服务保障。', location='第 1 段')
        framework = dict(id=framework_id, revision=3, requirements_confirmed=True,
                         projects={'word': key}, outline=outline, _plan={'blocks': blocks}, sources=[source],
                         requirements=[dict(id='req-' + ident, text='说明服务保障。',
                                            references=[{'source_id': source['id']}]) for ident, *_ in rows])
        for kind, value in [('project', project), ('framework', framework), ('material', material)]:
            self.store.put(kind, value)
        self.store.db.commit()
        return project

    def request(self, key='project-a', **extra):
        project = self.store.require('project', key)
        pack = self.store.get('writing_cases', key) or {'revision': 0}
        return dict(mapping_revision=self.store.mapping_revision(project),
                    revision=project.get('chapter_revision', 0), case_revision=pack['revision'], **extra)

    def upload(self, key='project-a', text='已完成甲客户现场服务项目。'):
        raw = text.encode()
        return self.store.writing.upload(key, self.request(key, files=[{
            'name': '项目案例.txt', 'data': base64.b64encode(raw).decode()}]))

    def model_result(self, context):
        return dict(chapters=[dict(chapter_id=row['id'], text='候选正文：' + row['id'],
                                   requirement_ids=row['requirement_ids'],
                                   source_refs=[context['case_sources'][0]['id']],
                                   material_refs=[context['materials'][0]['id']],
                                   missing_items=[], conflicts=[]) for row in context['chapters']],
                    notes=['引用案例仅用于说明历史经验。'])

    def generate(self, key='project-a', side_effect=None, chapter_ids=None):
        with patch('writing_model.generate', side_effect=side_effect or self.model_result) as model:
            options = {'chapter_ids': chapter_ids} if chapter_ids is not None else {}
            self.store.writing.start(key, self.request(key, business_notes='交付日期待确认。', **options))
        job = self.store.writing.describe(key)['job']
        return job, model

    def accept(self, job, ids, key='project-a'):
        return self.store.writing.accept(key, self.request(key, job_id=job['id'], chapter_ids=ids))

    def test_upload_preserves_package_and_failure_keeps_last_valid_package(self):
        result = self.upload(text='案例原件，应可回查。')
        self.assertEqual(1, result['case_revision'])
        self.assertEqual('案例原件，应可回查。', result['case_sources'][0]['text'])
        pack = self.store.require('writing_cases', 'project-a')
        stored = self.store.path(pack['storage']) / '1.txt'
        self.assertEqual('案例原件，应可回查。'.encode(), stored.read_bytes())
        with self.assertRaisesRegex(ValueError, '未读取到可用文字'):
            self.upload(text=' \n')
        self.assertEqual(pack, self.store.require('writing_cases', 'project-a'))
        self.assertEqual([stored], list(self.store.path('writing-cases').glob('*/*/*')))
        stale_request = self.request(files=[{'name': '案例.txt', 'data': base64.b64encode(b'case').decode()}])
        stale_request['case_revision'] = 0
        with self.assertRaisesRegex(ValueError, '案例资料已更新'):
            self.store.writing.upload('project-a', stale_request)

    def test_generation_is_candidate_only_and_skips_original_forms(self):
        self.upload()
        before = copy.deepcopy(self.store.require('project', 'project-a'))
        path = self.store.path(before['template_storage'])
        original_bytes = path.read_bytes()
        job, model = self.generate()
        self.assertEqual('completed', job['status'], job)
        self.assertFalse(job['stale'])
        self.assertEqual(['BidSection1', 'BidSection2'], [r['chapter_id'] for r in job['chapters']])
        self.assertEqual([], job['accepted_chapter_ids'])
        after = self.store.require('project', 'project-a')
        self.assertEqual(before['chapter_texts'], after['chapter_texts'])
        self.assertEqual(before['chapter_revision'], after['chapter_revision'])
        self.assertEqual(original_bytes, path.read_bytes())
        context = model.call_args.args[0]
        self.assertEqual('业务人员已保存的原稿', context['chapters'][0]['text'])
        self.assertEqual('交付日期待确认。', context['business_notes'])
        self.assertEqual(['req-BidSection1', 'req-BidSection2'], [r['id'] for r in context['requirements']])
        self.assertEqual(['material-project-a'], [m['id'] for m in context['materials']])
        self.assertTrue(any('授权委托书' in note and '采购方自带登记表' in note and '响应参数表' in note for note in job['notes']))

    def test_partial_adoption_preserves_other_chapters_and_allows_second_adoption(self):
        uploaded = self.upload()
        job, _ = self.generate()
        first = self.accept(job, ['BidSection2'])
        project = self.store.require('project', 'project-a')
        self.assertEqual({'BidSection1': '业务人员已保存的原稿', 'BidSection2': '候选正文：BidSection2'}, project['chapter_texts'])
        self.assertEqual(2, first['editor']['revision'])
        self.assertFalse(first['writing']['job']['stale'])
        self.assertEqual(['BidSection2'], first['writing']['job']['accepted_chapter_ids'])
        evidence = project['writing_evidence']['BidSection2']
        self.assertEqual(job['id'], evidence['job_id'])
        self.assertEqual(uploaded['case_sources'], evidence['case_sources'])
        second = self.accept(job, ['BidSection1'])
        self.assertEqual(3, second['editor']['revision'])
        self.assertEqual(['BidSection2', 'BidSection1'], second['writing']['job']['accepted_chapter_ids'])
        self.assertFalse(second['writing']['job']['stale'])
        with self.assertRaisesRegex(ValueError, '已采纳'):
            self.accept(job, ['BidSection2'])

    def test_case_material_and_manual_body_changes_expire_candidates(self):
        for change in ['case', 'material', 'body', 'framework']:
            with self.subTest(change=change):
                key = 'project-' + change
                self.make_project(key)
                self.upload(key)
                job, _ = self.generate(key)
                if change == 'case':
                    self.upload(key, '新的项目案例材料。')
                elif change == 'material':
                    material = self.store.require('material', 'material-' + key)
                    material.update(revision=2, content='公司简介已更新。')
                    self.store.put('material', material)
                    self.store.db.commit()
                elif change == 'body':
                    chapter_drafts.save_chapters(self.store, key, dict(revision=1,
                        chapters=[dict(id='BidSection1', text='新保存的人工正文')]))
                else:
                    framework = self.store.require('framework', 'framework-' + key)
                    framework['revision'] += 1
                    framework['requirements'][0]['text'] = '更新后的采购服务保障要求。'
                    self.store.put('framework', framework)
                    self.store.db.commit()
                before = copy.deepcopy(self.store.require('project', key)['chapter_texts'])
                self.assertTrue(self.store.writing.describe(key)['job']['stale'])
                with self.assertRaisesRegex(ValueError, '输入已变化'):
                    self.accept(job, ['BidSection1'], key)
                self.assertEqual(before, self.store.require('project', key)['chapter_texts'])

    def test_input_change_during_generation_does_not_publish_partial_candidates(self):
        self.upload()
        before = self.store.require('project', 'project-a')['chapter_texts']

        def model_with_concurrent_edit(context):
            chapter_drafts.save_chapters(self.store, 'project-a', dict(revision=1,
                chapters=[dict(id='BidSection2', text='生成期间保存的人工正文')]))
            return self.model_result(context)

        job, _ = self.generate(side_effect=model_with_concurrent_edit)
        self.assertEqual('failed', job['status'])
        self.assertEqual([], job['chapters'])
        self.assertIn('输入已更新', job['error'])
        self.assertEqual(dict(before, BidSection2='生成期间保存的人工正文'),
                         self.store.require('project', 'project-a')['chapter_texts'])

    def test_cross_project_old_job_and_unknown_chapter_are_rejected(self):
        self.make_project('project-b')
        self.upload('project-b')
        self.upload()
        job, _ = self.generate()
        with self.assertRaisesRegex(ValueError, '本项目最新'):
            self.accept(job, ['BidSection1'], 'project-b')
        with self.assertRaisesRegex(ValueError, '有效且不重复'):
            self.accept(job, ['BidSection999'])
        with self.assertRaisesRegex(ValueError, '有效且不重复'):
            self.accept(job, ['BidSection1', 'BidSection1'])
        new_job, _ = self.generate()
        self.assertNotEqual(job['id'], new_job['id'])
        with self.assertRaisesRegex(ValueError, '本项目最新'):
            self.accept(job, ['BidSection1'])
        self.assertEqual({'BidSection1': '业务人员已保存的原稿'},
                         self.store.require('project', 'project-b')['chapter_texts'])

    def test_archived_project_and_unconfirmed_requirements_cannot_start(self):
        self.upload()
        framework = self.store.require('framework', 'framework-project-a')
        framework['projects']['word'] = 'another-project'
        self.store.put('framework', framework)
        with self.assertRaisesRegex(ValueError, '当前整本项目'):
            self.store.writing.start('project-a', self.request())
        framework['projects']['word'] = 'project-a'
        framework['requirements_confirmed'] = False
        self.store.put('framework', framework)
        with self.assertRaisesRegex(ValueError, '确认采购要求'):
            self.store.writing.start('project-a', self.request())

    def test_only_original_forms_do_not_call_model(self):
        self.make_project('originals', only_originals=True)
        self.upload('originals')
        with patch('writing_model.generate') as model:
            with self.assertRaisesRegex(ValueError, '仅含采购原表'):
                self.store.writing.start('originals', self.request('originals'))
            model.assert_not_called()
        self.assertIsNone(self.store.writing.describe('originals')['job'])

    def test_changed_template_bytes_expire_candidate_and_block_adoption(self):
        self.upload()
        job, _ = self.generate()
        project = self.store.require('project', 'project-a')
        before = copy.deepcopy(project['chapter_texts'])
        template = self.store.path(project['template_storage'])
        document = Document(template)
        document.add_paragraph('候选完成后，采购框架已被外部修改。')
        document.save(template)
        self.assertNotEqual(project['template_hash'], hashlib.sha256(template.read_bytes()).hexdigest())
        self.assertTrue(self.store.writing.describe('project-a')['job']['stale'])
        with self.assertRaises(ValueError):
            self.accept(job, ['BidSection1'])
        self.assertEqual(before, self.store.require('project', 'project-a')['chapter_texts'])

    def test_editing_one_chapter_keeps_other_candidate_adoptable(self):
        self.upload()
        job, _ = self.generate()
        chapter_drafts.save_chapters(self.store, 'project-a', dict(revision=1,
            chapters=[dict(id='BidSection1', text='人工修改只属于第一章。')]))
        current = self.store.writing.describe('project-a')['job']
        self.assertEqual(['BidSection1'], current['stale_chapter_ids'])
        self.accept(current, ['BidSection2'])
        with self.assertRaisesRegex(ValueError, '输入已变化'):
            self.accept(current, ['BidSection1'])
        self.assertEqual('人工修改只属于第一章。',
                         self.store.require('project', 'project-a')['chapter_texts']['BidSection1'])

    def test_single_chapter_regeneration_preserves_other_candidate_and_manual_text(self):
        self.upload()
        previous, _ = self.generate()
        chapter_drafts.save_chapters(self.store, 'project-a', dict(revision=1,
            chapters=[dict(id='BidSection1', text='新人工正文，在采纳前必须保留。')]))
        job, model = self.generate(chapter_ids=['BidSection1'])
        self.assertEqual(['BidSection1'], [row['id'] for row in model.call_args.args[0]['chapters']])
        self.assertEqual(previous['chapters'][1], job['chapters'][1])
        self.assertEqual([], job['stale_chapter_ids'])
        self.accept(job, ['BidSection2'])
        self.assertEqual('新人工正文，在采纳前必须保留。',
                         self.store.require('project', 'project-a')['chapter_texts']['BidSection1'])
        self.accept(job, ['BidSection1'])

    def test_other_chapter_can_be_edited_during_single_chapter_generation(self):
        self.upload()
        self.generate()

        def generate_while_editing(context):
            # A different chapter's saved prose must neither abort this task nor
            # be overwritten when this chapter's result is published.
            chapter_drafts.save_chapters(self.store, 'project-a', dict(revision=1,
                chapters=[dict(id='BidSection2', text='第二章独立人工修改。')]))
            return self.model_result(context)

        job, _ = self.generate(chapter_ids=['BidSection1'], side_effect=generate_while_editing)
        self.assertEqual('completed', job['status'])
        self.assertEqual(['BidSection2'], job['stale_chapter_ids'])
        self.accept(job, ['BidSection1'])
        self.assertEqual('第二章独立人工修改。',
                         self.store.require('project', 'project-a')['chapter_texts']['BidSection2'])

    def test_failed_single_chapter_generation_keeps_previous_candidates(self):
        self.upload()
        previous, _ = self.generate()
        job, _ = self.generate(chapter_ids=['BidSection1'], side_effect=ValueError('模拟失败'))
        self.assertEqual('failed', job['status'])
        self.assertEqual(previous['chapters'], job['chapters'])
        self.accept(job, ['BidSection2'])

    def test_confirmation_revision_without_content_change_keeps_candidates(self):
        self.upload()
        job, _ = self.generate()
        framework = self.store.require('framework', 'framework-project-a')
        framework['revision'] += 1
        self.store.put('framework', framework); self.store.db.commit()
        self.assertFalse(self.store.writing.describe('project-a')['job']['stale'])
        self.accept(job, ['BidSection2'])
        framework['requirements_confirmed'] = False
        self.store.put('framework', framework); self.store.db.commit()
        self.assertTrue(self.store.writing.describe('project-a')['job']['stale'])
        with self.assertRaisesRegex(ValueError, '确认采购要求'):
            self.accept(job, ['BidSection1'])

    def test_status_poll_does_not_open_template_scan_materials_or_return_sources(self):
        self.upload()
        self.generate()
        full = self.store.writing.describe('project-a')
        with (patch.object(self.store, 'mapping_revision', side_effect=AssertionError('full fingerprint')),
             patch.object(self.store, 'all', side_effect=AssertionError('material scan')),
             patch.object(self.store.writing, '_pack', side_effect=AssertionError('case source read')),
             patch.object(Path, 'read_bytes', side_effect=AssertionError('template read'))):
            status = self.store.writing.describe('project-a', view='status')
        self.assertEqual(full['job']['id'], status['job']['id'])
        self.assertNotIn('chapters', status['job'])
        self.assertNotIn('case_sources', status)
        self.assertNotIn('procurement_sources', status)
        self.assertLess(len(str(status)), 1200)

    def test_each_batch_only_receives_procurement_sources_referenced_by_its_requirements(self):
        project = self.store.require('project', 'project-a')
        template = self.store.path(project['template_storage'])
        document = Document(template)
        framework = self.store.require('framework', project['framework_id'])
        second_source = dict(id='source-second-batch', text='仅用于第二批次的采购条款。', location='第 2 段')
        framework['sources'].extend([second_source, dict(id='unreferenced-source', text='不得发给模型。')])
        for number in range(6, 11):
            ident = 'BidSection' + str(number)
            paragraph = document.add_paragraph('补充服务方案' + str(number), 'BFHeading1')
            bookmark = OxmlElement('w:bookmarkStart')
            bookmark.set(qn('w:name'), ident); bookmark.set(qn('w:id'), str(number))
            paragraph._p.append(bookmark)
            framework['outline'].append(dict(id=ident, title=paragraph.text,
                requirement_ids=['req-' + ident], locked_format=False))
            framework['_plan']['blocks'].extend([dict(kind='heading', bookmark=ident), dict(kind='slot', text='待编')])
            source_id = second_source['id'] if number == 10 else framework['sources'][0]['id']
            framework['requirements'].append(dict(id='req-' + ident, text='必须响应。', references=[{'id': source_id}]))
        document.save(template)
        project['template_hash'] = hashlib.sha256(template.read_bytes()).hexdigest()
        self.store.put('project', project); self.store.put('framework', framework); self.store.db.commit()
        self.upload()
        job, model = self.generate()
        self.assertEqual('completed', job['status'])
        self.assertEqual(2, model.call_count)
        for call in model.call_args_list:
            context = call.args[0]
            referenced = {ref.get('source_id', ref.get('id')) for row in context['requirements'] for ref in row['references']}
            self.assertEqual(referenced, {source['id'] for source in context['procurement_sources']})
            self.assertNotIn('unreferenced-source', referenced)
        self.assertEqual({second_source['id']}, {source['id'] for source in model.call_args_list[1].args[0]['procurement_sources']})

    def test_service_plan_with_original_form_can_be_written_without_replacing_form(self):
        project = self.store.require('project', 'project-a')
        template = self.store.path(project['template_storage'])
        document = Document(template)
        for ident, _, paragraph in list(chapter_drafts._sections(document))[:2]:
            paragraph.runs[0].text = '服务实施方案'
            for block in list(paragraph._p.itersiblings()):
                style = block.find(qn('w:pPr') + '/' + qn('w:pStyle'))
                style_id = style.get(qn('w:val'), '') if style is not None else ''
                if style_id.startswith('BFHeading'):
                    break
                if style_id == 'BFSlot':
                    block.getparent().remove(block)
            table = document.add_table(rows=1, cols=1)
            table.cell(0, 0).text = '必须保留的方案原表-' + ident
            paragraph._p.addnext(table._tbl)
        document.save(template)
        original_bytes = template.read_bytes()
        original_tables = [table.cell(0, 0).text for table in document.tables]
        project['template_hash'] = hashlib.sha256(original_bytes).hexdigest()
        self.store.put('project', project)
        framework = self.store.require('framework', 'framework-project-a')
        for row in framework['outline'][:2]:
            row.update(title='服务实施方案', locked_format=row['id'] == 'BidSection2')
        active = None
        for block in framework['_plan']['blocks']:
            if block['kind'] == 'heading':
                active = block['bookmark']
            elif active in {'BidSection1', 'BidSection2'}:
                block['kind'] = 'copy'
        self.store.put('framework', framework)
        self.store.db.commit()
        self.upload()
        job, model = self.generate()
        self.assertEqual('completed', job['status'], job)
        self.assertEqual(['BidSection1', 'BidSection2'], [c['chapter_id'] for c in job['chapters']])
        context = model.call_args.args[0]
        self.assertEqual(['服务实施方案', '服务实施方案'], [c['title'] for c in context['chapters']])
        for row in context['chapters']:
            self.assertIn('必须保留的方案原表-' + row['id'], row['original_text'])
        self.assertTrue(any('授权委托书' in note and '采购方自带登记表' in note and '响应参数表' in note for note in job['notes']))
        self.accept(job, ['BidSection1', 'BidSection2'])
        draft = self.store.path('exports/service-plan.docx')
        engine.export_document(template, draft, [], [],
            framework_drafts=self.store.require('project', 'project-a')['chapter_texts'])
        exported = Document(draft)
        self.assertEqual(original_tables, [table.cell(0, 0).text for table in exported.tables])
        self.assertEqual(original_bytes, template.read_bytes())
        self.assertEqual(1, [p.text for p in exported.paragraphs].count('候选正文：BidSection1'))
        self.assertEqual(1, [p.text for p in exported.paragraphs].count('候选正文：BidSection2'))


if __name__ == '__main__':
    unittest.main()
