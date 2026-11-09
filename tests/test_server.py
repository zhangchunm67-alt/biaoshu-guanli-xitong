"""持久化、文件边界及同源保护测试；全部使用临时数据目录。"""
import base64
import io
import json
import socket
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def image_upload(name='凭证.png', color='white'):
    raw = io.BytesIO()
    Image.new('RGB', (40, 20), color).save(raw, 'PNG')
    return {'name': name, 'data': base64.b64encode(raw.getvalue()).decode()}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bid-server-test-')
        self.httpd = server.create_server(port=0, data_dir=self.tmp.name)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.httpd.server_port}'

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.httpd.store.close()
        self.tmp.cleanup()

    def request(self, method, path, payload=None, headers=None, raw=False, with_revision=True):
        if payload is not None and with_revision and method in {'POST', 'PATCH'}:
            payload = dict(payload)
            bits = path.strip('/').split('/')
            if len(bits) == 4 and bits[:2] == ['api', 'projects'] and bits[3] in {
                    'mappings', 'special-categories', 'text-materials', 'scan', 'export-jobs', 'export', 'chapter-drafts'}:
                latest = self.request('GET', '/api/projects/' + bits[2])[0]
                payload.setdefault('mapping_revision', latest['mapping_revision'])
            elif bits[:2] == ['api', 'materials'] and ((method == 'PATCH' and len(bits) == 3)
                    or (method == 'POST' and len(bits) == 4 and bits[3] == 'files')):
                payload.setdefault('revision', self.request('GET', '/api/materials/' + bits[2])[0]['revision'])
        values = {'Content-Type': 'application/json', 'Origin': self.base}
        values.update(headers or {})
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode() if payload is not None else None, method=method, headers=values)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as response:
            body = response.read()
            return (body if raw else json.loads(body)), dict(response.headers)

    def profile_project(self):
        from docx import Document
        doc = Document()
        doc.add_heading('公司介绍', 1)
        doc.add_heading('营业执照', 1)
        raw = io.BytesIO()
        doc.save(raw)
        project, _ = self.request('POST', '/api/projects', {
            'name': '材料联动测试', 'filename': 'template.docx',
            'data': base64.b64encode(raw.getvalue()).decode()})
        return project

    def profile(self, **overrides):
        body = {'name': '公司介绍', 'subject': server.SUBJECT,
                'category_id': 'company_profile', 'mode': 'text', 'content': '公司业务介绍'}
        body.update(overrides)
        return self.request('POST', '/api/materials', body)[0]

    def test_bootstrap_lists_saved_summaries_without_project_writes_or_scans(self):
        import engine
        project = self.profile_project()
        store = self.httpd.store
        legacy = store.require('project', project['id'])
        legacy['common_locator_version'] = 0
        store.put('project', legacy)
        store.db.commit()
        before = store.db.total_changes
        with patch.object(store, 'public_project', side_effect=AssertionError('list loaded detail')), \
                patch.object(store, 'scan_project_template', side_effect=AssertionError('list scanned Word')), \
                patch.object(store, 'put', side_effect=AssertionError('list wrote data')), \
                patch.object(engine, 'validate_mappings', side_effect=AssertionError('list validated materials')):
            result, _ = self.request('GET', '/api/bootstrap')
        self.assertEqual(before, store.db.total_changes)
        self.assertEqual(legacy, store.require('project', project['id']))
        summary = next(p for p in result['projects'] if p['id'] == project['id'])
        self.assertEqual(project['name'], summary['name'])
        self.assertNotIn('mappings', summary)
        self.assertNotIn('headings', summary)
        self.assertNotIn('mapping_revision', summary)
        detail, _ = self.request('GET', '/api/projects/' + project['id'])
        self.assertEqual(engine.COMMON_LOCATOR_VERSION, store.require('project', project['id'])['common_locator_version'])
        self.assertIn('mappings', detail)
        self.assertIn('mapping_revision', detail)
        self.assertIn('exports', detail)
        self.assertEqual(detail['mappings'], store.require('project', project['id'])['mappings'])

    def test_upload_fills_confirmed_missing_and_preserves_other_confirmations(self):
        p = self.profile_project()
        rows = [dict(m, confirmed=True) for m in p['mappings']]
        self.request('PATCH', '/api/projects/' + p['id'] + '/mappings', {'mappings': rows})
        material = self.profile()
        p, _ = self.request('GET', '/api/projects/' + p['id'])
        row = next(m for m in p['mappings'] if m['category_id'] == 'company_profile')
        self.assertEqual(('fill', 'ready', False), (row['action'], row['status'], row['confirmed']))
        self.assertEqual([material['id']], row['material_ids'])
        self.assertTrue(all(m['confirmed'] for m in p['mappings'] if m['id'] != row['id']))
        self.assertEqual([], row['issues'])
        self.request('PATCH', '/api/projects/' + p['id'] + '/mappings', {'mappings': [dict(row, confirmed=True)]})
        self.request('PATCH', '/api/materials/' + material['id'], {'content': '修订介绍'})
        p, _ = self.request('GET', '/api/projects/' + p['id'])
        self.assertFalse(next(m for m in p['mappings'] if m['id'] == row['id'])['confirmed'])

    def test_manual_skip_missing_and_selected_material_are_preserved(self):
        for action in ('skip', 'missing'):
            p = self.profile_project()
            row = next(m for m in p['mappings'] if m['category_id'] == 'company_profile')
            # Explicitly change the action before returning to missing, to record manual intent.
            self.request('PATCH', '/api/projects/' + p['id'] + '/mappings', {'mappings': [dict(row, action='skip')]})
            self.request('PATCH', '/api/projects/' + p['id'] + '/mappings', {'mappings': [dict(row, action=action)]})
            self.profile(name='后补材料' + action)
            updated, _ = self.request('GET', '/api/projects/' + p['id'])
            result = next(m for m in updated['mappings'] if m['id'] == row['id'])
            self.assertEqual(action, result['action'])
        p = self.profile_project()
        row = next(m for m in p['mappings'] if m['category_id'] == 'company_profile')
        chosen = self.httpd.store.all('material')[0]['id']
        self.request('PATCH', '/api/projects/' + p['id'] + '/mappings', {'mappings': [dict(row, action='fill', material_ids=[chosen], confirmed=True)]})
        self.profile(name='新版本')
        updated, _ = self.request('GET', '/api/projects/' + p['id'])
        result = next(m for m in updated['mappings'] if m['id'] == row['id'])
        self.assertEqual([chosen], result['material_ids'])
        self.assertTrue(result['confirmed'])

    def test_legacy_missing_recovers_and_ambiguous_versions_require_selection(self):
        p = self.profile_project()
        material = self.profile()
        stored = self.httpd.store.require('project', p['id'])
        row = next(m for m in stored['mappings'] if m['category_id'] == 'company_profile')
        row.update(action='missing', material_ids=[], confirmed=True)
        self.httpd.store.put('project', stored)
        self.httpd.store.db.commit()
        updated, _ = self.request('GET', '/api/projects/' + p['id'])
        result = next(m for m in updated['mappings'] if m['id'] == row['id'])
        self.assertEqual([material['id']], result['material_ids'])
        self.assertFalse(result['confirmed'])
        self.profile(name='另一版本')
        row.update(action='missing', material_ids=[], confirmed=True)
        self.httpd.store.put('project', stored)
        self.httpd.store.db.commit()
        updated, _ = self.request('GET', '/api/projects/' + p['id'])
        result = next(m for m in updated['mappings'] if m['id'] == row['id'])
        self.assertEqual(('fill', 'blocked', False), (result['action'], result['status'], result['confirmed']))
        self.assertEqual([], result['material_ids'])

    def test_invalid_material_not_automatically_selected_and_append_recovers(self):
        p = self.profile_project()
        with self.assertRaises(urllib.error.HTTPError):
            self.profile(subject='其他公司')
        material = self.profile(mode='page', content='', files=[image_upload()])
        self.request('PATCH', '/api/materials/' + material['id'], {'removed_file_ids': [material['files'][0]['id']]})
        updated, _ = self.request('GET', '/api/projects/' + p['id'])
        row = next(m for m in updated['mappings'] if m['category_id'] == 'company_profile')
        self.assertEqual('blocked', row['status'])
        self.assertFalse(row['confirmed'])
        self.request('POST', '/api/materials/' + material['id'] + '/files', {'files': [image_upload()]})
        updated, _ = self.request('GET', '/api/projects/' + p['id'])
        row = next(m for m in updated['mappings'] if m['category_id'] == 'company_profile')
        self.assertEqual([material['id']], row['material_ids'])
        self.assertEqual('ready', row['status'])

    def test_form_previews_linked_project_issues_without_writing(self):
        p = self.profile_project()
        material = self.profile()
        feedback, _ = self.request('POST', '/api/materials/validate', {
            'material_id': material['id'], 'category_id': 'iso_cert', 'valid_until': '长期'})
        self.assertEqual({}, feedback['errors'])
        self.assertTrue(any('所属栏目不符' in warning for warning in feedback['project_warnings']))
        self.assertEqual('company_profile', self.httpd.store.require('material', material['id'])['category_id'])

    def test_material_feedback_and_save_reject_invalid_metadata(self):
        body = {'name': 'ISO证书', 'category_id': 'iso_cert', 'subject': server.SUBJECT,
                'mode': 'text', 'content': '证书正文', 'valid_until': '2028/12/31'}
        for invalid in ('2028/12/31', '2028-02-30', '20281231', '2000-01-01'):
            body['valid_until'] = invalid
            feedback, _ = self.request('POST', '/api/materials/validate', body)
            self.assertIn('valid_until', feedback['errors'])
            with self.assertRaises(urllib.error.HTTPError):
                self.request('POST', '/api/materials', body)
        self.assertEqual([], self.httpd.store.all('material'))
        body['valid_until'] = '长期'
        feedback, _ = self.request('POST', '/api/materials/validate', body)
        self.assertEqual({}, feedback['errors'])
        saved, _ = self.request('POST', '/api/materials', body)
        with self.assertRaises(urllib.error.HTTPError):
            self.request('PATCH', '/api/materials/' + saved['id'], {'valid_until': '无效日期'})
        self.assertEqual('长期', self.httpd.store.require('material', saved['id'])['valid_until'])
        feedback, _ = self.request('POST', '/api/materials/validate', {**body, 'category_id': 'audit_report', 'years': [], 'valid_until': '', 'content': '', 'subject': '其他公司'})
        self.assertTrue({'years', 'content', 'subject'}.issubset(feedback['errors']))
        self.assertIn('valid_until', feedback['warnings'])

    def test_batch_remove_preserves_originals_order_and_invalidates_confirmation(self):
        p = self.profile_project()
        material = self.profile(mode='page', content='', files=[image_upload(str(i)+'.png') for i in range(3)])
        ids = [f['id'] for f in material['files']]
        p, _ = self.request('GET', '/api/projects/' + p['id'])
        row = next(m for m in p['mappings'] if m['category_id'] == 'company_profile')
        self.request('PATCH', '/api/projects/' + p['id'] + '/mappings', {'mappings': [dict(row, confirmed=True)]})
        result, _ = self.request('PATCH', '/api/materials/' + material['id'], {'removed_file_ids': [ids[0], ids[2]]})
        self.assertEqual([ids[1]], [f['id'] for f in result['files']])
        p, _ = self.request('GET', '/api/projects/' + p['id'])
        self.assertFalse(next(m for m in p['mappings'] if m['id'] == row['id'])['confirmed'])
        for fid in ids:
            self.assertTrue(self.request('GET', '/api/files/' + fid, raw=True)[0])
        result, _ = self.request('PATCH', '/api/materials/' + material['id'], {'removed_file_ids': [ids[1]]})
        self.assertEqual([], result['files'])
        p, _ = self.request('GET', '/api/projects/' + p['id'])
        self.assertEqual('blocked', next(m for m in p['mappings'] if m['id'] == row['id'])['status'])

    def test_no_seed_material_upload_reorder_rotate_and_persistence(self):
        state, _ = self.request('GET', '/api/bootstrap')
        self.assertEqual(10, len([c for c in state['categories'] if c['layer']=='common']))
        self.assertEqual(7, sum(c['enabled'] for c in state['categories'] if c['layer']=='common'))
        self.assertEqual([], state['materials'])
        self.assertEqual(4, len(state['industries']))
        self.assertEqual('not_imported', state['stats']['source_status']['state'])
        category, _ = self.request('POST', '/api/categories', {'name': '补充资质', 'aliases': ['新资质']})
        material, _ = self.request('POST', '/api/materials', {'name': '资质包', 'subject': server.SUBJECT, 'category_id': category['id'], 'years': [2026], 'files': [image_upload(), image_upload('第二页.png', 'blue')]})
        self.assertEqual(2, len(material['files']))
        first, second = [f['id'] for f in material['files']]
        material, _ = self.request('PATCH', '/api/materials/' + material['id'], {'file_order': [second, first], 'rotations': {second: 90}})
        self.assertEqual(second, material['files'][0]['id'])
        self.assertEqual(90, material['files'][0]['rotation'])
        self.assertEqual(2, material['revision'])
        self.assertNotIn(self.tmp.name, json.dumps(material))
        self.assertNotIn('storage', json.dumps(material))
        raw, response_headers = self.request('GET', material['files'][0]['url'] + '?thumb=1', raw=True)
        with Image.open(io.BytesIO(raw)) as picture:
            self.assertEqual((40, 20), picture.size)
        self.assertNotIn('Access-Control-Allow-Origin', response_headers)
        self.request('POST', '/api/industries', {'name': '证券'})
        self.httpd.store.db.commit()
        reopened = server.Store(self.tmp.name)
        try:
            count = reopened.db.execute("SELECT count(*) FROM objects WHERE kind='material'").fetchone()[0]
            self.assertEqual(1, count)
        finally:
            reopened.close()
        material, _ = self.request('PATCH', '/api/materials/' + material['id'], {'removed_file_ids': [first]})
        self.assertEqual([second], [file['id'] for file in material['files']])
        self.assertIsNotNone(self.httpd.store.get('file', first))

    def test_reject_invalid_files_and_private_paths(self):
        base = {'name': '材料', 'subject': server.SUBJECT, 'category_id': 'business_license'}
        for file in [image_upload('../bad.png'), {'name': 'bad.png', 'data': base64.b64encode(b'not an image').decode()}, {'name': 'bad.svg', 'data': base64.b64encode(b'<svg/>').decode()}]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('POST', '/api/materials', {**base, 'files': [file]})
            self.assertEqual(400, error.exception.code)
        for path in ['/api/files/../../server.py', '/data/store.sqlite3', '/server.py']:
            with self.assertRaises(urllib.error.HTTPError):
                self.request('GET', path)
        state, _ = self.request('GET', '/api/bootstrap')
        self.assertEqual([], state['materials'])

    def test_cross_origin_host_and_form_post_are_blocked(self):
        for headers in [{'Origin': 'https://example.com'}, {'Origin': 'null'}, {'Host': 'evil.example'}, {'Sec-Fetch-Site': 'cross-site'}]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('POST', '/api/industries', {'name': '非法修改'}, headers=headers)
            self.assertEqual(403, error.exception.code)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('POST', '/api/industries', {'name': '非法修改'}, headers={'Content-Type': 'text/plain'})
        self.assertEqual(400, error.exception.code)
        state, _ = self.request('GET', '/api/bootstrap')
        self.assertEqual(4, len(state['industries']))

    def test_foreign_file_rotation_and_duplicate_order_rejected(self):
        common = {'subject': server.SUBJECT, 'category_id': 'business_license', 'files': [image_upload()]}
        first, _ = self.request('POST', '/api/materials', {**common, 'name': '材料一'})
        second, _ = self.request('POST', '/api/materials', {**common, 'name': '材料二'})
        for update in [{'rotations': {second['files'][0]['id']: 90}}, {'file_order': []}, {'file_order': [first['files'][0]['id'], first['files'][0]['id']]}]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('PATCH', '/api/materials/' + first['id'], update)
            self.assertEqual(400, error.exception.code)

    def test_http_mutations_and_exports_require_current_snapshot(self):
        project = self.profile_project()
        for method, action in [('PATCH', 'mappings'), ('PATCH', 'special-categories'),
                ('POST', 'text-materials'), ('POST', 'scan'), ('POST', 'export'),
                ('POST', 'export-jobs'), ('PATCH', 'chapter-drafts')]:
            with self.subTest(action=action), self.assertRaises(urllib.error.HTTPError) as error:
                self.request(method, '/api/projects/' + project['id'] + '/' + action,
                             {}, with_revision=False)
            self.assertEqual(409, error.exception.code)
        material = self.profile()
        for method, tail, body in [('PATCH', '', {'content': '不能覆盖'}),
                ('POST', '/files', {'files': [image_upload()]})]:
            with self.subTest(tail=tail), self.assertRaises(urllib.error.HTTPError) as error:
                self.request(method, '/api/materials/' + material['id'] + tail, body, with_revision=False)
            self.assertEqual(409, error.exception.code)
        self.assertEqual('公司业务介绍', self.httpd.store.require('material', material['id'])['content'])

    def test_stale_window_cannot_overwrite_mapping_or_export_different_snapshot(self):
        project = self.profile_project()
        old = project['mapping_revision']
        first, _ = self.request('PATCH', '/api/projects/' + project['id'] + '/mappings', {
            'mapping_revision': old, 'mappings': [dict(id=project['mappings'][0]['id'], note='窗口A新核对')]})
        self.assertNotEqual(old, first['mapping_revision'])
        for action, method, body in [('mappings', 'PATCH', {'mappings': project['mappings']}),
                ('export-jobs', 'POST', {'mode': 'draft'}), ('export', 'POST', {'mode': 'draft'})]:
            with self.subTest(action=action), self.assertRaises(urllib.error.HTTPError) as error:
                self.request(method, '/api/projects/' + project['id'] + '/' + action,
                             dict(body, mapping_revision=old))
            self.assertEqual(409, error.exception.code)
        saved = self.httpd.store.require('project', project['id'])
        self.assertEqual('窗口A新核对', saved['mappings'][0]['note'])
        self.assertEqual([], self.httpd.store.all('export_job'))

    def test_material_revision_and_unconfirmed_project_evidence_are_versioned(self):
        project = self.profile_project()
        material = self.profile()
        project, _ = self.request('GET', '/api/projects/' + project['id'])
        token = project['mapping_revision']
        self.assertTrue(all(not row['confirmed'] for row in project['mappings']))
        updated, _ = self.request('PATCH', '/api/materials/' + material['id'], {
            'revision': material['revision'], 'content': '窗口A修订的介绍'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('PATCH', '/api/materials/' + material['id'], {
                'revision': material['revision'], 'content': '公司业务介绍', 'description': '旧窗口备注'})
        self.assertEqual(409, error.exception.code)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('POST', '/api/materials/' + material['id'] + '/files', {
                'revision': material['revision'], 'files': [image_upload()]})
        self.assertEqual(409, error.exception.code)
        self.assertEqual('窗口A修订的介绍', self.httpd.store.require('material', material['id'])['content'])
        latest, _ = self.request('GET', '/api/projects/' + project['id'])
        self.assertNotEqual(token, latest['mapping_revision'])
        self.assertTrue(all(not row['confirmed'] for row in latest['mappings']))

    def test_archived_projects_remain_unchanged_after_library_edits_and_reads(self):
        project = self.profile_project()
        material = self.profile()
        project, _ = self.request('GET', '/api/projects/' + project['id'])
        self.request('PATCH', '/api/projects/' + project['id'] + '/mappings', {
            'mappings': [dict(row, confirmed=True) for row in project['mappings']]})
        store = self.httpd.store
        with store.lock:
            archived = store.require('project', project['id'])
            archived['framework_id'] = 'framework_test_archive'
            store.put('project', archived)
            store.put('framework', {'id': 'framework_test_archive', 'projects': {'word': 'new_project'},
                'legacy_projects': [{'id': project['id']}], 'artifacts': []})
            store.db.commit()
            before = store.require('project', project['id'])
        self.request('PATCH', '/api/materials/' + material['id'], {'content': '新版本介绍'})
        self.request('PATCH', '/api/categories/company_profile', {'aliases': ['公司介绍', '新版简介']})
        self.request('GET', '/api/projects/' + project['id'])
        self.assertEqual(before, store.require('project', project['id']))

    def test_partial_upload_never_blocks_other_requests_and_times_out(self):
        upload_started = threading.Event()
        original_body = server.Handler.body

        def tracked_body(handler):
            if handler.path == '/api/industries':
                upload_started.set()
            return original_body(handler)

        with patch.object(server, 'REQUEST_READ_TIMEOUT', .5), patch.object(server.Handler, 'body', tracked_body):
            client = socket.create_connection(('127.0.0.1', self.httpd.server_port))
            client.settimeout(2)
            try:
                client.sendall((f'POST /api/industries HTTP/1.1\r\nHost: 127.0.0.1:{self.httpd.server_port}\r\n'
                    f'Origin: {self.base}\r\nContent-Type: application/json\r\nContent-Length: 100000\r\n\r\n{{').encode())
                self.assertTrue(upload_started.wait(1), 'The server must be reading the incomplete body first')
                # This ordinary read must complete while the upload is still incomplete.
                start = time.monotonic()
                self.request('GET', '/api/bootstrap')
                self.assertLess(time.monotonic() - start, .45)
                response = client.recv(4096)
                self.assertIn(b'400', response.split(b'\r\n', 1)[0])
            finally:
                client.close()
        self.assertEqual(4, len(self.request('GET', '/api/bootstrap')[0]['industries']))

    def test_keepalive_does_not_reuse_prior_json_body(self):
        import http.client
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=2)
        try:
            for name in ['首个新行业', '第二个新行业']:
                connection.request('POST', '/api/industries', json.dumps({'name': name}),
                    {'Content-Type': 'application/json', 'Origin': self.base})
                response = connection.getresponse()
                self.assertEqual(201, response.status)
                self.assertEqual(name, json.loads(response.read())['name'])
        finally:
            connection.close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OrayRemoteAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bid-oray-test-')
        self.public = 'demo.imwork.net'
        self.token = 'test-access-token'
        self.httpd, self.intranet = server.create_oray_servers(
            port=0, data_dir=self.tmp.name, seed=False, host='127.0.0.1',
            public_host=self.public, access_token=self.token)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.intranet_thread = threading.Thread(target=self.intranet.serve_forever, daemon=True)
        self.thread.start()
        self.intranet_thread.start()
        self.port = self.httpd.server_port
        self.intranet_port = self.intranet.server_port
        self.local = f'http://127.0.0.1:{self.port}'
        self.intranet_base = f'http://127.0.0.1:{self.intranet_port}'
        self.public_origin = f'http://{self.public}'

    def tearDown(self):
        self.httpd.shutdown()
        self.intranet.shutdown()
        self.httpd.server_close()
        self.intranet.server_close()
        self.thread.join()
        self.intranet_thread.join()
        self.httpd.store.close()
        self.tmp.cleanup()

    def call(self, method, path, *, host=None, origin=None, headers=None, payload=None, data=None,
             content_type=None, base=None):
        values = {}
        if host:
            values['Host'] = host
        if origin:
            values['Origin'] = origin
        if content_type:
            values['Content-Type'] = content_type
        elif payload is not None:
            values['Content-Type'] = 'application/json'
        values.update(headers or {})
        body = data if data is not None else (json.dumps(payload).encode() if payload is not None else None)
        req = urllib.request.Request((base or self.local) + path, data=body, method=method, headers=values)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(req, timeout=10) as response:
                raw = response.read()
                ctype = response.headers.get('Content-Type', '')
                parsed = json.loads(raw) if 'json' in ctype and raw else raw
                return parsed, dict(response.headers), response.status
        except urllib.error.HTTPError as error:
            raw = error.read()
            ctype = error.headers.get('Content-Type', '') if error.headers else ''
            parsed = json.loads(raw) if 'json' in ctype and raw else raw
            return parsed, dict(error.headers or {}), error.code

    def seed_material_and_export(self):
        material = self.httpd.store.put('material', {
            'id': 'material_oray', 'name': '营业执照', 'subject': server.SUBJECT,
            'category_id': 'business_license', 'mode': 'page', 'file_ids': [],
            'revision': 0, 'created_at': server.now(), 'updated_at': server.now()})
        raw = io.BytesIO()
        Image.new('RGB', (20, 10), 'white').save(raw, 'PNG')
        file_id = 'file_oray'
        path = Path(self.tmp.name) / 'files'
        path.mkdir(parents=True, exist_ok=True)
        (path / (file_id + '.png')).write_bytes(raw.getvalue())
        self.httpd.store.put('file', {
            'id': file_id, 'name': '执照.png', 'mime': 'image/png', 'storage': f'files/{file_id}.png',
            'rotation': 0, 'width': 20, 'height': 10, 'source': ''})
        material['file_ids'] = [file_id]
        self.httpd.store.put('material', material)
        export = self.httpd.store.put('export', {
            'id': 'export_oray', 'project_id': 'p', 'project_name': 'x', 'filename': 'a.docx',
            'mode': 'draft', 'created_at': server.now()})
        exports = Path(self.tmp.name) / 'exports'
        exports.mkdir(parents=True, exist_ok=True)
        (exports / (export['id'] + '.docx')).write_bytes(b'PK')
        (exports / (export['id'] + '.json')).write_bytes(json.dumps({
            'id': export['id'], 'files': [{'id': file_id, 'name': '执照.png', 'sha256': 'abc'}],
            'subject': server.SUBJECT}).encode())
        self.httpd.store.db.commit()
        return material, file_id, export

    def test_public_host_requires_token_and_allows_login(self):
        body, _, status = self.call('GET', '/api/bootstrap', host=self.public)
        self.assertEqual(401, status)
        self.assertIn('error', body)
        page, _, status = self.call('GET', '/login', host=self.public)
        self.assertEqual(401, status)
        self.assertIn('访问口令'.encode(), page)
        _, headers, status = self.call(
            'POST', '/login', host=self.public, origin=self.public_origin,
            content_type='application/x-www-form-urlencoded',
            data=f'token={self.token}'.encode())
        self.assertEqual(303, status)
        cookie = headers.get('Set-Cookie') or headers.get('set-cookie')
        self.assertIn('bid_access=', cookie)
        self.assertNotIn('Secure', cookie)
        state, _, status = self.call(
            'GET', '/api/bootstrap', host=self.public,
            headers={'Cookie': cookie.split(';', 1)[0]})
        self.assertEqual(200, status)
        self.assertTrue(state['remote_access']['materials_intranet_only'])
        self.assertEqual(self.intranet_port, state['remote_access']['intranet_port'])

    def test_https_forwarded_login_cookie_is_secure(self):
        _, headers, status = self.call(
            'POST', '/login', host=self.public, origin=self.public_origin,
            content_type='application/x-www-form-urlencoded',
            headers={'X-Forwarded-Proto': 'https'},
            data=f'token={self.token}'.encode())
        self.assertEqual(303, status)
        cookie = headers.get('Set-Cookie') or headers.get('set-cookie')
        self.assertIn('bid_access=', cookie)
        self.assertIn('Secure', cookie)

    def test_mobile_webview_login_origin_variants(self):
        for origin in ['null', f'http://{self.public}:80']:
            _, _, status = self.call(
                'POST', '/login', host=self.public, origin=origin,
                content_type='application/x-www-form-urlencoded',
                headers={'Sec-Fetch-Site': 'same-site'},
                data=f'token={self.token}'.encode())
            self.assertEqual(303, status)

    def test_public_host_blocks_material_files_and_exports(self):
        material, file_id, export = self.seed_material_and_export()
        auth = {'Authorization': f'Bearer {self.token}'}
        detail, _, status = self.call('GET', '/api/materials/' + material['id'], host=self.public, headers=auth)
        self.assertEqual(200, status)
        self.assertEqual([], detail['files'])
        self.assertTrue(detail['files_intranet_only'])
        _, _, status = self.call('GET', '/api/files/' + file_id, host=self.public, headers=auth)
        self.assertEqual(403, status)
        _, _, status = self.call('GET', '/api/exports/' + export['id'] + '/download', host=self.public, headers=auth)
        self.assertEqual(403, status)
        _, _, status = self.call('GET', '/api/exports/' + export['id'] + '/report', host=self.public, headers=auth)
        self.assertEqual(403, status)

    def test_host_spoof_cannot_unlock_materials_on_mapped_port(self):
        _, file_id, export = self.seed_material_and_export()
        auth = {'Authorization': f'Bearer {self.token}'}
        spoof = f'127.0.0.1:{self.port}'
        _, _, status = self.call('GET', '/api/files/' + file_id, host=spoof, headers=auth)
        self.assertEqual(403, status)
        _, _, status = self.call('GET', '/api/exports/' + export['id'] + '/download', host=spoof, headers=auth)
        self.assertEqual(403, status)
        _, _, status = self.call('GET', '/api/exports/' + export['id'] + '/report', host=spoof, headers=auth)
        self.assertEqual(403, status)

    def test_intranet_port_allows_material_files_and_reports(self):
        material, file_id, export = self.seed_material_and_export()
        auth = {'Authorization': f'Bearer {self.token}'}
        local_host = f'127.0.0.1:{self.intranet_port}'
        detail, _, status = self.call(
            'GET', '/api/materials/' + material['id'], host=local_host, headers=auth,
            base=self.intranet_base)
        self.assertEqual(200, status)
        self.assertEqual(1, len(detail['files']))
        self.assertNotIn('files_intranet_only', detail)
        body, _, status = self.call(
            'GET', '/api/files/' + file_id, host=local_host, headers=auth, base=self.intranet_base)
        self.assertEqual(200, status)
        self.assertTrue(body.startswith(b'\x89PNG'))
        raw, _, status = self.call(
            'GET', '/api/exports/' + export['id'] + '/download', host=local_host, headers=auth,
            base=self.intranet_base)
        self.assertEqual(200, status)
        self.assertEqual(b'PK', raw)
        report, _, status = self.call(
            'GET', '/api/exports/' + export['id'] + '/report', host=local_host, headers=auth,
            base=self.intranet_base)
        self.assertEqual(200, status)
        self.assertEqual(file_id, report['files'][0]['id'])

    def test_wrong_host_still_rejected_with_token(self):
        _, _, status = self.call(
            'POST', '/api/industries', host='evil.example', origin=self.local,
            headers={'Authorization': f'Bearer {self.token}'}, payload={'name': '非法'})
        self.assertEqual(403, status)


class DocumentValidationTests(unittest.TestCase):
    def docx(self, extra=None, document='<document/>'):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
            archive.writestr('word/document.xml', document)
            for name, content in extra or []:
                archive.writestr(name, content)
        return raw.getvalue()

    def test_zip_traversal_macro_and_expansion_rejected(self):
        for additions in [[('../escape.txt', 'x')], [('word/vbaProject.bin', 'x')], [('word/embeddings/blob.bin', 'x')], [('word/bomb.xml', b'x' * (2 * 1024 * 1024))]]:
            with self.assertRaises(ValueError):
                server.check_docx('template.docx', self.docx(additions))

    def test_foreign_extension_and_damaged_word_rejected(self):
        for name, raw in [('document.zip', self.docx()), ('document.docx', b'damaged')]:
            with self.assertRaises(ValueError):
                server.check_docx(name, raw)

    def test_templates_reject_external_objects_and_active_content_but_keep_hyperlinks(self):
        relationship = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdAudit" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{}" '
            'Target="https://example.invalid/audit" TargetMode="External"/></Relationships>')
        for kind in ['attachedTemplate', 'image', 'oleObject']:
            for procurement in (False, True):
                with self.subTest(kind=kind, procurement=procurement), self.assertRaises(ValueError):
                    server.check_docx('template.docx', self.docx([
                        ('word/_rels/settings.xml.rels', relationship.format(kind))]), procurement=procurement)
        for procurement in (False, True):
            server.check_docx('template.docx', self.docx([
                ('word/_rels/document.xml.rels', relationship.format('hyperlink'))]), procurement=procurement)
        with self.assertRaises(ValueError):
            server.check_docx('template.docx', self.docx([('word/activeX/control.bin', b'not executed')]))

    def test_word_table_geometry_is_bounded_before_any_cell_expansion(self):
        def document(spans=('1',), before='0', after='0', grid_columns=0):
            cells = ''.join(f'<w:tc><w:tcPr><w:gridSpan w:val="{span}"/></w:tcPr><w:p/></w:tc>'
                            for span in spans)
            grid = '<w:gridCol/>' * grid_columns
            return ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body><w:tbl><w:tblGrid>{grid}</w:tblGrid><w:tr><w:trPr>'
                f'<w:gridBefore w:val="{before}"/><w:gridAfter w:val="{after}"/>'
                f'</w:trPr>{cells}</w:tr></w:tbl></w:body></w:document>')

        invalid = [document((value,)) for value in ('0', '-1', '4097', '1.5', '1_0', 'invalid')]
        invalid += [document(before='-1'), document(after='4097'), document(('4096', '1')),
                    document(('4095',), before='1', after='1'), document(grid_columns=4097)]
        for xml in invalid:
            raw = self.docx(document=xml)
            for procurement in (False, True):
                with self.subTest(procurement=procurement), self.assertRaisesRegex(ValueError, 'Word 表格列数异常'):
                    server.check_docx('template.docx', raw, procurement=procurement)
            with self.assertRaisesRegex(ValueError, 'Word 表格列数异常'):
                server.framework_service.check_attachment(raw)
        for xml in (document(('4096',)), document(('4094',), before='1', after='1')):
            raw = self.docx(document=xml)
            for procurement in (False, True):
                server.check_docx('template.docx', raw, procurement=procurement)
            server.framework_service.check_attachment(raw)


if __name__ == '__main__':
    unittest.main()
