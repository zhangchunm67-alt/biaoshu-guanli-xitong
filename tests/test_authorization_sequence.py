"""Reviewed authorization text precedes both identity attachments."""
import base64
import io
import tempfile
import json
import threading
import urllib.request
import urllib.error
import unittest
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
from PIL import Image
from server import Store, SUBJECT, create_server

class AuthorizationSequenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='bid-auth-sequence-')
        self.store=Store(Path(self.tmp.name)/'data',seed=False)
        for c in self.store.all('category'):
            c['enabled']=c['id'] in {'authorization','legal_identity'}
            self.store.put('category',c)
        self.store.put('category',dict(id='text_template',name='文书模板',layer='special',industry_id='',aliases=['授权委托书'],enabled=True,sort_order=100))
        self.body=self.store.material(dict(name='法定代表人授权书模板',category_id='text_template',mode='text',subject=SUBJECT,valid_until='长期',content='本人张三系测试单位法定代表人，现委托李四，为本项目代理人。\nAUTH-BODY-END：签字盖章日期待核对。'))
        self.images=[]
        for name,category,color in [('张三（法定代表人）身份证','legal_identity','navy'),('李四（委托代理人）身份证','authorization','red')]:
            b=io.BytesIO();Image.new('RGB',(120,80),color).save(b,format='PNG')
            self.images.append(self.store.material(dict(name=name,category_id=category,mode='page',subject=SUBJECT,valid_until='长期',files=[dict(name='test.png',data=base64.b64encode(b.getvalue()).decode())])))
        category=self.store.require('category','legal_identity');category['enabled']=False;self.store.put('category',category)
        d=Document();d.add_heading('法定代表人授权委托书',1);d.add_paragraph('采购方指定签字盖章栏，原文必须保留。');d.add_heading('报价表',1);d.add_paragraph('QUOTE-BOUNDARY')
        b=io.BytesIO();d.save(b)
        self.project=self.store.create_project(dict(name='授权书顺序验收',filename='test.docx',data=base64.b64encode(b.getvalue()).decode()))
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def prepare(self):
        p=self.store.prepare_text_materials(self.project['id'],text_first=True)
        self.assertEqual('text-first',p['material_flow'])
        row=next(r for r in p['mappings'] if r['category_id']=='text_template')
        p=self.store.update_mappings(p['id'],{'mappings':[dict(id=row['id'],action='fill',material_ids=[self.body['id']])]})
        auth=next(r for r in p['mappings'] if r['category_id']=='authorization')
        self.assertTrue(auth['locator_id'].startswith('auth-body:'),p['headings'])
        return p,auth,row
    def test_body_then_both_ids_with_original_and_next_section_untouched(self):
        p,auth,row=self.prepare()
        p=self.store.update_mappings(p['id'],{'mappings':[dict(id=auth['id'],action='fill',material_ids=[m['id'] for m in reversed(self.images)])]})
        self.assertEqual('ready',next(r for r in p['mappings'] if r['id']==auth['id'])['status'])
        before=self.store.path(self.store.require('project',p['id'])['template_storage']).read_bytes()
        for _ in range(2):
            output=self.store.export(p['id'],{'mode':'draft'})
            d=Document(self.store.path('exports/'+output['id']+'.docx'))
            nodes=list(d.element.body);texts=[''.join(n.itertext()) for n in nodes]
            body=next(i for i,s in enumerate(texts) if 'AUTH-BODY-END' in s)
            quote=next(i for i,s in enumerate(texts) if 'QUOTE-BOUNDARY' in s)
            pictures=[i for i,n in enumerate(nodes) if list(n.iter(qn('w:drawing')))]
            self.assertEqual(2,len(pictures));self.assertTrue(all(body<i<quote for i in pictures))
            self.assertEqual(1,sum('AUTH-BODY-END' in p.text for p in d.paragraphs))
            self.assertIn('采购方指定签字盖章栏，原文必须保留。',[p.text for p in d.paragraphs])
        self.assertEqual(before,self.store.path(self.store.require('project',p['id'])['template_storage']).read_bytes())
    def test_attachment_needs_both_roles_and_body_cannot_be_removed_silently(self):
        p,auth,row=self.prepare()
        p=self.store.update_mappings(p['id'],{'mappings':[dict(id=auth['id'],action='fill',material_ids=[self.images[0]['id']])]})
        self.assertEqual('blocked',next(r for r in p['mappings'] if r['id']==auth['id'])['status'])
        with self.assertRaises(ValueError):self.store.export(p['id'],{'mode':'draft'})
        p=self.store.update_mappings(p['id'],{'mappings':[dict(id=row['id'],material_ids=[],action='missing')]})
        self.assertEqual('',next(r for r in p['mappings'] if r['id']==auth['id'])['locator_id'])
    def test_preview_composes_reviewed_body_without_mutating_project(self):
        p,auth,row=self.prepare()
        snapshot=self.store.preview_snapshot(p['id'],dict(mapping_id=auth['id'],locator_id=auth['locator_id'],action='fill',material_ids=[m['id'] for m in self.images]))
        self.assertEqual(self.body['id'],snapshot['authorization_body']['id'])
        before=snapshot['template'].read_bytes()
        result=self.store.location_preview.describe(snapshot)
        self.assertGreater(result['page_count'],0);self.assertTrue(result['markers'])
        self.assertEqual(before,snapshot['template'].read_bytes())

    def test_composed_preview_http_access_expires_when_body_changes(self):
        p,auth,_=self.prepare()
        httpd=create_server(0,self.store.root,seed=False)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{httpd.server_port}'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        endpoint=base+'/api/projects/'+p['id']+'/location-preview'
        try:
            request=urllib.request.Request(endpoint,data=json.dumps(dict(mapping_id=auth['id'],locator_id=auth['locator_id'],action='fill',material_ids=[])).encode(),headers={'Content-Type':'application/json','Origin':base})
            with opener.open(request,timeout=20) as response:result=json.load(response)
            url=endpoint+'/'+result['preview_id']+'/pages/1'
            with opener.open(url,timeout=10) as response:self.assertTrue(response.read().startswith(b'\x89PNG'))
            body=self.store.require('material',self.body['id']);body['revision']=body.get('revision',0)+1
            body['content']+='\n已变更正文';self.store.put('material',body);self.store.db.commit()
            with self.assertRaises(urllib.error.HTTPError) as error:opener.open(url,timeout=10)
            self.assertEqual(400,error.exception.code)
        finally:
            httpd.shutdown();thread.join();httpd.server_close();httpd.store.close()

    def test_names_must_match_the_reviewed_body(self):
        p,auth,_=self.prepare()
        material=self.store.require('material',self.images[0]['id'])
        material['name']='王五（法定代表人）身份证';self.store.put('material',material);self.store.db.commit()
        p=self.store.update_mappings(p['id'],{'mappings':[dict(id=auth['id'],action='fill',material_ids=[m['id'] for m in self.images])]})
        row=next(r for r in p['mappings'] if r['id']==auth['id'])
        self.assertEqual('blocked',row['status']);self.assertTrue(any('姓名不一致' in issue for issue in row['issues']))

    def test_changed_text_material_requires_review_again(self):
        p,_,_=self.prepare()
        self.assertTrue(p['text_review_confirmed'])
        self.store.material({'content':'重新修改的授权正文'},self.body['id'])
        p=self.store.public_project(self.store.require('project',p['id']))
        self.assertFalse(p['text_review_confirmed'])
        with self.assertRaisesRegex(ValueError,'保存文字材料核对'):
            self.store.export(p['id'],{'mode':'draft'})

if __name__=='__main__':unittest.main()
