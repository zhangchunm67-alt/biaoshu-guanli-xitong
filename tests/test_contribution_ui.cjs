/* Contribution pages: the mapped port submits only, the local entry reviews, and
   every colleague-supplied string is escaped. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\ninit\(\);\s*$/,'');

function harness(remote) {
  const context=vm.createContext({URL,URLSearchParams,
    document:{addEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],body:{style:{}}},
    window:{addEventListener(){}},setTimeout:()=>1,clearTimeout(){},IntersectionObserver:class{disconnect(){} observe(){}},
    tenderFlow:{projectArchives:()=>''}});
  vm.runInContext(source+'\nglobalThis.ui={state,contributionUi,renderMaterials,renderIndustries,renderSubmissions,renderCaseLibrary};',context);
  const h=context.ui;
  h.state.data={categories:[{id:'business_license',name:'营业执照',layer:'common',enabled:true},
    {id:'B01',name:'银行项目业绩与案例',layer:'special',enabled:true}],materials:[],projects:[],industries:[],
    stats:{pending_submission_count:2},credit_automation:{},...(remote?{remote_access:{public:true}}:{})};
  const hostile='<img src=x onerror=alert(1)>';
  h.contributionUi.result={total:2,counts:{pending:2,accepted:1,rejected:0},items:[
    {id:'s1',kind:'material',status:'pending',contributor:hostile,note:hostile,origin:'remote',created_at:'2026-11-09T00:00:00Z',
     material:{name:hostile,category_id:'B01',mode:'page'},file_count:2,size:4096,files:[]},
    {id:'s2',kind:'case',status:'pending',contributor:'李四',origin:'local',created_at:'2026-11-09T00:00:00Z',
     case:{name:'某银行运维服务案例',client_name:'某银行'},file_count:1,size:2048,files:[]}]};
  h.contributionUi.cases={counts:{active:1,archived:0},items:[{id:'c1',name:hostile,client_name:'某银行',description:'',
    contributor:'李四',created_at:'2026-11-09T00:00:00Z',revision:1,files:[{id:'f1',name:hostile,size:2048,...(remote?{}:{url:'/api/case-library/c1/files/f1'})}]}]};
  return h;
}

test('the mapped port offers submission instead of direct material uploads',()=>{
  const h=harness(true);
  for(const html of [h.renderMaterials(),h.renderIndustries()]){
    assert.match(html,/data-action="submit-material"/);
    assert.doesNotMatch(html,/data-action="new-material"/);
  }
  const page=h.renderSubmissions();
  assert.doesNotMatch(page,/data-action="review-submission"|data-action="reject-submission"/);
  assert.equal((page.match(/等待本机核对/g)||[]).length,2);
  assert.match(page,/已提交的图片和文件不会再从外网显示/);
  const cases=h.renderCaseLibrary();
  assert.doesNotMatch(cases,/download|data-action="archive-case"|data-action="edit-case"/);
});

test('the local entry reviews each pending submission',()=>{
  const h=harness(false);
  assert.match(h.renderMaterials(),/data-action="new-material"/);
  const page=h.renderSubmissions();
  assert.equal((page.match(/data-action="review-submission"/g)||[]).length,2);
  assert.equal((page.match(/data-action="reject-submission"/g)||[]).length,2);
  assert.match(page,/审核加入案例库/);
  assert.match(page,/2 个文件 · 4 KB/);
  const cases=h.renderCaseLibrary();
  assert.match(cases,/href="\/api\/case-library\/c1\/files\/f1" download/);
  assert.match(cases,/data-action="archive-case"/);
});

test('colleague-supplied names, notes and file names are escaped',()=>{
  for(const remote of [true,false]){
    const h=harness(remote);
    for(const html of [h.renderSubmissions(),h.renderCaseLibrary()]){
      assert.doesNotMatch(html,/<img src=x/);
      assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);
    }
  }
});

test('decided submissions show their outcome and rejection reason',()=>{
  const h=harness(true);
  h.contributionUi.status='rejected';
  h.contributionUi.result={total:1,counts:{pending:0,accepted:0,rejected:1},items:[{id:'s3',kind:'material',status:'rejected',
    contributor:'王五',origin:'remote',created_at:'2026-11-09T00:00:00Z',material:{name:'公司简介',mode:'text',category_id:''},
    file_count:0,size:0,files:[],decision:{reason:'内容过旧 <b>请更新</b>'}}]};
  const page=h.renderSubmissions();
  assert.match(page,/已退回/);
  assert.match(page,/内容过旧 &lt;b&gt;请更新&lt;\/b&gt;/);
  assert.match(page,/未指定栏目，由审核人归类/);
  assert.match(page,/文字内容/);
});
