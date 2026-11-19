/* Writing navigation explains incomplete prerequisites without saving or discarding them. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/workflow.js'),'utf8');

function harness({guided=false,confirmResult=null,redline=null}={}) {
  const calls=[],messages=[],focused=[],timers=[],guidance=[],confirms=[];
  const state={view:'dashboard',busy:false,dirty:false,project:{id:'project',framework_id:'framework',
    framework_artifact_id:'word',mapping_revision:'revision-1'}};
  const projectUi={step:'common'};
  let h;
  const context=vm.createContext({state,projectUi,URL,URLSearchParams,
    location:{href:'http://127.0.0.1/'},history:{replaceState(){}},
    window:{addEventListener(){},scrollTo(){},confirm(message){
      confirms.push(message);
      if(confirmResult===null)assert.fail('navigation must not discard edits');
      return confirmResult;
    }},
    $:()=>null,document:{addEventListener(){},querySelector:selector=>({
      outerHTML:"",scrollIntoView(){},focus(){focused.push(selector);},closest(){return null;},querySelector(){return null;}})},
    esc:value=>String(value??''),date:value=>String(value??''),render(){},toast:message=>messages.push(message),
    exportProject:async target=>calls.push({url:'export',target}),
    showStagePreview:async()=>calls.push({url:'stage-preview'}),
    projectReviewed:()=>!guided&&!state.dirty,
    commonReviewed:()=>!state.dirty,
    saveProject:async()=>assert.fail('navigation must not save material choices'),
    guideMaterialCompletion:async target=>{
      guidance.push({target,step:h.ui.step});
      if(guided)projectUi.step='special';
      return guided;
    },
    api:async(url,options)=>{
      calls.push({url,options});
      if(url.endsWith('/chapter-drafts')){assert.equal(options,undefined,'navigation must not save drafts');return {revision:0,chapters:[{id:'BidSection1',text:''}]};}
      if(url.endsWith('/writing?view=chat'))return {files:[],case_revision:0,model:{ready:false},turns:[]};
      if(url.endsWith('/writing')){assert.equal(options,undefined,'navigation must not save writing');return {files:[],case_revision:0,job:null};}
      if(url.endsWith('/redline-scan')){
        if(options?.method==='POST')return redline||{items:[],complete:true,pending_count:0,acknowledged_ids:[],input_token:'token'};
        assert.equal(options,undefined,'redline reads must not write');
        return redline||{items:[],complete:true,pending_count:0,acknowledged_ids:[],input_token:'token'};
      }
      assert.fail('unexpected request '+url);
    },
    setTimeout:callback=>{timers.push(callback);return timers.length;},clearTimeout(){}});
  const instrumented=source.replace('  return {view,enter,url,resumeProject,beforeLeave,setStep,projectArchives,isPreview:',
    '  globalThis.navigationTest={ui,setStep,stepper,action,paint,redlinePanel,adoptAiRedline,ensureRedlineForExport};\n  return {view,enter,url,resumeProject,beforeLeave,setStep,projectArchives,isPreview:');
  vm.runInContext(instrumented,context);
  h=context.navigationTest;
  h.ui.record={id:'framework',requirements_confirmed:true,projects:{word:'project'}};
  h.ui.step=4;
  return {...h,state,projectUi,calls,messages,focused,guidance,confirms,flush:()=>{while(timers.length)timers.shift()();}};
}

test('writing step stays clickable before prerequisites and disables while busy',()=>{
  const h=harness();h.ui.record=null;h.state.project=null;
  const button=()=>h.stepper().match(/<button[^>]+data-id="5"[^>]*>/)[0];
  assert.doesNotMatch(button(),/disabled/);
  h.ui.busy='正在解析';assert.match(button(),/disabled/);
});

test('stepper forks into A draft export and B writing after system filling',async()=>{
  const h=harness();
  const html=h.stepper();
  assert.match(html,/wf-stepper-fork/);
  assert.match(html,/wf-fork/);
  assert.match(html,/data-wf="export"/);
  assert.match(html,/导出半成品标书/);
  assert.match(html,/<span class="wf-number">A<\/span>/);
  assert.match(html,/<span class="wf-number">B<\/span>/);
  assert.match(html,/data-id="5"[^>]*>[\s\S]*?<span class="wf-number">B<\/span>/);
  const branch=()=>html.match(/<button[^>]+data-wf="export"[^>]*>/)[0];
  assert.doesNotMatch(branch(),/disabled/);
  h.state.dirty=true;
  const dirty=h.stepper();
  assert.match(dirty.match(/<button[^>]+data-wf="export"[^>]*>/)[0],/disabled/);
  h.state.dirty=false;
  h.ui.step=5;
  assert.match(h.stepper(),/wf-fork is-b-current/);
  h.ui.step=4;
  assert.doesNotMatch(h.stepper(),/wf-fork is-b-current/);
  await h.action({dataset:{wf:'export'}});
  assert.deepEqual(h.calls.map(call=>call.url),['stage-preview']);
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

test('jumping to outline or filling without finishing prior steps lands on the unfinished gate',async()=>{
  const outline=harness();outline.ui.record.requirements_confirmed=false;outline.ui.step=2;
  await outline.setStep(3);outline.flush();
  assert.equal(outline.ui.step,2);assert.match(outline.ui.error,/确认采购要求/);
  assert.deepEqual(outline.focused,['[data-wf="confirm-requirements"]']);

  const filling=harness();filling.ui.record.projects={};filling.state.project=null;filling.ui.step=2;
  await filling.setStep(4);filling.flush();
  assert.equal(filling.ui.step,3);assert.match(filling.ui.error,/确认目录，进入系统填充/);
  assert.deepEqual(filling.focused,['[data-wf="confirm"]']);

  const locked=harness();locked.ui.record=null;locked.state.project=null;locked.ui.step=2;
  const html=locked.stepper();
  assert.doesNotMatch(html.match(/<button[^>]+data-id="3"[^>]*>/)[0],/disabled/);
  assert.doesNotMatch(html.match(/<button[^>]+data-id="4"[^>]*>/)[0],/disabled/);
});

test('material guidance runs on the material page and keeps unsaved selections',async()=>{
  const h=harness({guided:true});h.ui.step=3;h.state.dirty=true;
  h.state.project.mappings=[{id:'map',material_ids:['new-choice'],note:'尚未保存备注'}];
  await h.setStep(5);
  assert.equal(h.ui.step,4);assert.equal(h.projectUi.step,'special');
  assert.equal(h.state.dirty,true);assert.equal(h.state.project.mappings[0].note,'尚未保存备注');
  assert.deepEqual(h.guidance,[{target:'export',step:4}]);assert.equal(h.calls.length,0);
});

test('redline panel labels AI passes as 通过 and keeps rule warnings visible',()=>{
  const h=harness();
  h.ui.redline={items:[{id:'r1',label:'未附营业执照',text:'未附营业执照的，投标无效。',
    status:'risk',findings:['本地规则：材料待补'],hints:['人工提示'],acknowledged:false,
    empty_chapter_ids:[],material_category_ids:[]}],
    pending_count:1,complete:false,saved_ids:[],acknowledged_ids:[],
    ai_review:{r1:{verdict:'satisfied',reason:'正文已提供营业执照。',
      evidence:'我方已提供营业执照复印件。',evidence_verified:false,chapter_ids:[],suggestion:'递交前核对原件。'}},
    ai_reviewed_at:'2026-11-09T00:00:00Z'};
  const html=h.redlinePanel();
  assert.match(html,/AI：通过/);
  assert.match(html,/AI 核对：通过 1/);
  assert.match(html,/AI 判定通过，但系统规则仍发现风险/);
  assert.match(html,/未能逐字核对，请人工确认/);
  assert.match(html,/对应正文/);
  assert.match(html,/AI 建议/);
  assert.match(html,/我方已提供营业执照复印件/);
  assert.match(html,/递交前核对原件/);
  assert.match(html,/本地规则：材料待补/);
  assert.match(html,/人工提示/);
  assert.match(html,/规则提示/);
  assert.match(html,/type="checkbox"/);
  assert.doesNotMatch(html,/一键全部通过/);
  assert.match(html,/AI 逐条核对/);
});

test('stale AI review is announced without showing old verdicts',()=>{
  const h=harness();
  h.ui.redline={items:[{id:'r1',label:'响应时限',text:'两小时内响应。',status:'attention',
    findings:[],hints:[],acknowledged:false}],pending_count:1,complete:false,
    saved_ids:[],acknowledged_ids:[],ai_review_stale:true};
  const html=h.redlinePanel();
  assert.match(html,/AI 核对结果已过期/);
  assert.doesNotMatch(html,/AI：/);
});

test('pending-only filter hides redlines that already passed',()=>{
  const h=harness();
  h.ui.redline={items:[
    {id:'passed',label:'已通过',text:'',status:'attention',findings:[],hints:[],acknowledged:true},
    {id:'pending',label:'待确认',text:'',status:'attention',findings:[],hints:[],acknowledged:false}
  ],pending_count:1,complete:false,saved_ids:['passed'],acknowledged_ids:['passed']};
  h.ui.redlinePendingOnly=true;
  const html=h.redlinePanel();
  assert.doesNotMatch(html,/data-redline="passed"/);
  assert.match(html,/data-redline="pending"/);
  assert.match(html,/仅显示待过关（1）/);
});

test('AI-passed items can be adopted in one action while risky items stay pending',()=>{
  const h=harness();
  h.ui.step=5;
  h.ui.redline={items:[
    {id:'pass',auto_checked:true,label:'响应时限',text:'两小时内响应。',status:'attention',findings:[],hints:[],manual_check:false,acknowledged:false},
    {id:'risk',label:'营业执照',text:'未附营业执照的，投标无效。',status:'risk',findings:['材料待补'],hints:[],manual_check:false,acknowledged:false},
    {id:'unclear',label:'签章',text:'须签字盖章。',status:'attention',findings:[],hints:['人工核验签章'],manual_check:true,acknowledged:false},
    {id:'unverified',label:'业绩',text:'须提供同类合同。',status:'attention',findings:[],hints:[],manual_check:false,acknowledged:false},
    {id:'manual',label:'保证金',text:'须缴纳保证金。',status:'attention',findings:[],hints:['人工核对保证金到账时间。'],manual_check:true,acknowledged:false}
  ],pending_count:5,complete:false,saved_ids:[],acknowledged_ids:[],
  ai_review:{
    pass:{verdict:'satisfied',reason:'已承诺。',evidence:'两小时内响应。',evidence_verified:true,chapter_ids:[],suggestion:''},
    risk:{verdict:'satisfied',reason:'已提供。',evidence:'已提供营业执照。',evidence_verified:true,chapter_ids:[],suggestion:''},
    unclear:{verdict:'unclear',reason:'签章无法从正文判断。',evidence:'',evidence_verified:false,chapter_ids:[],suggestion:''},
    unverified:{verdict:'satisfied',reason:'已提供合同。',evidence:'附合同复印件。',evidence_verified:false,chapter_ids:[],suggestion:''},
    manual:{verdict:'satisfied',reason:'已承诺缴纳。',evidence:'已缴纳保证金。',evidence_verified:true,chapter_ids:[],suggestion:''}
  }};
  h.adoptAiRedline();
  assert.equal(h.ui.redline.items[0].acknowledged,true);
  assert.equal(h.ui.redline.items[1].acknowledged,false);
  assert.equal(h.ui.redline.items[2].acknowledged,false);
  assert.equal(h.ui.redline.items[3].acknowledged,false);
  assert.equal(h.ui.redline.items[4].acknowledged,false);
  assert.equal(h.ui.redline.pending_count,4);
  assert.equal(h.ui.redline.complete,false);
  assert.match(h.messages.at(-1),/已采纳 AI 通过的 1 条/);
});

test('formal export preflight blocks unconfirmed redlines',async()=>{
  const scan={items:[{id:'r1',label:'待确认',status:'attention',acknowledged:false}],
    pending_count:1,complete:false,saved_ids:[],acknowledged_ids:[]};
  const h=harness({redline:scan});
  h.ui.step=5;
  assert.equal(await h.ensureRedlineForExport(),false);
});

test('completed prerequisites load writing without confirming or saving material choices',async()=>{
  const h=harness();h.ui.businessNotes='未用于生成的业务信息';h.ui.businessNotesDirty=true;
  await h.setStep(5);
  assert.equal(h.ui.step,5);assert.deepEqual(h.guidance,[]);
  assert.deepEqual(h.calls.map(call=>call.url),['/api/projects/project/writing?view=chat','/api/projects/project/redline-scan']);
  assert.equal(h.ui.businessNotes,'未用于生成的业务信息');assert.equal(h.ui.businessNotesDirty,true);
});

test('busy material operation cannot be bypassed through writing navigation',async()=>{
  const h=harness();h.state.busy=true;
  await h.setStep(5);
  assert.equal(h.ui.step,4);assert.equal(h.guidance.length,0);assert.equal(h.calls.length,0);
});
