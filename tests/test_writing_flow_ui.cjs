/* Real local HTTP/browser chain; only model inference is deterministic and mocked.
 * All procurement, upload, persistence, redline orchestration, and DOCX export code is real.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');
const playwright = require('playwright');
const app = path.resolve(__dirname, '..');
const python = process.env.BID_TEST_PYTHON || path.join(app, '.venv/bin/python');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bid-writing-ui-'));
const env = {...process.env,  PYTHONDONTWRITEBYTECODE:'1', BID_CASE_OCR:'off'};

async function stop(server) {
  if (!server || server.exitCode !== null) return;
  const exited = new Promise(resolve => server.once('exit', resolve));
  server.kill('SIGTERM');
  await exited;
}

(async () => {
  let server, browser, page, serverLog = '';
  try {
    server = spawn(python, ['-B', '-u', '-c', String.raw`
import base64,io,json,os,signal,sys,time
from pathlib import Path
from zipfile import ZipFile
from docx import Document
import framework_service as framework
import writing_model
from server import create_server

root=Path(sys.argv[1])
writing_model.configuration=lambda:dict(ready=True,model='deterministic-test-model',models=[{'id':'deterministic-test-model','label':'测试模型'},{'id':'alternate-model','label':'备选模型'}],message='测试模型已就绪',status='ready',reasoning='medium')
writing_model.resolve_model=lambda requested=None: (requested or 'deterministic-test-model')
def generate(context, model=None):
    time.sleep(1.1)
    if 'SIMULATED-FAILURE' in context['business_notes']:
        raise ValueError('模拟写作服务失败，请重试。')
    assert 'BUSINESS-NOTES-ALPHA' in context['business_notes']
    if '只改' in context['business_notes']:
        assert [c['title'] for c in context['chapters']] == [c['title'] for c in context['chapters'] if '测试实施计划' in c['title']]
        assert 'WRITING-CANDIDATE-' in context['chapters'][0]['text']
    assert all('技术方案' not in c['title'] and '现有技术细节' not in c['title'] for c in context['chapters'])
    source=next(source for source in context['case_sources'] if 'CASE-' in source['text'])
    rows=[]
    for chapter in context['chapters']:
        rows.append(dict(chapter_id=chapter['id'],text='WRITING-CANDIDATE-'+chapter['id']+'：'+chapter['title']+'。案例依据：'+source['text']+'。项目负责人：【待补】。',
            requirement_ids=chapter['requirement_ids'],source_refs=[source['id']],material_refs=[],
            missing_items=['项目负责人姓名【待补】'],conflicts=['历史案例的工期须按本次采购要求重新核对。']))
        images={s['id']:s for s in context['case_sources'] if s.get('image_id')}
        if images and '测试实施计划' in chapter['title']:
            rows[-1]['visuals']=[
                dict(kind='image', after=1, caption='系列海报展示', image_id=next(iter(images))),
                dict(kind='flow', after=1, caption='本项目审校流程', steps=['编辑提交初稿','复核并返修','确认后交付']),
                dict(kind='table', after=1, caption='阶段交付与验收', headers=['阶段','交付物','验收依据'],
                     rows=[['初审','设计初稿','核对需求清单'],['交付','设计定稿','复核确认记录']])]
            import writing_visuals
            writing_visuals.prepare(rows[-1], images)
    return dict(chapters=rows,notes=['模型为确定性测试替身；业务事实不可用于真实投标。'])
writing_model.generate=generate
def complete(system, context, schema, **kwargs):
    # Redline checks and redline rewrites always use Kimi, whichever model wrote the body.
    assert kwargs['model'] == 'moonshotai/kimi-k3.1-pro', kwargs.get('model')
    if 'redlines' not in context:
        first=context['issues'][0]
        source=next((r for r in context['cases'] if '曾按里程碑完成测试服务' in r['text']),None)
        chapter=next(r for r in context['chapters'] if r['id']==first['chapters'][0])
        return {'items': [] if not source else [dict(chapter_id=chapter['id'], issue_ids=[first['id']],
            source_ids=[source['id']], summary='按红线补充历史经验',
            text=chapter['text']+'\n历史案例曾按里程碑完成测试服务。')]}
    assert any('保留原始技术段落' in row['text'] for row in context['chapters'])
    target=next((r for r in context['chapters'] if '测试实施计划' in r['path']),None)
    return {'items': [dict(id=row['id'], verdict='unclear', reason='案例不足', evidence='',
        chapter_ids=[target['id']] if target else [], suggestion='核对签章和递交要求。') for row in context['redlines']]}
writing_model.complete_json=complete
s=create_server(0,root/'data',seed=False)
store=s.store
for category in store.all('category'):
    category['enabled']=False;store.put('category',category)
store.db.commit()
doc=Document()
for line in ['设备维保服务采购项目','第一章 合格供应商资格要求','供应商须提供2023年1月1日以来至少两个同类合同。','供应商须在两小时内响应报修。','未在两小时内响应报修的，投标无效。','第五章 响应文件格式','一、投标函','我方报价为____，供应商签字盖章：____。BUYER-ORIGINAL-KEEP。','二、人员表']:
    doc.add_paragraph(line)
table=doc.add_table(rows=2,cols=3)
for cell,text in zip(table.rows[0].cells,['姓名','资格证书','证明页码']):cell.text=text
doc.add_paragraph('注：逐人填写并签章。')
doc.add_paragraph('三、技术方案')
doc.add_paragraph('按采购要求编写技术方案。')
stream=io.BytesIO();doc.save(stream)
record=framework.create(store,dict(name='第四步写作浏览器验收',filename='synthetic-procurement.docx',data=base64.b64encode(stream.getvalue()).decode(),outline_choice=True))
record=framework.revise(store,record['id'],dict(revision=record['revision'],items=record['requirements'],confirmed=True,notes_acknowledged=True,acknowledged_removed_ids=[]),'requirements')
custom=Document()
custom.add_heading('一、投标函',1)
custom.add_heading('二、人员表',1)
custom.add_heading('技术方案',1)
custom.add_heading('现有技术细节',2)
custom.add_paragraph('保留原始技术段落').runs[0].bold=True
custom.add_table(rows=1,cols=1).cell(0,0).text='TECH-TABLE-KEEP'
from PIL import Image
pic=io.BytesIO()
Image.new('RGB',(30,20),(20,80,160)).save(pic,format='PNG')
pic.seek(0)
custom.add_picture(pic)
custom.add_heading('测试实施计划',1)
custom.add_heading('测试交付安排',1)
stream=io.BytesIO();custom.save(stream)
record=framework.revise(store,record['id'],dict(revision=record['revision'],docx={
    'name':'自定义目录.docx','data':base64.b64encode(stream.getvalue()).decode()}),'outline')
project=framework.confirm(store,record['id'],dict(revision=record['revision']))
with store.lock:
    saved=store.require('project',project['id'])
    saved.update(special_selection_confirmed=True,text_review_confirmed=True)
    store.put('project',saved);store.db.commit()
chapters={row['text']:row['id'] for row in record['outline']}
(root/'case-one.txt').write_text('CASE-TXT-EVIDENCE：合成案例，曾按里程碑完成测试服务。',encoding='utf-8')
with ZipFile(root/'case-bundle.zip','w') as archive:
    poster=os.getenv('BID_CHAT_QA_IMAGE')
    with Image.open(poster) if poster else Image.new('RGB',(320,180),'blue') as im:
        out=io.BytesIO()
        im.save(out,format='PNG')
        archive.writestr('案例资料/系列展示.png',out.getvalue())
    archive.writestr('案例资料/项目说明.md','# 测试相关案例\nCASE-ZIP-EVIDENCE：替换后的合成案例，以交付清单验收。')
print(json.dumps(dict(port=s.server_port,framework=record['id'],project=project['id'],first=chapters['测试实施计划'],second=chapters['测试交付安排'])),flush=True)
def stop(*args):raise KeyboardInterrupt
signal.signal(signal.SIGTERM,stop)
try:s.serve_forever()
except KeyboardInterrupt:pass
finally:s.server_close();store.close()
`, root], {cwd:app, env, stdio:['ignore','pipe','pipe']});
    server.stderr.on('data', data => { serverLog += data.toString(); });
    const fixture = await new Promise((resolve, reject) => {
      let output = '';
      const timer = setTimeout(() => reject(Error('Fixture startup timed out: '+serverLog)), 90000);
      server.stdout.on('data', data => {
        output += data.toString();
        const line = output.split('\n').find(line => line.startsWith('{'));
        if (line) { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch (e) { reject(e); } }
      });
      server.once('exit', code => { clearTimeout(timer); reject(Error('Fixture exited '+code+': '+serverLog)); });
    });
    const base = 'http://127.0.0.1:'+fixture.port;
    browser = await playwright.chromium.launch({headless:true});
    page = await browser.newPage({viewport:{width:1440,height:1100}});
    page.setDefaultTimeout(30000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const requests=[];
    page.on('request',request=>requests.push(request.url()));
    page.on('dialog',dialog=>dialog.accept());
    const url=base+'/?workflow='+fixture.framework+'&step=5';
    await page.goto(url);
    await page.locator('#wf-chat-upload').waitFor({state:'attached'});
    await page.locator('#wf-redline-scan').waitFor();
    await page.locator('[data-wf=formal-export][data-id=reviewed]').waitFor();
    await page.locator('[data-wf=formal-export][data-id=draft]').waitFor();
    const message=page.locator('#wf-chat-message');
    const upload=page.locator('#wf-chat-upload');
    const send=page.locator('[data-wf=chat-send]');
    await send.waitFor();
    await message.fill('未提供案例');
    await send.click();
    await page.getByText('请先用加号上传相关项目案例。',{exact:true}).first().waitFor();
    await upload.setInputFiles(path.join(root,'case-one.txt'));
    await page.locator('.wf-chat-files').getByText('case-one.txt',{exact:true}).waitFor();
    await page.locator('#wf-writing-model').selectOption('alternate-model');
    await message.fill('BUSINESS-NOTES-ALPHA：编写初稿');
    await send.click();
    await page.getByText('正在编写…',{exact:true}).waitFor();
    await page.reload();
    await page.getByRole('link',{name:'投标文件初稿',exact:true}).waitFor();
    await upload.setInputFiles(path.join(root,'case-bundle.zip'));
    await page.locator('.wf-chat-files').getByText(/case-bundle/).waitFor();
    assert.equal(await page.locator('.wf-chat-files').getByText('case-one.txt',{exact:true}).count(),1);
    await message.fill('BUSINESS-NOTES-ALPHA：只改测试实施计划');
    await message.press('Enter');
    await page.waitForFunction(()=>document.querySelectorAll('.wf-chat-downloads').length===2);
    await page.getByRole('button',{name:'预览章节正文',exact:true}).last().click();
    let chapter=page.locator('.wf-chat-chapter').filter({hasText:'系列海报展示'});
    await chapter.locator('summary').click();
    const poster=chapter.locator('img');
    await poster.scrollIntoViewIfNeeded();
    await page.waitForFunction(()=>document.querySelector('.wf-case-picture img')?.naturalWidth>0);
    assert.equal(await chapter.locator('.wf-flow li').count(),3);
    assert.equal(await chapter.locator('.wf-delivery-table tbody tr').count(),2);
    await chapter.getByRole('button',{name:'采纳本章正文'}).click();
    await chapter.getByText('已采纳',{exact:true}).waitFor({state:'attached'});
    await page.reload();
    await page.getByRole('button',{name:'预览章节正文',exact:true}).last().click();
    await chapter.locator('summary').click();
    await chapter.getByText('已采纳',{exact:true}).waitFor();
    const saved=await (await page.request.get(base+'/api/projects/'+fixture.project+'/chapter-drafts')).json();
    assert.deepEqual(saved.chapters.find(c=>c.id===fixture.first).visuals.map(v=>v.kind),['image','flow','table']);
    await message.fill('SIMULATED-FAILURE');
    await send.click();
    await page.getByText('模拟写作服务失败，请重试。',{exact:true}).waitFor();
    assert.equal(await page.locator('.wf-chat-downloads').count(),2);
    await page.setViewportSize({width:390,height:844});
    await page.reload();
    await page.locator('.wf-chat-downloads').nth(1).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    for(const [label,name] of [['投标文件初稿','draft.docx'],['投标文件编制说明','notes.docx']]){
      const link=page.getByRole('link',{name:label,exact:true}).last();
      const response=await page.request.get(base+await link.getAttribute('href'));
      assert.ok(response.ok());
      fs.writeFileSync(path.join(root,name),await response.body());
    }
    const projectApi=base+'/api/projects/'+fixture.project;
    const snapshot=await (await page.request.get(projectApi+'/stage-preview')).json();
    const exported=await page.request.post(projectApi+'/export',{data:{mode:'stage',
      mapping_revision:snapshot.mapping_revision,stage_revision:snapshot.stage_revision}});
    assert.ok(exported.ok(),await exported.text());
    const stageId=(await exported.json()).id;
    const stageFile=await page.request.get(base+'/api/exports/'+stageId+'/download');
    assert.ok(stageFile.ok());
    fs.writeFileSync(path.join(root,'stage.docx'),await stageFile.body());
    const inspected=spawnSync(python,['-B','-c',String.raw`
import sys
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
from zipfile import ZipFile
root=Path(sys.argv[1])
draft=Document(root/'draft.docx')
notes=Document(root/'notes.docx')
text='\n'.join(p.text for p in draft.paragraphs)
assert text.count('保留原始技术段落')==1,text
assert len(list(draft.element.body.iter(qn('a:blip'))))==2
assert any(t.cell(0,0).text=='TECH-TABLE-KEEP' for t in draft.tables)
assert '系列海报展示' in text and '案例资料/系列展示.png' in text
assert any(t.cell(0,0).text=='阶段' and t.cell(2,1).text=='设计定稿' for t in draft.tables)
assert any(t.cell(2,0).text=='复核并返修' for t in draft.tables if len(t.rows)==5)
stage=Document(root/'stage.docx')
assert any(p.text=='系列海报展示' for p in stage.paragraphs)
assert list(stage.element.body.iter(qn('a:blip')))
assert any(t.cell(0,0).text=='阶段' and t.cell(2,1).text=='设计定稿' for t in stage.tables)
assert 'BUYER-ORIGINAL-KEEP' in text
assert 'WRITING-CANDIDATE-' in text and '项目负责人：【待补】' in text
assert '历史案例曾按里程碑完成测试服务' in text
assert {p.text for p in notes.paragraphs}>={'采购要点','目录对齐','素材台账','待补清单','评分索引'}
assert '项目负责人姓名【待补】' in '\n'.join(p.text for p in notes.paragraphs)
for name in ('draft.docx','notes.docx'):
    with ZipFile(root/name) as archive: assert archive.testzip() is None
`,root],{cwd:app,env,encoding:'utf8'});
    assert.equal(inspected.status,0,inspected.stderr);
    const project=await (await page.request.get(base+'/api/projects/'+fixture.project)).json();
    const formal=await page.request.post(base+'/api/projects/'+fixture.project+'/export-jobs',
      {data:{mapping_revision:project.mapping_revision,mode:'reviewed'}});
    assert.ok(!formal.ok(), 'incomplete chat draft may not bypass the formal export gate');
    await page.locator('.wf-branch-a').click();
    await page.getByRole('button',{name:'生成阶段 Word',exact:true}).waitFor();
    assert.ok(requests.some(url=>url.endsWith('/redline-scan')), 'writing step must read the current redline scan');
    assert.deepEqual(errors,[]);
    if(process.env.BID_CHAT_QA_DIR){
      for(const name of ['draft.docx','notes.docx']) fs.copyFileSync(path.join(root,name),path.join(process.env.BID_CHAT_QA_DIR,name));
    }
    console.log('PASS: case uploads, model selection, background checks, same-project history, follow-up, preserved technical content, two Word downloads and mobile layout.');
  } catch(error) {
    if(page)console.error('Page:',await page.locator('body').innerText().catch(()=>''));
    if(serverLog)console.error('Server:',serverLog.slice(-8000));
    throw error;
  } finally {
    if(browser)await browser.close();
    await stop(server);
    fs.rmSync(root,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
