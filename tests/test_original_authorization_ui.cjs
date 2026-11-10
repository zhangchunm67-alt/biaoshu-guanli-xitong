/* Browser regression: retain buyer authorization form without selecting a company template. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,spawnSync}=require('node:child_process');
const {chromium}=require('playwright');
const app=path.resolve(__dirname,'..'),python=process.env.BID_TEST_PYTHON||path.join(app,'.venv/bin/python');
(async()=>{let server,browser;try{
server=spawn(python,['-B','-u','-c',`import sys,json,signal
sys.path.insert(0,'tests')
from test_original_authorization import OriginalAuthorizationTests
from server import create_server
c=OriginalAuthorizationTests();c.setUp();p,row=c.original_project()
c.store.update_mappings(p['id'],{'mappings':[dict(id=row['id'],action='missing')]})
draft=c.store.require('project',p['id']);draft['text_review_confirmed']=False
c.store.put('project',draft);c.store.db.commit();c.store.close()
s=create_server(0,str(c.tmp.name)+'/data',seed=False)
print(json.dumps(dict(port=s.server_port,project=p['id'],images=[m['id'] for m in c.images],root=c.tmp.name)),flush=True)
def stop(*args):raise KeyboardInterrupt
signal.signal(signal.SIGTERM,stop)
try:s.serve_forever()
except KeyboardInterrupt:pass
finally:s.server_close();s.store.close();c.tmp.cleanup()
`],{cwd:app,env:{...process.env,},stdio:['ignore','pipe','pipe']});
const fixture=await new Promise((resolve,reject)=>{server.stdout.once('data',b=>resolve(JSON.parse(b)));server.once('exit',c=>reject(Error('server '+c)));});
const base='http://127.0.0.1:'+fixture.port;
browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(30000);
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(base);await page.locator('[data-action=open-project][data-id="'+fixture.project+'"]').first().click();
await page.getByRole('heading',{name:'核对文字材料',exact:true}).waitFor();
const text=page.locator('.mapping-card').filter({has:page.getByRole('heading',{name:'法定代表人授权委托书',exact:true})});
assert.match(await text.locator('select[data-map-field=action]').innerText(),/保留采购原表，不另填模板/);
await text.locator('select[data-map-field=action]').selectOption('skip');
await page.getByRole('button',{name:'保存核对',exact:true}).first().click();
await text.getByText('已跳过系统填充',{exact:true}).waitFor();
await page.getByRole('button',{name:'下一步：核对图片材料',exact:true}).click();
const card=page.locator('.mapping-card').filter({has:page.getByRole('heading',{name:'授权人及代理人身份证',exact:true})});
assert.match(await card.locator('select[data-map-field=locator_id]').inputValue(),/^auth-body:/);
for(const id of fixture.images)await card.locator('input[data-map-material][value="'+id+'"]').check();
await page.getByRole('button',{name:'保存核对',exact:true}).first().click();
await page.waitForFunction(()=>!document.querySelector('#app')?.inert&&document.querySelector('.project-next [data-id=export]')?.disabled===false);
assert.doesNotMatch(await card.innerText(),/选定位置与栏目类别不符|请先选择并保存对应授权书文字模板/);
await card.locator('.inline-location-preview').scrollIntoViewIfNeeded();
await card.locator('.inline-location-preview img').waitFor();
await page.waitForFunction(()=>[...document.querySelectorAll('.inline-location-preview img')].some(i=>i.complete&&i.naturalWidth>0));
if(process.env.BID_AUTH_ORIGINAL_QA_DIR){fs.mkdirSync(process.env.BID_AUTH_ORIGINAL_QA_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.BID_AUTH_ORIGINAL_QA_DIR,'original-preview.png'),fullPage:true});}
await page.getByRole('button',{name:'下一步：导出标书',exact:true}).click();
await page.getByRole('button',{name:'导出',exact:true}).click();
const link=page.getByRole('link',{name:'Word 文件',exact:true}).first();await link.waitFor();
const response=await page.request.get(base+await link.getAttribute('href'));assert.ok(response.ok());
const file=path.join(fixture.root,'browser-export.docx');fs.writeFileSync(file,await response.body());
const check=spawnSync(python,['-B','-c',`from docx import Document
from docx.oxml.ns import qn
import sys
d=Document(sys.argv[1]);nodes=list(d.element.body)
texts=[''.join(t.text or '' for t in n.iter(qn('w:t'))) for n in nodes]
date=texts.index('日期：');end=texts.index('承诺与声明')
images=[i for i,n in enumerate(nodes) if list(n.iter(qn('w:drawing')))]
assert len(images)==2 and all(date<i<end for i in images)
assert texts.count('法定代表人授权委托书')==1
assert 'AUTH-BODY-END' not in '\\n'.join(texts)
assert '人工跳过：法定代表人授权委托书' not in '\\n'.join(texts)
`,file],{encoding:'utf8'});assert.equal(check.status,0,check.stderr);
assert.deepEqual(errors,[]);console.log('PASS: keep buyer form → select both IDs → original-form preview → export after date, no duplicate template.');
}finally{if(browser)await browser.close();if(server){server.kill('SIGTERM');await new Promise(resolve=>server.once('exit',resolve));}}})().catch(e=>{console.error(e);process.exitCode=1});
