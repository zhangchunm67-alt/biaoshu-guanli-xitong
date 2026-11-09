/* Full live HTTP/browser acceptance, isolated synthetic procurement and materials.
 * No procurement/framework/export API is mocked. BID_CHAIN_QA_DIR retains evidence.
 */
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn,spawnSync}=require('node:child_process');
let pw;try{pw=require('playwright');}catch{pw=require('/Users/mac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');}
const app=path.resolve(__dirname,'..'),python=process.env.BID_TEST_PYTHON||path.join(app,'.venv/bin/python');
const root=process.env.BID_CHAIN_QA_DIR||fs.mkdtempSync(path.join(os.tmpdir(),'bid-chain-'));
fs.mkdirSync(root,{recursive:true});
const env={...process.env,PYTHONDONTWRITEBYTECODE:'1'};
const fixture=spawnSync(python,['-B','-c',String.raw`
import io,json,base64,sys
from pathlib import Path
from docx import Document
from docx.shared import Pt
from PIL import Image,ImageDraw
from server import Store,SUBJECT
root=Path(sys.argv[1]);store=Store(root/'data',seed=False)
for c in store.all('category'):
    c['enabled']=c['id'] in {'business_license','I01'};store.put('category',c)
image=Image.new('RGB',(600,400),'#edf4ff');draw=ImageDraw.Draw(image)
draw.text((40,130),'TEST LICENSE - NOT REAL EVIDENCE',fill='navy',font_size=22)
buf=io.BytesIO();image.save(buf,format='PNG')
license=store.material(dict(name='验收测试营业执照（非真实证照）',category_id='business_license',subject=SUBJECT,mode='page',valid_until='长期',files=[dict(name='TEST-LICENSE.png',data=base64.b64encode(buf.getvalue()).decode())]))
case=store.material(dict(name='验收测试案例（非真实业绩）',category_id='I01',subject=SUBJECT,mode='text',valid_until='长期',client_name='测试客户',content='PHASE1-TEXT-ALPHA：仅用于系统验收，并非真实业绩。'))
doc=Document();doc.styles['Normal'].font.name='Songti SC';doc.styles['Normal'].font.size=Pt(11)
for text in ['采购项目：一期全链路验收测试采购','仅用于系统验收，不可用于投标。','第一章 采购要求','供应商须提供有效营业执照复印件。','投标文件逾期提交的，作无效投标处理。','供应商须在接到通知后2小时内响应，并提供实施计划。','第三章 评审办法']:
    doc.add_paragraph(text)
score=doc.add_table(rows=2,cols=3)
for c,t in zip(score.rows[0].cells,['评分项','评分内容','分值']):c.text=t
for c,t in zip(score.rows[1].cells,['服务方案','方案完整、响应时限符合要求，得20分。','20']):c.text=t
doc.add_paragraph('第五章 响应文件格式')
for title,body in [('附件1 投标函','我方承诺按本采购文件要求响应。FIXED-BUYER-ALPHA。供应商盖章：____'),('附件2 营业执照','附有效营业执照复印件，并加盖公章。'),('附件3 保险项目业绩与案例','请提交同类保险服务项目的业绩证明。'),('附件4 分项报价表','金额和税率须按实际报价填写。')]:
    doc.add_paragraph(title);doc.add_paragraph(body)
table=doc.add_table(rows=3,cols=4)
for c,t in zip(table.rows[0].cells,['服务项目','数量','含税单价','含税总价']):c.text=t
table.cell(1,0).text='测试服务';table.cell(1,1).text='1'
table.cell(2,0).merge(table.cell(2,2)).text='合计'
doc.add_paragraph('注：报价应包含所有税费。QUOTE-NOTE-ALPHA。')
doc.save(root/'source.docx');store.close()
print(json.dumps(dict(license=license['id'],case=case['id'])))
`,root],{cwd:app,env,encoding:'utf8'});
if(fixture.status!==0)throw Error(fixture.stderr);
const ids=JSON.parse(fixture.stdout);
async function stop(server){if(!server||server.exitCode!==null)return;server.kill('SIGTERM');await new Promise(resolve=>server.once('exit',resolve));}
async function start(){
 const child=spawn(python,['-B','-u','-c','from server import create_server\nimport sys\ns=create_server(0,sys.argv[1],seed=False);print(s.server_port,flush=True)\ntry:s.serve_forever()\nfinally:s.server_close();s.store.close()',path.join(root,'data')],{cwd:app,env,stdio:['ignore','pipe','pipe']});
 child.stderr.on('data',data=>fs.appendFileSync(path.join(root,'server-test.log'),data));
 const port=await new Promise((resolve,reject)=>{child.stdout.once('data',b=>resolve(Number(b.toString().trim())));child.once('exit',code=>reject(Error('server exit '+code)));});
 return {child,base:'http://127.0.0.1:'+port};
}
(async()=>{
 let server,browser,page;
 try{
  let running=await start();server=running.child;let base=running.base;
  browser=await pw.chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1440,height:1100}});
  page.setDefaultTimeout(20000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#wf-name').fill('一期全链路验收（测试数据）');
  await page.locator('#wf-upload').setInputFiles(path.join(root,'source.docx'));
  await page.locator('.wf-requirement-reading').waitFor({timeout:90000});
  assert.equal(await page.locator('.wf-metric').count(),5);
  const fwId=new URL(page.url()).searchParams.get('workflow');
  let fw=await (await page.request.get(base+'/api/frameworks/'+fwId)).json();
  for(const key of ['redline','qualification','score','spec','format'])assert.ok(fw.requirements.some(r=>r.category===key),'missing requirement group '+key);
  // The rule extractor can repeat a qualification proof in a broad requirements
  // section. Exercise the supported human correction before confirming the list.
  const duplicate=fw.requirements.find(r=>r.category==='spec'&&r.text.includes('营业执照'));
  if(duplicate){
   await page.locator('[data-wf=metric][data-id=spec]').click();
   await page.locator('[data-wf=select-requirement][data-id="'+duplicate.id+'"]').click();
   await page.locator('.wf-requirement-edit > summary').click();
   page.once('dialog',d=>d.accept());await page.locator('[data-wf=delete-requirement]').click();
   await page.locator('[data-wf=metric][data-id=redline]').click();
  }
  await page.locator('.wf-requirement-edit > summary').click();
  await page.locator('#wf-requirement-note').fill('CHAIN-REVIEW-ALPHA：人工核对测试备注');
  if(await page.locator('#wf-notes-ack').count())await page.locator('#wf-notes-ack').check();
  for(const box of await page.locator('[data-wf=ack-removed]').all())await box.check();
  await page.locator('[data-wf=confirm-requirements]').click();await page.locator('[data-wf=confirm]:enabled').waitFor();
  await page.locator('.wf-outline-editor > summary').click();
  const count=await page.locator('.wf-outline-choice').count();
  await page.locator('.wf-outline-choice').last().click();await page.locator('[data-wf=outline-add-sibling]').click();
  await page.locator('#wf-outline-title').fill('验收测试补充响应');
  await page.locator('[data-wf=outline-indent]').click();assert.match(await page.locator('.wf-outline-properties').innerText(),/第 2 级/);
  await page.locator('[data-wf=outline-outdent]').click();
  await page.locator('[data-wf=outline-add-child]').click();await page.locator('#wf-outline-title').fill('应删除的测试子节');
  page.once('dialog',d=>d.accept());await page.locator('[data-wf=outline-delete]').click();
  await page.locator('[data-wf=save-outline]').click();await page.locator('[data-wf=confirm]:enabled').waitFor();
  await page.reload();await page.locator('[data-wf=confirm]:enabled').waitFor();
  assert.equal(await page.locator('.wf-outline-choice').count(),count+1);
  await page.screenshot({path:path.join(root,'outline.png'),fullPage:true});
  await page.locator('[data-wf=confirm]').click();
  await page.getByRole('heading',{name:'核对文字材料',exact:true}).waitFor();
  const special=page.locator('.mapping-card').filter({has:page.locator('input[data-map-material][value="'+ids.case+'"]')});
  await special.locator('input[data-map-material][value="'+ids.case+'"]').check();
  // Human review can select a precise original chapter when no automatic match is claimed.
  const location=special.locator('select[data-map-field=locator_id]');
  const target=await location.locator('option').filter({hasText:'附件3 保险项目业绩与案例'}).getAttribute('value');
  await location.selectOption(target);
  await page.getByRole('button',{name:'保存核对',exact:true}).first().click();
  await page.screenshot({path:path.join(root,'filled.png'),fullPage:true});
  await page.getByRole('button',{name:'下一步：核对图片材料',exact:true}).click();
  await page.getByRole('heading',{name:'核对图片材料',exact:true}).waitFor();
  const license=page.locator('.mapping-card').filter({has:page.locator('input[data-map-material][value="'+ids.license+'"]')});
  await license.locator('input[data-map-material][value="'+ids.license+'"]').check();
  await license.locator('textarea[data-map-field=note]').fill('CHAIN-IMAGE-REVIEW：测试证照已核对');
  await page.getByRole('button',{name:'保存核对',exact:true}).first().click();
  await page.getByRole('button',{name:'下一步：编写投标文件',exact:true}).click();await page.locator('#wf-chapter').waitFor();
  const option=page.locator('#wf-chapter option').filter({hasText:'验收测试补充响应'});const section=await option.getAttribute('value');
  await page.locator('#wf-chapter').selectOption(section);await page.locator('#wf-body').fill('CHAIN-MANUAL-BETA：人工输入的验收正文；未启用 Skill 2。待提供的事实：【待补】。');
  await page.locator('[data-wf=save]').click();await page.waitForFunction(()=>document.querySelector('#wf-save-status')?.textContent==='已保存');
  await page.locator('#wf-redline-scan').waitFor();
  const redlines=page.locator('[data-wf=ack-redline]');
  for(let i=0;i<await redlines.count();i++)await redlines.nth(i).check();
  if(await redlines.count()){await page.locator('[data-wf=save-redline]').click();await page.waitForFunction(()=>/全部红线已勾选|未识别到废标红线/.test(document.querySelector('#wf-redline-scan')?.innerText||''));}
  await page.locator('[data-wf=export]').click();await page.getByRole('link',{name:'Word 文件',exact:true}).first().waitFor({timeout:90000});
  const href=await page.getByRole('link',{name:'Word 文件',exact:true}).first().getAttribute('href');
  const response=await page.request.get(base+href);assert.ok(response.ok());fs.writeFileSync(path.join(root,'chain-export.docx'),await response.body());
  await page.screenshot({path:path.join(root,'export.png'),fullPage:true});
  fw=await (await page.request.get(base+'/api/frameworks/'+fwId)).json();
  assert.ok(fw.requirements.some(r=>r.note?.includes('CHAIN-REVIEW-ALPHA')));
  assert.ok(fw.outline.some(r=>r.id===section&&r.text==='验收测试补充响应'));
  const project=await (await page.request.get(base+'/api/projects/'+fw.projects.word)).json();
  assert.equal(project.mappings.length,2);assert.equal(project.mappings.every(m=>m.status==='ready'),true);
  const report=await (await page.request.get(base+project.exports[0].report_url)).json();
  assert.equal(report.export.filled_count,2);
  // Restart the actual server and reopen the persisted chain, then export again.
  await stop(server);running=await start();server=running.child;base=running.base;
  await page.goto(base+'/?workflow='+fwId+'&step=5');await page.locator('#wf-chapter').waitFor();
  await page.locator('#wf-chapter').selectOption(section);assert.match(await page.locator('#wf-body').inputValue(),/CHAIN-MANUAL-BETA/);
  await page.locator('#wf-redline-scan').waitFor();
  const again=page.locator('[data-wf=ack-redline]');
  for(let i=0;i<await again.count();i++)await again.nth(i).check();
  if(await again.count()){await page.locator('[data-wf=save-redline]').click();await page.waitForFunction(()=>/全部红线已勾选|未识别到废标红线/.test(document.querySelector('#wf-redline-scan')?.innerText||''));}
  await page.locator('[data-wf=export]').click();await page.getByRole('link',{name:'Word 文件',exact:true}).first().waitFor();
  await page.waitForFunction(()=>document.querySelectorAll('a').length>0);
  let after;
  for(let i=0;i<100;i++){after=await (await page.request.get(base+'/api/projects/'+project.id)).json();if(after.exports.length===2)break;await new Promise(r=>setTimeout(r,100));}
  assert.equal(after.exports.length,2);
  const second=await page.request.get(base+after.exports[0].download_url);assert.ok(second.ok());fs.writeFileSync(path.join(root,'chain-repeat.docx'),await second.body());
  assert.deepEqual(errors,[]);
  const validation=spawnSync(python,['-B','-c',String.raw`
import json,sys
from pathlib import Path
from docx import Document
from zipfile import ZipFile
from lxml import etree
root=Path(sys.argv[1]);source=Document(root/'source.docx')
def sig(t):return [[c.text for c in r.cells] for r in t.rows]
quote=sig(source.tables[-1]);counts=[]
for file in ['chain-export.docx','chain-repeat.docx']:
    d=Document(root/file);text='\n'.join(p.text for p in d.paragraphs)
    assert text.count('PHASE1-TEXT-ALPHA')==1,text
    assert text.count('CHAIN-MANUAL-BETA')==1,text
    assert text.count('FIXED-BUYER-ALPHA')==1,text
    assert text.count('QUOTE-NOTE-ALPHA')==1,text
    assert quote in [sig(t) for t in d.tables]
    assert len(d.inline_shapes)==1,len(d.inline_shapes)
    with ZipFile(root/file) as z:
        assert z.testzip() is None
        xml=etree.fromstring(z.read('word/document.xml'));ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        names=xml.xpath('//w:bookmarkStart/@w:name',namespaces=ns)
        assert len(names)==len(set(names))
        assert set(xml.xpath('//w:hyperlink/@w:anchor',namespaces=ns))<=set(names)
    counts.append(dict(file=file,tables=len(d.tables),images=len(d.inline_shapes)))
print(json.dumps(counts))
`,root],{cwd:app,env,encoding:'utf8'});
  assert.equal(validation.status,0,validation.stderr);
  const result={result:'passed',flow:'DOCX → requirement edit/confirmation → directory edit/confirmation → image and text fill → manual response → Word → restart/reexport',procurement:'local extraction and manual confirmation',writing:'manual response path; AI writing covered separately',manualReview:{removedCrossCategoryDuplicate:!!duplicate,textMaterialLocation:'selected original attachment 3'},requirements:fw.metrics,outlineChapters:fw.outline.length,materialMappings:2,filledCount:2,word:JSON.parse(validation.stdout),browserErrors:errors};
  fs.writeFileSync(path.join(root,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(error){if(page)await page.screenshot({path:path.join(root,'failure.png'),fullPage:true}).catch(()=>{});throw error;}
 finally{if(browser)await browser.close();await stop(server);if(!process.env.BID_CHAIN_QA_DIR)fs.rmSync(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
