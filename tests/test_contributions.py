"""Colleague submissions stay quarantined until a reviewer on the local entry accepts them."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from docx import Document
from PIL import Image

import server
from server import SUBJECT, Store


def png(color='white', size=(40, 30)):
    raw = io.BytesIO()
    Image.new('RGB', size, color).save(raw, 'PNG')
    return raw.getvalue()


def upload(name, raw):
    return {'name': name, 'data': base64.b64encode(raw).decode()}


def docx_bytes(*lines):
    document = Document()
    for line in lines:
        document.add_paragraph(line)
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def material_body(**material):
    return {'kind': 'material', 'contributor': '张三', 'note': '新换发的执照',
            'material': {'name': '营业执照（2026 换发）', 'category_id': 'business_license', 'subject': SUBJECT,
                         'valid_until': '长期', 'mode': 'page', **material},
            'files': [upload('执照正本.png', png()), upload('执照副本.png', png('gray'))]}


def case_body(name='某银行运维服务案例'):
    return {'kind': 'case', 'contributor': '李四', 'case': {'name': name, 'client_name': '某银行'},
            'files': [upload('服务方案.docx', docx_bytes('项目背景：为某银行提供七乘二十四小时运维服务。')),
                      upload('验收说明.txt', '验收结论：服务达标。\n客户评价良好。'.encode())]}


class ContributionStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='bid-contrib-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'data'
        self.store = Store(self.root)
        self.addCleanup(self.store.close)
        self.contributions = self.store.contributions

    def review_fields(self, **changes):
        return {'name': '营业执照（2026 换发）', 'category_id': 'business_license', 'subject': SUBJECT,
                'version': '2026 换发', 'years': [], 'valid_until': '长期', 'mode': 'page', 'content': '',
                'description': '同事投稿，已核对原件', **changes}

    def test_material_is_quarantined_until_accepted_with_provenance(self):
        before = len(self.store.all('material'))
        remote = self.contributions.submit(material_body(), remote=True)
        self.assertEqual(('pending', 2, []), (remote['status'], remote['file_count'], remote['files']))
        self.assertTrue(remote['files_intranet_only'])
        self.assertEqual(before, len(self.store.all('material')))
        self.assertNotIn(remote['id'], json.dumps(self.store.bootstrap()['materials']))
        self.assertEqual(1, self.store.bootstrap()['stats']['pending_submission_count'])
        local = self.contributions.describe(remote['id'], remote=False)
        self.assertEqual(['执照正本.png', '执照副本.png'], [f['name'] for f in local['files']])
        _, raw, kind = self.contributions.file(remote['id'], local['files'][0]['id'])
        self.assertEqual(('material', True), (kind, raw.startswith(b'\x89PNG')))

        result = self.contributions.accept(remote['id'], {'revision': local['revision'],
                                                          'material': self.review_fields()})
        material = self.store.require('material', result['material']['id'])
        self.assertEqual(2, len(material['file_ids']))
        self.assertEqual(hashlib.sha256(png()).hexdigest(), self.store.require('file', material['file_ids'][0])['sha256'])
        self.assertEqual(('张三', remote['id'], 'remote'), (material['contribution']['contributor'],
                         material['contribution']['submission_id'], material['contribution']['origin']))
        decided = self.contributions.describe(remote['id'], remote=False)
        self.assertEqual(('accepted', material['id']), (decided['status'], decided['decision']['material_id']))
        self.assertNotIn('url', decided['files'][0])
        self.assertFalse((self.root / 'submissions' / remote['id']).exists())
        self.assertEqual(0, self.store.bootstrap()['stats']['pending_submission_count'])
        with self.assertRaisesRegex(server.ConflictError, '已处理'):
            self.contributions.accept(remote['id'], {'revision': decided['revision'], 'material': self.review_fields()})

    def test_failed_review_creates_nothing_and_keeps_submission_pending(self):
        submitted = self.contributions.submit(material_body(), remote=True)
        before = len(self.store.all('material'))
        with self.assertRaisesRegex(ValueError, '材料主体须为'):
            self.contributions.accept(submitted['id'], {'revision': 1, 'material': self.review_fields(subject='其他公司')})
        self.store.db.rollback()
        with self.assertRaisesRegex(server.ConflictError, '已更新'):
            self.contributions.accept(submitted['id'], {'revision': 9, 'material': self.review_fields()})
        self.assertEqual(before, len(self.store.all('material')))
        self.assertEqual('pending', self.store.require('submission', submitted['id'])['status'])
        self.assertEqual(2, len(list((self.root / 'submissions' / submitted['id']).iterdir())))

    def test_reject_needs_reason_and_discards_files(self):
        submitted = self.contributions.submit(material_body(), remote=False)
        with self.assertRaisesRegex(ValueError, '退回原因'):
            self.contributions.reject(submitted['id'], {'revision': 1, 'reason': ' '})
        result = self.contributions.reject(submitted['id'], {'revision': 1, 'reason': '图片模糊，请重新扫描'})
        self.assertEqual(('rejected', '图片模糊，请重新扫描'),
                         (result['submission']['status'], result['submission']['decision']['reason']))
        self.assertFalse((self.root / 'submissions' / submitted['id']).exists())
        with self.assertRaises(LookupError):
            self.contributions.file(submitted['id'], result['submission']['files'][0]['id'])
        listing = self.contributions.list({'status': ['rejected']}, remote=True)
        self.assertEqual(([submitted['id']], 1), ([i['id'] for i in listing['items']], listing['counts']['rejected']))

    def test_invalid_submissions_store_nothing(self):
        cases = [
            ({**material_body(), 'contributor': ' '}, '提交人'),
            ({**material_body(), 'kind': 'other'}, '提交材料或案例'),
            (material_body(mode='text', content='正文'), '无需上传图片'),
            ({**material_body(mode='text'), 'files': []}, '文字材料正文'),
            ({**material_body(), 'files': []}, '至少选择一张'),
            ({**material_body(), 'files': [upload('坏图.png', b'not an image')]}, '图片损坏'),
            ({**material_body(), 'files': [upload('执照.pdf', b'%PDF-1.4')]}, 'PNG'),
            (material_body(category_id='credit_dishonest'), '建议栏目'),
            (material_body(years=['2025']), '年度'),
            ({**case_body(), 'files': [upload('说明.txt', b' \n')]}, '未读取到可用文字'),
            ({**case_body(), 'case': {'name': '案例\x07'}}, '控制字符'),
        ]
        for body, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.contributions.submit(body, remote=True)
        self.assertEqual([], self.store.all('submission'))
        self.assertFalse((self.root / 'submissions').exists() and any((self.root / 'submissions').iterdir()))

    def test_text_material_with_unspecified_category_is_classified_on_review(self):
        submitted = self.contributions.submit({'kind': 'material', 'contributor': '王五', 'files': [],
                                               'material': {'name': '公司简介（2026）', 'mode': 'text',
                                                            'content': '腾宝科技成立于 2010 年。'}}, remote=True)
        self.assertEqual(('', 0), (submitted['material']['category_id'], submitted['file_count']))
        result = self.contributions.accept(submitted['id'], {'revision': 1, 'material': self.review_fields(
            name='公司简介（2026）', category_id='company_profile', mode='text', content='腾宝科技成立于 2010 年。')})
        self.assertEqual('腾宝科技成立于 2010 年。', self.store.require('material', result['material']['id'])['content'])

    def test_pending_queue_is_bounded(self):
        with patch('contributions.MAX_PENDING', 1):
            self.contributions.submit(material_body(), remote=True)
            with self.assertRaisesRegex(ValueError, '已达上限'):
                self.contributions.submit(material_body(), remote=True)
        with patch('contributions.MAX_PENDING_BYTES', 10):
            with self.assertRaisesRegex(ValueError, '已达上限'):
                self.contributions.submit(material_body(), remote=True)

    def test_accepted_case_joins_library_and_archiving_hides_it(self):
        submitted = self.contributions.submit(case_body(), remote=True)
        self.assertGreater(submitted['summary']['source_count'], 0)
        self.assertNotIn('warnings', submitted['summary'])
        result = self.contributions.accept(submitted['id'], {'revision': 1, 'case': {'description': '适用于银行运维类项目'}})
        case = result['case']
        self.assertEqual(('某银行运维服务案例', '适用于银行运维类项目', '李四', 2),
                         (case['name'], case['description'], case['contributor'], case['file_count']))
        self.assertFalse((self.root / 'submissions' / submitted['id']).exists())
        _, raw = self.contributions.case_file(case['id'], case['files'][1]['id'])
        self.assertIn('服务达标'.encode(), raw)
        self.assertEqual(['案例库/某银行运维服务案例/服务方案.docx', '案例库/某银行运维服务案例/验收说明.txt'],
                         [item['name'] for item in self.contributions.package([case['id']])])
        self.assertEqual([case['id']], [row['id'] for row in self.contributions.library_summary()])
        archived = self.contributions.update_case(case['id'], {'revision': case['revision'], 'archived': True})
        self.assertEqual([], self.contributions.library_summary())
        self.assertEqual([case['id']], [row['id'] for row in self.contributions.cases({'archived': ['true']}, remote=False)['items']])
        with self.assertRaisesRegex(ValueError, '移出案例库'):
            self.contributions.package([case['id']])
        with self.assertRaises(server.ConflictError):
            self.contributions.update_case(case['id'], {'revision': case['revision'], 'archived': False})
        self.contributions.update_case(case['id'], {'revision': archived['revision'], 'archived': False})
        self.assertEqual(1, len(self.contributions.library_summary()))

    def test_same_named_cases_get_distinct_folders(self):
        ids = []
        for _ in range(2):
            submitted = self.contributions.submit(case_body('运维/服务案例'), remote=True)
            ids.append(self.contributions.accept(submitted['id'], {'revision': 1})['case']['id'])
        names = [item['name'] for item in self.contributions.package(ids)]
        self.assertEqual('案例库/运维／服务案例/服务方案.docx', names[0])
        self.assertEqual('案例库/运维／服务案例（2）/服务方案.docx', names[2])
        for bad in ([], [ids[0], ids[0]], 'x', [1]):
            with self.subTest(selection=bad), self.assertRaisesRegex(ValueError, '不重复'):
                self.contributions.package(bad)


class LibraryCaseWritingTests(unittest.TestCase):
    """Step 4 builds its case package from library cases through the normal upload path."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='bid-contrib-writing-')
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / 'data')
        self.addCleanup(self.store.close)
        template = self.store.path('templates/project.docx')
        Document().save(template)
        project = dict(id='project', name='案例库写作', framework_id='framework',
                       template_storage=str(template.relative_to(self.store.root)),
                       template_hash=hashlib.sha256(template.read_bytes()).hexdigest(),
                       material_flow='text-first', special_selection_confirmed=True, text_review_confirmed=True,
                       special_category_ids=[], chapter_revision=0, chapter_texts={}, mappings=[])
        framework = dict(id='framework', revision=1, requirements_confirmed=True, projects={'word': 'project'},
                         outline=[], sources=[], requirements=[])
        self.store.put('project', project)
        self.store.put('framework', framework)
        self.store.db.commit()
        submitted = self.store.contributions.submit(case_body(), remote=True)
        self.case = self.store.contributions.accept(submitted['id'], {'revision': 1})['case']

    def request(self, **extra):
        project = self.store.require('project', 'project')
        pack = self.store.get('writing_cases', 'project') or {'revision': 0}
        return dict(mapping_revision=self.store.mapping_revision(project), case_revision=pack['revision'], **extra)

    def test_library_cases_and_uploads_form_one_package(self):
        described = self.store.writing.describe('project')
        self.assertEqual([self.case['id']], [row['id'] for row in described['library_cases']])
        result = self.store.writing.upload('project', self.request(
            library_case_ids=[self.case['id']], files=[upload('补充说明.md', '# 补充\n团队稳定。'.encode())]))
        self.assertEqual(['补充说明.md', '案例库/某银行运维服务案例/服务方案.docx', '案例库/某银行运维服务案例/验收说明.txt'],
                         [item['name'] for item in result['files']])
        self.assertEqual([self.case['id']], result['library_case_ids'])
        self.assertTrue(any('七乘二十四小时' in source['text'] for source in result['case_sources']))
        pack = self.store.get('writing_cases', 'project')
        self.assertEqual(3, len(list(self.store.path(pack['storage']).iterdir())))
        only_library = self.store.writing.upload('project', self.request(library_case_ids=[self.case['id']]))
        self.assertEqual(2, len(only_library['files']))
        self.store.contributions.update_case(self.case['id'], {'revision': self.case['revision'], 'archived': True})
        with self.assertRaisesRegex(ValueError, '移出案例库'):
            self.store.writing.upload('project', self.request(library_case_ids=[self.case['id']]))
        with self.assertRaisesRegex(ValueError, '请先上传'):
            self.store.writing.upload('project', self.request())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ContributionAccessTests(unittest.TestCase):
    """The mapped port may submit and read status; files and decisions stay on the local entry."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='bid-contrib-http-')
        self.addCleanup(directory.cleanup)
        self.public = 'demo.imwork.net'
        self.auth = {'Authorization': 'Bearer test-access-token'}
        self.remote, self.local = server.create_oray_servers(
            port=0, data_dir=directory.name, seed=False, host='127.0.0.1',
            public_host=self.public, access_token='test-access-token')
        for httpd in (self.remote, self.local):
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join)
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)
        self.addCleanup(self.remote.store.close)

    def call(self, method, path, payload=None, *, local=False):
        port = (self.local if local else self.remote).server_port
        host = f'127.0.0.1:{port}' if local else self.public
        headers = dict(self.auth, Host=host, Origin=f'http://{host}')
        body = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(payload).encode()
        request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=body, method=method, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=20) as response:
                raw, status, ctype = response.read(), response.status, response.headers.get('Content-Type', '')
        except urllib.error.HTTPError as error:
            raw, status, ctype = error.read(), error.code, error.headers.get('Content-Type', '')
        return (json.loads(raw) if 'json' in ctype else raw), status

    def test_remote_submits_but_cannot_read_files_or_decide(self):
        submitted, status = self.call('POST', '/api/submissions', material_body())
        self.assertEqual(201, status)
        self.assertEqual(([], True), (submitted['files'], submitted['files_intranet_only']))
        listing, status = self.call('GET', '/api/submissions')
        self.assertEqual((200, 1, []), (status, listing['counts']['pending'], listing['items'][0]['files']))
        local, status = self.call('GET', '/api/submissions/' + submitted['id'], local=True)
        self.assertEqual(200, status)
        file_url = local['files'][0]['url']
        for method, path, payload in [('GET', file_url, None), ('GET', file_url + '?thumb=1', None),
                                      ('POST', f'/api/submissions/{submitted["id"]}/accept', {'revision': 1}),
                                      ('POST', f'/api/submissions/{submitted["id"]}/reject', {'revision': 1, 'reason': '重复'})]:
            body, status = self.call(method, path, payload)
            self.assertEqual(403, status, path)
            self.assertIn('仅限本机', body['error'])
        raw, status = self.call('GET', file_url, local=True)
        self.assertEqual((200, True), (status, raw.startswith(b'\x89PNG')))
        result, status = self.call('POST', f'/api/submissions/{submitted["id"]}/accept', {
            'revision': 1, 'material': {'name': '营业执照', 'category_id': 'business_license', 'subject': SUBJECT,
                                        'valid_until': '长期', 'mode': 'page'}}, local=True)
        self.assertEqual(200, status)
        material, status = self.call('GET', '/api/materials/' + result['material']['id'])
        self.assertEqual((200, []), (status, material['files']))
        again, status = self.call('POST', f'/api/submissions/{submitted["id"]}/reject',
                                  {'revision': 2, 'reason': '重复'}, local=True)
        self.assertEqual(409, status, again)

    def test_case_library_is_listed_remotely_without_download(self):
        submitted, _ = self.call('POST', '/api/submissions', case_body())
        accepted, status = self.call('POST', f'/api/submissions/{submitted["id"]}/accept', {'revision': 1}, local=True)
        self.assertEqual(200, status)
        remote, status = self.call('GET', '/api/case-library')
        self.assertEqual((200, ['服务方案.docx', '验收说明.txt']), (status, [f['name'] for f in remote['items'][0]['files']]))
        self.assertNotIn('url', remote['items'][0]['files'][0])
        url = accepted['case']['files'][0]['url']
        _, status = self.call('GET', url)
        self.assertEqual(403, status)
        raw, status = self.call('GET', url, local=True)
        self.assertEqual((200, True), (status, raw.startswith(b'PK')))
        body, status = self.call('PATCH', '/api/case-library/' + accepted['case']['id'], {'revision': 1, 'archived': True})
        self.assertEqual(403, status)


if __name__ == '__main__':
    unittest.main()
