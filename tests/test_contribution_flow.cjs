/* Real two-port browser chain for colleague contributions.
 * A colleague on the 花生壳 mapped port submits a material and a case; the reviewer on the local
 * entry checks the quarantined files, accepts or rejects them, and uses the case in step 4.
 * Run with Playwright available and BID_TEST_PYTHON pointing to the app Python.
 * BID_QA_DIR optionally keeps the screenshots for visual review.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {spawn} = require('node:child_process');
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require('/Users/mac/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'); }
const app = path.resolve(__dirname, '..');
const python = process.env.BID_TEST_PYTHON || path.join(app, '.venv/bin/python');
const root = process.env.BID_QA_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'bid-contribution-ui-'));
fs.mkdirSync(root, {recursive:true});
const token = 'contribution-test-token';

async function stop(server) {
  if (!server || server.exitCode !== null) return;
  const exited = new Promise(resolve => server.once('exit', resolve));
  server.kill('SIGTERM');
  await exited;
}

(async () => {
  let server, browser, serverLog = '';
  try {
    server = spawn(python, ['-B', '-u', '-c', String.raw`
import base64,io,json,signal,socket,sys,threading
from pathlib import Path
from docx import Document
from PIL import Image,ImageDraw
import framework_service as framework
from server import create_oray_servers

root=Path(sys.argv[1])
sockets=[socket.socket(),socket.socket()]
for item in sockets:item.bind(('127.0.0.1',0))
port,local_port=[item.getsockname()[1] for item in sockets]
for item in sockets:item.close()
remote,local=create_oray_servers(port=port,intranet_port=local_port,data_dir=root/'data',seed=False,host='127.0.0.1',
    public_host=f'demo.test:{port}',access_token='contribution-test-token')
store=remote.store
for category in store.all('category'):
    if category.get('layer')=='common':category['enabled']=False;store.put('category',category)
store.db.commit()
doc=Document()
for line in ['设备维保服务采购项目','第一章 合格供应商资格要求','供应商须提供2023年1月1日以来至少两个同类合同。','供应商须在两小时内响应报修。','第五章 响应文件格式','附件1 报价函','我方报价为____，供应商签字盖章：____。','附件2 人员表']:
    doc.add_paragraph(line)
table=doc.add_table(rows=2,cols=3)
for cell,text in zip(table.rows[0].cells,['姓名','资格证书','证明页码']):cell.text=text
doc.add_paragraph('注：逐人填写并签章。')
stream=io.BytesIO();doc.save(stream)
record=framework.create(store,dict(name='投稿案例浏览器验收',filename='synthetic-procurement.docx',data=base64.b64encode(stream.getvalue()).decode()))
record=framework.revise(store,record['id'],dict(revision=record['revision'],items=record['requirements'],confirmed=True,notes_acknowledged=True,acknowledged_removed_ids=[]),'requirements')
project=framework.confirm(store,record['id'],dict(revision=record['revision']))
store.prepare_text_materials(project['id'],text_first=True)
for page in (1,2):
    image=Image.new('RGB',(600,800),'#f4f1ea');draw=ImageDraw.Draw(image)
    draw.rectangle((40,40,560,760),outline='#6b5640',width=4);draw.text((90,120),f'CONTRACT EVIDENCE / PAGE {page}',fill='black',font_size=28)
    image.save(root/f'contract-{page}.png')
case=Document()
for line in ['某银行运维服务案例','CASE-LIBRARY-EVIDENCE：为某银行提供七乘二十四小时运维，按季度验收。']:
    case.add_paragraph(line)
case.save(root/'service-plan.docx')
print(json.dumps(dict(port=port,local=local_port,framework=record['id'],project=project['id'])),flush=True)
threading.Thread(target=local.serve_forever,daemon=True).start()
def stop(*args):raise KeyboardInterrupt
signal.signal(signal.SIGTERM,stop)
try:remote.serve_forever()
except KeyboardInterrupt:pass
finally:
    local.shutdown();local.server_close();remote.server_close();store.close()
`, root], {cwd:app, env:{...process.env, PYTHONDONTWRITEBYTECODE:'1'}, stdio:['ignore','pipe','pipe']});
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
    const remoteBase = `http://demo.test:${fixture.port}`, localBase = `http://127.0.0.1:${fixture.local}`;
    browser = await playwright.chromium.launch({headless:true, args:['--host-resolver-rules=MAP demo.test 127.0.0.1','--no-proxy-server']});
    const errors = [];
    const open = async base => {
      const context = await browser.newContext({viewport:{width:1366,height:1000}});
      const page = await context.newPage();
      page.setDefaultTimeout(30000);
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base+'/login');
      await page.locator('input[name=token]').fill(token);
      await page.locator('button[type=submit]').click();
      await page.waitForURL(base+'/');
      await page.locator('.nav-button[data-view="materials"]').first().click();
      await page.locator('.material-subnav').waitFor();
      return page;
    };
    const shot = (page, name) => page.screenshot({path:path.join(root,name), fullPage:true});
    const toast = (page, text) => page.locator('.toast').filter({hasText:text}).first().waitFor();
    // Buffers keep the Chinese display names independent of the container's file-system locale.
    const upload = (file, name, mimeType) => ({name, mimeType, buffer:fs.readFileSync(path.join(root, file))});

    // A colleague on the mapped port can submit, but never sees submitted files again.
    const colleague = await open(remoteBase);
    assert.equal(await colleague.locator('[data-action="new-material"]').count(), 0);
    await colleague.locator('[data-action="submit-material"]').first().click();
    await colleague.locator('#submission-contributor').fill('张三');
    await colleague.locator('#submission-category').selectOption('B01');
    await colleague.locator('#submission-name').fill('某银行运维服务合同');
    await colleague.locator('#submission-client').fill('某银行');
    await colleague.locator('#submission-files').setInputFiles([upload('contract-1.png','合同第1页.png','image/png'), upload('contract-2.png','合同第2页.png','image/png')]);
    await colleague.locator('#submission-note').fill('合同原件在商务部');
    await shot(colleague, '1-colleague-submit-material.png');
    await colleague.locator('form[data-form="submit-material"] button[type=submit]').click();
    await toast(colleague, '已提交');
    await colleague.locator('.submission-table').getByText('某银行运维服务合同').waitFor();
    assert.equal(await colleague.locator('[data-action="review-submission"]').count(), 0);
    assert.match(await colleague.locator('.submission-table tbody tr').first().textContent(), /等待本机核对/);

    await colleague.locator('[data-action="submit-case"]').first().click();
    assert.equal(await colleague.locator('#case-contributor').inputValue(), '张三');
    await colleague.locator('#case-name').fill('某银行运维服务案例');
    await colleague.locator('#case-client').fill('某银行');
    await colleague.locator('#case-files').setInputFiles(upload('service-plan.docx','运维服务方案.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document'));
    await colleague.locator('form[data-form="submit-case"] button[type=submit]').click();
    await toast(colleague, '加入案例库');

    await colleague.locator('[data-action="submit-material"]').first().click();
    await colleague.locator('#submission-name').fill('公司简介（旧版）');
    await colleague.locator('#submission-mode').selectOption('text');
    await colleague.locator('#submission-content').fill('旧版公司简介。');
    await colleague.locator('form[data-form="submit-material"] button[type=submit]').click();
    await colleague.locator('.submission-table').getByText('公司简介（旧版）').waitFor();
    assert.equal(await colleague.locator('.submission-table tbody tr').count(), 3);
    await shot(colleague, '2-colleague-pending.png');

    // The local reviewer sees quarantined images, accepts with full rules, or rejects with a reason.
    const reviewer = await open(localBase);
    await reviewer.locator('.material-subnav [data-view="submissions"]').click();
    await reviewer.locator('.submission-table').waitFor();
    assert.match(await reviewer.locator('.material-subnav [data-view="submissions"]').textContent(), /3/);
    const row = name => reviewer.locator('.submission-table tbody tr').filter({hasText:name});
    await row('某银行运维服务合同').locator('[data-action="review-submission"]').click();
    const review = reviewer.locator('form[data-form="material"][data-submission]');
    await review.waitFor();
    assert.equal(await review.locator('.submission-thumb img').count(), 2);
    await reviewer.waitForFunction(() => [...document.querySelectorAll('.submission-thumb img')].every(img => img.complete && img.naturalWidth > 0));
    assert.equal(await review.locator('#material-category').inputValue(), 'B01');
    assert.equal(await review.locator('input[name="client_name"]').isVisible(), true);
    assert.equal(await review.locator('input[name="client_name"]').inputValue(), '某银行');
    await shot(reviewer, '3-reviewer-material.png');
    await review.locator('button[type=submit]').click();
    await toast(reviewer, '已核对入库');
    await row('某银行运维服务案例').locator('[data-action="review-submission"]').click();
    const caseReview = reviewer.locator('form[data-form="accept-case"]');
    await caseReview.waitFor();
    assert.equal(await caseReview.locator('.case-file-list a').textContent(), '运维服务方案.docx');
    await caseReview.locator('#review-case-description').fill('适用于银行运维类项目');
    await caseReview.locator('button[type=submit]').click();
    await toast(reviewer, '已加入案例库');
    await row('公司简介（旧版）').locator('[data-action="reject-submission"]').click();
    await reviewer.locator('#reject-reason').fill('内容过旧，请提交 2026 版');
    await reviewer.locator('form[data-form="reject-submission"] button[type=submit]').click();
    await toast(reviewer, '已退回');
    await reviewer.getByText('没有待审核的投稿').waitFor();

    await reviewer.locator('[data-action="submission-status"][data-id="accepted"]').click();
    await row('某银行运维服务合同').locator('[data-action="open-material"]').click();
    await reviewer.locator('.drawer').getByText('投稿 · 张三').waitFor();
    assert.equal(await reviewer.locator('.drawer .preview-thumb').count(), 2);
    await reviewer.locator('.drawer [data-action="close-drawer"]').first().click();
    await reviewer.locator('.material-subnav [data-view="cases"]').click();
    await reviewer.locator('.case-table').getByText('某银行运维服务案例').waitFor();
    assert.equal(await reviewer.locator('.case-table .case-file-list a').count(), 1);
    await shot(reviewer, '4-reviewer-case-library.png');

    // The colleague sees the outcome and reason; originals stay off the mapped port.
    await colleague.reload();
    await colleague.locator('.nav-button[data-view="materials"]').first().click();
    await colleague.locator('.material-subnav [data-view="submissions"]').click();
    await colleague.locator('[data-action="submission-status"][data-id="rejected"]').click();
    await colleague.getByText('内容过旧，请提交 2026 版').waitFor();
    await colleague.locator('[data-action="submission-status"][data-id="accepted"]').click();
    await colleague.locator('.filter-tab.active[data-id="accepted"]').waitFor();
    assert.equal(await colleague.locator('.submission-table tbody tr').count(), 2);
    await colleague.locator('.submission-table [data-action="open-material"]').click();
    await colleague.locator('.drawer').getByText('图片原件仅限本机入口查看（共 2 张）').waitFor();
    assert.equal(await colleague.locator('.drawer [data-action="append-files"]').count(), 0);
    await colleague.locator('.drawer [data-action="close-drawer"]').first().click();
    await colleague.locator('.material-subnav [data-view="cases"]').click();
    await colleague.locator('.case-table').getByText('某银行运维服务案例').waitFor();
    assert.equal(await colleague.locator('.case-table .case-file-list a').count(), 0);
    await shot(colleague, '5-colleague-results.png');

    // Step 4 builds its case package from the library through the normal upload path.
    await reviewer.goto(localBase+'/?workflow='+fixture.framework+'&step=5');
    await reviewer.locator('#wf-case-upload').waitFor({state:'attached'});
    const picker = reviewer.locator('details.wf-library-cases');
    assert.match(await picker.locator('summary').textContent(), /1 个可用/);
    await picker.locator('summary').click();
    await picker.locator('input[name="wf-library-case"]').check();
    await picker.locator('[data-wf="use-library-cases"]').click();
    await reviewer.locator('.wf-case-files').getByText('案例库/某银行运维服务案例/运维服务方案.docx').waitFor();
    assert.equal(await reviewer.locator('.wf-case-files .wf-status').first().textContent(), '案例库');
    const writing = await (await reviewer.request.get(localBase+'/api/projects/'+fixture.project+'/writing')).json();
    assert.ok(writing.case_sources.some(source => source.text.includes('CASE-LIBRARY-EVIDENCE')));
    await shot(reviewer, '6-step4-library-case.png');

    assert.deepEqual(errors, []);
    console.log('contribution flow passed; screenshots in', root);
  } catch (error) {
    console.error(error, serverLog.slice(-3000));
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    await stop(server);
    if (!process.env.BID_QA_DIR) fs.rmSync(root, {recursive:true, force:true});
  }
})();
