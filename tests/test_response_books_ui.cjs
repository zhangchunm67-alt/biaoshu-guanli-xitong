/* Real backend/browser check: only buyer-required books get separate directories. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn,spawnSync}=require('node:child_process');
let chromium;
try { ({chromium}=require('playwright')); }
catch { ({chromium}=require('/Users/mac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')); }
const app=path.resolve(__dirname,'..'),root=fs.mkdtempSync(path.join(os.tmpdir(),'bid-books-ui-'));
const python=path.join(app,'.venv/bin/python');
const setup=spawnSync(python,['-B','-c',`
import base64,json,os,sys
from pathlib import Path
from docx import Document
from server import Store
import framework_service as service
root=Path(sys.argv[1]);store=Store(root);ids={}
try:
 for split in (False,True):
  doc=Document()
  for line in ['内容服务采购邀请函','特邀请贵公司参加采购。','报价人应准备投标文件，包括公司介绍、历史项目、方案、报价。']:
   doc.add_paragraph(line)
  if split:doc.add_paragraph('投标文件须分为商务标和技术标，分别成册。')
  source=root/('source-'+str(split)+'.docx');doc.save(source)
  r=service.create(store,{'name':str(split),'filename':source.name,'data':base64.b64encode(source.read_bytes()).decode()})
  service.revise(store,r['id'],{'revision':r['revision'],'items':r['requirements'],'confirmed':True,'notes_acknowledged':True,'acknowledged_removed_ids':[]},'requirements')
  ids[str(split)]=r['id']
 print(json.dumps(ids))
finally:store.close()
`,root],{cwd:app,encoding:'utf8',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
let server,browser;
(async()=>{try{
 assert.equal(setup.status,0,setup.stderr);const ids=JSON.parse(setup.stdout);
 server=spawn(python,['-B','server.py','--port','0','--data-dir',root],{cwd:app,stdio:['ignore','pipe','pipe']});
 const base=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(new Error('server startup timed out')),12000);
  server.stdout.on('data',b=>{output+=b;const m=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});
  server.once('exit',code=>{clearTimeout(timer);reject(new Error('server exited '+code));});
 });
 browser=await chromium.launch({headless:true});const page=await browser.newPage();const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/?workflow='+ids.True+'&step=3');
 await page.getByRole('region',{name:'分册目录'}).waitFor();
 await page.getByRole('button',{name:/商务标目录/}).click();
 assert.equal(await page.locator('#wf-outline-title').inputValue(),'商务标');
 await page.getByRole('button',{name:/技术标目录/}).click();
 assert.equal(await page.locator('#wf-outline-title').inputValue(),'技术标');
 let frame=page.frameLocator('iframe');
 const business=await frame.locator('[data-book-role="commercial"] li').allTextContents();
 assert.equal(business[business.indexOf('公司介绍')-1],'营业执照');
 assert.ok((await frame.locator('[data-book-role="technical"] li').allTextContents()).includes('项目方案'));
 await page.goto(base+'/?workflow='+ids.False+'&step=3');
 await page.locator('[data-wf=confirm]:enabled').waitFor();
 assert.equal(await page.locator('#wf-outline-title').isVisible(),false);
 assert.equal(await page.getByRole('region',{name:'分册目录'}).count(),0);
 frame=page.frameLocator('iframe');await frame.locator('ul.outline').waitFor({state:'attached'});
 assert.equal(await frame.locator('[data-book-role]').count(),0);
 const unified=await frame.locator('ul.outline li').allTextContents();
 assert.equal(unified[unified.indexOf('公司介绍')-1],'营业执照');
 assert.deepEqual(errors,[]);
 console.log('Response book UI: required split, unified fallback, book selection and identity adjacency passed.');
}finally{
 if(browser)await browser.close();
 if(server){const stopped=new Promise(resolve=>server.once('exit',resolve));server.kill('SIGTERM');await stopped;}
 fs.rmSync(root,{recursive:true,force:true});
}})().catch(error=>{console.error(error);process.exitCode=1});
