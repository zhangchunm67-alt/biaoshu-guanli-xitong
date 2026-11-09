/* Contextual guidance must retain real warnings without repeating every field. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\ninit\(\);\s*$/,'');
function harness() {
  const events={},nodes=new Map(),context=vm.createContext({URL,URLSearchParams,
    document:{addEventListener:(name,callback)=>events[name]=callback,querySelector:q=>nodes.get(q)||null,
      querySelectorAll:()=>[],body:{style:{}}},window:{addEventListener(){}},
    setTimeout:()=>1,clearTimeout(){},IntersectionObserver:class{disconnect(){} observe(){}},
    tenderFlow:{projectArchives:()=>''}});
  vm.runInContext(source+'\nglobalThis.copyTest={state,projectUi,renderMapping,renderMappingPreview,renderLocationDescription,openLocationPreview,renderLocationPreview,locationUi,renderCreditEvidence,renderCreditCard,materialFeedbackSummary,updateBatchSelection,exportTasks,pollExportTasks,validityBadge};',context);
  const h=context.copyTest;
  h.state.data={categories:[{id:'general',name:'普通栏目',layer:'common'},{id:'authorization',name:'授权',layer:'common'},
    {id:'text_template',name:'文书',layer:'special'}],materials:[],projects:[]};
  return {...h,context,events,nodes};
}
const mapping=(values={})=>({id:'row',category_id:'general',action:'fill',status:'ready',material_ids:[],...values});

test('normal mapping omits generic instructions and retains actual issues',()=>{
  const h=harness();
  const normal=h.renderMapping(mapping({location_match:{status:'matched',reason:'栏目名称或配置别名'},locator_id:'here'}),0,[]);
  assert.doesNotMatch(normal,/栏目名称或配置别名/);
  assert.doesNotMatch(normal,/身份证不替代|人工检查材料|已自动识别|已匹配原文条目/);
  const blocked=h.renderMapping(mapping({status:'blocked',issues:['主体与投标主体不一致'],warnings:['材料下周到期']}),0,[]);
  assert.match(blocked,/主体与投标主体不一致/);assert.match(blocked,/材料下周到期/);
});

test('authorization, manual position, and skip guidance follow the actual card',()=>{
  const h=harness();
  const html=h.renderMapping(mapping({category_id:'authorization',action:'skip',location_match:{status:'manual'}}),0,[]);
  assert.equal((html.match(/身份证附件不替代证明或授权正文/g)||[]).length,1);
  assert.equal((html.match(/跳过不视为材料齐全/g)||[]).length,1);
  assert.equal((html.match(/人工指定/g)||[]).length,1);
  assert.equal(h.renderMappingPreview(mapping({action:'skip'})),'');
  assert.match(h.renderLocationDescription('导出初稿将在此处标记“跳过”。'),/标记“跳过”/);
  assert.equal(h.renderLocationDescription(''),'');
  const original=h.renderMapping(mapping({category_id:'text_template',action:'skip',locator_id:'auth'}),0,[{id:'auth',authorization_original_body_present:true}]);
  assert.match(original,/身份证附件不替代证明或授权正文/);
  assert.doesNotMatch(original,/跳过不视为材料齐全/);
});

test('material preview distinguishes complete two pages from truncated three pages',()=>{
  const h=harness(),m={id:'material',name:'合同',files:[{id:'a'},{id:'b'}]};
  h.state.data.materials=[m];
  const row=mapping({material_ids:['material']});
  assert.match(h.renderMappingPreview(row),/共 2 页/);
  assert.doesNotMatch(h.renderMappingPreview(row),/全部展示|仅展示首页和末页/);
  m.files.push({id:'c'});
  assert.match(h.renderMappingPreview(row),/仅展示首页和末页/);
});

test('preserved original letters show source preview without template-fill controls',()=>{
  const h=harness();
  for(const title of ['投标函','承诺函','法定代表人授权书']){
    const row=mapping({category_id:'text_template',title,preserve_original:true,action:'skip',locator_id:'original'});
    const before=JSON.stringify(row),html=h.renderMapping(row,0,[{id:'original',title}]);
    assert.match(html,new RegExp(title));assert.match(html,/已跳过系统填充/);
    assert.match(html,/采购文件原表将原样保留/);
    assert.match(html,/data-location-mapping="row"/);assert.match(html,/查看原文/);
    assert.doesNotMatch(html,/material-picker|data-project-search|data-map-material|new-material|data-map-field|<select|<textarea|缺少材料|此分类暂无材料|本项目跳过/);
    assert.equal(JSON.stringify(row),before);
  }
});

test('preservation is server-directed and leaves identity attachments editable',()=>{
  const h=harness();
  const regular=h.renderMapping(mapping({category_id:'text_template',title:'承诺函'}),0,[]);
  assert.match(regular,/material-picker/);assert.match(regular,/data-map-field="action"/);
  const identity=h.renderMapping(mapping({category_id:'authorization',preserve_original:true}),1,[]);
  assert.match(identity,/授权人及代理人身份证/);assert.match(identity,/material-picker/);
  assert.match(identity,/data-map-field="action"/);assert.doesNotMatch(identity,/preserved-template-card/);
  const blocked=h.renderMapping(mapping({category_id:'text_template',title:'投标函',preserve_original:true,status:'blocked',issues:['原文位置无法定位']}),0,[]);
  assert.match(blocked,/原文位置无法定位/);assert.doesNotMatch(blocked,/已保留原文/);
  const note=h.renderMapping(mapping({category_id:'text_template',preserve_original:true,note:'签章由业务核对\n旧备注 <保留>'}),0,[]);
  assert.match(note,/签章由业务核对\n旧备注 &lt;保留&gt;/);
  assert.doesNotMatch(note,/<textarea|data-map-field="note"/);
});

test('original-source modal hides location editing while ordinary preview retains it',async()=>{
  const h=harness();let modal;
  h.context.document.createElement=()=>({setAttribute(){},showModal(){},addEventListener(){}});
  h.context.document.body.append=node=>modal=node;
  vm.runInContext('loadLocationPreview=async()=>{};',h.context);
  h.state.project={id:'project',template_name:'采购原件.docx',headings:[{id:'original',title:'投标函'}],
    mappings:[mapping({category_id:'text_template',preserve_original:true,locator_id:'original'})]};
  await h.openLocationPreview('row');
  assert.match(modal.innerHTML,/原文预览/);assert.match(modal.innerHTML,/location-modal-content/);
  assert.doesNotMatch(modal.innerHTML,/location-selector|location-locator|改选后须/);
  h.state.project.mappings[0].preserve_original=false;
  await h.openLocationPreview('row');
  assert.match(modal.innerHTML,/id="location-locator"/);assert.match(modal.innerHTML,/改选后须返回核对页保存/);
});

test('preserved-source preview returns to the source page without insertion controls',async()=>{
  const h=harness(),content={};
  h.nodes.set('.location-modal',{querySelector:()=>content});
  Object.assign(h.locationUi,{preserveOriginal:true,projectId:'project',page:2,
    result:{preview_id:'preview',title:'投标函',description:'',page_count:8,initial_page:5,target_pages:[5],markers:[]}});
  h.renderLocationPreview();
  assert.match(content.innerHTML,/原文位于预览第 5 页/);assert.match(content.innerHTML,/回到原文/);
  assert.doesNotMatch(content.innerHTML,/回到插入位置|插入位置在|隐藏标记|显示标记|location-toggle/);
  assert.doesNotMatch(content.innerHTML,/data-action="location-target"[^>]*disabled/);
  const click=()=>h.events.click({preventDefault(){},target:{closest:()=>({dataset:{action:'location-target'}})}});
  await click();assert.equal(h.locationUi.page,5);assert.match(content.innerHTML,/pages\/5/);
  h.locationUi.result.target_pages=[];h.locationUi.result.initial_page=3;h.locationUi.page=1;
  h.renderLocationPreview();assert.doesNotMatch(content.innerHTML,/data-action="location-target"[^>]*disabled/);
  await click();assert.equal(h.locationUi.page,3);
});

test('ordinary insertion preview retains its insertion target and marker toggle',()=>{
  const h=harness(),content={};
  h.nodes.set('.location-modal',{querySelector:()=>content});
  Object.assign(h.locationUi,{preserveOriginal:false,projectId:'project',page:2,
    result:{preview_id:'preview',title:'材料位置',description:'',page_count:8,initial_page:5,target_pages:[5],markers:[{page:5,kind:'after',y:.3,label:'在此插入'}]}});
  h.renderLocationPreview();
  assert.match(content.innerHTML,/插入位置在预览第 5 页/);assert.match(content.innerHTML,/回到插入位置/);
  assert.match(content.innerHTML,/location-toggle/);assert.match(content.innerHTML,/隐藏标记/);
  h.locationUi.result.markers=[];h.renderLocationPreview();
  assert.doesNotMatch(content.innerHTML,/location-toggle/);
});

test('attachment boundary appears once while concrete identity conflicts remain',()=>{
  const h=harness(),note='身份证附件不替代证明或授权正文，空白信息和签章须核对。';
  const html=h.renderMapping(mapping({category_id:'authorization',warnings:[note,
    '甲证件：本次仅装配身份证附件，不代表身份证明或授权正文已填写；人员、日期和签章由商务核对。',
    '乙证件：仅作为身份证附件；原证明/授权正文、人员、日期和签章须单独核对。',
    '授权书正文与所选身份证姓名不一致，请核对双方人员。'],
    issues:['甲证件：身份证附件不能替代证明/授权正文，目标节未识别到相应文书正文。']}),0,[]);
  assert.equal(html.split(note).length-1,1);
  assert.doesNotMatch(html,/本次仅装配身份证附件|仅作为身份证附件/);
  assert.match(html,/授权书正文与所选身份证姓名不一致/);
  assert.match(html,/目标节未识别到相应文书正文/);
});

test('form summary links to field details and retains errors without a field',()=>{
  const h=harness(),feedback={errors:{subject:'主体不能为空',files:'已有材料没有图片'},warnings:{years:'年度需核对'},project_warnings:[]};
  const html=h.materialFeedbackSummary(feedback,new Set(['subject','years']),false);
  assert.match(html,/请先修正 2 项问题/);assert.match(html,/请核对 1 项提醒/);
  assert.match(html,/data-id="subject"/);assert.match(html,/data-id="years"/);
  assert.doesNotMatch(html,/主体不能为空|年度需核对/);
  assert.match(html,/已有材料没有图片/);
});

test('project impact is displayed only for a changed material with known affected warnings',()=>{
  const h=harness(),feedback={errors:{},warnings:{},project_warnings:['项目甲：主体不一致']};
  assert.equal(h.materialFeedbackSummary(feedback,new Set(),false),'');
  assert.match(h.materialFeedbackSummary(feedback,new Set(),true),/受影响项目需重新核对.*项目甲/s);
  assert.equal(h.materialFeedbackSummary({...feedback,project_warnings:[]},new Set(),true),'');
});

test('field-summary action focuses the corresponding input',async()=>{
  const h=harness();let focused=false,scrolled=false;
  const field={focus:()=>focused=true,scrollIntoView:()=>scrolled=true};
  const target={dataset:{action:'focus-material-field',id:'subject'},closest:()=>({elements:{namedItem:id=>id==='subject'?field:null}})};
  await h.events.click({target:{closest:()=>target},preventDefault(){}});
  assert.equal(focused,true);assert.equal(scrolled,true);
});

test('empty-material-group warning appears only when every image is selected',()=>{
  const h=harness(),boxes=[{checked:false},{checked:false}],all={},warning={},count={},submit={},error={};
  const nodes={'#batch-select-all':all,'#batch-empty-warning':warning,'#batch-selection-count':count,'button[type="submit"]':submit,'.form-error':error};
  h.nodes.set('form[data-form="remove-files"]',{querySelector:q=>nodes[q],querySelectorAll:()=>boxes});
  h.updateBatchSelection();assert.equal(warning.hidden,true);
  boxes[0].checked=true;h.updateBatchSelection();assert.equal(warning.hidden,true);assert.equal(all.indeterminate,true);
  boxes[1].checked=true;h.updateBatchSelection();assert.equal(warning.hidden,false);assert.equal(count.textContent,'已选 2 / 2 张');
  boxes[1].checked=false;h.updateBatchSelection();assert.equal(warning.hidden,true);
});

test('credit evidence keeps conclusions visible, folds technical IDs, and flags only invalid evidence',()=>{
  const h=harness(),evidence={subject:'主体甲',source_name:'官网',verification_status:'verified',result_status:'no_records',run_id:'run-secret',screenshot_sha256:'hash-secret'};
  const good=h.renderCreditEvidence(evidence);
  assert.match(good,/本次查询未发现记录/);assert.doesNotMatch(good,/历史截图需重新验证/);
  assert.match(good,/<details><summary>证据技术详情<\/summary>.*run-secret.*hash-secret.*<\/details>/s);
  assert.match(h.renderCreditEvidence({...evidence,verification_status:'needs_review'}),/历史截图需重新验证，不作为当前已验证结果/);
  assert.match(h.renderCreditCard(),/Word 中补充，本系统不支持手动归档/);
});

test('completion toast only appears for a background export',async()=>{
  const h=harness();
  vm.runInContext('globalThis.messages=[];toast=message=>messages.push(message);paintExportTasks=()=>{};refreshData=async()=>{};render=()=>{};api=async()=>[{id:"job",status:"completed"}];',h.context);
  const check=async modal=>{h.exportTasks.jobs=[{id:'job',status:'running'}];h.exportTasks.modalId=modal;await h.pollExportTasks();};
  await check('job');assert.equal(h.context.messages.length,0);
  await check(null);assert.equal(h.context.messages.length,1);
});
