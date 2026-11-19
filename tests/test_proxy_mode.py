"""商机系统 nginx 反向代理模式：只信任门户注入的 X-Remote-User。"""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class ProxyModeTests(unittest.TestCase):
    gateway_key = 'test-gateway-configured-key-'+ 'x' * 16
    gateway_origin = 'https://portal.test'
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bid-proxy-test-')
        self.httpd = server.create_server(
            port=0, data_dir=self.tmp.name, behind_proxy=True, portal_link='/portal/',
            proxy_secret=self.gateway_key, proxy_origins=[self.gateway_origin], proxy_admins=['qa-maintainer'])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_port

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.httpd.store.close()
        self.tmp.cleanup()

    def request(self, method, path, payload=None, headers=None, host=None):
        values = {'Content-Type': 'application/json', 'Host': 'portal.test'}
        values.update(headers or {})
        if host:
            values['Host'] = host
        body = json.dumps(payload).encode() if payload is not None else None
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=values)
            response = conn.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), raw
        finally:
            conn.close()

    @staticmethod
    def json_body(raw):
        return json.loads(raw.decode())

    def test_requires_portal_user(self):
        status, _, raw = self.request('GET', '/api/bootstrap')
        self.assertEqual(status, 401)
        self.assertEqual(self.json_body(raw)['error'], '请从受信商机入口登录')

    def test_accepts_portal_user_and_returns_portal_link(self):
        status, _, raw = self.request('GET', '/api/bootstrap', headers={'X-Bid-Proxy-Key': self.gateway_key, 'X-Remote-User': 'alice'})
        self.assertEqual(status, 200)
        payload = self.json_body(raw)
        self.assertEqual(payload['portal'], {'href': '/portal/', 'user': 'alice'})

    def test_rejects_invalid_portal_user(self):
        status, _, raw = self.request('GET', '/api/bootstrap', headers={'X-Bid-Proxy-Key': self.gateway_key, 'X-Remote-User': 'a/b'})
        self.assertEqual(status, 401)
        self.assertEqual(self.json_body(raw)['error'], '请从受信商机入口登录')

    def test_login_form_is_not_exposed_behind_proxy(self):
        status, headers, body = self.request('GET', '/login')
        self.assertEqual(status, 401)
        self.assertIn('application/json', headers.get('Content-Type', ''))
        self.assertNotIn(b'name="token"', body)

    def test_same_origin_post_is_allowed(self):
        origin = self.gateway_origin
        status, _, raw = self.request(
            'POST', '/api/industries', {'name': '代理模式测试行业'},
            headers={'X-Bid-Proxy-Key': self.gateway_key, 'X-Remote-User': 'qa-maintainer', 'Origin': origin})
        self.assertEqual(status, 201)
        self.assertEqual(self.json_body(raw)['name'], '代理模式测试行业')

    def test_regular_portal_account_has_no_maintenance_or_exports(self):
        headers = {'X-Bid-Proxy-Key': self.gateway_key, 'X-Remote-User': 'alice'}
        status, _, _ = self.request('POST', '/api/industries', {'name': '不应创建'}, headers=headers)
        self.assertEqual(403, status)
        status, _, _ = self.request('GET', '/api/exports/unavailable/download', headers=headers)
        self.assertEqual(403, status)

    def test_only_portal_maintainer_can_read_audit_report(self):
        key = 'export_fixture'
        store = self.httpd.store
        with store.lock:
            store.path(f'exports/{key}.json').write_text('{"result":"fixture"}', encoding='utf-8')
            store.put('export', {'id': key, 'filename': 'fixture.docx'})
            store.db.commit()
        for user, expected in (('alice', 403), ('qa-maintainer', 200)):
            headers = {'X-Bid-Proxy-Key': self.gateway_key, 'X-Remote-User': user}
            status, _, raw = self.request('GET', f'/api/exports/{key}/report', headers=headers)
            self.assertEqual(expected, status)
            if status == 200:
                self.assertEqual({'result': 'fixture'}, self.json_body(raw))
        log = store.root / 'logs' / 'access.log'
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            if any(record['entry'] == 'proxy' and record.get('portal', {}).get('user') == 'qa-maintainer'
                   for record in records):
                break
            time.sleep(.01)
        else:
            self.fail('未记录已验证的门户管理员操作')
        self.assertNotIn(self.gateway_key, log.read_text())

    def test_gateway_credential_required(self):
        status, _, _ = self.request('GET', '/api/bootstrap', headers={'X-Remote-User': 'qa-maintainer'})
        self.assertEqual(401, status)

    def test_open_listener_requires_configuration(self):
        with self.assertRaises(ValueError):
            server.create_server(port=0, data_dir=self.tmp.name, host='0.0.0.0')
        with self.assertRaises(ValueError):
            server.create_server(port=0, data_dir=self.tmp.name)

    def test_cross_origin_post_is_rejected(self):
        status, _, raw = self.request(
            'POST', '/api/industries', {'name': '不应创建'},
            headers={'X-Bid-Proxy-Key': self.gateway_key, 'X-Remote-User': 'alice', 'Origin': 'http://evil.example'})
        self.assertEqual(status, 403)
        self.assertIn('请求来源不正确', self.json_body(raw)['error'])

    def test_proxy_mode_cannot_combine_with_access_token(self):
        with tempfile.TemporaryDirectory(prefix='bid-proxy-invalid-') as tmp:
            with self.assertRaises(ValueError):
                server.create_server(port=0, data_dir=tmp, behind_proxy=True, access_token='secret')

    def test_portal_link_must_be_local_path(self):
        with tempfile.TemporaryDirectory(prefix='bid-proxy-invalid-') as tmp:
            with self.assertRaises(ValueError):
                server.create_server(port=0, data_dir=tmp, behind_proxy=True,
                                     portal_link='http://evil.example/')


if __name__ == '__main__':
    unittest.main()
