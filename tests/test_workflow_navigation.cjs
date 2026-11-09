/* Writing navigation explains incomplete prerequisites without saving or discarding them. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/workflow.js'),'utf8');

function harness({guided=false}={}) {
  const calls=[],messages=[],focused=[],timers=[],guidance=[];
  const state={view:'dashboard',busy:false,dirty:false,project:{id:'project',framework_id:'framework',
    framework_artifact_id:'word',mapping_revision:'revision-1'}};
  const projectUi={step:'common'};
  let h;
  const context=vm.createContext({state,projectUi,URL,URLSearchParams,
    location:{href:'http://127.0.0.1/'},history:{replaceState(){}},
    window:{addEventListener(){},scrollTo(){},confirm(){assert.fail('navigation must not discard edits');}},
    document:{addEventListener(){},querySelector:selector=>({
      scrollIntoView(){},focus(){focused.push(selector);}})},
    esc:value=>String(value??''),render(){},toast:message=>messages.push(message),
    projectReviewed:()=>!guided&&!state.dirty,
    saveProject:async()=>assert.fail('navigation must not save material choices'),
    guideMaterialCompletion:async target=>{
      guidance.push({target,step:h.ui.step});
      if(guided)projectUi.step='special';
      return guided;
    },
    api:async(url,options)=>{
      calls.push({url,options});
      assert.equal(options,undefined,'navigation must not confirm or save prerequisites');
      if(url.endsWith('/chapter-drafts'))return {revision:0,chapters:[{id:'BidSection1',text:''}]};
      if(url.endsWith('/writing'))return {files:[],case_revision:0,job:null};
      assert.fail('unexpected request '+url);
    },
    setTimeout:callback=>{timers.push(callback);return timers.length;},clearTimeout(){}});
  const instrumented=source.replace('  return {view,enter,url,resumeProject,beforeLeave,setStep,projectArchives,isPreview:',
    '  globalThis.navigationTest={ui,setStep,stepper};\n  return {view,enter,url,resumeProject,beforeLeave,setStep,projectArchives,isPreview:');
  vm.runInContext(instrumented,context);
  h=context.navigationTest;
  h.ui.record={id:'framework',requirements_confirmed:true,projects:{word:'project'}};
  h.ui.step=4;
  return {...h,state,projectUi,calls,messages,focused,guidance,flush:()=>{while(timers.length)timers.shift()();}};
}

test('writing step stays clickable before prerequisites and disables while busy',()=>{
  const h=harness();h.ui.record=null;h.state.project=null;
  const button=()=>h.stepper().match(/<button[^>]+data-id="5"[^>]*>/)[0];
  assert.doesNotMatch(button(),/disabled/);
  h.ui.busy='正在解析';assert.match(button(),/disabled/);
});

test('missing upload directs to the upload control without creating anything',async()=>{
  const h=harness();h.ui.record=null;h.state.project=null;h.ui.name='尚未上传的项目';
  await h.setStep(5);
  assert.equal(h.ui.step,2);assert.match(h.ui.error,/上传并解析/);
  assert.equal(h.ui.name,'尚未上传的项目');assert.equal(h.focused.length,0);
  h.flush();assert.deepEqual(h.focused,['[data-wf="upload"]']);
  assert.equal(h.calls.length,0);assert.equal(h.guidance.length,0);
});

test('unconfirmed or unsaved requirements guide to the right control without losing edits',async()=>{
  for(const dirty of [false,true]){
    const h=harness();h.ui.record.requirements_confirmed=false;h.ui.requirementsDirty=dirty;
    h.ui.requirements=[{text:'人工修改的要求'}];h.ui.outlineDirty=true;
    h.ui.outlineDraft=[{text:'尚未保存目录'}];h.ui.edits.set('BidSection1','未保存正文');
    await h.setStep(5);h.flush();
    assert.equal(h.ui.step,2);assert.equal(h.ui.requirementsDirty,dirty);
    assert.equal(h.ui.requirements[0].text,'人工修改的要求');
    assert.equal(h.ui.outlineDraft[0].text,'尚未保存目录');
    assert.equal(h.ui.edits.get('BidSection1'),'未保存正文');
    assert.deepEqual(h.focused,[dirty?'[data-wf="save-requirements"]':'[data-wf="confirm-requirements"]']);
    assert.equal(h.calls.length,0);assert.equal(h.guidance.length,0);
  }
});

test('unsaved outline stays intact and opens its save control',async()=>{
  const h=harness();h.ui.outlineDirty=true;h.ui.outlineDraft=[{text:'未保存章节'}];
  await h.setStep(5);h.flush();
  assert.equal(h.ui.step,3);assert.equal(h.ui.outlineEditing,true);
  assert.equal(h.ui.outlineDirty,true);assert.equal(h.ui.outlineDraft[0].text,'未保存章节');
  assert.deepEqual(h.focused,['[data-wf="save-outline"]']);assert.equal(h.calls.length,0);
});

test('unconfirmed directory points to confirmation without creating a working project',async()=>{
  const h=harness();h.ui.record.projects={};h.state.project=null;
  await h.setStep(5);h.flush();
  assert.equal(h.ui.step,3);assert.match(h.ui.error,/确认目录，进入系统填充/);
  assert.deepEqual(h.focused,['[data-wf="confirm"]']);
  assert.equal(h.calls.length,0);assert.equal(h.guidance.length,0);
});

test('material guidance runs on the material page and keeps unsaved selections',async()=>{
  const h=harness({guided:true});h.ui.step=3;h.state.dirty=true;
  h.state.project.mappings=[{id:'map',material_ids:['new-choice'],note:'尚未保存备注'}];
  await h.setStep(5);
  assert.equal(h.ui.step,4);assert.equal(h.projectUi.step,'special');
  assert.equal(h.state.dirty,true);assert.equal(h.state.project.mappings[0].note,'尚未保存备注');
  assert.deepEqual(h.guidance,[{target:'export',step:4}]);assert.equal(h.calls.length,0);
});

test('completed prerequisites load writing without confirming or saving material choices',async()=>{
  const h=harness();h.ui.businessNotes='未用于生成的业务信息';h.ui.businessNotesDirty=true;
  await h.setStep(5);
  assert.equal(h.ui.step,5);assert.deepEqual(h.guidance,[{target:'export',step:4}]);
  assert.deepEqual(h.calls.map(call=>call.url),['/api/projects/project/chapter-drafts','/api/projects/project/writing']);
  assert.equal(h.ui.businessNotes,'未用于生成的业务信息');assert.equal(h.ui.businessNotesDirty,true);
});

test('busy material operation cannot be bypassed through writing navigation',async()=>{
  const h=harness();h.state.busy=true;
  await h.setStep(5);
  assert.equal(h.ui.step,4);assert.equal(h.guidance.length,0);assert.equal(h.calls.length,0);
});
