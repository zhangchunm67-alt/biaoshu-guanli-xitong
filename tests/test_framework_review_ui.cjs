/* Isolated browser checks for the requirement and directory editors.
 * Backend semantics are covered by Python service tests; this suite supplies
 * explicit API fixtures to test browser edits, requests, conflicts and navigation.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawn} = require('node:child_process');
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require('/Users/mac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'); }
const app=path.resolve(__dirname,'..');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'bid-review-ui-'));
const python=process.env.BID_TEST_PYTHON||path.join(app,'.venv/bin/python');
const categories=['redline','qualification','score','spec','format'];
const label=['废标红线条款','强制资格要求','评分细则要点','商务技术规范','指定格式'];
const source={id:'src1',part:'主件',locator:'正文第 1 段',context:'供应商要求',text:'供应商应具有独立承担民事责任的能力。',index:0,row:0};
const clone=x=>JSON.parse(JSON.stringify(x));
const initial={id:'ui-review',name:'界面核对测试',filename:'采购原件.docx',revision:1,review_editable:true,requirements_confirmed:false,created_at:'2026-11-03T12:00:00Z',projects:{},project_id:null,notes:['核对附件'],sources:[source],requirements:categories.map((category,i)=>({id:'req'+i,category,label:label[i]+'测试',text:source.text,scope:'共同适用',references:[{...source,source_id:source.id}],note:''})),outline:[{id:'Book1',text:'第一章',level:1,requirement_ids:['req1']},{id:'Book2',text:'第一章子节',level:2,requirement_ids:[]},{id:'Book3',text:'第二章',level:1,requirement_ids:['req4'],locked_format:true}],model_review:{status:'completed',model:'gpt-5.6-terra',effort:'medium',notes:['旧复核说明不得显示']},artifacts:[{id:'word',kind:'word',name:'框架.docx',url:'/api/frameworks/ui-review/artifacts/word'},{id:'html',kind:'html',name:'预览.html',url:'/api/frameworks/ui-review/artifacts/html'}]};
function metrics(record){record.metrics=categories.map((key,i)=>({key,label:label[i],count:record.requirements.filter(r=>r.category===key).length,unit:'项'}));return record;}
let record=metrics(clone(initial)), conflict=false, requests=[], holdSave=false, releaseSave;
record.sources.push(...Array.from({length:100},(_,i)=>({...source,id:'extra'+i,text:'其他采购原文',context:'其他说明',locator:'附录第 '+(i+1)+' 段'})));
(async()=>{
 let server,browser;
 try{
  server=spawn(python,['-B','-u','-c','from server import create_server\nimport sys\ns=create_server(port=0,data_dir=sys.argv[1],seed=False);print(s.server_port,flush=True)\ntry:s.serve_forever()\nfinally:s.server_close();s.store.close()',path.join(root,'data')],{cwd:app,stdio:['ignore','pipe','pipe']});
  const port=await new Promise((resolve,reject)=>{server.stdout.once('data',b=>resolve(Number(b.toString().trim())));server.once('exit',code=>reject(Error('server exited '+code)));});
  const base='http://127.0.0.1:'+port;
  browser=await playwright.chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[];page.on('pageerror',err=>errors.push(err.message));
  await page.route('**/api/frameworks**',async route=>{
   const request=route.request(), url=new URL(request.url()),p=url.pathname;
   const json=(obj,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(obj)});
   if(p==='/api/frameworks')return json([record]);
   if(p.endsWith('/artifacts/html'))return route.fulfill({contentType:'text/html',body:'<!doctype html><main id="top"><section id="redline">采购要求预览</section><details><summary>本次生成的目录</summary><p class="desc">按采购原文分册</p><p class="desc">采购原文未要求商务标、技术标分册，保留统一目录；营业执照与公司介绍相邻排列。</p><p class="desc">按内容编排，递交方式以采购原文为准</p><p class="desc">商务标与技术标须分别密封递交。</p><ul class="outline"><li>预览章节</li></ul></details></main>'});
   if(request.method()==='GET')return json(record);
   const body=request.postDataJSON();requests.push({path:p,body});
   if(conflict){conflict=false;return json({error:'修改已过期，请重新读取最新版本。'},409);}
   assert.equal(body.revision,record.revision);
   if(p.endsWith('/requirements')){if(holdSave)await new Promise(resolve=>releaseSave=resolve);record.requirements=body.items.map((r,i)=>({...r,id:r.id||'added'+i}));record.requirements_confirmed=body.confirmed;record.revision++;metrics(record);return json(record);}
   if(p.endsWith('/outline')){record.outline=body.items.map((r,i)=>({...record.outline.find(old=>old.id===r.id),...r,id:r.id||'AddedSection'+i}));record.revision++;return json(record);}
   if(p.endsWith('/confirm'))return json({error:'测试：目录确认请求已接收'},400);
   return json({error:'未定义测试路由'},404);
  });
  await page.goto(base+'/?workflow=ui-review&step=2');
  await page.locator('.wf-requirement-reading').waitFor();
  assert.equal(await page.locator('.wf-step').count(),4);
  assert.equal(await page.locator('#wf-requirement-label').isVisible(),false);
  assert.equal(await page.locator('.wf-source-evidence').isVisible(),true);
  assert.equal(await page.locator('.wf-note').filter({hasText:'核对附件'}).count(),1);
  await page.locator('.wf-requirement-edit > summary').click();
  await page.locator('#wf-requirement-label').waitFor();
  await page.locator('.wf-source-picker > summary').click();
  assert.equal(await page.locator('#wf-source-count').isVisible(),true);
  await page.locator('#wf-source-query').fill('独立');
  assert.equal(await page.locator('#wf-source-count').isVisible(),false);
  assert.equal(await page.locator('#wf-requirement-source option').count(),2);
  assert.equal(await page.locator('.wf-metric').count(),5);
  assert.equal(await page.locator('.wf-model-status,[data-wf=retry-model]').count(),0);
  assert.doesNotMatch(await page.locator('main').innerText(),/模型复核|重新复核|gpt-5.6-terra|旧复核说明/);
  if(process.env.BID_REVIEW_QA_DIR){fs.mkdirSync(process.env.BID_REVIEW_QA_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.BID_REVIEW_QA_DIR,'requirements.png'),fullPage:true});}
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.setViewportSize({width:1440,height:1100});
  await page.locator('#wf-requirement-label').fill('已核实的要求');
  holdSave=true;
  await page.locator('[data-wf=save-requirements]').click();
  await page.waitForFunction(()=>document.querySelector('.wf-workspace')?.inert===true);
  // A delayed response must freeze the newly rendered editor, not a detached node.
  assert.equal(await page.locator('#wf-requirement-label').evaluate(el=>{el.focus();return document.activeElement===el;}),false);
  while(!releaseSave)await new Promise(resolve=>setTimeout(resolve,10));
  holdSave=false;releaseSave();releaseSave=null;
  await page.waitForFunction(()=>document.querySelector('#wf-requirements-status')?.textContent==='要求待确认');
  await page.waitForFunction(()=>document.querySelector('.wf-workspace')?.inert===false);
  assert.equal(record.requirements[0].label,'已核实的要求');
  assert.equal(record.requirements_confirmed,false);
  assert.equal(requests.at(-1).body.items.some(r=>'_key' in r),false);
  await page.locator('[data-wf=metric][data-id=qualification]').click();
  await page.locator('[data-wf=add-requirement]').click();
  await page.locator('#wf-source-query').fill('独立');
  await page.locator('#wf-requirement-source').selectOption('src1');
  await page.locator('[data-wf=add-reference]').click();
  assert.equal(await page.locator('#wf-requirement-text').inputValue(),source.text);
  await page.locator('#wf-requirement-label').fill('补充资格');
  assert.equal(await page.locator('.wf-confirm-gate #wf-notes-ack').count(),1);
  await page.locator('.wf-confirm-gate #wf-notes-ack').check();
  await page.locator('[data-wf=confirm-requirements]').click();
  await page.locator('[data-wf=confirm]:enabled').waitFor();
  assert.equal(record.requirements.length,6);
  assert.equal(record.requirements_confirmed,true);
  assert.equal(requests.at(-1).body.notes_acknowledged,true);
  assert.deepEqual(requests.at(-1).body.acknowledged_removed_ids,[]);
  assert.equal(await page.locator('.wf-preview-frame').count(),1);
  // Already-confirmed in-progress files must advance with “下一步”, not re-block on notes.
  await page.locator('.wf-step[data-id="2"]').click();
  await page.locator('.wf-requirement-reading').waitFor();
  assert.equal(await page.locator('[data-wf=to-outline]').count(),1);
  assert.equal(await page.locator('[data-wf=confirm-requirements]').count(),0);
  assert.equal(await page.locator('#wf-notes-ack').count(),0);
  await page.locator('[data-wf=to-outline]').click();
  await page.locator('[data-wf=confirm]:enabled').waitFor();
  if(process.env.BID_REVIEW_QA_DIR)await page.screenshot({path:path.join(process.env.BID_REVIEW_QA_DIR,'outline.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.setViewportSize({width:1440,height:1100});
  assert.equal(await page.locator('#wf-outline-requirements').count(),0);
  assert.equal(await page.locator('#wf-outline-title').isVisible(),false);
  assert.equal(await page.frameLocator('iframe').locator('#redline').count(),0);
  const preview=await page.frameLocator('iframe').locator('main').innerText();
  assert.doesNotMatch(preview,/按采购原文分册|保留统一目录|按内容编排/);
  assert.match(preview,/递交方式以采购原文为准/);
  assert.match(preview,/商务标与技术标须分别密封递交/);
  await page.locator('.wf-outline-editor > summary').click();
  assert.match(await page.locator('.wf-outline-properties').innerText(),/调整会同时移动下属章节/);
  assert.doesNotMatch(await page.locator('.wf-outline-properties').innerText(),/采购方指定的原表随所属章节移动/);
  assert.equal(await page.locator('#wf-outline-status').isVisible(),false);
  // Indent and outdent the entire selected subtree, then add and persist chapters.
  await page.locator('[data-wf=select-outline][data-id=Book3]').click();
  assert.doesNotMatch(await page.locator('.wf-outline-properties').innerText(),/调整会同时移动下属章节/);
  assert.match(await page.locator('.wf-outline-properties').innerText(),/采购方指定的原表随所属章节移动/);
  await page.locator('[data-wf=outline-indent]').click();
  assert.equal(await page.locator('#wf-outline-status').isVisible(),true);
  assert.match(await page.locator('.wf-outline-properties').innerText(),/第 2 级/);
  await page.locator('[data-wf=outline-outdent]').click();
  assert.match(await page.locator('.wf-outline-properties').innerText(),/第 1 级/);
  await page.locator('[data-wf=outline-add-sibling]').click();
  await page.locator('#wf-outline-title').fill('自定义章节');
  await page.locator('[data-wf=outline-up]').click();
  await page.locator('[data-wf=outline-add-child]').click();
  await page.locator('#wf-outline-title').fill('自定义子节');
  assert.equal(await page.locator('[data-wf=confirm]').isDisabled(),true);
  // Reject leaving with unsaved work and ensure it stays in the editor.
  page.once('dialog',dialog=>dialog.dismiss());
  await page.locator('.wf-step[data-id="2"]').click();
  assert.equal(await page.locator('#wf-outline-title').inputValue(),'自定义子节');
  await page.locator('[data-wf=save-outline]').click();
  await page.locator('[data-wf=confirm]:enabled').waitFor();
  assert.deepEqual(record.outline.map(r=>[r.text,r.level]),[['第一章',1],['第一章子节',2],['自定义章节',1],['自定义子节',2],['第二章',1]]);
  assert.ok(requests.at(-1).body.items.every(r=>!('requirement_ids' in r)));
  assert.deepEqual(record.outline[0].requirement_ids,['req1']);
  await page.reload();await page.locator('.wf-outline-editor > summary').click();await page.locator('#wf-outline-title').waitFor();
  assert.equal(await page.locator('.wf-outline-choice').count(),5);
  // Optimistic conflict must not discard local changes.
  await page.locator('#wf-outline-title').fill('冲突时保留的名称');conflict=true;
  await page.locator('[data-wf=save-outline]').click();await page.locator('.wf-error').waitFor();
  assert.match(await page.locator('.wf-error').innerText(),/修改已过期/);
  assert.equal(await page.locator('#wf-outline-title').inputValue(),'冲突时保留的名称');
  page.once('dialog',dialog=>dialog.accept());await page.locator('[data-wf=reload-review]').click();
  await page.waitForFunction(()=>document.querySelector('#wf-outline-title')?.value==='第一章');
  // Deleting a custom subtree keeps the remaining hierarchy and lets the server validate mappings.
  const customId=record.outline.find(r=>r.text==='自定义章节').id;
  await page.locator('[data-wf=select-outline][data-id="'+customId+'"]').click();
  page.once('dialog',dialog=>dialog.accept());await page.locator('[data-wf=outline-delete]').click();
  await page.locator('[data-wf=save-outline]').click();await page.locator('[data-wf=confirm]:enabled').waitFor();
  assert.equal(record.outline.length,3);
  // Final confirmation must identify the exact revision shown in the preview.
  await page.locator('[data-wf=confirm]').click();await page.locator('.wf-error').waitFor();
  assert.equal(requests.at(-1).path,'/api/frameworks/ui-review/confirm');
  assert.equal(requests.at(-1).body.revision,record.revision);
  // Old model states neither render obsolete notices nor gate manual confirmation.
  for(const status of ['failed','completed']){
    record.model_review={status,model:'gpt-5.6-terra',message:'旧复核失败原因不得显示',notes:['旧复核说明不得显示']};
    record.requirements_confirmed=false;
    await page.goto(base+'/?workflow=ui-review&step=2');await page.locator('.wf-requirement-reading').waitFor();
    assert.equal(await page.locator('.wf-model-status,[data-wf=retry-model]').count(),0);
    assert.doesNotMatch(await page.locator('main').innerText(),/模型复核|重新复核|gpt-5.6-terra|旧复核/);
    assert.equal(await page.locator('.wf-note').filter({hasText:'核对附件'}).count(),1);
    // Collapsed coverage must not contain hidden old model notes either.
    assert.doesNotMatch(await page.locator('main').textContent(),/旧复核/);
    await page.locator('.wf-confirm-gate #wf-notes-ack').check();
    await page.locator('[data-wf=confirm-requirements]').click();
    await page.locator('[data-wf=confirm]:enabled').waitFor();
    assert.equal(record.requirements_confirmed,true);
    assert.equal(requests.at(-1).path,'/api/frameworks/ui-review/requirements');
    assert.equal(requests.at(-1).body.confirmed,true);
    assert.equal(requests.at(-1).body.notes_acknowledged,true);
  }
  assert.equal(requests.some(request=>request.path.endsWith('/model-review')),false);
  // Legacy records stay readable without pretending that online edits are available.
  record={...record,review_editable:false,revision:0,requirements_confirmed:true,requirements:[],sources:[],model_review:{status:'legacy',message:'历史结果'}};
  await page.goto(base+'/?workflow=ui-review&step=3');await page.locator('[data-wf=confirm]:enabled').waitFor();
  assert.equal(await page.locator('.wf-outline-editor').count(),0);
  assert.equal(await page.locator('.wf-preview-frame').count(),1);
  assert.deepEqual(errors,[]);
  console.log('Framework review UI: editing, source selection, hierarchy, revision conflict, persistence, legacy-model removal, manual confirmation and legacy checks passed.');
 }finally{
  if(releaseSave)releaseSave();
  if(browser)await browser.close();
  if(server){server.kill('SIGTERM');await new Promise(resolve=>{server.once('exit',resolve);setTimeout(resolve,3000);});}
  fs.rmSync(root,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
