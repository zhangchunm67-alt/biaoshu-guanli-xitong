"""The framework pipeline keeps content canonical without redundant rendering or parsing."""
import base64
import copy
from email.message import Message
import io
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from docx import Document

import framework_service as service
from server import Handler, Store


class FrameworkPipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bid-pipeline-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / 'data')
        self.addCleanup(self.store.close)
        document = Document()
        for text in ['内容服务项目采购邀请函', '特邀请贵公司参加采购。',
                     '报价人应准备投标文件，包括公司介绍、历史项目、方案、报价。',
                     '供应商须提供有效营业执照复印件。']:
            document.add_paragraph(text)
        stream = io.BytesIO()
        document.save(stream)
        self.raw = stream.getvalue()

    def create(self, filename='采购.docx', raw=None):
        return service.create(self.store, {'name': '流水线验证', 'filename': filename,
            'data': base64.b64encode(self.raw if raw is None else raw).decode()})

    def confirm_requirements(self, record, **extra):
        body = {'revision': record['revision'], 'items': copy.deepcopy(record['requirements']),
                'confirmed': True, 'notes_acknowledged': True, 'acknowledged_removed_ids': []}
        body.update(extra)
        return service.revise(self.store, record['id'], body, 'requirements')

    def rename(self, record, name):
        rows = copy.deepcopy(record['outline'])
        rows[-1]['text'] = name
        return service.revise(self.store, record['id'], {'revision': record['revision'], 'items': rows}, 'outline')

    def test_configured_key_does_not_send_procurement_to_model_and_manual_flow_works(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-no-network', 'BID_MODEL_REVIEW': 'on'}), \
             patch('urllib.request.urlopen', side_effect=AssertionError('procurement must stay local')) as network, \
             patch.object(service, 'build_edition', side_effect=service.build_edition_in_process):
            record = self.create()
            self.assertNotIn('model_review', record)
            self.assertNotIn('model_review', self.store.require('framework', record['id']))
            with self.assertRaisesRegex(ValueError, '先保存并确认'):
                service.confirm(self.store, record['id'], {'revision': record['revision']})
            rows = copy.deepcopy(record['requirements'])
            original = rows[0]['references'][0]['text']
            rows[0]['label'] = '人工核对后的要求'
            rows[0]['references'][0]['text'] = '不能覆盖采购原文'
            confirmed = service.revise(self.store, record['id'], {'revision': record['revision'],
                'items': rows, 'confirmed': True, 'notes_acknowledged': True,
                'acknowledged_removed_ids': []}, 'requirements')
            self.assertEqual(original, confirmed['requirements'][0]['references'][0]['text'])
            edited = self.rename(confirmed, '已核对的实施计划')
            project = service.confirm(self.store, record['id'], {'revision': edited['revision']})
            self.assertEqual(record['id'], project['framework_id'])
            self.assertIn('已核对的实施计划', [row['text'] for row in edited['outline']])
            self.assertNotIn('model_review', edited)
            network.assert_not_called()

    def test_framework_cache_does_not_depend_on_ai_settings_or_key(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'first-test-key', 'BID_MODEL_REVIEW': 'on',
                                      'BID_OPENAI_MODEL': 'first-model', 'BID_REASONING_EFFORT': 'low'}):
            first = self.create()
        with patch.dict('os.environ', {'OPENAI_API_KEY': '', 'BID_MODEL_REVIEW': 'off',
                                      'BID_OPENAI_MODEL': 'another-model', 'BID_REASONING_EFFORT': 'high'}), \
             patch.object(service, 'build_edition', side_effect=AssertionError('same source must reuse cache')):
            repeated = self.create()
        self.assertEqual(first['id'], repeated['id'])
        self.assertEqual(1, len(self.store.all('framework')))

    def test_old_model_metadata_is_private_without_rewriting_history_or_notes(self):
        record = self.create()
        stored = self.store.require('framework', record['id'])
        historical = {'status': 'failed', 'message': '历史复核状态', 'notes': ['历史实质提示']}
        stored['model_review'] = historical
        stored['notes'].append('采购附件中的图片仍需人工核对。')
        self.store.put('framework', stored)
        self.store.db.commit()
        public = service.describe(self.store, record['id'])
        self.assertNotIn('model_review', public)
        self.assertEqual(stored['notes'], public['notes'])
        self.assertEqual(stored, self.store.require('framework', record['id']))
        confirmed = self.confirm_requirements(public)
        edited = self.rename(confirmed, '保留历史记录的人工修订')
        self.assertNotIn('model_review', edited)
        self.assertEqual(historical, self.store.require('framework', record['id'])['model_review'])
        legacy = dict(stored, review_version=None)
        self.assertNotIn('model_review', service.public(legacy))

    def test_removed_model_review_route_returns_404_without_revision(self):
        record = self.create()
        handler = Handler.__new__(Handler)
        handler.server = SimpleNamespace(store=self.store, server_port=8766)
        handler.command = 'POST'
        handler.path = '/api/frameworks/' + record['id'] + '/model-review'
        body = json.dumps({'revision': record['revision']}).encode()
        handler.headers = Message()
        for key, value in [('Host', '127.0.0.1:8766'), ('Origin', 'http://127.0.0.1:8766'),
                           ('Content-Type', 'application/json'), ('Content-Length', str(len(body)))]:
            handler.headers[key] = value
        handler.rfile = io.BytesIO(body)
        handler.json_response = Mock()
        with patch.object(service, 'revise', side_effect=AssertionError('retired route must not revise')):
            handler.run_request()
        handler.json_response.assert_called_once_with({'error': '页面或接口不存在'}, 404)
        self.assertEqual(record, service.describe(self.store, record['id']))
        with self.assertRaisesRegex(ValueError, '未知核对操作'):
            service.revise(self.store, record['id'], {'revision': record['revision']}, 'model-review')

    def test_unchanged_confirmation_only_changes_state_and_rejects_stale_revision(self):
        before = self.create()
        stored = self.store.require('framework', before['id'])
        folder = self.store.path(Path('frameworks') / before['id'])
        files = {str(p.relative_to(folder)): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in folder.rglob('*') if p.is_file()}
        with patch.object(service, 'build_edition', side_effect=AssertionError('must not render')), \
             patch.object(service, 'normalize_word', side_effect=AssertionError('must not convert')):
            after = self.confirm_requirements(before)
        self.assertTrue(after['requirements_confirmed'])
        self.assertEqual(before['revision'] + 1, after['revision'])
        self.assertEqual(stored['storage'], self.store.require('framework', before['id'])['storage'])
        self.assertEqual(files, {str(p.relative_to(folder)): (p.read_bytes(), p.stat().st_mtime_ns)
                                for p in folder.rglob('*') if p.is_file()})
        with self.assertRaisesRegex(ValueError, '其他窗口'):
            self.confirm_requirements(before)

    def test_canonical_storage_expands_old_embedded_records_without_losing_evidence(self):
        before = self.create()
        stored = self.store.require('framework', before['id'])
        self.assertNotIn('reviewed_requirements', stored['_plan'])
        self.assertTrue(all(set(ref) == {'id'} for row in stored['requirements'] for ref in row['references']))
        self.assertTrue(all('source_id' not in source for source in stored['sources']))
        # An existing expanded record is accepted without migrating unrelated snapshots.
        legacy = copy.deepcopy(stored)
        legacy['requirements'] = copy.deepcopy(before['requirements'])
        legacy['sources'] = copy.deepcopy(before['sources'])
        legacy['_plan']['reviewed_requirements'] = copy.deepcopy(before['requirements'])
        self.store.put('framework', legacy)
        self.store.db.commit()
        self.assertEqual(before, service.describe(self.store, before['id']))
        with patch.object(service, 'build_edition', side_effect=AssertionError('must not render')):
            after = self.confirm_requirements(before)
        self.assertEqual(before['requirements'], after['requirements'])
        self.assertEqual(before['sources'], after['sources'])
        self.assertNotIn('reviewed_requirements', self.store.require('framework', before['id'])['_plan'])

    def test_unchanged_confirmation_still_checks_original_and_cached_source_fingerprints(self):
        before = self.create(filename='采购.doc')
        stored = self.store.require('framework', before['id'])
        folder = self.store.path(Path('frameworks') / before['id'])
        normalized = folder / stored['normalized_storage']
        normalized.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '转换结果已变化'):
            self.confirm_requirements(before)
        self.assertEqual(stored, self.store.require('framework', before['id']))
        normalized.write_bytes(self.raw)
        (folder / stored['source_storage']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '原件指纹'):
            self.confirm_requirements(before)
        self.assertEqual(stored, self.store.require('framework', before['id']))

    def test_converted_doc_is_materialized_once_and_reused_on_content_revisions(self):
        with patch.object(service, 'normalize_word', return_value=(self.raw, 'converted_doc')) as convert:
            before = self.create(filename='采购.doc', raw=b'legacy DOC original')
        self.assertEqual(1, convert.call_count)
        stored = self.store.require('framework', before['id'])
        self.assertNotEqual(stored['source_storage'], stored['normalized_storage'])
        with patch.object(service, 'normalize_word', side_effect=AssertionError('must reuse normalized source')):
            confirmed = self.confirm_requirements(before)
            revised = self.rename(confirmed, '已核对的响应方案')
            self.rename(revised, '响应方案与交付安排')

    def test_old_doc_without_cached_normalization_converts_only_on_first_real_revision(self):
        before = self.confirm_requirements(self.create(filename='采购.doc'))
        stored = self.store.require('framework', before['id'])
        folder = self.store.path(Path('frameworks') / before['id'])
        (folder / stored.pop('normalized_storage')).unlink()
        self.store.put('framework', stored)
        self.store.db.commit()
        normalize = service.normalize_word
        with patch.object(service, 'normalize_word', wraps=normalize) as convert:
            revised = self.rename(before, '第一次修改方案')
            self.rename(revised, '第二次修改方案')
        self.assertEqual(1, convert.call_count)
        cached = self.store.require('framework', before['id'])['normalized_storage']
        self.assertEqual(self.raw, (folder / cached).read_bytes())

    def test_service_uses_one_bounded_worker(self):
        run = subprocess.run
        with patch.object(service.subprocess, 'run', wraps=run) as calls:
            self.create()
        self.assertEqual(1, calls.call_count)
        self.assertEqual('framework_worker.py', Path(calls.call_args.args[0][2]).name)
        self.assertEqual(360, calls.call_args.kwargs['timeout'])
        self.assertIn('input', calls.call_args.kwargs)

    def test_worker_timeout_leaves_no_partial_framework(self):
        with patch.object(service.subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker', 360)):
            with self.assertRaisesRegex(ValueError, '处理超时'):
                self.create()
        self.assertEqual([], self.store.all('framework'))
        self.assertEqual([], list(self.store.path('frameworks').iterdir()))

    def test_shared_pipeline_reads_units_once_and_does_not_parse_its_html(self):
        source = self.root / '采购.docx'
        source.write_bytes(self.raw)
        metrics = service.metric_module()
        save_report = metrics.save_report
        def differently_styled_report(source, output, *args, **kwargs):
            counts = save_report(source, output, *args, **kwargs)
            page = Path(output)
            page.write_text(page.read_text().replace('class="cards"', 'class="summary"')
                            .replace('data-count=', 'data-total='))
            return counts
        with patch.object(metrics, 'read_units', wraps=metrics.read_units) as reads, \
             patch.object(metrics, 'save_report', side_effect=differently_styled_report), \
             patch('subprocess.run', side_effect=AssertionError('no nested CLI')):
            metadata, _, _ = service.build_edition_in_process(
                self.root, self.root / 'deliverables', source, '共享构建', 'test')
        self.assertEqual(1, reads.call_count)
        self.assertEqual({row['key']: row['count'] for row in metadata['metrics']},
                         {group: sum(r['category'] == group for r in metadata['requirements'])
                          for group in service.GROUPS})

    def test_export_review_reads_units_once(self):
        source = self.root / '采购.docx'
        source.write_bytes(self.raw)
        metrics = service.metric_module()
        with patch.object(metrics, 'read_units', wraps=metrics.read_units) as reads:
            payload = metrics.export_review(source)
        self.assertEqual(1, reads.call_count)
        self.assertTrue(payload['requirements'])
        self.assertTrue(payload['sources'])

    def test_cli_scope_preserves_plan_and_refuses_to_overwrite_output(self):
        source = self.root / '分册采购.docx'
        document = Document()
        for text in ['采购文件', '投标函', '投标人签字：____', '营业执照', '附营业执照复印件',
                     '授权委托书', '授权人：____；被授权人：____', '技术方案', '技术实施内容：____']:
            document.add_paragraph(text)
        document.save(source)
        blocks = [{'kind': 'title', 'text': '响应文件'}, {'kind': 'toc'}, {'kind': 'break'}]
        for index, title in [(1, '投标函'), (3, '营业执照'), (5, '授权委托书'), (7, '技术方案')]:
            blocks.extend([{'kind': 'heading', 'text': title, 'level': 1, 'source_index': index},
                           {'kind': 'copy', 'start': index + 1, 'end': index + 1}])
        plan = self.root / 'manual.json'
        payload = json.dumps({'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                              'blocks': blocks, 'review': []}, ensure_ascii=False)
        plan.write_text(payload)
        output = self.root / '主体授权.docx'
        command = [sys.executable, '-B', str(service.SCRIPTS / 'generate.py'), str(source), str(output),
                   '--plan', str(plan), '--scope', 'identity-and-authorization']
        subprocess.run(command, check=True, capture_output=True)
        texts = '\n'.join(p.text for p in Document(output).paragraphs)
        self.assertIn('授权委托书', texts)
        self.assertNotIn('技术实施内容', texts)
        self.assertEqual(payload, plan.read_text())
        original = output.read_bytes()
        repeated = subprocess.run(command, capture_output=True)
        self.assertNotEqual(0, repeated.returncode)
        self.assertEqual(original, output.read_bytes())


if __name__ == '__main__':
    unittest.main()
