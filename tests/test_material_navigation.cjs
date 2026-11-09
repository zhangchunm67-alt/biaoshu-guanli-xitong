/* Navigation explains actual blockers and focuses the corresponding editable item. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\ninit\(\);\s*$/,'');

function harness(rows=[]) {
  const events={},timers=[],focused=[],scrolled=[],cards=[];
  const node=(id,dataset={})=>({dataset,tabIndex:-1,setAttribute(){},
    classList:{add(){},remove(){}},focus(){focused.push(id);},
    scrollIntoView(){scrolled.push(id);},querySelector(){return null;}});
  const save=node('save',{action:'save-project'});
  for(const row of rows)cards.push(node(row.id,{mapping:row.id}));
  const context=vm.createContext({URL,URLSearchParams,
    document:{addEventListener:(name,callback)=>events[name]=callback,
      querySelector:q=>q.includes('save-project')?save:null,
      querySelectorAll:q=>q.includes('data-mapping')?cards:q.includes('save-project')?[save]:[],
      body:{style:{}}},
    window:{addEventListener(){},scrollTo(){}},
    setTimeout:callback=>{timers.push(callback);return timers.length;},clearTimeout(){},
    IntersectionObserver:class{disconnect(){} observe(){}},
    tenderFlow:{projectArchives:()=>''}});
  vm.runInContext(source+`
    globalThis.renders=[];globalThis.messages=[];globalThis.apiCalls=[];
    render=()=>renders.push(projectUi.step);
    toast=(message,error)=>messages.push({message,error});
    api=async(...args)=>{apiCalls.push(args);throw Error('Navigation must not write or fetch project data');};
    globalThis.navigationTest={state,projectUi,materialCompletionIssues,guideMaterialCompletion,
      renderMaterialCompletionNotice,focusMaterialCompletionIssue};`,context);
  const h=context.navigationTest;
  h.state.data={categories:[{id:'image',name:'图片材料',layer:'common'},
    {id:'text_template',name:'文字模板',layer:'special'}],materials:[],projects:[]};
  h.state.project={id:'project',name:'合成项目',framework_id:'framework',material_flow:'text-first',
    special_selection_confirmed:true,text_review_confirmed:true,mappings:rows,headings:[]};
  h.projectUi.id='project';h.projectUi.step='common';
  return {...h,context,events,focused,scrolled,flush(){while(timers.length)timers.shift()();}};
}
const mapping=(id,category_id,extra={})=>({id,category_id,title:id,action:'fill',status:'ready',
  material_ids:[],issues:[],...extra});
const plain=value=>JSON.parse(JSON.stringify(value));

test('blocked text precedes blocked images and names every original problem',async()=>{
  const h=harness([
    mapping('identity','image',{title:'代理人身份证',status:'blocked',issues:['证件已过期']}),
    mapping('letter','text_template',{title:'授权书',status:'blocked',issues:['姓名不一致','尚未选择有效位置']})]);
  const before=JSON.stringify(h.state.project);
  const issues=h.materialCompletionIssues();
  assert.deepEqual(plain(issues.map(i=>[i.step,i.mappingId,i.kind])),
    [['special','letter','mapping'],['common','identity','mapping']]);
  assert.match(issues[0].message,/姓名不一致/);assert.match(issues[0].message,/尚未选择有效位置/);
  assert.equal(await h.guideMaterialCompletion(),true);h.flush();
  assert.equal(h.projectUi.step,'special');assert.equal(h.focused.at(-1),'letter');
  assert.equal(h.scrolled.at(-1),'letter');
  const html=h.renderMaterialCompletionNotice();
  assert.match(html,/role="alert"/);assert.match(html,/授权书/);assert.match(html,/代理人身份证/);
  assert.match(html,/姓名不一致/);assert.match(html,/证件已过期/);
  assert.equal((html.match(/data-action="locate-material-issue"/g)||[]).length,2);
  assert.equal(JSON.stringify(h.state.project),before);
  assert.equal(h.context.apiCalls.length,0);
});

test('an image-only problem goes directly to image review and its own card',async()=>{
  const h=harness([mapping('license','image',{title:'营业执照',status:'blocked',issues:['材料主体不一致']})]);
  h.projectUi.step='special';
  assert.equal(await h.guideMaterialCompletion('export'),true);h.flush();
  assert.equal(h.projectUi.step,'common');assert.equal(h.focused.at(-1),'license');
  assert.match(h.renderMaterialCompletionNotice(),/营业执照/);
  assert.match(h.renderMaterialCompletionNotice(),/材料主体不一致/);
});

test('clicking another listed issue changes review section and focuses that item',async()=>{
  const h=harness([
    mapping('first','text_template',{status:'blocked',issues:['文字位置待核对']}),
    mapping('second','image',{status:'blocked',issues:['图片格式错误']})]);
  await h.guideMaterialCompletion();
  const target={dataset:{action:'locate-material-issue',id:'1'}};
  await h.events.click({target:{closest:()=>target},preventDefault(){}});h.flush();
  assert.equal(h.projectUi.step,'common');assert.equal(h.focused.at(-1),'second');
  assert.deepEqual(h.focused,['second']);
  assert.equal(h.scrolled.at(-1),'second');assert.equal(h.context.apiCalls.length,0);
});

test('unsaved choices are kept and the save control is focused before other blockers',async()=>{
  const h=harness([mapping('identity','image',{status:'blocked',issues:['服务器旧问题']})]);
  h.state.dirty=true;h.state.project.mappings[0].note='尚未保存的人工选择';
  const before=JSON.stringify(h.state.project),issues=h.materialCompletionIssues();
  assert.equal(issues.length,1);assert.equal(issues[0].kind,'save');
  assert.match(issues[0].message+' '+issues[0].title,/保存/);
  assert.equal(await h.guideMaterialCompletion(),true);h.flush();
  assert.equal(h.focused.at(-1),'save');assert.equal(h.state.dirty,true);
  assert.equal(JSON.stringify(h.state.project),before);assert.equal(h.context.apiCalls.length,0);
});

test('unconfirmed text review returns to its save control even when every row is ready',async()=>{
  const h=harness([mapping('original','text_template'),mapping('identity','image')]);
  h.state.project.text_review_confirmed=false;
  const issues=h.materialCompletionIssues();
  assert.equal(issues.length,1);assert.equal(issues[0].step,'special');assert.equal(issues[0].kind,'save');
  assert.equal(await h.guideMaterialCompletion(),true);h.flush();
  assert.equal(h.projectUi.step,'special');assert.equal(h.focused.at(-1),'save');
  assert.equal(h.state.project.text_review_confirmed,false);assert.equal(h.context.apiCalls.length,0);
});

test('explicit missing and skip choices remain draft-compatible without navigation blockers',async()=>{
  const h=harness([
    mapping('missing','text_template',{action:'missing',status:'missing',issues:['本栏目待补材料。']}),
    mapping('skipped','image',{action:'skip',status:'skipped',issues:[]})]);
  assert.equal(h.materialCompletionIssues().length,0);
  assert.equal(await h.guideMaterialCompletion(),false);h.flush();
  assert.equal(h.focused.length,0);assert.equal(h.context.apiCalls.length,0);
});

test('buyer forms already preserved as ready do not block progress',async()=>{
  const h=harness([
    ...['投标函','承诺函','授权书'].map((title,i)=>mapping('form'+i,'text_template',
      {title,action:'skip',status:'ready',preserve_original:true})),
    mapping('identity','image')]);
  const before=JSON.stringify(h.state.project);
  assert.equal(h.materialCompletionIssues().length,0);
  assert.equal(await h.guideMaterialCompletion(),false);h.flush();
  assert.equal(JSON.stringify(h.state.project),before);assert.equal(h.focused.length,0);
});

test('entering image review is not blocked by issues that belong to the image step',async()=>{
  const h=harness([mapping('identity','image',{status:'blocked',issues:['请选择身份证材料']})]);
  h.projectUi.step='special';
  assert.equal(h.materialCompletionIssues('common').length,0);
  assert.equal(await h.guideMaterialCompletion('common'),false);
  assert.equal(h.focused.length,0);
  assert.equal(h.materialCompletionIssues('export').length,1);
  assert.equal(await h.guideMaterialCompletion('export'),true);h.flush();
  assert.equal(h.projectUi.step,'common');assert.equal(h.focused.at(-1),'identity');
});
