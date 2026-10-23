import base64
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from docx import Document
from server import Store
import engine

class ExportJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='bid-export-jobs-')
        self.store=Store(self.temp.name)
        doc=Document();doc.add_heading('公司介绍',1);buf=io.BytesIO();doc.save(buf)
        p=self.store.create_project({'name':'后台导出测试','filename':'test.docx','data':base64.b64encode(buf.getvalue()).decode()})
        p=self.store.require('project',p['id'])
        for row in p['mappings']: row.update(action='skip',confirmed=True,note='测试跳过')
        p=self.store.sync_material_matches(p)
        for row in p['mappings']: row['confirmed']=True
        p['special_selection_confirmed']=True
        self.store.put('project',p);self.store.db.commit();self.key=p['id']
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def start(self):
        with self.store.lock:return self.store.export_jobs.start(self.key,{'mode':'draft'})
    def wait(self):
        for _ in range(200):
            with self.store.lock:job=self.store.export_jobs.list()[0]
            if job['status'] in {'completed','failed'}:return job
            time.sleep(.01)
        self.fail('Job did not finish')
    def test_background_dedup_snapshot_download_and_persistence(self):
        entered=threading.Event();release=threading.Event();original=engine.export_document
        def delayed(*args,**kwargs):
            entered.set();release.wait(5);return original(*args,**kwargs)
        with patch('engine.export_document',side_effect=delayed):
            first=self.start();self.assertTrue(entered.wait(2))
            self.assertEqual(first['id'],self.start()['id'])
            # Reading and editing the DB must remain possible during document generation.
            with self.store.lock:
                p=self.store.require('project',self.key);p['mappings'][0]['note']='导出期间新增备注'
                self.store.put('project',p);self.store.db.commit()
                active=self.store.export_jobs.list()[0]
            self.assertEqual('running',active['status']);self.assertGreater(active['remaining_seconds'],0)
            release.set();job=self.wait()
        self.assertEqual('completed',job['status']);self.assertEqual(100,job['progress'])
        self.assertTrue(self.store.path('exports/'+job['result']['id']+'.docx').is_file())
        self.assertEqual('导出期间新增备注',self.store.require('project',self.key)['mappings'][0]['note'])
        self.store.close();self.store=Store(self.temp.name)
        self.assertEqual(job['result'],self.store.export_jobs.list()[0]['result'])
    def test_failure_and_service_restart_recovery(self):
        with patch('engine.export_document',side_effect=ValueError('测试生成失败')):
            self.start();job=self.wait()
        self.assertEqual('failed',job['status']);self.assertIn('测试生成失败',job['error'])
        job.update(status='running');self.store.put('export_job',job);self.store.db.commit()
        self.store.close();self.store=Store(self.temp.name)
        self.assertEqual('failed',self.store.export_jobs.list()[0]['status'])
        self.assertIn('本地服务曾停止',self.store.export_jobs.list()[0]['error'])
