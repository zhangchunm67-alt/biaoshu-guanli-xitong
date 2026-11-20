/* A success message must not hide an error that is still on screen; a new error replaces everything. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\ninit\(\);\s*$/,'');

function harness(){
  const shown=[];
  const root={append(el){el.parent=root;shown.push(el);}};
  const element=()=>{const classes=new Set();const el={
    set className(v){classes.clear();v.split(/\s+/).filter(Boolean).forEach(c=>classes.add(c));},
    classList:{contains:c=>classes.has(c)},setAttribute(){},innerHTML:'',
    remove(){const i=shown.indexOf(el);if(i>=0)shown.splice(i,1);}};return el;};
  const context=vm.createContext({URL,URLSearchParams,setTimeout:()=>0,clearTimeout(){},
    document:{addEventListener(){},createElement:element,body:{style:{}},
      querySelector:q=>q==='#toast-root'?root:null,
      querySelectorAll:q=>q==='#toast-root .toast'?[...shown]:[]},
    window:{addEventListener(){},scrollTo(){}},IntersectionObserver:class{disconnect(){} observe(){}},
    tenderFlow:{projectArchives:()=>''}});
  vm.runInContext(source+';globalThis.toastTest={toast};',context);
  const text=()=>shown.map(el=>(el.classList.contains('error')?'E:':'S:')+el.innerHTML.replace(/<[^>]+>/g,''));
  return {toast:context.toastTest.toast,text};
}

test('success toast keeps a visible error',()=>{
  const h=harness();
  h.toast('尚有 1 条废标红线未过关',true);
  h.toast('正文已保存。');
  assert.deepEqual(h.text(),['E:尚有 1 条废标红线未过关','S:正文已保存。']);
});

test('new error replaces older messages, and success replaces success',()=>{
  const h=harness();
  h.toast('已保存');h.toast('已删除');
  assert.deepEqual(h.text(),['S:已删除']);
  h.toast('旧错误',true);h.toast('新错误',true);
  assert.deepEqual(h.text(),['E:新错误']);
});
