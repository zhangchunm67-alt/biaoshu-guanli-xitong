"""真实来源的端到端测试，所有写入均在独立临时目录中。"""
import base64
import io
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

APP = Path(__file__).resolve().parents[1]
# The seed workflow reads the original delivery package next to the code.
SOURCE_DATA = all(path.exists() for path in (
    APP.parent / "标书自动填充/示例/材料台账.xlsx", APP.parent / "材料", APP.parent / "填充测试文档.docx"))


class FullWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bid-e2e-")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.start()

    def start(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(APP / "server.py"), "--port", str(self.port),
             "--data-dir", self.tmp.name, "--seed"],
            cwd=APP, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        for _ in range(200):
            try:
                self.request("GET", "/api/bootstrap")
                return
            except (urllib.error.URLError, ConnectionError):
                if self.proc.poll() is not None:
                    self.fail(self.proc.stderr.read().decode())
                time.sleep(.1)
        self.fail("本地服务未及时启动")

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        self.proc.wait(timeout=10)
        self.proc.stderr.close()
        self.tmp.cleanup()

    def request(self, method, path, value=None, raw=False, origin=None):
        data = json.dumps(value).encode() if value is not None else None
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=120) as res:
            blob = res.read()
            return blob if raw else json.loads(blob)

    @unittest.skipUnless(SOURCE_DATA, "需要父目录中的原始交付材料（台账、材料目录、填充测试文档）")
    def test_seed_confirm_export_repeat_and_persist(self):
        state = self.request("GET", "/api/bootstrap")
        self.assertEqual(10, len([c for c in state["categories"] if c["layer"]=="common"]))
        self.assertEqual(7, sum(c["enabled"] for c in state["categories"] if c["layer"]=="common"))
        self.assertEqual(4, len(state["industries"]))
        self.assertEqual(5, len(state["materials"]))
        self.assertEqual(55, sum(len(m["files"]) for m in state["materials"]))
        self.assertNotIn(str(APP.parent).encode(), json.dumps(state).encode())
        p = self.request("POST", "/api/projects", {"name": "端到端临时验收", "use_sample": True})
        self.assertEqual(7, len(p["mappings"]))
        self.assertTrue(all(not m["confirmed"] for m in p["mappings"]))
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request("POST", f"/api/projects/{p['id']}/export", {"mode": "draft"})
        self.assertIn(e.exception.code, (400, 409, 422))
        rows = [{k: m[k] for k in ("id", "locator_id", "material_ids", "action", "note")}
                | {"confirmed": True} for m in p["mappings"]]
        p = self.request("PATCH", f"/api/projects/{p['id']}/mappings", {"mappings": rows})
        self.assertTrue(all(m["confirmed"] for m in p["mappings"]))
        p = self.request("PATCH", f"/api/projects/{p['id']}/special-categories",
                         {"category_ids": [], "no_special_materials": True})
        with self.assertRaises(urllib.error.HTTPError):
            self.request("POST", f"/api/projects/{p['id']}/export", {"mode": "reviewed"})
        for _ in range(2):
            export = self.request("POST", f"/api/projects/{p['id']}/export", {"mode": "draft"})
            self.assertEqual(4, export["filled_count"])
            self.assertGreaterEqual(export["missing_count"] + export["skipped_count"], 3)
            data = self.request("GET", export["download_url"], raw=True)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = ET.fromstring(z.read("word/document.xml"))
                n = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
                # The original template retains two text-box drawings, but no images.
                self.assertEqual(55, len(xml.findall(".//a:blip", n)))
                self.assertIn("待补", "".join(xml.itertext()))
            report = self.request("GET", export["report_url"])
            self.assertNotIn(str(APP.parent), json.dumps(report, ensure_ascii=False))
        material = state["materials"][0]
        self.request("PATCH", f"/api/materials/{material['id']}", {"description": "临时测试修订"})
        refreshed = self.request("GET", f"/api/projects/{p['id']}")
        affected = [m for m in refreshed["mappings"] if material["id"] in m["material_ids"]]
        self.assertTrue(affected)
        self.assertTrue(all(not m["confirmed"] for m in affected))
        self.proc.terminate()
        self.proc.wait(timeout=10)
        self.proc.stderr.close()
        self.start()
        final = self.request("GET", "/api/bootstrap")
        self.assertEqual(1, len(final["projects"]))
        self.assertEqual(2, len(final["exports"]))
        self.assertEqual(5, len(final["materials"]))

    def test_reject_invalid_upload_and_cross_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request("POST", "/api/projects", {"name": "bad", "filename": "bad.docx", "data": base64.b64encode(b"bad").decode()})
        self.assertIn(e.exception.code, (400, 422))
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request("POST", "/api/industries", {"name": "blocked"}, origin="https://external.invalid")
        self.assertEqual(403, e.exception.code)
        self.assertEqual(4, len(self.request("GET", "/api/bootstrap")["industries"]))


if __name__ == "__main__":
    unittest.main()
