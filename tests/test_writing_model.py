"""Fourth-step model boundaries, with mocked HTTP only and synthetic materials."""
import copy
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import writing_model


class WritingModelTests(unittest.TestCase):
    def setUp(self):
        self.key = 'synthetic-writing-test-secret'
        env = patch.dict('os.environ', {'OPENAI_API_KEY': self.key}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.context = {
            'project': {'id': 'project_test', 'name': '合成案例项目'},
            'chapters': [
                {'id': 'BidSection1', 'title': '服务方案', 'path': '包一/服务方案',
                 'text': '现有人工正文。', 'requirement_ids': ['req1'],
                 'original_text': '原有固定表单', 'has_table': True, 'locked_format': True},
                {'id': 'BidSection2', 'title': '服务方案', 'path': '包二/服务方案',
                 'text': '', 'requirement_ids': ['req2']}],
            'requirements': [{'id': 'req1', 'text': '说明服务流程'},
                             {'id': 'req2', 'text': '说明实施方法'}],
            'procurement_sources': [{'id': 'src1', 'text': '说明服务流程和实施方法'}],
            'case_sources': [{'id': 'case1', 'file_name': '合成案例.txt', 'location': '第 1 段',
                              'text': '历史项目曾采用分阶段交付方法。'}],
            'materials': [{'id': 'mat1', 'name': '案例证明', 'text_available': False}],
            'business_notes': '沿用已确认内容，人员待确认。'}
        self.payload = {'chapters': [
            {'chapter_id': 'BidSection1', 'text': '按采购要求开展服务。人员投入【待确认：拟投入人员】。',
             'requirement_ids': ['req1'], 'source_refs': ['src1', 'case1'],
             'material_refs': ['mat1'], 'missing_items': ['拟投入人员待确认'], 'conflicts': []},
            {'chapter_id': 'BidSection2', 'text': '依照要求分阶段交付。',
             'requirement_ids': ['req2'], 'source_refs': ['src1'], 'material_refs': [],
             'missing_items': [], 'conflicts': []}], 'notes': ['候选正文需业务核对。']}

    def response(self, payload=None, **updates):
        value = {'id': 'resp_synthetic', 'status': 'completed', 'output': [
            {'type': 'reasoning', 'summary': []},
            {'type': 'message', 'content': [{'type': 'output_text',
                'text': json.dumps(self.payload if payload is None else payload, ensure_ascii=False)}]}]}
        value.update(updates)
        return io.BytesIO(json.dumps(value, ensure_ascii=False).encode('utf-8'))

    def assert_rejected(self, response, message=None):
        before = copy.deepcopy(self.context)
        with patch('writing_model.urllib.request.urlopen', side_effect=lambda *args, **kwargs: type(response)(response.getvalue())):
            with self.assertRaises(ValueError) as raised:
                writing_model.generate(self.context)
        self.assertNotIn(self.key, str(raised.exception))
        if message:
            self.assertIn(message, str(raised.exception))
        self.assertEqual(before, self.context)

    def test_real_skill_strict_schema_no_storage_and_safe_context(self):
        before = copy.deepcopy(self.context)
        self.context['unrelated_server_secret'] = self.key
        with patch('writing_model.urllib.request.urlopen', return_value=self.response()) as call:
            result = writing_model.generate(self.context)
        self.assertEqual(self.payload, result)
        self.context.pop('unrelated_server_secret')
        self.assertEqual(before, self.context)
        request = call.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual('https://api.openai.com/v1/responses', request.full_url)
        self.assertEqual(120, call.call_args.kwargs['timeout'])
        self.assertEqual('gpt-5.6-terra', body['model'])
        self.assertEqual({'effort': 'medium'}, body['reasoning'])
        self.assertEqual(16000, body['max_output_tokens'])
        self.assertIs(False, body['store'])
        self.assertIs(True, body['stream'])
        self.assertIs(True, body['text']['format']['strict'])
        self.assertIs(False, body['text']['format']['schema']['additionalProperties'])
        self.assertEqual(writing_model.CHAPTER_FIELDS,
                         set(body['text']['format']['schema']['properties']['chapters']['items']['required']))
        self.assertIn(writing_model.SKILL_PATH.read_text(encoding='utf-8'), body['instructions'])
        self.assertIn('历史客户、人员、日期、价格', body['instructions'])
        self.assertIn('不等于图片内容已核验', body['instructions'])
        self.assertIn('固定表', body['instructions'])
        self.assertNotIn(self.key, request.data.decode())
        self.assertEqual(self.context, json.loads(body['input']))

    def test_identical_titles_are_ordered_by_id_not_title(self):
        payload = copy.deepcopy(self.payload)
        payload['chapters'].reverse()
        with patch('writing_model.urllib.request.urlopen', return_value=self.response(payload)):
            result = writing_model.generate(self.context)
        self.assertEqual(self.payload, result)

    def test_writing_uses_configured_model_and_ignores_review_off(self):
        with patch.dict(os.environ, {'BID_MODEL_REVIEW': 'off', 'BID_OPENAI_MODEL': 'configured-model',
                                    'BID_REASONING_EFFORT': 'high'}):
            self.assertTrue(writing_model.configuration()['ready'])
            with patch('writing_model.urllib.request.urlopen', return_value=self.response()) as call:
                writing_model.generate(self.context)
        body = json.loads(call.call_args.args[0].data)
        self.assertEqual('configured-model', body['model'])
        self.assertEqual({'effort': 'high'}, body['reasoning'])
        with patch.dict(os.environ, {'BID_WRITING_MODEL': 'writing-override', 'BID_OPENAI_MODEL': 'general'}):
            self.assertEqual('writing-override', writing_model.configuration()['model'])

    def test_configuration_is_safe_and_key_disabled_states_do_not_call(self):
        self.assertEqual({'ready', 'model', 'reasoning', 'status', 'message'},
                         set(writing_model.configuration()))
        self.assertNotIn(self.key, json.dumps(writing_model.configuration()))
        for env, expected in [({}, 'unconfigured'),
                              ({'OPENAI_API_KEY': self.key, 'BID_WRITING_ENABLED': 'OFF'}, 'disabled')]:
            with self.subTest(expected=expected), patch.dict(os.environ, env, clear=True), \
                    patch('writing_model.urllib.request.urlopen') as call:
                self.assertEqual(expected, writing_model.configuration()['status'])
                with self.assertRaises(ValueError):
                    writing_model.generate(self.context)
                call.assert_not_called()

    def test_missing_or_empty_skill_prevents_call(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'SKILL.md'
            with patch('writing_model.SKILL_PATH', path), patch('writing_model.urllib.request.urlopen') as call:
                self.assertEqual('skill_missing', writing_model.configuration()['status'])
                with self.assertRaisesRegex(ValueError, '写作规则缺失'):
                    writing_model.generate(self.context)
                path.write_text('', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, '写作规则为空'):
                    writing_model.generate(self.context)
                call.assert_not_called()

    def test_private_env_loads_writing_flags_without_shell_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env.local'
            marker = Path(folder) / 'shell-command-must-not-run'
            literal = f'$(touch {marker})'
            path.write_text("OPENAI_API_KEY=ignored-existing-key\nBID_WRITING_MODEL='writing-model'\n"
                            f'BID_OPENAI_MODEL={literal}\nBID_REASONING_EFFORT=high\n'
                            'BID_WRITING_ENABLED=off\nBID_MODEL_REVIEW=off\nUNKNOWN_CONFIG=not-loaded\n',
                            encoding='utf-8')
            writing_model.load_local_env(folder)
            self.assertFalse(marker.exists())
        self.assertEqual(self.key, os.environ['OPENAI_API_KEY'])
        self.assertEqual('writing-model', os.environ['BID_WRITING_MODEL'])
        self.assertEqual(literal, os.environ['BID_OPENAI_MODEL'])
        self.assertEqual('high', writing_model.configuration()['reasoning'])
        self.assertEqual('disabled', writing_model.configuration()['status'])
        self.assertNotIn('BID_MODEL_REVIEW', os.environ)
        self.assertNotIn('UNKNOWN_CONFIG', os.environ)

    def test_legacy_review_flag_in_private_env_does_not_disable_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env.local'
            contents = f'OPENAI_API_KEY={self.key}\nBID_MODEL_REVIEW=off\n'
            path.write_text(contents, encoding='utf-8')
            with patch.dict(os.environ, {}, clear=True):
                writing_model.load_local_env(folder)
                self.assertTrue(writing_model.configuration()['ready'])
                self.assertNotIn('BID_MODEL_REVIEW', os.environ)
                with patch('writing_model.urllib.request.urlopen', return_value=self.response()) as call:
                    self.assertEqual(self.payload, writing_model.generate(self.context))
                self.assertEqual('gpt-5.6-terra', json.loads(call.call_args.args[0].data)['model'])
            self.assertEqual(contents, path.read_text(encoding='utf-8'))

    def test_existing_environment_settings_take_priority_over_private_file(self):
        values = {'OPENAI_API_KEY': self.key, 'BID_OPENAI_MODEL': 'environment-general',
                  'BID_REASONING_EFFORT': 'low', 'BID_WRITING_MODEL': '',
                  'BID_WRITING_ENABLED': 'on'}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, values, clear=True):
            (Path(folder) / '.env.local').write_text(
                'OPENAI_API_KEY=file-key\nBID_OPENAI_MODEL=file-general\n'
                'BID_REASONING_EFFORT=high\nBID_WRITING_MODEL=file-writing\n'
                'BID_WRITING_ENABLED=off\n', encoding='utf-8')
            writing_model.load_local_env(folder)
            self.assertEqual(values, dict(os.environ))
            self.assertTrue(writing_model.configuration()['ready'])
            self.assertEqual('environment-general', writing_model.configuration()['model'])
            self.assertEqual('low', writing_model.configuration()['reasoning'])

    def test_private_env_symlink_is_not_read(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'linked-settings'
            target.write_text('BID_WRITING_ENABLED=off\n', encoding='utf-8')
            (Path(folder) / '.env.local').symlink_to(target)
            writing_model.load_local_env(folder)
            self.assertNotIn('BID_WRITING_ENABLED', os.environ)
            self.assertTrue(writing_model.configuration()['ready'])

    def test_unknown_source_material_or_cross_chapter_requirement_rejects_whole_batch(self):
        for field, invalid in [('source_refs', ['made_up']), ('material_refs', ['made_up']),
                               ('requirement_ids', ['req2'])]:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.payload)
                payload['chapters'][0][field] = invalid
                self.assert_rejected(self.response(payload), '不存在或不属于本章')

    def test_duplicate_missing_extra_or_changed_chapters_rejected(self):
        for transform in [lambda rows: rows.pop(), lambda rows: rows.append(copy.deepcopy(rows[0])),
                          lambda rows: rows[1].update(chapter_id='BidSection1'),
                          lambda rows: rows[1].update(chapter_id='invented')]:
            payload = copy.deepcopy(self.payload)
            transform(payload['chapters'])
            self.assert_rejected(self.response(payload))

    def test_empty_irrelevant_or_refused_results_cannot_succeed(self):
        self.assert_rejected(self.response({'chapters': [], 'notes': ['全部案例无关。']}), '未完整生成')
        self.assert_rejected(self.response(status='incomplete'), '未完成')
        self.assert_rejected(self.response(output=[{'type': 'message', 'content': [
            {'type': 'refusal', 'refusal': self.key}]}]), '未能生成')
        self.assert_rejected(self.response(output=[]), '没有返回')

    def test_output_text_must_be_nonempty_chinese_plaintext(self):
        for text in ['', ' ', 'English only', '<p>中文文本</p>', '```中文文本```', '中' * 50001, None]:
            with self.subTest(text=repr(text)[:30]):
                payload = copy.deepcopy(self.payload)
                payload['chapters'][0]['text'] = text
                self.assert_rejected(self.response(payload))

    def test_malformed_field_shapes_and_duplicate_refs_rejected(self):
        for field, value in [('source_refs', 'src1'), ('material_refs', [{}]),
                             ('requirement_ids', None), ('missing_items', [1]),
                             ('conflicts', ['']), ('source_refs', ['src1', 'src1'])]:
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(self.payload)
                payload['chapters'][0][field] = value
                self.assert_rejected(self.response(payload))
        for payload in [[], {}, {'chapters': self.payload['chapters'], 'notes': 'bad'},
                        dict(self.payload, extra=True),
                        {'chapters': [None, self.payload['chapters'][1]], 'notes': []}]:
            self.assert_rejected(self.response(payload))
        for field in ['text', 'source_refs']:
            payload = copy.deepcopy(self.payload)
            payload['chapters'][0].pop(field)
            self.assert_rejected(self.response(payload))

    def test_malformed_envelopes_and_json_have_safe_errors(self):
        for raw in [b'null', b'[]', self.key.encode(), b'\xff',
                    json.dumps({'status': 'completed', 'output': [None]}).encode(),
                    json.dumps({'status': 'completed', 'output': [{'type': 'message', 'content': [None]}]}).encode()]:
            self.assert_rejected(io.BytesIO(raw))
        for part in [{'type': 'output_text', 'text': None}, {'type': 'output_text', 'text': self.key}]:
            self.assert_rejected(self.response(output=[{'type': 'message', 'content': [part]}]))

    def test_http_and_network_errors_never_expose_secret_or_remote_body(self):
        for error in [urllib.error.HTTPError('https://api.openai.com/v1/responses', 401,
                      'remote:' + self.key, {}, io.BytesIO(('body:' + self.key).encode())),
                      urllib.error.URLError(self.key), TimeoutError(self.key), OSError(self.key),
                      ValueError('Invalid header value: Bearer ' + self.key)]:
            with self.subTest(kind=type(error)), patch('writing_model.urllib.request.urlopen', side_effect=error):
                with self.assertRaises(ValueError) as raised:
                    writing_model.generate(self.context)
                self.assertNotIn(self.key, str(raised.exception))
                self.assertNotIn('remote:', str(raised.exception))

    def test_response_capacity_is_bounded(self):
        with patch('writing_model.MAX_RESPONSE_BYTES', 100):
            self.assert_rejected(io.BytesIO(b'x' * 101), '大小限制')

    def test_malformed_transport_response_is_retried_once(self):
        truncated = io.BytesIO(self.response().getvalue()[:-7])
        with patch('writing_model.urllib.request.urlopen', side_effect=[truncated, self.response()]) as call:
            self.assertEqual(self.payload, writing_model.generate(self.context))
        self.assertEqual(2, call.call_count)

        with patch('writing_model.urllib.request.urlopen', side_effect=lambda *a, **k: io.BytesIO(b'{')) as call:
            with self.assertRaisesRegex(ValueError, '自动重试后仍失败'):
                writing_model.generate(self.context)
        self.assertEqual(2, call.call_count)

    def test_stream_uses_completed_response_and_ignores_deltas(self):
        terminal = {'type': 'response.completed', 'response': json.loads(self.response().getvalue())}
        raw = (': keepalive\r\n\r\nevent: response.output_text.delta\r\ndata: '
               + json.dumps({'type': 'response.output_text.delta', 'delta': '不能单独采纳的片段'})
               + '\r\n\r\nevent: response.completed\r\ndata: '
               + json.dumps(terminal, ensure_ascii=False) + '\r\n\r\ndata: [DONE]\r\n\r\n').encode()
        class ChunkedResponse(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 31))
        with patch('writing_model.urllib.request.urlopen', return_value=ChunkedResponse(raw)):
            self.assertEqual(self.payload, writing_model.generate(self.context))

    def test_stream_without_completion_or_with_failed_response_cannot_succeed(self):
        self.assert_rejected(io.BytesIO(b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'), '无法读取')
        for status in ['incomplete', 'failed']:
            raw = ('data: ' + json.dumps({'type': 'response.' + status,
                'response': {'status': status, 'output': []}}) + '\n\n').encode()
            self.assert_rejected(io.BytesIO(raw), '未完成')

    def test_completed_json_with_invalid_candidate_is_not_retried(self):
        invalid = copy.deepcopy(self.payload)
        invalid['chapters'][0]['source_refs'] = ['unknown-source']
        with patch('writing_model.urllib.request.urlopen', return_value=self.response(invalid)) as call:
            with self.assertRaisesRegex(ValueError, '不存在'):
                writing_model.generate(self.context)
        self.assertEqual(1, call.call_count)

    def test_interrupted_http_body_retries_without_accepting_partial_text(self):
        class InterruptedResponse(io.BytesIO):
            def read(self, size=-1):
                raise http.client.IncompleteRead(super().read(size), 100)
        broken = InterruptedResponse(b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n')
        with patch('writing_model.urllib.request.urlopen', side_effect=[broken, self.response()]) as call:
            self.assertEqual(self.payload, writing_model.generate(self.context))
        self.assertEqual(2, call.call_count)

    def test_network_disconnect_retries_once_and_keeps_safe_error(self):
        with patch('writing_model.urllib.request.urlopen', side_effect=[http.client.RemoteDisconnected(self.key), self.response()]) as call:
            self.assertEqual(self.payload, writing_model.generate(self.context))
        self.assertEqual(2, call.call_count)

    def test_disconnect_after_terminal_event_keeps_complete_valid_result(self):
        terminal = {'type': 'response.completed', 'response': json.loads(self.response().getvalue())}
        raw = ('data: ' + json.dumps(terminal) + '\n\n').encode()
        class InterruptedResponse(io.BytesIO):
            def read(self, size=-1):
                raise http.client.IncompleteRead(super().read(size), 1)
        with patch('writing_model.urllib.request.urlopen', return_value=InterruptedResponse(raw)) as call:
            self.assertEqual(self.payload, writing_model.generate(self.context))
        self.assertEqual(1, call.call_count)

    def test_short_transport_reads_are_joined_before_json_decoding(self):
        class ChunkedResponse(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 17))
        raw = self.response().getvalue()
        with patch('writing_model.urllib.request.urlopen', return_value=ChunkedResponse(raw)):
            self.assertEqual(self.payload, writing_model.generate(self.context))

    def test_truncated_or_oversized_chunked_response_is_not_accepted(self):
        class ChunkedResponse(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 17))
        self.assert_rejected(ChunkedResponse(self.response().getvalue()[:-7]), '无法读取')
        with patch('writing_model.MAX_RESPONSE_BYTES', 100):
            self.assert_rejected(ChunkedResponse(b'x' * 101), '大小限制')

    def test_case_required_and_input_capacity_not_silently_truncated(self):
        for cases in [[], [{'id': 'case1', 'text': ''}], [{'id': 'case1', 'text': None}]]:
            self.context['case_sources'] = cases
            with patch('writing_model.urllib.request.urlopen') as call:
                with self.assertRaisesRegex(ValueError, '上传可读取'):
                    writing_model.generate(self.context)
                call.assert_not_called()
        self.context['case_sources'] = [{'id': 'case1', 'text': '可读取案例。'}]
        with patch('writing_model.MAX_CONTEXT_CHARS', 10), patch('writing_model.urllib.request.urlopen') as call:
            with self.assertRaisesRegex(ValueError, '未被截断'):
                writing_model.generate(self.context)
            call.assert_not_called()

    def test_input_ids_are_unambiguous_and_required_fields_are_valid(self):
        transforms = [lambda ctx: ctx['chapters'].append(copy.deepcopy(ctx['chapters'][0])),
                      lambda ctx: ctx['chapters'][0].update(requirement_ids=['unknown']),
                      lambda ctx: ctx['case_sources'][0].update(id='src1'),
                      lambda ctx: ctx.update(business_notes=None),
                      lambda ctx: ctx.update(materials=[None]),
                      lambda ctx: ctx.update(chapters=[])]
        for transform in transforms:
            context = copy.deepcopy(self.context)
            transform(context)
            with patch('writing_model.urllib.request.urlopen') as call:
                with self.assertRaises(ValueError):
                    writing_model.generate(context)
                call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
