"""Credit automation uses isolated fixtures, never live queries or invented live success."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from PIL import Image
from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import credit_service
import server


def fixture_result(subject, output_dir, blocked=False):
    result = {'schema_version': 1, 'subject': subject, 'started_at': credit_service.timestamp(),
              'finished_at': credit_service.timestamp(), 'sources': [], 'all_verified': not blocked}
    for index, source in enumerate(credit_service.SOURCES):
        image = io.BytesIO()
        Image.new('RGB', (80, 60), (230, 230 - index * 30, 200)).save(image, 'PNG')
        raw = image.getvalue()
        name = source['id'] + '.png'
        (output_dir / name).write_bytes(raw)
        result['sources'].append({**{key: source[key] for key in ('id', 'name', 'url')},
                                  'final_url': source['url'], 'status': 'blocked' if blocked else 'no_records',
                                  'reason': '验证码需要人工输入' if blocked else '测试夹具：模拟已完成查询',
                                  **{flag: not blocked for flag in credit_service.FLAGS},
                                  'screenshot': name, 'sha256': hashlib.sha256(raw).hexdigest()})
    return result


class CreditStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='credit-fixture-')
        self.store = server.Store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def run_worker(self, worker):
        self.store.credit.worker = worker
        initial = self.store.credit.check({'subject': server.SUBJECT})
        self.assertEqual('running', initial['status'])
        self.store.credit.thread.join(5)
        self.assertFalse(self.store.credit.thread.is_alive())
        with self.store.lock:
            return self.store.credit.status()

    def test_migration_is_idempotent_and_defaults_disabled(self):
        self.assertEqual(10, len([c for c in self.store.all('category') if c['layer']=='common']))
        self.assertEqual(7, sum(c['enabled'] for c in self.store.all('category') if c['layer']=='common'))
        self.assertEqual('untested', self.store.bootstrap()['credit_automation']['status'])
        self.assertEqual([], self.store.credit.status()['materials'])
        self.store.category({'name': '自定义栏目', 'aliases': []})
        self.store.close()
        self.store = server.Store(self.tmp.name)
        self.assertEqual(11, len([c for c in self.store.all('category') if c['layer']=='common']))
        self.assertEqual(3, sum(c.get('managed_by') == 'credit_automation' for c in self.store.all('category')))
        self.assertTrue(all(not self.store.require('category', sid)['enabled'] for sid in credit_service.MANAGED_IDS))

    def test_ordinary_routes_cannot_enable_or_supply_credit_material(self):
        sid = credit_service.SOURCES[0]['id']
        for payload in [{'enabled': True}, {'name': '改名'}, {'aliases': ['信用']}]:
            with self.assertRaises(PermissionError):
                self.store.category(payload, sid)
        with self.assertRaises(PermissionError):
            self.store.material({'name': '伪造截图', 'subject': server.SUBJECT, 'category_id': sid})
        with self.assertRaises(ValueError):
            self.store.credit.check({'subject': server.SUBJECT, 'all_verified': True, 'sources': []})
        with self.assertRaises(ValueError):
            self.store.credit.check({'subject': '其他公司'})

    def test_failure_saves_diagnostics_not_materials_and_survives_restart(self):
        state = self.run_worker(lambda subject, output: fixture_result(subject, output, blocked=True))
        self.assertEqual('blocked', state['status'])
        self.assertFalse(state['enabled'])
        self.assertEqual([], self.store.all('material'))
        self.assertEqual([], self.store.all('file'))
        self.assertEqual(3, len(list((Path(self.tmp.name) / 'diagnostics').rglob('*.png'))))
        self.assertEqual([], list((Path(self.tmp.name) / 'materials').rglob('*.png')))
        run_id = state['run_id']
        self.store.close()
        self.store = server.Store(self.tmp.name)
        self.assertEqual(run_id, self.store.credit.status()['run_id'])
        self.assertEqual('blocked', self.store.credit.status()['status'])

    def test_fixture_success_archives_and_preserves_evidence_on_export(self):
        state = self.run_worker(fixture_result)
        self.assertTrue(state['enabled'])
        self.assertEqual('verified', state['status'])
        self.assertEqual(3, len(state['materials']))
        self.assertTrue(all(self.store.require('category', sid)['enabled'] for sid in credit_service.MANAGED_IDS))
        for material in state['materials']:
            self.assertEqual('verified', material['credit_evidence']['verification_status'])
            self.assertEqual(state['run_id'], material['credit_evidence']['run_id'])
            self.assertNotIn(self.tmp.name, json.dumps(material))
            with self.assertRaises(PermissionError):
                self.store.material({'description': '手改'}, material['id'])
            with self.assertRaises(PermissionError):
                self.store.add_files(material['id'], {'files': []})
        # Existing ordinary columns are disabled only in this isolated export fixture.
        for category in self.store.all('category'):
            if category['layer'] == 'common' and category['id'] not in credit_service.MANAGED_IDS:
                self.store.category({'enabled': False}, category['id'])
        template = Document()
        for source in credit_service.SOURCES:
            template.add_heading(source['name'], level=1)
        stream = io.BytesIO()
        template.save(stream)
        project = self.store.create_project({'name': '信用夹具导出', 'filename': 'template.docx',
                                             'data': base64.b64encode(stream.getvalue()).decode()})
        self.assertEqual(3, len(project['mappings']))
        self.store.update_mappings(project['id'], {'mappings': [dict(id=row['id'], confirmed=True) for row in project['mappings']]})
        self.store.select_special_categories(project['id'], {'category_ids': [], 'no_special_materials': True})
        exported = self.store.export(project['id'], {'mode': 'reviewed'})
        report = json.loads((Path(self.tmp.name) / 'exports' / (exported['id'] + '.json')).read_text())
        self.assertEqual(3, len(report['materials']))
        self.assertTrue(all(m['credit_evidence']['run_id'] == state['run_id'] for m in report['materials']))
        self.store.close()
        self.store = server.Store(self.tmp.name)
        self.assertTrue(self.store.credit.status()['enabled'])

    def test_failed_recheck_disables_and_invalidates_old_materials(self):
        state = self.run_worker(fixture_result)
        previous_ids = {material['id'] for material in state['materials']}
        self.store.put('project', {'id': 'fixture_project', 'mappings': [
            {'category_id': sid, 'material_ids': ['auto_' + sid], 'confirmed': True}
            for sid in credit_service.MANAGED_IDS]})
        self.store.db.commit()
        failed = self.run_worker(lambda subject, output: fixture_result(subject, output, blocked=True))
        self.assertFalse(failed['enabled'])
        self.assertEqual([], failed['materials'])
        self.assertEqual(previous_ids, {material['id'] for material in self.store.all('material')})
        self.assertTrue(all(m['credit_evidence']['verification_status'] == 'needs_review' for m in self.store.all('material')))
        self.assertTrue(all(not row['confirmed'] for row in self.store.require('project', 'fixture_project')['mappings']))

    def test_untrusted_or_incomplete_success_is_never_archived(self):
        mutations = [
            lambda p: p['sources'][0].update(sha256='0' * 64),
            lambda p: p['sources'][0].update(final_url='https://example.com/result'),
            lambda p: p['sources'][0].update(screenshot='../outside.png'),
            lambda p: p['sources'][0].update(subject_verified=False),
            lambda p: p['sources'][0].update(status='records_found'),
            lambda p: p.update(all_verified=False),
            lambda p: p.update(subject='其他公司'),
            lambda p: p.update(sources=p['sources'][:2]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                def worker(subject, output):
                    payload = fixture_result(subject, output)
                    mutate(payload)
                    return payload
                self.assertFalse(self.run_worker(worker)['enabled'])
                self.assertEqual([], self.store.all('material'))

    def test_worker_exception_and_interrupted_restart_fail_closed(self):
        def crash(subject, output):
            raise RuntimeError('运行依赖尚未安装')
        self.assertEqual('blocked', self.run_worker(crash)['status'])
        state = self.store.get('meta', 'credit_automation')
        state.update(status='running', enabled=False)
        self.store.put('meta', state)
        self.store.db.commit()
        self.store.close()
        self.store = server.Store(self.tmp.name)
        self.assertEqual('blocked', self.store.credit.status()['status'])
        self.assertIn('重启', self.store.credit.status()['message'])

    def test_archive_failure_rolls_back_both_database_and_material_copies(self):
        original = self.store.create_files
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('fixture disk failure')
            return original(*args, **kwargs)
        with patch.object(self.store, 'create_files', side_effect=fail_second):
            result = self.run_worker(fixture_result)
        self.assertFalse(result['enabled'])
        self.assertEqual([], self.store.all('material'))
        self.assertEqual([], self.store.all('file'))
        self.assertEqual([], list((Path(self.tmp.name) / 'materials').rglob('*.png')))

    def test_recheck_updates_three_materials_instead_of_creating_duplicate_versions(self):
        first = self.run_worker(fixture_result)
        second = self.run_worker(fixture_result)
        self.assertNotEqual(first['run_id'], second['run_id'])
        self.assertEqual(3, len(self.store.all('material')))
        self.assertTrue(all(m['revision'] == 2 for m in second['materials']))
        self.assertEqual(6, len(self.store.all('file')))


class CreditHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='credit-http-fixture-')
        self.httpd = server.create_server(port=0, data_dir=self.tmp.name)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.httpd.server_port}'
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        worker = self.httpd.store.credit.thread
        if worker:
            worker.join(5)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.httpd.store.close()
        self.tmp.cleanup()

    def request(self, method, path, payload=None):
        request = urllib.request.Request(self.base + path, method=method,
                    data=json.dumps(payload).encode() if payload is not None else None,
                    headers={'Content-Type': 'application/json', 'Origin': self.base})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2) as response:
            return response.status, json.loads(response.read())

    def test_background_worker_does_not_hold_request_lock_and_prevents_parallel_runs(self):
        entered = threading.Event()
        def waiting_worker(subject, output):
            entered.set()
            self.release.wait(5)
            return fixture_result(subject, output, blocked=True)
        self.httpd.store.credit.worker = waiting_worker
        start = time.monotonic()
        code, state = self.request('POST', '/api/credit-automation/check', {'subject': server.SUBJECT})
        self.assertEqual(202, code)
        self.assertEqual('running', state['status'])
        self.assertTrue(entered.wait(1))
        self.assertEqual('running', self.request('GET', '/api/credit-automation')[1]['status'])
        self.assertEqual('running', self.request('GET', '/api/bootstrap')[1]['credit_automation']['status'])
        self.assertLess(time.monotonic() - start, 1.5)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('POST', '/api/credit-automation/check', {'subject': server.SUBJECT})
        self.assertEqual(400, raised.exception.code)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('PATCH', '/api/categories/credit_dishonest', {'enabled': True})
        self.assertEqual(403, raised.exception.code)


if __name__ == '__main__':
    unittest.main()
