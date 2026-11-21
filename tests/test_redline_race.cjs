/* An older redline status read must not overwrite a newer AI review or saved acknowledgement. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/workflow.js'),'utf8');
const marker='  return {view,enter,url,resumeProject,beforeLeave,setStep,projectArchives,isPreview:';

function harness(){
  const reads=[],posts=[];
  const state={view:'dashboard',dirty:false,project:{id:'project',framework_id:'framework',framework_artifact_id:'word'}};
  const context=vm.createContext({state,URL,URLSearchParams,location:{href:'http://localhost/',search:''},
    history:{replaceState(){}},window:{addEventListener(){},scrollTo(){},confirm:()=>true},
    document:{addEventListener(){},querySelector:()=>null},render(){},toast(){},
    api:(url,options)=>new Promise(resolve=>(options?.method==='POST'?posts:reads).push(resolve)),
    setTimeout:()=>1,clearTimeout(){}});
  assert.ok(source.includes(marker));
  vm.runInContext(source.replace(marker,'  globalThis.redlineTest={loadRedline,reviewRedlineAi,saveRedline,getUi:()=>ui};\n'+marker),context);
  const h=context.redlineTest,ui=h.getUi();
  ui.record={id:'framework',projects:{word:'project'}};ui.step=5;ui.redlineAiOutboundAck=true;
  return {...h,ui,reads,posts};
}
const scan=(label,extra={})=>({items:[{id:'r1',label}],acknowledged_ids:[],pending_count:0,complete:true,...extra});
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('AI review result survives a status read that was started before it',async()=>{
  const h=harness();
  const stale=h.loadRedline();await settle();
  const review=h.reviewRedlineAi();await settle();
  h.posts.shift()(scan('AI 结果',{ai_review:{items:[]}}));await review;
  h.reads.shift()(scan('过期读取',{ai_review_stale:true}));await stale;
  assert.equal(h.ui.redline.items[0].label,'AI 结果');
  assert.equal(h.ui.redline.ai_review_stale,undefined);
});

test('saved acknowledgement survives an older status read',async()=>{
  const h=harness();
  h.ui.redline=scan('初始');h.ui.redline.mapping_revision='m';h.ui.redline.review_revision=1;
  const stale=h.loadRedline({keepLocal:true});await settle();
  const save=h.saveRedline({quiet:true});await settle();
  h.posts.shift()(scan('已保存',{acknowledged_ids:['r1']}));await save;
  h.reads.shift()(scan('过期读取'));await stale;
  assert.equal(h.ui.redline.items[0].label,'已保存');
});
