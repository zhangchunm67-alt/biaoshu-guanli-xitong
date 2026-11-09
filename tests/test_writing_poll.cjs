/* Delayed read responses must not roll back newer writing actions. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/workflow.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const job=(values={})=>({id:'old',status:'completed',version:2,chapters:[
  {chapter_id:'BidSection1',text:'第一章候选'},
  {chapter_id:'BidSection2',text:'第二章候选'}
],accepted_chapter_ids:[],stale_chapter_ids:[],chapter_revisions:{BidSection1:0,BidSection2:0},...values});
const writing=currentJob=>({case_revision:1,source_revision:[1,1],state_revision:1,
  files:[{name:'案例.txt'}],case_sources:[{id:'case',text:'原案例'}],procurement_sources:[],
  model:{ready:true},job:currentJob});

function harness(respond,currentJob=job()) {
  const calls=[],timers=new Map();let timerId=0;
  const state={view:'dashboard',dirty:false,project:{id:'project',framework_id:'framework',
    framework_artifact_id:'word',mapping_revision:'mapping-1'}};
  const context=vm.createContext({state,URL,URLSearchParams,location:{href:'http://localhost/'},
    history:{replaceState(){}},window:{confirm:()=>true,addEventListener(){}},
    document:{addEventListener(){},querySelector:()=>null},
    render(){},toast(){},toFiles:async files=>files,
    api:async(url,options)=>{calls.push({url,options});return respond(url,options);},
    setTimeout:callback=>{const id=++timerId;timers.set(id,callback);return id;},
    clearTimeout:id=>timers.delete(id)});
  const instrumented=source.replace('  return {view,enter,url,resumeProject,beforeLeave,setStep,projectArchives,isPreview:',
    '  globalThis.writingTest={ui,loadWriting,generateWriting,acceptWriting,saveDrafts,uploadCases};\n  return {view,enter,url,resumeProject,beforeLeave,setStep,projectArchives,isPreview:');
  vm.runInContext(instrumented,context);
  const h=context.writingTest;
  h.ui.record={id:'framework',projects:{word:'project'}};
  h.ui.step=5;h.ui.chapter='BidSection1';h.ui.writing=writing(currentJob);
  h.ui.editor={revision:0,chapters:[{id:'BidSection1',text:'',revision:0},{id:'BidSection2',text:'',revision:0}]};
  return {...h,state,calls,timers};
}

test('delayed completed GET cannot replace newly queued generation or stop its polling',async()=>{
  const old=deferred(),newJob=job({id:'new',status:'queued',version:1,target_chapter_ids:['BidSection1']});
  const h=harness((url,options)=>options?.method==='POST'?newJob:old.promise);
  const read=h.loadWriting();
  await h.generateWriting(true);
  old.resolve(writing(job()));await read;
  assert.equal(h.ui.writing.job.id,'new');
  assert.equal(h.ui.writing.job.status,'queued');
  assert.equal(h.timers.size,1);
  assert.deepEqual(clone(h.calls[1].options.body.chapter_ids),['BidSection1']);
});

test('delayed job refresh cannot undo adoption of an unrelated chapter during generation',async()=>{
  const old=deferred(),requested=deferred();
  const current=job({status:'running',target_chapter_ids:['BidSection2']});
  const adopted=job({...current,version:3,accepted_chapter_ids:['BidSection1'],chapter_revisions:{BidSection1:1,BidSection2:0}});
  const h=harness((url,options)=>{
    if(url.endsWith('?view=status'))return {case_revision:1,source_revision:[1,1],state_revision:2,job:{id:'old',status:'running',version:2}};
    if(url.endsWith('?view=job')){requested.resolve();return old.promise;}
    if(options?.method==='POST')return {writing:writing(adopted),editor:{revision:1,mapping_revision:'mapping-2',
      chapters:[{id:'BidSection1',text:'第一章候选',revision:1},{id:'BidSection2',text:'',revision:0}]}};
    assert.fail('unexpected request '+url);
  },current);
  const read=h.loadWriting({poll:true});await requested.promise;
  await h.acceptWriting();
  old.resolve({case_revision:1,source_revision:[1,1],state_revision:2,job:current});await read;
  assert.deepEqual(clone(h.ui.writing.job.accepted_chapter_ids),['BidSection1']);
  assert.equal(h.ui.writing.job.version,3);
  assert.equal(h.ui.editor.chapters[0].text,'第一章候选');
  assert.equal(h.state.project.mapping_revision,'mapping-2');
  assert.equal(h.timers.size,1);
});

test('delayed GET error cannot replace successful manual-save state',async()=>{
  const old=deferred();
  const h=harness((url,options)=>options?.method==='PATCH'?{revision:1,mapping_revision:'mapping-2',
    chapters:[{id:'BidSection1',text:'人工修改',revision:1},{id:'BidSection2',text:'',revision:0}]}:old.promise);
  const read=h.loadWriting();
  h.ui.edits.set('BidSection1','人工修改');await h.saveDrafts();
  old.reject(new Error('旧请求连接失败'));await read;
  assert.equal(h.ui.writingError,'');
  assert.deepEqual(clone(h.ui.writing.job.stale_chapter_ids),['BidSection1']);
  assert.equal(h.ui.editor.chapters[0].text,'人工修改');
  assert.equal(h.ui.edits.size,0);
});

test('delayed old case response cannot replace newly uploaded sources',async()=>{
  const old=deferred(),updated={...writing(job()),case_revision:2,source_revision:[2,1],
    files:[{name:'新案例.txt'}],case_sources:[{id:'new-case',text:'新案例'}]};
  const h=harness((url,options)=>options?.method==='POST'?updated:old.promise);
  const read=h.loadWriting();
  await h.uploadCases([{name:'新案例.txt',size:10}]);
  old.resolve(writing(job()));await read;
  assert.equal(h.ui.writing.case_revision,2);
  assert.equal(h.ui.writing.case_sources[0].id,'new-case');
});

test('later query wins when two reads finish out of order',async()=>{
  const old=deferred();let reads=0;
  const h=harness(()=>++reads===1?old.promise:writing(job({version:3})));
  const first=h.loadWriting();await h.loadWriting();
  old.resolve(writing(job()));await first;
  assert.equal(h.ui.writing.job.version,3);
});

test('unchanged status polling keeps sources and does not request full candidates',async()=>{
  const current=job({status:'running',target_chapter_ids:['BidSection1']});
  const h=harness(()=>({case_revision:1,source_revision:[1,1],state_revision:1,
    job:{id:'old',status:'running',version:2,progress:50}}),current);
  await h.loadWriting({poll:true});
  assert.deepEqual(h.calls.map(call=>call.url),['/api/projects/project/writing?view=status']);
  assert.equal(h.ui.writing.case_sources[0].text,'原案例');
  assert.equal(h.ui.writing.job.chapters.length,2);
  assert.equal(h.ui.writing.job.progress,50);
  assert.equal(h.timers.size,1);
});
