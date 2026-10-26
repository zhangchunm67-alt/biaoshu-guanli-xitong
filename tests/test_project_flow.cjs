/* Run with Playwright available and BID_TEST_PYTHON pointing to the app Python.
   BID_QA_DIR optionally retains screenshots and the exported DOCX for visual QA. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');
const {chromium} = require('playwright');

const app = path.resolve(__dirname, '..');
const root = process.env.BID_QA_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'bid-flow-'));
fs.mkdirSync(root, {recursive:true});
const python = process.env.BID_TEST_PYTHON || 'python3';
const fixture = spawnSync(python, ['-c', `
import io,json,base64,sys
from pathlib import Path
from docx import Document
from docx.shared import Pt
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image,ImageDraw
from server import Store,SUBJECT
root=Path(sys.argv[1]); store=Store(root/'data')
for c in store.all('category'):
    if c['layer']=='common' and c['id']!='company_profile':
        c['enabled']=False;store.put('category',c)
store.material(dict(name='通用公司介绍',category_id='company_profile',subject=SUBJECT,
    mode='text',content='这是用于流程验收的公司介绍正文。',valid_until='长期'))
ids=[]
for i in range(12):
    body=dict(name=f'保险核对材料{i:02}',category_id='I01',subject=SUBJECT,valid_until='长期',
        mode='text',content=f'流程验收专用材料 {i:02}',client_name='验收客户')
    if i==11:
        files=[]
        for page in range(1,3):
            im=Image.new('RGB',(600,800),'#f0eee9');draw=ImageDraw.Draw(im)
            draw.rectangle((40,40,560,760),outline='#755640',width=4)
            draw.text((90,120),f'TEST EVIDENCE / PAGE {page}',fill='black',font_size=28)
            buf=io.BytesIO();im.save(buf,format='PNG')
            files.append(dict(name=f'证明第{page}页.png',data=base64.b64encode(buf.getvalue()).decode()))
        body.update(mode='page',files=files)
    item=store.material(body)
    # Production timestamps have second precision; give pagination fixtures
    # distinct times so random IDs cannot change which evidence is on each page.
    saved=store.require('material',item['id'])
    saved['updated_at']=f'2026-10-20T00:00:{i:02}+00:00'
    store.put('material',saved);store.db.commit()
    ids.append(item['id'])
person=store.material(dict(name='人员经历证明',category_id='B02',subject='测试人员',subject_kind='person',
    mode='text',content='这是用于流程验收的人员经历证明。',valid_until='长期'))
extra={}
selected_categories=[c for c in store.all('category') if c.get('layer')=='special'
                     and c.get('industry_id') in {'insurance','bank'}]
for category in selected_categories:
    if category['id'] not in {'I01','B02'}:
        item=store.material(dict(name=category['name']+'材料',category_id=category['id'],subject=SUBJECT,
            mode='text',content=category['name']+'验收正文',valid_until='长期'))
        extra[category['id']]=item['id']
doc=Document()
for style in doc.styles:
    if style.type==1:
        style.font.name='Songti SC';style.font.size=Pt(12)
        rpr=style.element.get_or_add_rPr();fonts=rpr.find(qn('w:rFonts'))
        if fonts is None: fonts=OxmlElement('w:rFonts');rpr.append(fonts)
        fonts.set(qn('w:eastAsia'),'Songti SC')
for title in ['公司简介；']+[c['name'] for c in selected_categories]:
    doc.add_heading(title,1)
doc.save(root/'template.docx')
store.close()
print(json.dumps(dict(ids=ids,person=person['id'],extra=extra)))
`, root], {cwd:app, env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},encoding:'utf8'});
if(fixture.status!==0)throw new Error(fixture.stderr);
const materials=JSON.parse(fixture.stdout);

(async()=>{
  let browser,server;
  try{
    // Let the OS choose the port and read it from the actual HTTP server.
    server=spawn(python,['-u','-c',`from server import create_server
import sys
s=create_server(port=0,data_dir=sys.argv[1]);print(s.server_port,flush=True)
try:s.serve_forever()
finally:s.server_close();s.store.close()
`,path.join(root,'data')],{cwd:app,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
    const port=await new Promise((resolve,reject)=>{
      server.stdout.once('data',b=>resolve(Number(b.toString().trim())));
      server.once('exit',code=>reject(new Error('Server exited '+code)));
    });
    const base='http://127.0.0.1:'+port;
    browser=await chromium.launch({headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);
    await page.getByRole('button',{name:'创建项目',exact:true}).click();
    await page.locator('#project-name').fill('专用流程浏览器验收');
    await page.locator('#template-file').setInputFiles(path.join(root,'template.docx'));
    await page.getByRole('button',{name:'创建并扫描',exact:true}).click();
    await page.locator('.review-stage-heading').getByRole('heading',{name:'核对图片材料',exact:true}).waitFor();
    assert.equal(await page.locator('.mapping-card').count(),1);
    assert.equal(await page.locator('.location-recognition').filter({hasText:'已匹配原文条目'}).count(),1);
    assert.equal(await page.locator('[data-action="export-project"]').count(),0);
    assert.equal(await page.getByRole('button',{name:'确认已就绪项',exact:true}).count(),0);
    assert.deepEqual(await page.locator('.project-stepper strong').allTextContents(),['上传标书','核对图片材料','核对文字材料','导出标书']);
    await page.getByRole('button',{name:'下一步：核对文字材料',exact:true}).click();
    assert.equal(await page.locator('[data-project-industry]').count(),0);
    assert.equal(await page.locator('[data-project-category]').count(),0);
    await page.locator('.review-stage-heading').getByRole('heading',{name:'核对文字材料',exact:true}).waitFor();
    assert.equal(await page.locator('.mapping-card').count(),8);
    assert.equal(await page.locator('.project-blocked-hint').evaluate(el=>getComputedStyle(el).fontSize),'18px');
    assert.doesNotMatch(await page.locator('.mapping-list').innerText(),/已识别提交要求|查看原文依据|原位置已有内容/);
    const insurance=page.locator('.mapping-card').filter({has:page.getByRole('heading',{name:'保险项目业绩与案例',exact:true})});
    await insurance.locator('input[data-map-material][value="'+materials.ids[11]+'"]').check();
    await insurance.locator('.mapping-preview img').last().waitFor();
    assert.equal(await insurance.locator('.mapping-preview img').count(),2);
    await insurance.getByRole('button',{name:'下一页',exact:true}).click();
    await insurance.locator('input[data-map-material][value="'+materials.ids[0]+'"]').waitFor();
    // Selected material remains visible when paging or searching.
    assert.equal(await insurance.locator('input[value="'+materials.ids[11]+'"]').isChecked(),true);
    await Promise.all([
      page.waitForResponse(r=>r.url().includes('/api/materials?')&&new URL(r.url()).searchParams.get('q')==='保险核对材料00'),
      insurance.locator('input[data-project-search]').fill('保险核对材料00')
    ]);
    await insurance.locator('input[data-map-material][value="'+materials.ids[0]+'"]').check();
    const person=page.locator('.mapping-card').filter({has:page.getByRole('heading',{name:'银行人员经历与专业证明',exact:true})});
    await person.locator('input[value="'+materials.person+'"]').check();
    await person.locator('.mapping-text-preview').waitFor();
    for(const [category,id] of Object.entries(materials.extra)){
      const card=page.locator('.mapping-card').filter({has:page.locator('input[data-project-search="'+category+'"]')});
      await card.locator('input[data-map-material][value="'+id+'"]').check();
      await card.locator('.mapping-text-preview').waitFor();
    }
    await page.getByRole('button',{name:'保存核对',exact:true}).first().click();
    await page.waitForFunction(()=>!document.querySelector('.project-next button[data-id="export"]')?.disabled);
    await page.screenshot({path:path.join(root,'special-review.png'),fullPage:true});
    await page.getByRole('button',{name:'下一步：导出标书',exact:true}).click();
    await page.getByRole('button',{name:'导出',exact:true}).click();
    await page.getByRole('link',{name:'Word 文件',exact:true}).waitFor();
    const download=await page.getByRole('link',{name:'Word 文件',exact:true}).getAttribute('href');
    const response=await page.request.get(base+download);assert.equal(response.ok(),true);
    fs.writeFileSync(path.join(root,'draft.docx'),await response.body());
    await page.screenshot({path:path.join(root,'export.png'),fullPage:true});
    const bootstrap=await (await page.request.get(base+'/api/bootstrap')).json();
    const project=bootstrap.projects[0];
    assert.equal(project.mappings.find(m=>m.category_id==='company_profile').locator_id.startsWith('at:'),true);
    assert.deepEqual(project.special_industry_ids,['insurance','bank']);
    assert.deepEqual(project.special_category_ids,['I01','I02','I03','I04','B01','B02','B03','B04']);
    assert.equal(project.mappings.every(m=>!m.confirmed&&m.status==='ready'),true);
    assert.equal(project.exports[0].mode,'draft');
    const report=await (await page.request.get(base+project.exports[0].report_url)).json();
    assert.equal(report.materials.length,10);
    assert.equal(report.export.filled_count,9);
    await page.reload();
    await page.locator('[data-action="open-project"][data-id="'+project.id+'"]').first().click();
    await page.getByRole('button',{name:'导出',exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'导出',exact:true}).isEnabled(),true);
    await page.setViewportSize({width:390,height:844});
    await page.locator('[data-action="project-step"][data-id="special"]').click();
    assert.equal(await page.locator('[data-project-industry]').count(),0);
    await page.screenshot({path:path.join(root,'mobile-text-review.png'),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({result:'passed',steps:4,selectedMaterials:10,exportedColumns:9,browserErrors:errors}));
  }finally{
    if(browser)await browser.close();
    if(server){server.kill('SIGTERM');await new Promise(resolve=>server.once('exit',resolve));}
    if(!process.env.BID_QA_DIR)fs.rmSync(root,{recursive:true,force:true});
  }
})().catch(e=>{console.error(e);process.exitCode=1});
