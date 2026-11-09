/* Starting a new project must not discard edits or hide save failures. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/workflow.js'),'utf8');
function harness({view='dashboard',confirm=false,respond=()=>[],saveProject=async()=>{}}={}) {
  const calls=[],prompts=[],state={view,dirty:false,project:{id:'project',framework_id:'framework',framework_artifact_id:'word'}};
  const context=vm.createContext({state,URL,URLSearchParams,location:{href:'http://localhost/?workflow=framework&step=3',search:'?workflow=framework&step=3'},
    history:{replaceState(){}},window:{addEventListener(){},scrollTo(){},confirm:message=>{prompts.push(message);return confirm;}},
    document:{addEventListener(){},querySelector:()=>null},render(){},saveProject,
    api:async(url,options)=>{calls.push({url,options});return respond(url,options);},setTimeout:()=>1,clearTimeout(){}});
  vm.runInContext(source.replace('  return {view,enter,url,resumeProject,beforeLeave,setStep,projectArchives,isPreview:',
    '  globalThis.entryTest={enter,getUi:()=>ui};\n  return {view,enter,url,resumeProject,beforeLeave,setStep,projectArchives,isPreview:'),context);
  const h=context.entryTest,ui=h.getUi();
  ui.record={id:'framework',name:'旧项目',projects:{word:'project'}};ui.name='旧项目';ui.step=3;
  ui.editor={revision:1,chapters:[{id:'chapter',text:'旧正文',revision:0}]};
  return {...h,ui,state,calls,prompts};
}
test('new project from the list opens a blank upload without creating a database record',async()=>{
  const h=harness({view:'projects'});await h.enter();
  assert.equal(h.state.view,'dashboard');assert.equal(h.state.project,null);
  assert.equal(h.getUi().record,null);assert.equal(h.getUi().name,'');assert.equal(h.getUi().step,1);
  assert.deepEqual(h.calls.map(c=>c.url),['/api/frameworks']);assert.equal(h.calls[0].options,undefined);
});
test('cancelling dirty directory edits keeps the current project and draft',async()=>{
  const h=harness();h.ui.outlineDirty=true;h.ui.outlineDraft=[{text:'未保存目录'}];
  await assert.rejects(h.enter(),/已保留未保存修改/);
  assert.equal(h.getUi(),h.ui);assert.equal(h.ui.outlineDraft[0].text,'未保存目录');assert.equal(h.calls.length,0);
});
test('cancelling unused business notes keeps their text and current project',async()=>{
  const h=harness();h.ui.businessNotesDirty=true;h.ui.businessNotes='尚未用于生成的说明';
  await h.enter();assert.equal(h.getUi(),h.ui);assert.equal(h.ui.businessNotes,'尚未用于生成的说明');
  assert.equal(h.state.project.id,'project');assert.equal(h.prompts.length,1);assert.equal(h.calls.length,0);
});
test('chapter save failure prevents leaving and keeps the unsaved body',async()=>{
  const h=harness({respond:()=>{throw Error('保存失败');}});h.ui.edits.set('chapter','未保存正文');
  await assert.rejects(h.enter(),/保存失败/);assert.equal(h.getUi(),h.ui);
  assert.equal(h.ui.edits.get('chapter'),'未保存正文');assert.equal(h.calls.length,1);assert.equal(h.calls[0].options.method,'PATCH');
});
test('material save failure prevents clearing the active project',async()=>{
  const h=harness({view:'projects',saveProject:async()=>{throw Error('核对保存失败');}});h.state.dirty=true;
  await assert.rejects(h.enter(),/核对保存失败/);assert.equal(h.state.project.id,'project');assert.equal(h.state.dirty,true);
});
test('an in-flight action prevents starting another project',async()=>{
  const h=harness();h.ui.pending=true;await assert.rejects(h.enter(),/等待当前操作完成/);
  assert.equal(h.getUi(),h.ui);assert.equal(h.calls.length,0);
});
