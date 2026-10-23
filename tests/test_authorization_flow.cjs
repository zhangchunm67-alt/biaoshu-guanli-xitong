/* Uses isolated fixture data from prepare_authorization_cases.py; never writes daily materials. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const tmp=process.env.BID_AUTH_QA_DIR;
if(!tmp)throw new Error('BID_AUTH_QA_DIR required');
const fixture=JSON.parse(fs.readFileSync(path.join(tmp,'cases.json'),'utf8'));
const app=path.resolve(__dirname,'..');
(async()=>{
 let server,browser;
 try{
  server=spawn(process.env.BID_TEST_PYTHON,['-u','-c',`from server import create_server
import sys
s=create_server(port=0,data_dir=sys.argv[1]);print(s.server_port,flush=True)
try:s.serve_forever()
finally:s.server_close();s.store.close()`,path.join(tmp,'data')],{cwd:app,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
  const port=await new Promise((res,rej)=>{server.stdout.once('data',b=>res(Number(b.toString().trim())));server.once('exit',c=>rej(new Error('server '+c)));});
  const base='http://127.0.0.1:'+port;
  browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(15000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const result=[];
  for(const c of fixture.cases){
   await page.goto(base);
   await page.getByRole('button',{name:'授权书验证_'+c.label,exact:true}).click();
   const card=page.locator('.mapping-card').filter({has:page.getByRole('heading',{name:'法定代表人（负责人）授权委托书',exact:true})});
   assert.equal(await card.locator('[data-auth-field]').count(),0);
   assert.doesNotMatch(await card.innerText(),/填写授权书正文|授权项目名称|授权书签署日期|正文与人员核对/);
   for(const id of fixture.ids)await card.locator('input[data-map-material][value="'+id+'"]').check();
   await card.getByRole('checkbox',{name:'我已逐项核对此匹配',exact:true}).check();
   await page.getByRole('button',{name:'保存核对',exact:true}).first().click();
   await page.getByRole('button',{name:'下一步：选择行业分类',exact:true}).waitFor({state:'visible'});
   await page.waitForFunction(()=>!document.querySelector('[data-action="save-project"]')?.disabled);
   assert.equal(await card.getByRole('checkbox',{name:'我已逐项核对此匹配',exact:true}).isChecked(),true);
   assert.equal(await card.locator('.issue-list').count(),0);
   assert.equal(await card.locator('input[data-map-material]:checked').count(),2);
   assert.equal(await card.locator('.mapping-preview-image img').count()>0,true);
   await page.screenshot({path:path.join(tmp,c.label+'-核对页.png'),fullPage:true});
   await page.getByRole('button',{name:'下一步：选择行业分类',exact:true}).click();
   await page.getByRole('checkbox',{name:'本项目无需专用材料',exact:true}).check();
   await page.getByRole('button',{name:'保存行业并继续',exact:true}).click();
   // With no special materials the application moves directly to export.
   await page.getByRole('button',{name:'导出',exact:true}).click();
   await page.getByRole('link',{name:'下载 Word 文件',exact:true}).waitFor({state:'visible',timeout:30000});
   const download=page.waitForEvent('download');
   await page.getByRole('link',{name:'下载 Word 文件',exact:true}).click();
   const file=await download;await file.saveAs(path.join(tmp,c.label+'-图片填充结果.docx'));
   result.push({case:c.label,browser_saved:true,downloaded:true});
   await page.getByRole('button',{name:'关闭',exact:true}).click();
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(tmp,'browser-result.json'),JSON.stringify({result,errors},null,2));
  console.log(JSON.stringify({result,errors}));
 }finally{if(browser)await browser.close();if(server)server.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exitCode=1;});
