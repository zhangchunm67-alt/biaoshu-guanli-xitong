/* Real local HTTP/browser chain; only model inference is deterministic and mocked.
 * All procurement, upload, persistence, adoption, and DOCX export code is real.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require('/Users/mac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'); }
const app = path.resolve(__dirname, '..');
const python = process.env.BID_TEST_PYTHON || path.join(app, '.venv/bin/python');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bid-writing-ui-'));
const env = {...process.env,  PYTHONDONTWRITEBYTECODE:'1'};

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
import base64,io,json,signal,sys,time
from pathlib import Path
from zipfile import ZipFile
from docx import Document
import framework_service as framework
import writing_model
from server import create_server

root=Path(sys.argv[1])
writing_model.configuration=lambda:dict(ready=True,model='deterministic-test-model',message='测试模型已就绪')
def generate(context):
    time.sleep(1.1)
    if 'SIMULATED-FAILURE' in context['business_notes']:
        raise ValueError('模拟写作服务失败，请重试。')
    assert 'BUSINESS-NOTES-ALPHA' in context['business_notes']
    source=next(source for source in context['case_sources'] if 'CASE-' in source['text'])
    rows=[]
    for chapter in context['chapters']:
        rows.append(dict(chapter_id=chapter['id'],text='WRITING-CANDIDATE-'+chapter['id']+'：'+chapter['title']+'。案例依据：'+source['text']+'。项目负责人：【待补】。',
            requirement_ids=chapter['requirement_ids'],source_refs=[source['id']],material_refs=[],
            missing_items=['项目负责人姓名【待补】'],conflicts=['历史案例的工期须按本次采购要求重新核对。']))
    return dict(chapters=rows,notes=['模型为确定性测试替身；业务事实不可用于真实投标。'])
writing_model.generate=generate
s=create_server(0,root/'data',seed=False)
store=s.store
for category in store.all('category'):
    category['enabled']=False;store.put('category',category)
store.db.commit()
doc=Document()
for line in ['设备维保服务采购项目','第一章 合格供应商资格要求','供应商须提供2023年1月1日以来至少两个同类合同。','供应商须在两小时内响应报修。','第五章 响应文件格式','附件1 报价函','我方报价为____，供应商签字盖章：____。BUYER-ORIGINAL-KEEP。','附件2 人员表']:
    doc.add_paragraph(line)
table=doc.add_table(rows=2,cols=3)
for cell,text in zip(table.rows[0].cells,['姓名','资格证书','证明页码']):cell.text=text
doc.add_paragraph('注：逐人填写并签章。')
stream=io.BytesIO();doc.save(stream)
record=framework.create(store,dict(name='第四步写作浏览器验收',filename='synthetic-procurement.docx',data=base64.b64encode(stream.getvalue()).decode()))
record=framework.revise(store,record['id'],dict(revision=record['revision'],items=record['requirements'],confirmed=True,notes_acknowledged=True,acknowledged_removed_ids=[]),'requirements')
rows=record['outline']+[dict(id='',text='测试实施计划',level=1,requirement_ids=[]),dict(id='',text='测试交付安排',level=1,requirement_ids=[])]
record=framework.revise(store,record['id'],dict(revision=record['revision'],items=rows),'outline')
project=framework.confirm(store,record['id'],dict(revision=record['revision']))
project=store.prepare_text_materials(project['id'],text_first=True)
chapters={row['text']:row['id'] for row in record['outline']}
(root/'case-one.txt').write_text('CASE-TXT-EVIDENCE：合成案例，曾按里程碑完成测试服务。',encoding='utf-8')
with ZipFile(root/'case-bundle.zip','w') as archive:
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
    const writingUrl = base+'/api/projects/'+fixture.project+'/writing';
    browser = await playwright.chromium.launch({headless:true});
    page = await browser.newPage({viewport:{width:1440,height:1100}});
    page.setDefaultTimeout(30000);
    const errors = [];
    const writingRequests = [];
    page.on('request', request => {if(request.url().startsWith(writingUrl))writingRequests.push(request);});
    page.on('pageerror', error => errors.push(error.message));
    // 生成前的“外发资料确认”统一接受并记录；其他确认框按 nextDialog 处理（默认取消，与无监听时一致）。
    let nextDialog=null; const outbound=[];
    page.on('dialog', dialog => {
      if(dialog.message().startsWith('生成初稿将把以下资料')){outbound.push(dialog.message());return dialog.accept();}
      const act=nextDialog; nextDialog=null;
      return act==='accept'?dialog.accept():dialog.dismiss();
    });
    const readWriting = async () => {
      const response = await page.request.get(writingUrl);
      assert.ok(response.ok(), await response.text());
      return response.json();
    };
    const waitCompleted = async () => {
      await page.waitForFunction(() => document.querySelector('#wf-writing-status')?.textContent.includes('个章节候选'), null, {timeout:45000});
      const writing = await readWriting();
      assert.equal(writing.job.status, 'completed', JSON.stringify(writing.job));
      return writing;
    };
    await page.goto(base+'/?workflow='+fixture.framework+'&step=5');
    await page.locator('#wf-case-upload').waitFor({state:'attached'});
    assert.equal(await page.locator('[data-wf=generate-writing]').isDisabled(), true);
    assert.equal(await page.locator('#wf-business-notes').getAttribute('maxlength'), '10000');
    assert.equal(await page.locator('#wf-business-notes-status').isVisible(), false);

    await page.locator('#wf-case-upload').setInputFiles(path.join(root,'case-one.txt'));
    await page.locator('.wf-case-files').getByText('case-one.txt',{exact:true}).waitFor();
    await page.locator('[data-wf=generate-writing]:enabled').waitFor();
    await page.locator('#wf-chapter').selectOption(fixture.first);
    await page.locator('#wf-body').fill('MANUAL-BEFORE-GENERATION：已保存的人工正文。');
    await page.locator('#wf-business-notes').fill('BUSINESS-NOTES-ALPHA：测试输入，不是真实业务事实。');
    assert.equal(await page.locator('#wf-business-notes-status').isVisible(), true);
    await page.locator('[data-wf=generate-writing]').click();
    await page.locator('.wf-writing-progress').waitFor();
    assert.equal(outbound.length,1);
    assert.match(outbound[0],/case-one\.txt/);
    assert.match(outbound[0],/业务补充信息：\d+ 字/);
    assert.match(outbound[0],/证件、授权书等正文不发送/);
    assert.equal(await page.locator('.wf-step[data-id="4"]').isEnabled(), true);
    await page.locator('.wf-step[data-id="4"]').click();
    await page.getByRole('heading',{name:'系统填充',exact:true}).waitFor();
    await page.locator('.wf-step[data-id="5"]').click();
    let writing = await waitCompleted();
    assert.equal(await page.locator('#wf-business-notes-status').isVisible(), false);
    assert.ok(writing.job.chapters.length >= 2);
    assert.equal(writing.job.accepted_chapter_ids.length, 0);
    await page.locator('#wf-chapter').selectOption(fixture.first);
    assert.match(await page.locator('#wf-body').inputValue(), /MANUAL-BEFORE-GENERATION/);
    assert.match(await page.locator('.wf-candidate-text').innerText(), /CASE-TXT-EVIDENCE/);
    assert.match(await page.locator('#wf-writing-candidate').innerText(), /项目负责人姓名【待补】/);
    assert.match(await page.locator('#wf-writing-candidate').innerText(), /历史案例的工期/);
    await page.locator('#wf-writing-candidate .wf-source-evidence > summary').click();
    assert.match(await page.locator('.wf-writing-sources').innerText(), /case-one\.txt/);

    // Full reload restores uploaded evidence, unaccepted candidates and hand-written text.
    await page.reload();
    await page.locator('#wf-chapter').waitFor();
    await page.locator('#wf-chapter').selectOption(fixture.first);
    assert.match(await page.locator('.wf-case-files').innerText(), /case-one\.txt/);
    assert.match(await page.locator('#wf-business-notes').inputValue(), /BUSINESS-NOTES-ALPHA/);
    const savedNotes=await page.locator('#wf-business-notes').inputValue();
    await page.locator('#wf-business-notes').fill(savedNotes+'补充');
    assert.equal(await page.locator('#wf-business-notes-status').isVisible(), true);
    await page.locator('#wf-business-notes').fill(savedNotes);
    assert.equal(await page.locator('#wf-business-notes-status').isVisible(), false);
    assert.match(await page.locator('#wf-body').inputValue(), /MANUAL-BEFORE-GENERATION/);
    nextDialog='dismiss';
    await page.locator('[data-wf=accept-writing]').click();
    assert.match(await page.locator('#wf-body').inputValue(), /MANUAL-BEFORE-GENERATION/);
    nextDialog='accept';
    await page.locator('[data-wf=accept-writing]').click();
    await page.waitForFunction(() => document.querySelector('#wf-body')?.value.includes('WRITING-CANDIDATE-'));
    assert.equal(await page.locator('[data-wf=accept-writing]').isDisabled(), true);
    writing = await readWriting();
    assert.deepEqual(writing.job.accepted_chapter_ids, [fixture.first]);
    assert.equal(writing.job.stale, false);
    await page.locator('#wf-chapter').selectOption(fixture.second);
    assert.equal(await page.locator('[data-wf=accept-writing]').isEnabled(), true);
    await page.locator('[data-wf=accept-all-writing]').click();
    await page.waitForFunction(() => document.querySelector('[data-wf=accept-all-writing]')?.disabled === true);
    writing = await readWriting();
    assert.equal(writing.job.accepted_chapter_ids.length, writing.job.chapters.length);

    // Editing one chapter does not discard another candidate. A failed targeted
    // rewrite also leaves other candidates available for review and adoption.
    await page.locator('[data-wf=generate-writing]').click();
    await page.locator('.wf-writing-progress').waitFor();
    writing = await waitCompleted();
    const secondCandidate = writing.job.chapters.find(c=>c.chapter_id===fixture.second);
    await page.locator('#wf-chapter').selectOption(fixture.first);
    await page.locator('#wf-body').fill('MANUAL-LOCAL-EDIT：只修改第一章，不影响其余候选。');
    await page.locator('[data-wf=save]').click();
    assert.equal(await page.locator('[data-wf=accept-writing]').isDisabled(),true);
    writing = await readWriting();
    assert.deepEqual(writing.job.stale_chapter_ids,[fixture.first]);
    await page.locator('#wf-business-notes').fill('SIMULATED-FAILURE');
    nextDialog='accept';
    await page.locator('[data-wf=generate-chapter-writing]').click();
    await page.waitForFunction(()=>document.querySelector('#wf-writing-status')?.textContent.includes('模拟写作服务失败'));
    await page.locator('#wf-chapter').selectOption(fixture.second);
    assert.match(await page.locator('.wf-candidate-text').innerText(),/WRITING-CANDIDATE-/);
    assert.equal(await page.locator('[data-wf=accept-writing]').isEnabled(),true);
    nextDialog='accept';
    await page.locator('[data-wf=accept-writing]').click();
    writing = await readWriting();
    assert.ok(writing.job.accepted_chapter_ids.includes(fixture.second));
    await page.locator('#wf-chapter').selectOption(fixture.first);
    assert.match(await page.locator('#wf-body').inputValue(),/MANUAL-LOCAL-EDIT/);
    await page.locator('#wf-business-notes').fill('BUSINESS-NOTES-ALPHA：仅重新编写当前章节。');
    nextDialog='accept';
    await page.locator('[data-wf=generate-chapter-writing]').click();
    await page.locator('.wf-writing-progress').waitFor();
    writing = await waitCompleted();
    assert.deepEqual(writing.job.target_chapter_ids,[fixture.first]);
    assert.deepEqual(writing.job.chapters.find(c=>c.chapter_id===fixture.second),secondCandidate);
    assert.ok(writing.job.accepted_chapter_ids.includes(fixture.second));
    assert.match(await page.locator('#wf-body').inputValue(),/MANUAL-LOCAL-EDIT/);
    nextDialog='accept';
    await page.locator('[data-wf=accept-all-writing]').click();
    await page.waitForFunction(()=>document.querySelector('[data-wf=accept-all-writing]')?.disabled===true);
    assert.ok(writingRequests.some(request=>request.url().includes('?view=status')), 'browser must use the lightweight polling view');
    assert.ok(writingRequests.some(request=>request.url().endsWith('/generate')&&request.postDataJSON()?.chapter_ids?.length===1));
    const lightweight = await (await page.request.get(writingUrl+'?view=status')).json();
    assert.equal('case_sources' in lightweight,false);
    assert.equal('procurement_sources' in lightweight,false);
    assert.equal('chapters' in lightweight.job,false);

    // A new package invalidates old candidates while preserving adopted prose.
    nextDialog='accept';
    await page.locator('#wf-case-upload').setInputFiles(path.join(root,'case-bundle.zip'));
    await page.locator('.wf-case-files').getByText('case-bundle.zip',{exact:true}).waitFor();
    writing = await readWriting();
    assert.equal(writing.case_revision, 2);
    assert.equal(writing.job.stale, true);
    assert.match(await page.locator('#wf-body').inputValue(), /CASE-TXT-EVIDENCE/);
    assert.equal(await page.locator('[data-wf=accept-writing]').isDisabled(), true);
    await page.reload();
    await page.locator('#wf-chapter').waitFor();
    await page.locator('#wf-chapter').selectOption(fixture.first);
    await page.locator('#wf-business-notes').fill('SIMULATED-FAILURE');
    await page.locator('[data-wf=generate-writing]').click();
    await page.waitForFunction(() => document.querySelector('#wf-writing-status')?.textContent.includes('模拟写作服务失败'));
    assert.match(await page.locator('#wf-body').inputValue(), /CASE-TXT-EVIDENCE/);
    await page.locator('#wf-business-notes').fill('BUSINESS-NOTES-ALPHA：使用替换后的案例包。');
    await page.locator('[data-wf=generate-writing]').click();
    writing = await waitCompleted();
    assert.match(await page.locator('.wf-candidate-text').innerText(), /CASE-ZIP-EVIDENCE/);
    await page.locator('#wf-writing-candidate .wf-source-evidence > summary').click();
    assert.match(await page.locator('.wf-writing-sources').innerText(), /case-bundle\.zip.*项目说明\.md/);
    nextDialog='accept';
    await page.locator('[data-wf=accept-all-writing]').click();
    await page.waitForFunction(() => document.querySelector('#wf-body')?.value.includes('CASE-ZIP-EVIDENCE'));

    await page.setViewportSize({width:390,height:844});
    await page.reload();
    await page.locator('#wf-chapter').waitFor();
    await page.locator('#wf-chapter').selectOption(fixture.first);
    assert.match(await page.locator('#wf-body').inputValue(), /CASE-ZIP-EVIDENCE/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1), true, 'mobile page overflows viewport');
    for (const selector of ['.wf-writing-inputs','.wf-candidate','.wf-saved-draft']) {
      const box = await page.locator(selector).boundingBox();
      assert.ok(box && box.x >= 0 && box.x+box.width <= 391, selector+' overflows mobile viewport');
    }
    await page.locator('#wf-redline-scan').waitFor();
    const redlines=page.locator('[data-wf=ack-redline]');
    for(let i=0;i<await redlines.count();i++)await redlines.nth(i).check();
    if(await redlines.count()){
      await page.locator('[data-wf=save-redline]').click();
      await page.waitForFunction(()=>/全部红线已勾选|未识别到废标红线/.test(document.querySelector('#wf-redline-scan')?.innerText||''));
    }
    await page.locator('[data-wf=export]').click();
    const download = page.getByRole('link',{name:'Word 文件',exact:true}).first();
    await download.waitFor({timeout:90000});
    const response = await page.request.get(base+await download.getAttribute('href'));
    assert.ok(response.ok());
    const exported = path.join(root,'writing-export.docx');
    fs.writeFileSync(exported,await response.body());
    writing = await readWriting();
    const check = spawnSync(python,['-B','-c',String.raw`
import json,sys
from docx import Document
from zipfile import ZipFile
path=sys.argv[1];expected=json.loads(sys.argv[2]);d=Document(path)
text='\n'.join(p.text for p in d.paragraphs)
for chapter in expected:
    assert text.count('WRITING-CANDIDATE-'+chapter+'：')==1,text
assert 'CASE-ZIP-EVIDENCE' in text
assert 'CASE-TXT-EVIDENCE' not in text
assert 'MANUAL-BEFORE-GENERATION' not in text
assert text.count('BUYER-ORIGINAL-KEEP')==1,text
assert '项目负责人：【待补】' in text
with ZipFile(path) as archive:assert archive.testzip() is None
`, exported, JSON.stringify(writing.job.accepted_chapter_ids)], {cwd:app,env,encoding:'utf8'});
    assert.equal(check.status,0,check.stderr);
    assert.deepEqual(errors,[]);
    console.log('PASS: uploads → lightweight polling → chapter-local invalidation/targeted regeneration → failed rewrite preserves other candidates → guarded adoption → source replacement → mobile layout → real DOCX contents.');
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
