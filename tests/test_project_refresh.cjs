/* Project lists are summaries; refreshing them must never erase an open editor. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');

function harness(respond) {
  const calls=[];
  const context=vm.createContext({
    URLSearchParams,
    fetch:async url=>{calls.push(url);return {ok:true,json:async()=>respond(url)};}
  });
  vm.runInContext(source.slice(0,source.indexOf('function safeExternalUrl'))+
    '\nglobalThis.editor={state,specialState,refreshData};',context);
  return {...context.editor,calls};
}
const summary={id:'project',name:'项目',template_name:'模板.docx'};
const bootstrap=()=>({categories:[],industries:[],projects:[summary],materials:[]});

test('list refresh has no detail request until an editor is open',async()=>{
  const h=harness(url=>url==='/api/bootstrap'?bootstrap():assert.fail('unexpected request '+url));
  await h.refreshData();
  assert.deepEqual(h.calls,['/api/bootstrap']);
  assert.equal(h.state.project,null);
});

test('open editor reloads full detail instead of accepting a list summary',async()=>{
  const detail={...summary,mappings:[{id:'row',note:'saved'}],exports:[{id:'export'}],mapping_revision:'current'};
  const h=harness(url=>url==='/api/bootstrap'?bootstrap():detail);
  h.state.project={...summary,mappings:[],mapping_revision:'old'};
  await h.refreshData();
  assert.deepEqual(h.calls,['/api/bootstrap','/api/projects/project']);
  assert.equal(h.state.project,detail);
  assert.equal(h.state.project.mappings[0].note,'saved');
  assert.equal(h.state.project.exports[0].id,'export');
});

test('unsaved choices survive list refresh without a detail request',async()=>{
  const current={...summary,mappings:[{id:'row',note:'unsaved'}]};
  const h=harness(()=>bootstrap());h.state.project=current;h.state.dirty=true;
  await h.refreshData();
  assert.equal(h.state.project,current);
  assert.deepEqual(h.calls,['/api/bootstrap']);
});

test('edits made while detail is loading are not overwritten',async()=>{
  let resolveDetail,requested;
  const started=new Promise(resolve=>{requested=resolve;});
  const h=harness(url=>url==='/api/bootstrap'?bootstrap():new Promise(resolve=>{resolveDetail=resolve;requested();}));
  const current={...summary,mappings:[{id:'row',note:'before'}]};h.state.project=current;
  const refresh=h.refreshData();await started;
  current.mappings[0].note='new edit';h.state.dirty=true;
  resolveDetail({...summary,mappings:[{id:'row',note:'server'}]});await refresh;
  assert.equal(h.state.project,current);
  assert.equal(h.state.project.mappings[0].note,'new edit');
});

test('fresh save response can refresh list counts without rereading its detail',async()=>{
  const saved={...summary,mappings:[{id:'row'}],mapping_revision:'saved'};
  const h=harness(()=>bootstrap());h.state.project=saved;
  await h.refreshData({refreshProject:false});
  assert.equal(h.state.project,saved);
  assert.deepEqual(h.calls,['/api/bootstrap']);
});
