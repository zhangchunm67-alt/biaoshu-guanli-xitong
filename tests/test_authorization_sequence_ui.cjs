/* Real server + browser: reviewed authorization text, then both identity images. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,spawnSync}=require('node:child_process');
let pw;try{pw=require('playwright');}catch{pw=require('/Users/mac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');}
const app=path.resolve(__dirname,'..'),python=process.env.BID_TEST_PYTHON||path.join(app,'.venv/bin/python');
(async()=>{let server,browser,fixtureRoot;try{
server=spawn(python,['-B','-u','-c',`import sys,json,signal
sys.path.insert(0,'tests')
from test_authorization_sequence import AuthorizationSequenceTests
from server import create_server
c=AuthorizationSequenceTests();c.setUp();c.store.close()
s=create_server(0,str(c.tmp.name)+'/data',seed=False)
print(json.dumps(dict(port=s.server_port,project=c.project['id'],body=c.body['id'],images=[m['id'] for m in c.images],root=c.tmp.name)),flush=True)
def stop(*args):raise KeyboardInterrupt
signal.signal(signal.SIGTERM,stop)
try:s.serve_forever()
except KeyboardInterrupt:pass
finally:s.server_close();s.store.close();c.tmp.cleanup()
`],{cwd:app,env:{...process.env,},stdio:['ignore','pipe','pipe']});
const fixture=await new Promise((resolve,reject)=>{server.stdout.once('data',b=>resolve(JSON.parse(b)));server.once('exit',c=>reject(Error('server '+c)));});
fixtureRoot=fixture.root;
const base='http://127.0.0.1:'+fixture.port;
browser=await pw.chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(30000);
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(base);await page.locator('[data-action=open-project][data-id="'+fixture.project+'"]').first().click();
await page.getByRole('heading',{name:'核对文字材料',exact:true}).waitFor();
const nextImages=page.getByRole('button',{name:'下一步：核对图片材料',exact:true});
assert.equal(await nextImages.isEnabled(),true);
await nextImages.click();
await page.locator('.material-completion-notice').waitFor();
assert.match(await page.locator('.material-completion-notice').innerText(),/文字材料|授权/);
await page.waitForFunction(()=>document.activeElement?.matches('.mapping-card[data-mapping], [data-action="save-project"]'));
assert.equal(await page.getByRole('heading',{name:'核对文字材料',exact:true}).isVisible(),true);
assert.equal(await page.getByRole('heading',{name:'核对图片材料',exact:true}).count(),0);
await page.locator('input[data-map-material][value="'+fixture.body+'"]').check();
await page.locator('.mapping-text-preview').waitFor();
await nextImages.click();
await page.waitForFunction(()=>document.activeElement?.dataset.action==='save-project');
assert.match(await page.locator('.material-completion-notice').innerText(),/未保存/);
assert.equal(await page.getByRole('heading',{name:'核对文字材料',exact:true}).isVisible(),true);
assert.equal(await page.getByRole('heading',{name:'核对图片材料',exact:true}).count(),0);
assert.equal(await page.locator('input[data-map-material][value="'+fixture.body+'"]').isChecked(),true);
await page.getByRole('button',{name:'保存核对',exact:true}).first().click();
await page.getByRole('button',{name:'下一步：核对图片材料',exact:true}).click();
await page.getByRole('heading',{name:'核对图片材料',exact:true}).waitFor();
const card=page.locator('.mapping-card').filter({has:page.getByRole('heading',{name:'授权人及代理人身份证',exact:true})});
assert.match(await card.locator('select[data-map-field=locator_id]').inputValue(),/^auth-body:/);
for(const id of fixture.images)await card.locator('input[data-map-material][value="'+id+'"]').check();
await page.getByRole('button',{name:'保存核对',exact:true}).first().click();
await page.waitForFunction(()=>!document.querySelector('#app')?.inert&&document.querySelector('.project-next [data-id=export]')?.disabled===false);
await card.locator('.inline-location-preview').scrollIntoViewIfNeeded();
await card.locator('.inline-location-preview img').waitFor().catch(async error=>{console.error(await card.locator('.inline-location-preview').innerText());throw error;});
await page.waitForFunction(()=>[...document.querySelectorAll('.inline-location-preview img')].some(i=>i.complete&&i.naturalWidth>0));
if(process.env.BID_AUTH_SEQUENCE_QA_DIR){fs.mkdirSync(process.env.BID_AUTH_SEQUENCE_QA_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.BID_AUTH_SEQUENCE_QA_DIR,'preview.png'),fullPage:true});}
await page.getByRole('button',{name:'下一步：导出标书',exact:true}).click();
await page.getByRole('button',{name:'导出',exact:true}).click();
await page.getByRole('link',{name:'Word 文件',exact:true}).first().waitFor();
const href=await page.getByRole('link',{name:'Word 文件',exact:true}).first().getAttribute('href');
const response=await page.request.get(base+href);assert.ok(response.ok());
const file=path.join(fixture.root,'browser-export.docx');fs.writeFileSync(file,await response.body());
const check=spawnSync(python,['-B','-c',`from docx import Document
from docx.oxml.ns import qn
import sys
d=Document(sys.argv[1]);nodes=list(d.element.body)
texts=[''.join(n.itertext()) for n in nodes]
b=next(i for i,t in enumerate(texts) if 'AUTH-BODY-END' in t)
q=next(i for i,t in enumerate(texts) if 'QUOTE-BOUNDARY' in t)
images=[i for i,n in enumerate(nodes) if list(n.iter(qn('w:drawing')))]
assert len(images)==2 and all(b<i<q for i in images)
assert [p.text for p in d.paragraphs if p.text in ('授权人身份证','代理人身份证')]==['授权人身份证','代理人身份证']
`,file],{encoding:'utf8'});assert.equal(check.status,0,check.stderr);
if(process.env.BID_AUTH_SEQUENCE_QA_DIR)fs.copyFileSync(file,path.join(process.env.BID_AUTH_SEQUENCE_QA_DIR,'authorization-example.docx'));
assert.deepEqual(errors,[]);console.log('PASS: text first → automatic authorization-bottom target → both IDs → inline preview → Word body/image order, original boundaries intact.');
}finally{if(browser)await browser.close();if(server&&server.exitCode===null){const stopped=new Promise(resolve=>server.once('exit',resolve));server.kill('SIGTERM');await stopped;}if(fixtureRoot)fs.rmSync(fixtureRoot,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exitCode=1});
