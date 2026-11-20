/* Overlapping picker searches/refreshes must not let an older response overwrite a newer one. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\ninit\(\);\s*$/,'');

function harness(){
  const context=vm.createContext({URL,URLSearchParams,
    document:{addEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],body:{style:{}}},
    window:{addEventListener(){},scrollTo(){}},setTimeout:()=>0,clearTimeout(){},
    IntersectionObserver:class{disconnect(){} observe(){}},tenderFlow:{projectArchives:()=>''}});
  vm.runInContext(source+`
    globalThis.pending=[];
    api=url=>new Promise(resolve=>pending.push({query:new URLSearchParams(url.split('?')[1]).get('q'),resolve}));
    globalThis.pickerTest={projectUi,loadProjectPicker};`,context);
  return {...context.pickerTest,pending:context.pending};
}
const page=(query,total)=>({items:[],total,page:1,page_size:10,query});

test('older search response arriving last is dropped',async()=>{
  const h=harness();
  const narrow=h.loadProjectPicker('I01',1,'保险核对材料11');
  const cleared=h.loadProjectPicker('I01',1,'');
  h.pending[1].resolve(page('',12));
  assert.equal(await cleared,true);
  h.pending[0].resolve(page('保险核对材料11',1));
  assert.equal(await narrow,false);
  assert.equal(h.projectUi.pickers.I01.total,12);
  assert.equal(h.projectUi.pickers.I01.query,'');
});

test('refresh started while a search is pending uses the newer query',async()=>{
  const h=harness();
  h.projectUi.pickers.I01={...page('保险核对材料11',1),query:'保险核对材料11'};
  h.loadProjectPicker('I01',1,'');
  h.loadProjectPicker('I01');
  assert.equal(h.pending[1].query,'');
});

test('typed search text survives until its results arrive',async()=>{
  const h=harness();
  h.projectUi.pickerTyped.I01='保险核对材料11';
  const load=h.loadProjectPicker('I01',1,'保险核对材料11');
  assert.equal(h.projectUi.pickerTyped.I01,'保险核对材料11');
  h.pending[0].resolve(page('保险核对材料11',1));
  await load;
  assert.equal(h.projectUi.pickerTyped.I01,undefined);
  assert.equal(h.projectUi.pickers.I01.query,'保险核对材料11');
});
