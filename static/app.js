'use strict';

const $ = (q, root = document) => root.querySelector(q);
const $$ = (q, root = document) => [...root.querySelectorAll(q)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths = {
  grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  folder:'<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M3 11h18"/>',
  layers:'<path d="m12 3 10 5-10 5L2 8Z"/><path d="m2 12 10 5 10-5M2 16l10 5 10-5"/>',
  file:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h6"/>',
  settings:'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="currentColor" stroke="none"/><circle cx="15" cy="17" r="3" fill="currentColor" stroke="none"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  chevron:'<path d="m9 5 7 7-7 7"/>',
  back:'<path d="m15 5-7 7 7 7"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  alert:'<path d="m12 3 10 18H2Z"/><path d="M12 9v5M12 17v.1"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v.1"/>',
  upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  download:'<path d="M12 3v13m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  rotate:'<path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5"/>',
  up:'<path d="M12 20V4m-6 6 6-6 6 6"/>',
  down:'<path d="M12 4v16m-6-6 6 6 6-6"/>',
  edit:'<path d="m15 4 5 5M4 20l5-1L20 8a2 2 0 0 0-4-4L5 15Z"/>',
  building:'<path d="M4 21V5l8-3 8 3v16M2 21h20M9 21v-5h6v5M8 7h1m6 0h1M8 11h1m6 0h1"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  refresh:'<path d="M20 9a8 8 0 0 0-14-4L3 8m0-5v5h5M4 15a8 8 0 0 0 14 4l3-3m0 5v-5h-5"/>',
  image:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m21 15-5-5L5 21"/>',
  lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4M12 14v3"/>'
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.file}</svg>`;
const state = {data:null,view:'dashboard',category:'all',query:'',project:null,dirty:false,drawerId:null,pageIndex:0,busy:false};
const CREDIT_SUBJECT = '北京腾宝科技有限公司';
const AUTHORIZATION_CARD_NOTE = '身份证附件不替代证明或授权正文，空白信息和签章须核对。';
const CREDIT_CATEGORY_IDS = new Set(['credit_dishonest','credit_tax_violation','credit_procurement_violation']);
const creditUi = {requesting:false,polling:false,timer:null,error:''};
const isCreditCategory = id => CREDIT_CATEGORY_IDS.has(id);
const navs = [{id:'dashboard',label:'工作台',icon:'grid'},{id:'materials',label:'材料库',icon:'folder'},{id:'projects',label:'标书项目',icon:'file'}];
const specialState={industry:'',category:'',query:'',page:1,result:{items:[],total:0,counts:{}},cache:new Map()};
const projectUi={id:null,step:'common',pickers:{},navigation:null};
const locationUi={mappingId:null,projectId:null,result:null,page:1,zoom:1,showMarkers:true,requestId:0};
function isSpecial(id){return state.data?.categories.find(c=>c.id===id)?.layer==='special';}
function projectRows(layer){return (state.project?.mappings||[]).filter(m=>(isSpecial(m.category_id)?'special':'common')===layer);}
function rowsReviewed(rows){return rows.every(m=>m.status!=='blocked');}
function commonReviewed(){const rows=projectRows('common');return (rows.length>0||!!state.project?.framework_id)&&rowsReviewed(rows)&&!state.dirty;}
function textReviewed(){return !!state.project?.special_selection_confirmed&&(state.project.material_flow!=='text-first'||state.project.text_review_confirmed===true)&&rowsReviewed(projectRows('special'))&&!state.dirty;}
function projectReviewed(){return commonReviewed()&&textReviewed();}
function cacheProjectMaterials(){for(const m of state.project?.selected_special_materials||[])specialState.cache.set(m.id,m);}
function resetProjectUi(){
  projectUi.id=state.project.id;projectUi.pickers={};projectUi.navigation=null;cacheProjectMaterials();
  projectUi.step=!textReviewed()?'special':!commonReviewed()?'common':projectReviewed()?'export':'special';
}
function materialCompletionIssues(target='export'){
  if(!state.project)return [];
  const stepName=step=>step==='special'?'文字材料':'图片材料';
  if(state.dirty){
    const step=projectUi.step==='special'?'special':'common';
    return [{kind:'save',step,title:stepName(step),message:`${stepName(step)}有未保存修改，请先保存核对。`}];
  }
  const issues=[];
  for(const step of target==='common'?['special']:['special','common']){
    const rows=projectRows(step),blocked=rows.filter(row=>row.status==='blocked');
    for(const row of blocked){
      const reasons=(row.issues||[]).map(issue=>typeof issue==='string'?issue:JSON.stringify(issue));
      issues.push({kind:'mapping',step,mappingId:row.id,
        title:row.category_id==='text_template'?row.title:(row.category_name||row.title||categoryName(row.category_id)),
        message:reasons.join('；')||'请处理本栏目的材料或位置问题。'});
    }
    if(step==='special'&&!blocked.length&&!textReviewed()){
      issues.push({kind:'save',step,title:'文字材料',message:'文字材料尚未保存核对，请先点击“保存核对”。'});
    }
    if(step==='common'&&!rows.length&&!state.project.framework_id){
      issues.push({kind:'scan',step,title:'图片材料',message:'尚未识别图片材料栏目，请重新扫描模板。'});
    }
  }
  return issues;
}
function renderMaterialCompletionNotice(){
  const notice=projectUi.navigation;
  if(!notice?.issues.length)return '';
  return `<section class="material-completion-notice" role="alert" aria-label="未完成事项"><strong>还有 ${notice.issues.length} 项需处理</strong><ul>${notice.issues.map((issue,index)=>`<li><button type="button" class="text-button" data-action="locate-material-issue" data-id="${index}">${esc(issue.step==='special'?'核对文字材料':'核对图片材料')} · ${esc(issue.title)}</button><p>${esc(issue.message)}</p></li>`).join('')}</ul></section>`;
}
function focusMaterialCompletionIssue(index){
  const notice=projectUi.navigation,issue=notice?.issues[index];
  if(!issue)return;
  notice.activeIndex=index;
  projectUi.step=issue.step;
  render();
  const projectId=state.project?.id;
  setTimeout(()=>{
    if(state.project?.id!==projectId||projectUi.navigation!==notice||notice.activeIndex!==index)return;
    const target=issue.kind==='mapping'
      ?$$('.mapping-card[data-mapping]').find(card=>card.dataset.mapping===issue.mappingId)
      :$(issue.kind==='scan'?'[data-action="rescan"]':'[data-action="save-project"]');
    if(!target)return;
    target.focus({preventScroll:true});
    target.scrollIntoView({behavior:'smooth',block:issue.kind==='mapping'?'start':'center'});
  },0);
}
async function guideMaterialCompletion(target='export'){
  if(state.busy){toast('正在保存核对，请等待完成。',true);return true;}
  const issues=materialCompletionIssues(target);
  if(!issues.length){projectUi.navigation=null;return false;}
  projectUi.navigation={target,issues,activeIndex:0};
  focusMaterialCompletionIssue(0);
  toast(`${issues[0].title}：${issues[0].message}`,true);
  return true;
}
async function loadProjectPicker(category, page, query){
  const previous=projectUi.pickers[category]||{page:1,query:''};
  const q=new URLSearchParams({category_id:category,page:page??previous.page,page_size:10,q:query??previous.query});
  const result=await api('/api/materials?'+q);
  projectUi.pickers[category]={...result,query:query??previous.query};
}
async function loadProjectPickers(){
  if(!state.project)return;
  if(projectUi.id!==state.project.id)resetProjectUi();
  if(state.project.material_flow!=='text-first'||state.project.text_review_confirmed===undefined){
    state.project=await api(`/api/projects/${encodeURIComponent(state.project.id)}/text-materials`,{method:'POST',body:{mapping_revision:state.project.mapping_revision,text_first:true}});
    projectUi.step='special';
  }
  cacheProjectMaterials();
  await Promise.all([...new Set(projectRows('special').map(m=>m.category_id))].map(c=>loadProjectPicker(c)));
}
async function projectStep(step){
  if(['common','export'].includes(step)&&await guideMaterialCompletion(step))return;
  if(state.dirty)await saveProject(false);
  if(step==='special'){
    state.project=await api(`/api/projects/${encodeURIComponent(state.project.id)}/text-materials`,{method:'POST',body:{mapping_revision:state.project.mapping_revision,text_first:true}});
    await loadProjectPickers();
  }
  if(step==='export'&&state.view==='dashboard'){await tenderFlow.setStep(5);return;}
  projectUi.navigation=null;projectUi.step=step;render();window.scrollTo({top:0});
}
async function loadSpecial(){const q=new URLSearchParams({industry_id:specialState.industry,category_id:specialState.category,q:specialState.query,page:specialState.page});specialState.result=await api('/api/materials?'+q);specialState.page=specialState.result.page;}
let dialogReturnFocus = null;
let drawerReturnFocus = null;

async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers:{'Content-Type':'application/json',...(options.headers || {})},body:options.body === undefined ? undefined : JSON.stringify(options.body)});
  let result;
  try { result = await response.json(); } catch { throw new Error('无法读取服务响应，请检查本地服务是否正在运行。'); }
  if (!response.ok) throw new Error(result.error || '操作未完成，请稍后重试。');
  return result;
}
async function refreshData({refreshProject=true}={}) {
  state.data = await api('/api/bootstrap');
  const auditCategory = state.data.categories.find(c=>c.id==='audit_report');
  if(auditCategory?.name==='2023-2024年财务审计报告')auditCategory.name='近三年财务审计报告';
  if(!state.data.industries.some(i=>i.id===specialState.industry))specialState.industry=state.data.industries[0]?.id||'';
  if(state.view==='industries')await loadSpecial();
  if(state.drawerId&&specialState.cache.has(state.drawerId))specialState.cache.set(state.drawerId,await api('/api/materials/'+encodeURIComponent(state.drawerId)));
  const currentProject=state.project;
  if(refreshProject&&currentProject&&!state.dirty){
    const latest=await api(`/api/projects/${encodeURIComponent(currentProject.id)}`);
    if(state.project===currentProject&&!state.dirty)state.project=latest;
  }
  cacheProjectMaterials();
}
function safeExternalUrl(value) {
  try {const parsed=new URL(String(value));return ['https:','http:'].includes(parsed.protocol)?parsed.href:'';}catch{return '';}
}
function creditSourceList() {
  const entries=state.data?.credit_automation?.sources;
  return Array.isArray(entries)&&entries.length?entries:[
    {id:'credit_dishonest',name:'失信被执行人',status:'untested',reason:'尚未完成全自动条件检测。'},
    {id:'credit_tax_violation',name:'重大税收违法失信主体',status:'untested',reason:'尚未完成全自动条件检测。'},
    {id:'credit_procurement_violation',name:'政府采购严重违法失信行为记录名单',status:'untested',reason:'尚未完成全自动条件检测。'}
  ];
}
function creditIsRunning() {return creditUi.requesting||state.data?.credit_automation?.status==='running';}
function renderCreditCard() {
  return `<section class="panel credit-panel" aria-labelledby="credit-title"><div class="credit-header"><div class="credit-heading-icon">${icon('shield')}</div><div class="credit-heading"><h2 id="credit-title">无违法违规记录查询截图</h2><p>手动查询截图需自行在 Word 中补充，本系统不支持手动归档。</p></div></div><div class="credit-controls"><div class="credit-subject"><label for="credit-subject">当前投标主体</label><input id="credit-subject" class="input" readonly value="${CREDIT_SUBJECT}"></div></div><div id="credit-live-region">${renderCreditLive()}</div></section>`;
}
function renderCreditLive() {
  const sourceCards=creditSourceList().map(source=>{
    const link=safeExternalUrl(source.url);
    return `<article class="credit-source"><div class="credit-source-title"><h3>${esc(source.name||'官方查询来源')}</h3></div>${link?`<a class="credit-source-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer">官方查询入口 ↗</a>`:''}</article>`;
  }).join('');
  return `<div class="credit-sources">${sourceCards}</div>`;
}
function updateCreditCard() {
  if(state.view!=='materials'||state.project)return;
  const live=$('#credit-live-region');if(live)live.innerHTML=renderCreditLive();
}
function syncCreditPolling() {
  if(creditUi.timer){clearTimeout(creditUi.timer);creditUi.timer=null;}
  if(state.view==='materials'&&!state.project&&state.data?.credit_automation?.status==='running'&&!creditUi.polling&&!creditUi.requesting&&!creditUi.error){creditUi.timer=setTimeout(pollCreditStatus,2000);}
}
async function pollCreditStatus() {
  creditUi.timer=null;if(creditUi.polling||state.view!=='materials'||state.project)return;
  creditUi.polling=true;
  try {
    const wasRunning=state.data.credit_automation?.status==='running';
    state.data.credit_automation=await api('/api/credit-automation');creditUi.error='';
    const current=state.data.credit_automation;
    if(wasRunning&&current.status!=='running'){
      await refreshData();
      if(state.view==='materials'&&!state.project){
        const library=$('#material-library-region');
        if(library&&!library.contains(document.activeElement)&&!$('#active-modal'))library.innerHTML=renderMaterialsLibrary();
        toast(current.enabled===true?'三项全自动条件已通过，已验证材料已归档。':'全自动条件尚未全部满足，功能保持未启用。');
      }
    }
    updateCreditCard();
  } catch(error) {creditUi.error=`无法继续读取检测状态：${error.message} 当前结果不能视为已验证。`;updateCreditCard();}
  finally {creditUi.polling=false;syncCreditPolling();}
}
async function runCreditCheck() {
  if(creditIsRunning())return;
  creditUi.requesting=true;creditUi.error='';updateCreditCard();
  try {state.data.credit_automation=await api('/api/credit-automation/check',{method:'POST',body:{subject:CREDIT_SUBJECT}});await refreshData();}
  catch(error){creditUi.error=`未能启动全自动检测：${error.message}`;}
  finally {creditUi.requesting=false;updateCreditCard();syncCreditPolling();}
}
function renderCreditEvidence(evidence) {
  if(!evidence||typeof evidence!=='object')return '';
  const verified=evidence.verification_status==='verified',link=safeExternalUrl(evidence.source_url);
  const fields=[['查询主体',evidence.subject],['官方来源',evidence.source_name],['查询时间',evidence.queried_at?date(evidence.queried_at,true):'—'],['查询结论',verified&&evidence.result_status==='no_records'?'本次查询未发现记录':!verified?'历史截图需重新验证，不作为当前已验证结果':'请核对官方查询结果']];
  const technical=[['检测批次',evidence.run_id],['截图 SHA-256',evidence.screenshot_sha256]].filter(([,value])=>value);
  const details=rows=>rows.filter(([,value])=>value!==undefined&&value!==null&&value!=='').map(([label,value])=>`<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join('');
  return `<section class="credit-evidence"><div class="credit-evidence-heading"><h3>官方查询证据</h3>${statusBadge(verified?'ready':'warning',verified?'已验证归档':'需重新验证')}</div><dl>${details(fields)}</dl>${link?`<a href="${esc(link)}" target="_blank" rel="noopener noreferrer">官方来源 ↗</a>`:''}${technical.length?`<details><summary>证据技术详情</summary><dl>${details(technical)}</dl></details>`:''}</section>`;
}
function toast(message, error = false) {
  const el = document.createElement('div'); el.className = `toast${error ? ' error' : ''}`; el.setAttribute('role',error ? 'alert' : 'status');
  el.innerHTML = `${icon(error ? 'alert' : 'check')}<span>${esc(message)}</span>`;
  $('#toast-root').append(el); setTimeout(() => el.remove(),error ? 9000 : 4500);
}
function date(value, withTime = false) {
  if (!value) return '—';
  const d = new Date(value); if (Number.isNaN(d.getTime())) return esc(String(value).slice(0,10));
  return new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',...(withTime ? {hour:'2-digit',minute:'2-digit'} : {})}).format(d);
}
function categoryName(id) { return state.data.categories.find(c => c.id === id)?.name || '未分类'; }
function material(id) { return state.data.materials.find(m => m.id === id)||specialState.cache.get(id); }
function enabledCategories() { return state.data.categories.filter(c => c.layer === 'common' && c.enabled); }
function statusBadge(status, label) {
  const names = {ready:'已核对',missing:'缺少材料',blocked:'待处理',skipped:'已跳过',draft:'待核对',reviewed:'已导出核对版',confirmed:'已确认',success:'已就绪',error:'异常'};
  return `<span class="status ${esc(status)}">${esc(label || names[status] || '待核对')}</span>`;
}
function validityBadge(value) {
  const raw=String(value||'').trim();let tone='warning',label='有效期待核对';
  if(/年度.*更新|下.*年.*更新/.test(raw))label='年度更新提醒';
  else if(/^(长期|长期有效|永久)$/.test(raw)){tone='skipped';label='已登记长期';}
  else if(/^\d{4}-\d{2}-\d{2}$/.test(raw)){
    const parsed=new Date(raw+'T23:59:59');
    if(!Number.isNaN(parsed.getTime())){tone=parsed<new Date()?'blocked':'skipped';label=parsed<new Date()?`已过期 · ${raw}`:`登记到期日 ${raw}`;}
  }
  const explanation=raw||'未登记有效期，不代表已失效或已通过审核';
  return `<span class="status ${tone}" title="${esc(explanation)}"${!raw?` aria-label="${label}：${esc(explanation)}"`:''}>${esc(label)}</span>`;
}
function emptyState(title, text, button = '', compact = false, name = 'folder') {
  return `<div class="empty-state${compact?' compact':''}"><div class="empty-icon">${icon(name)}</div><h3>${esc(title)}</h3>${text?`<p>${esc(text)}</p>`:''}${button}</div>`;
}
function heading(title, subtitle, actions = '', eyebrow = '') {
  return `<div class="page-heading"><div>${eyebrow ? `<div class="heading-eyebrow">${esc(eyebrow)}</div>`:''}<h1>${esc(title)}</h1>${subtitle ? `<p>${esc(subtitle)}</p>`:''}</div>${actions ? `<div class="actions">${actions}</div>`:''}</div>`;
}
function button(label, action, opts = {}) {
  return `<button type="button" class="btn ${opts.primary?'primary':''} ${opts.small?'small':''} ${opts.ghost?'ghost':''}" data-action="${esc(action)}" ${opts.id ? `data-id="${esc(opts.id)}"`:''} ${opts.category ? `data-category="${esc(opts.category)}"`:''} ${opts.disabled?'disabled':''}>${opts.icon?icon(opts.icon):''}${esc(label)}</button>`;
}
function render() {
  if (!state.data) return;
  const nav = navs.find(n => n.id === (state.view==='industries'?'materials':state.view)) || navs[0];
  $('#app').innerHTML = `<div class="app-shell"><button type="button" class="mobile-shade" data-action="toggle-nav" aria-label="关闭导航"></button><aside class="sidebar" aria-label="主导航"><a class="brand" href="#" data-action="navigate" data-view="dashboard"><span class="brand-mark">${icon('layers')}</span><span><span class="brand-title">腾宝 · 标书管理</span></span></a><nav class="nav-items">${navs.map(n=>{const active=n.id===state.view||(n.id==='materials'&&state.view==='industries');return `<button type="button" class="nav-button ${active?'active':''}" data-action="navigate" data-view="${n.id}" ${active?'aria-current="page"':''}>${icon(n.icon)}${n.label}${n.id==='projects'?`<span class="nav-count" style="margin-left:auto">${state.data.projects.length}</span>`:''}</button>${n.id==='materials'&&active?`<div class="material-subnav" aria-label="材料库分类">${[['materials','图片材料'],['industries','文字材料']].map(([id,label])=>`<button type="button" class="nav-button ${state.view===id?'active':''}" data-action="navigate" data-view="${id}" ${state.view===id?'aria-current="page"':''}>${label}</button>`).join('')}</div>`:''}`;}).join('')}</nav><div class="sidebar-bottom"><div class="sidebar-footnote">文件保存在本机；使用 AI 时，相关文本会发送至所配置的模型服务。</div></div></aside><div class="main-shell"><header class="topbar"><button type="button" class="icon-button mobile-menu" data-action="toggle-nav" aria-label="打开导航">${icon('menu')}</button><div class="breadcrumb"><strong>${nav.label}</strong>${state.project&&state.view==='projects'?`${icon('chevron')}<span>项目核对</span>`:''}</div><div class="topbar-right">${button('新建项目','new-framework',{primary:true,icon:'plus'})}<span class="avatar" aria-label="腾宝">腾</span></div></header><main id="main-content" class="main-content" tabindex="-1">${renderView()}</main></div></div>`;
  $('#app').inert=state.busy;
  updateCreditCard();syncCreditPolling();observeInlineLocations();
}
function renderView() {
  if(state.view==='dashboard')return renderDashboard();
  if (state.project) return renderProject();
  return ({materials:renderMaterials,industries:renderIndustries,projects:renderProjects}[state.view] || renderDashboard)();
}
function renderDashboard() {
  const d = state.data;
  if(tenderFlow.isPreview())return tenderFlow.view();
  return `${heading('投标文件编制')}
  ${d.stats?.source_status?.state==='error'?`<div class="warning-box">原始材料导入尚未完成：${esc(d.stats.source_status.message)}。请检查后重新启动本地服务。</div>`:''}
  ${tenderFlow.view()}
`;
}
function projectTable(projects, compact = false) {
  return `<div class="table-wrap"><table class="data-table"><thead><tr><th>项目名称</th>${compact?'':'<th>模板文件</th>'}<th>状态</th><th>最近更新</th><th>操作</th></tr></thead><tbody>${projects.map(p=>`<tr><td><div class="name-cell"><span class="file-icon">${icon('file')}</span><div><button type="button" class="text-button table-title" data-action="open-project" data-id="${esc(p.id)}">${esc(p.name)}</button>${compact?`<div class="table-subtitle">${esc(p.template_name || 'Word 模板')}</div>`:''}</div></div></td>${compact?'':`<td class="muted">${esc(p.template_name)}</td>`}<td>${statusBadge(p.status)}</td><td class="muted mono">${date(p.updated_at || p.created_at)}</td><td><button type="button" class="text-button" data-action="open-project" data-id="${esc(p.id)}">${p.framework_artifact_id?.startsWith('part-')?'查看历史工作稿':'进入核对'}</button></td></tr>`).join('')}</tbody></table></div>`;
}
function renderMaterials() {
  return `${heading('图片材料','',button('新增材料','new-material',{primary:true,icon:'plus',category:state.category!=='all'?state.category:undefined,disabled:isCreditCategory(state.category)}))}${renderCreditCard()}<div id="material-library-region">${renderMaterialsLibrary()}</div>`;
}
function renderMaterialsLibrary() {
  if(isCreditCategory(state.category))state.category='all';
  const cats = state.data.categories.filter(c=>c.layer==='common'&&!isCreditCategory(c.id)), query = state.query.toLocaleLowerCase();
  const rows = state.data.materials.filter(m=>(state.category==='all'||m.category_id===state.category)&&(!query||[m.name,m.subject,m.version,categoryName(m.category_id),(m.years||[]).join(' ')].join(' ').toLocaleLowerCase().includes(query)));
  return `<div class="filter-tabs" aria-label="按栏目筛选"><button type="button" class="filter-tab ${state.category==='all'?'active':''}" data-action="filter-category" data-id="all">全部材料 <span>${state.data.materials.length}</span></button>${cats.map(c=>`<button type="button" class="filter-tab ${state.category===c.id?'active':''}" data-action="filter-category" data-id="${esc(c.id)}">${esc(c.name)}${!c.enabled?'（停用）':''}<span>${state.data.materials.filter(m=>m.category_id===c.id).length}</span></button>`).join('')}</div>
  <div class="toolbar"><div class="search-box">${icon('search')}<input id="material-search" class="input" type="search" placeholder="搜索材料名称、主体或年度" aria-label="搜索图片材料" value="${esc(state.query)}"></div><span class="muted small">共 ${rows.length} 份材料</span></div>
  <section class="panel">${rows.length?`<div class="table-wrap material-table-wrap"><table class="data-table material-table"><thead><tr><th>材料名称 / 所属栏目</th><th>所属主体</th><th>年度 / 版本</th><th>有效期</th><th>操作</th></tr></thead><tbody>${rows.map(m=>`<tr><td><div class="name-cell"><span class="file-icon">${icon(m.mode==='text'?'file':'image')}</span><div><button type="button" class="text-button table-title" data-action="open-material" data-id="${esc(m.id)}">${esc(m.name)}</button><div class="table-subtitle">${esc(categoryName(m.category_id))}</div></div></div></td><td>${esc(m.subject || '待补充')}</td><td>${(m.years||[]).length?esc(m.years.join('、')):'—'}<div class="table-subtitle">${esc(m.version || '—')}</div></td><td>${validityBadge(m.valid_until)}</td><td><button type="button" class="text-button" data-action="open-material" data-id="${esc(m.id)}">查看材料</button></td></tr>`).join('')}</tbody></table></div>`:emptyState(query?'没有找到符合条件的材料':isCreditCategory(state.category)?'此栏目等待全自动验证归档':'此栏目尚未添加材料',query?'试试其他名称、主体或年度。':isCreditCategory(state.category)?'仅在三项官方查询全部验证完成后自动生成材料；当前不提供手工接力。':'',query||isCreditCategory(state.category)?'':button('添加材料','new-material',{icon:'plus',category:state.category!=='all'?state.category:undefined}))}</section>
  `;
}
function renderIndustries() {
  const ss=specialState,r=ss.result,cats=state.data.categories.filter(c=>c.layer==='special'),rows=r.items;
  return `${heading('文字材料','',button('新增文字材料','new-material',{primary:true,icon:'plus',category:cats[0]?.id,disabled:!cats.length}))}
  <div class="toolbar special-toolbar"><div class="search-box">${icon('search')}<input class="input" id="special-search" type="search" aria-label="搜索文字材料" placeholder="搜索模板名称或内容说明" value="${esc(ss.query)}"></div></div>
  <section class="panel">${rows.length?`<div class="table-wrap"><table class="data-table"><thead><tr><th>模板名称</th><th>所属主体</th><th>版本</th><th>操作</th></tr></thead><tbody>${rows.map(m=>`<tr><td><button class="text-button table-title" data-action="open-material" data-id="${esc(m.id)}">${esc(m.name)}</button></td><td>${esc(m.subject)}</td><td>${esc(m.version||'—')}</td><td>${button('查看','open-material',{small:true,id:m.id})}</td></tr>`).join('')}</tbody></table></div>`:emptyState('暂无文字材料','添加投标函、授权书等模板')}
  <div class="panel-body actions">${button('上一页','special-prev',{small:true,disabled:ss.page<=1})}<span>第 ${ss.page} / ${Math.max(1,Math.ceil(r.total/(r.page_size||25)))} 页 · 共 ${r.total} 份</span>${button('下一页','special-next',{small:true,disabled:ss.page*(r.page_size||25)>=r.total})}</div></section>`;
}
function specialFormFields(m){return `<div class="form-field"><label>客户名称<input class="input" name="client_name" value="${esc(m?.client_name)}" maxlength="300"></label></div><div class="form-field"><label>项目名称<input class="input" name="case_name" value="${esc(m?.case_name)}" maxlength="300"></label></div><div class="form-field"><label>证明主体类型<select name="subject_kind">${[['company','公司'],['person','人员'],['partner','合作方'],['unknown','待核对']].map(([k,v])=>`<option value="${k}" ${m?.subject_kind===k?'selected':''}>${v}</option>`).join('')}</select></label></div><div class="form-field full"><label>关联使用栏目（保留原始归属）</label><div class="special-links">${state.data.categories.filter(c=>c.layer==='special').map(c=>`<label><input type="checkbox" name="linked_category_ids" value="${esc(c.id)}" ${m?.linked_category_ids?.includes(c.id)?'checked':''}>${esc(c.name)}</label>`).join('')}</div></div>`;}
function sourceDescription(source){if(!source)return '<p>手动上传，尚未登记来源。</p>';if(typeof source==='string')return `<p>${esc(source)}</p>`;const position=source.page?'PDF 第 '+source.page+' 页':source.slide?'幻灯片第 '+source.slide+' 页':source.paragraph?'文档段落 P'+source.paragraph:source.paragraph_start?'文档段落 P'+source.paragraph_start+'–P'+source.paragraph_end:source.chapter||source.section!==undefined?'章节／段落：'+(source.chapter||source.section):'';return `<p><strong>${esc(source.path||'来源待补充')}</strong><br>${esc([position,source.method].filter(Boolean).join(' · '))}</p>`;}

function renderProjects() {
  const rows=state.data.projects.filter(p=>!state.query||[p.name,p.template_name].join(' ').toLocaleLowerCase().includes(state.query.toLocaleLowerCase()));
  return `${heading('标书项目','')}<div class="toolbar"><div class="search-box">${icon('search')}<input id="project-search" class="input" type="search" placeholder="搜索项目或模板名称" aria-label="搜索标书项目" value="${esc(state.query)}"></div><span class="muted small">${rows.length} 个项目</span></div><section class="panel">${rows.length?projectTable(rows):emptyState(state.query?'没有找到项目':'创建你的第一个标书项目',state.query?'试试其他项目名称或模板名称。':'点击右上角“新建项目”，上传招标文件。','',false,'file')}</section>${tenderFlow.projectArchives()}`;
}
function renderProject(insideWorkflow=false) {
  if(projectUi.id!==state.project.id)resetProjectUi();
  const p=state.project, all=p.mappings||[], step=projectUi.step;
  const maps=projectRows(step==='special'?'special':'common'),ready=maps.filter(m=>m.status==='ready').length,missing=maps.filter(m=>['missing','blocked'].includes(m.status)).length,confirmed=maps.filter(m=>m.confirmed).length;
  const allConfirmed=projectReviewed(),reviewing=['common','special'].includes(step);
  const stages=[['upload','上传标书',true,false],['special','核对文字材料',textReviewed(),true],['common','核对图片材料',commonReviewed(),textReviewed()],['export','导出标书',false,allConfirmed]];
  return `${insideWorkflow?'':`<button type="button" class="back-link" data-action="navigate" data-view="projects">${icon('back')}返回项目列表</button><div class="project-header">${heading(p.name,'',button('重新扫描','rescan',{icon:'refresh'})+(reviewing?button('保存核对','save-project',{primary:true,icon:'check',disabled:state.busy}):''), '')}<div class="project-facts"><span>${icon('file')}${esc(p.template_name)}</span></div></div>
  <nav class="stepper project-stepper" aria-label="标书制作步骤">${stages.map(([id,label,done,enabled],i)=>`<button type="button" class="step ${done?'done':''} ${step===id?'active':''}" data-action="project-step" data-id="${id}" aria-label="${esc(label)}，${id==='upload'?'已上传':done?'已完成':step===id?'当前步骤':'待完成'}" ${id==='upload'||state.busy?'disabled':''} ${step===id?'aria-current="step"':''}><span class="step-number">${done?icon('check'):String(i+1).padStart(2,'0')}</span><span><strong>${label}</strong></span></button>`).join('')}</nav>`}
  ${reviewing?`<div class="panel-heading review-stage-heading"><h2>${step==='common'?'核对图片材料':'核对文字材料'}</h2></div>
  <div class="mapping-toolbar"><div class="mapping-summary"><span><strong>${maps.length}</strong>个匹配项</span><span><strong>${ready}</strong>项就绪</span><span><strong>${missing}</strong>项待处理</span></div><div class="actions">${insideWorkflow?button('保存核对','save-project',{primary:true,disabled:state.busy}):''}</div></div>

  ${renderMaterialCompletionNotice()}
  <div class="mapping-list">${maps.length?maps.map((m,i)=>renderMapping(m,i,p.headings||[])).join(''):`<section class="panel panel-body">暂无待核对的${step==='common'?'图片':'文字'}材料，可继续下一步。</section>`}</div>
  ${state.dirty?`<div class="sticky-save"><div><p>有未保存修改，请先保存</p></div>${button('保存核对','save-project',{primary:true,icon:'check',disabled:state.busy})}</div>`:''}
  <div class="project-next">${step==='common'?button('上一步：核对文字材料','project-step',{id:'special'}):''}<span class="muted ${!state.dirty&&!rowsReviewed(maps)?'project-blocked-hint':'small'}">${!state.dirty&&!rowsReviewed(maps)?'请处理材料或位置问题后继续':''}</span>${button(step==='special'?'下一步：核对图片材料':insideWorkflow?'下一步：编写投标文件':'下一步：导出标书','project-step',{id:step==='special'?'common':'export',primary:true,icon:'chevron',disabled:state.busy})}</div>`:''}
  ${step==='export'?`<section class="panel export-panel"><div class="panel-heading"><h2>导出标书</h2><span class="count-label">共 ${all.length} 项</span></div><div class="export-controls"><div><h3>${allConfirmed?'材料匹配可导出':'请先处理材料匹配问题'}</h3><p>图片材料 ${projectRows('common').length} 项 · 文字材料 ${projectRows('special').length} 项 · 导出初稿</p></div><div class="actions">${button('导出','export-project',{primary:true,icon:'download',disabled:!allConfirmed||state.dirty})}</div></div><div class="note-bar" style="margin:0 20px 20px">${icon('info')}<span>导出后请在 Word 中更新目录，复核项目名称、日期、待补项、报价、签章及页码。</span></div></section>${renderExports(p.exports||[])}`:''}`;
}
function renderMaterialPicker(m){
  const special=isSpecial(m.category_id),picker=projectUi.pickers[m.category_id],selected=m.material_ids||[];
  const candidates=special?[...(picker?.items||[])]:state.data.materials.filter(x=>(x.category_id===m.category_id||(m.category_id==='authorization'&&x.category_id==='legal_identity'))&&(m.category_id!=='authorization'||['page','side_by_side'].includes(x.mode)));
  if(special)for(const id of selected){if(!candidates.some(x=>x.id===id)){const x=material(id);if(x)candidates.unshift(x);}}
  return `${special?`<div class="project-picker-search"><input type="search" class="input" data-project-search="${esc(m.category_id)}" aria-label="搜索${esc(m.category_name)}" placeholder="搜索材料、客户、人员或项目" value="${esc(picker?.query||'')}"></div>`:''}<div class="material-picker" role="group" aria-labelledby="materials-label-${esc(m.id)}">${candidates.length?candidates.map(x=>`<div class="project-material-option"><label class="material-option"><input type="checkbox" data-map-material="${esc(m.id)}" value="${esc(x.id)}" ${selected.includes(x.id)?'checked':''} ${m.action==='skip'?'disabled':''}><span>${esc(x.name)}<small>${esc(x.subject)}${x.years?.length?` · ${esc(x.years.join('、'))}`:''}${special?` · ${esc(x.client_name||x.case_name||'')}`:''}</small></span></label>${special?`<button class="text-button small" data-action="open-material" data-id="${esc(x.id)}">预览</button>`:''}</div>`).join(''):`<div class="picker-empty">${special?(picker?.query?'没有找到符合搜索条件的材料':'此分类暂无材料，可新增材料或明确记录缺项'):'此栏目还没有材料'}</div>`}</div>${special?`<div class="project-picker-pagination">${button('上一页','project-picker-prev',{small:true,category:m.category_id,disabled:!picker||picker.page<=1})}<span class="muted small">${picker?`第 ${picker.page} / ${Math.max(1,Math.ceil(picker.total/picker.page_size))} 页 · 共 ${picker.total} 份`:'加载材料中…'}</span>${button('下一页','project-picker-next',{small:true,category:m.category_id,disabled:!picker||picker.page*picker.page_size>=picker.total})}</div>`:''}`;
}
function renderLocationRecognition(m) {
  const match=m.location_match||{}, requirement=m.requirement||{};
  let location='';
  if(match.status==='appendix')location='<div class="location-recognition matched">已识别材料要求，未找到可靠的原文插入位置，将在文末新增材料区。</div>';

  else if(match.status==='manual')location='<div class="location-recognition">人工指定</div>';
  else if(match.reason&&match.status!=='matched')location=`<div class="location-recognition">${esc(match.reason)}${match.status==='ambiguous'?'；下拉选项中已标出候选位置。':''}</div>`;
  const years=requirement.year_options?.length?`<div>可接受年度：${esc(requirement.year_options.map(x=>x.join('、')).join(' 或 '))}</div>`:'';
  return location+(requirement.status&&years?`<div class="mapping-hint">${years}</div>`:'');
}
function renderLocationDescription(description,className='') {
  if(!description||description==='当前选择跳过，不填入材料；导出初稿时会在此位置之后添加跳过说明。')return '';
  return `<p${className?` class="${esc(className)}"`:''}>${esc(description)}</p>`;
}
function isPreservedTemplateMapping(mapping) {
  return mapping.category_id==='text_template'&&mapping.preserve_original===true;
}
function renderMapping(m,index,headings) {
  const attention=projectUi.navigation?.issues[projectUi.navigation.activeIndex]?.mappingId===m.id?' mapping-needs-attention':'';
  if(isPreservedTemplateMapping(m)){
    const notices=[...(m.issues||[]),...(m.warnings||[])];
    return `<article class="mapping-card preserved-template-card${attention}" data-mapping="${esc(m.id)}" tabindex="-1"><div class="mapping-top"><div class="mapping-name"><span class="mapping-index">${String(index+1).padStart(2,'0')}</span><h3>${esc(m.title||m.category_name||categoryName(m.category_id))}</h3></div><div class="actions">${statusBadge(m.status,m.status==='blocked'?'':'已跳过系统填充')}</div></div><div class="panel-body"><p class="preserved-template-skip">本栏目已跳过系统填充，采购文件原表将原样保留；无需选模板或材料，可直接继续下一步。</p><div class="inline-location-preview" data-location-mapping="${esc(m.id)}" aria-live="polite"><p class="muted small">正在加载原文预览…</p></div><div class="location-preview-entry">${button('查看原文','preview-location',{small:true,icon:'file',id:m.id})}</div>${typeof m.note==='string'&&m.note.trim()?`<p class="detail-description" style="white-space:pre-wrap">${esc(m.note)}</p>`:''}</div>${notices.length?`<div class="mapping-bottom"><ul class="issue-list">${notices.map(note=>`<li>${esc(typeof note==='string'?note:JSON.stringify(note))}</li>`).join('')}</ul></div>`:''}</article>`;
  }
  const selected=m.material_ids||[];
  const action=m.action||'fill';
  const preservesForm=m.category_id==='text_template'&&headings.some(h=>h.id===m.locator_id&&h.authorization_original_body_present);
  const authorizationCard=m.category_id==='authorization'||preservesForm||(m.category_id==='text_template'&&/授权|委托/.test(m.title||''));
  const authorizationHint=authorizationCard||m.category_id==='legal_identity'?AUTHORIZATION_CARD_NOTE:'';
  const genericAttachmentWarnings=[AUTHORIZATION_CARD_NOTE,
    '本次仅装配身份证附件，不代表身份证明或授权正文已填写；人员、日期和签章由商务核对。',
    '仅作为身份证附件；原证明/授权正文、人员、日期和签章须单独核对。'];
  const warnings=(m.warnings||[]).filter(w=>!authorizationHint||typeof w!=='string'||
    !genericAttachmentWarnings.some(note=>w===note||w.endsWith('：'+note)));
  return `<article class="mapping-card${attention}" data-mapping="${esc(m.id)}" tabindex="-1"><div class="mapping-top"><div class="mapping-name"><span class="mapping-index">${String(index+1).padStart(2,'0')}</span><h3>${esc(m.category_id === 'authorization' ? '授权人及代理人身份证' : (m.category_id==='audit_report' ? '近三年财务审计报告' : (m.category_id==='text_template' ? m.title : (m.category_name || m.title || categoryName(m.category_id)))))}</h3></div><div class="actions">${statusBadge(m.status,m.preserve_original?'已保留原表':'')}</div></div><div class="mapping-main"><div><label class="field-label" for="locator-${esc(m.id)}">模板插入位置</label><select id="locator-${esc(m.id)}" data-map-field="locator_id" data-map-id="${esc(m.id)}"><option value="">请选择模板位置</option>${headings.map(h=>`<option value="${esc(h.id)}" ${h.id===m.locator_id?'selected':''}>${esc(h.title || h.preview || '未命名段落')}${h.kind==='placeholder'?' · 占位符':''}${m.location_match?.status==='ambiguous'&&m.location_match.candidates?.some(c=>c.locator_id===h.id)?' · 候选位置':''}</option>`).join('')}</select><div class="inline-location-preview" data-location-mapping="${esc(m.id)}" aria-live="polite"><p class="muted small">正在加载插入位置预览…</p></div><div class="location-preview-entry">${button('放大查看','preview-location',{small:true,icon:'file',id:m.id})}</div>${renderLocationRecognition(m)}${m.required_years?.length?`<div class="mapping-hint">要求年度：${esc(m.required_years.join('、'))}</div>`:''}</div><div><span class="field-label" id="materials-label-${esc(m.id)}">匹配材料${selected.length?` · 已选 ${selected.length} 份`:''}</span>${renderMaterialPicker(m)}<button type="button" class="text-button small" data-action="${isSpecial(m.category_id)?'new-material':'project-materials'}" data-category="${esc(m.category_id)}" style="margin-top:7px">${isSpecial(m.category_id)?'新增此分类材料':'管理此栏目材料'} →</button></div><div class="mapping-processing"><div><label class="field-label" for="action-${esc(m.id)}">处理方式</label><select id="action-${esc(m.id)}" data-map-field="action" data-map-id="${esc(m.id)}"><option value="fill" ${action==='fill'?'selected':''}>填入已选材料</option><option value="missing" ${action==='missing'?'selected':''}>保留为缺项</option><option value="skip" ${action==='skip'?'selected':''}>${preservesForm?'保留采购原表，不另填模板':'本项目跳过'}</option></select>${action==='skip'&&!preservesForm?'<p class="muted small">跳过不视为材料齐全。</p>':''}${authorizationHint?`<p class="muted small">${authorizationHint}</p>`:''}</div><div style="margin-top:10px"><label class="field-label" for="note-${esc(m.id)}">核对备注</label><textarea rows="2" id="note-${esc(m.id)}" placeholder="记录缺项原因或使用说明" data-map-field="note" data-map-id="${esc(m.id)}">${esc(m.note)}</textarea></div></div></div>${renderMappingPreview(m)}<div class="mapping-bottom"><div>${m.issues?.length?`<ul class="issue-list">${m.issues.map(issue=>`<li>${esc(typeof issue==='string'?issue:JSON.stringify(issue))}</li>`).join('')}</ul>`:''}${warnings.length?`<div class="mapping-advisories"><span>材料核对提示</span><ul>${warnings.map(w=>`<li>${esc(typeof w==='string'?w:JSON.stringify(w))}</li>`).join('')}</ul></div>`:''}</div></div></article>`;
}
const inlineLocationCache=new Map();
let inlineLocationObserver;
function observeInlineLocations() {
  inlineLocationObserver?.disconnect();
  inlineLocationObserver=new IntersectionObserver(entries=>{
    for(const entry of entries)if(entry.isIntersecting){inlineLocationObserver.unobserve(entry.target);loadInlineLocation(entry.target);}
  },{rootMargin:'300px'});
  $$('[data-location-mapping]').forEach(el=>inlineLocationObserver.observe(el));
}
async function loadInlineLocation(el,attempt=0) {
  const project=state.project, mapping=project?.mappings.find(m=>m.id===el.dataset.locationMapping);
  if(!mapping)return;
  const body={mapping_id:mapping.id,locator_id:mapping.locator_id,material_ids:mapping.material_ids||[],action:mapping.action||'fill'};
  const key=JSON.stringify([project.id,project.updated_at,body]);
  const failed=error=>{
    if(!el.isConnected||state.project?.id!==project.id)return;
    inlineLocationCache.delete(key);
    if(attempt<2){
      el.innerHTML='<p class="muted small" role="status">正在重新加载原页预览…</p>';
      setTimeout(()=>{if(el.isConnected&&state.project?.id===project.id)loadInlineLocation(el,attempt+1);},1000*(attempt+1));
    }else{
      el.innerHTML=`<p class="form-error">${esc(error.message)}</p><button type="button" class="btn small">重新加载预览</button>`;
      $('button',el).addEventListener('click',()=>{el.innerHTML='<p class="muted small" role="status">正在加载原页预览…</p>';loadInlineLocation(el);},{once:true});
    }
  };
  try {
    if(!inlineLocationCache.has(key)){
      const request=api(`/api/projects/${encodeURIComponent(project.id)}/location-preview`,{method:'POST',body});
      inlineLocationCache.set(key,request);
      request.catch(()=>inlineLocationCache.delete(key));
      if(inlineLocationCache.size>60)inlineLocationCache.delete(inlineLocationCache.keys().next().value);
    }
    const result=await inlineLocationCache.get(key);
    if(!el.isConnected||state.project?.id!==project.id)return;
    const page=result.initial_page;
    el.innerHTML=`<p class="small"><strong>${esc(result.title)}</strong></p>${renderLocationDescription(result.description,'muted small')}<div class="location-paper"><img class="location-page-image" src="/api/projects/${encodeURIComponent(project.id)}/location-preview/${encodeURIComponent(result.preview_id)}/pages/${page}" alt="${isPreservedTemplateMapping(mapping)?'原文预览':'插入位置原页预览'}，第 ${page} 页">${result.markers.filter(m=>m.page===page).map(m=>`<div class="location-marker ${m.kind==='cell'?'cell':'after'}" style="top:${m.y*100}%;${m.kind==='cell'?`left:${m.x*100}%;`:''}"><span>${esc(m.label)}</span></div>`).join('')}</div><p class="muted small">第 ${page} / ${result.page_count} 页</p>`;
    $('img',el).addEventListener('error',()=>failed(new Error('原页暂时加载失败，请重试。')),{once:true});
  }catch(error){failed(error);}
}
async function openLocationPreview(mappingId) {
  const mapping=state.project?.mappings.find(m=>m.id===mappingId);
  if(!mapping)return;
  const preserved=isPreservedTemplateMapping(mapping);
  closeModal(false);dialogReturnFocus=document.activeElement;
  Object.assign(locationUi,{mappingId,projectId:state.project.id,preserveOriginal:preserved,result:null,page:1,zoom:1,showMarkers:true});
  const modal=document.createElement('dialog');
  modal.id='active-modal';modal.className='modal location-modal';modal.setAttribute('aria-labelledby','modal-title');
  modal.innerHTML=`<div class="modal-header"><div><h2 id="modal-title">${preserved?'原文预览':'插入位置预览'}</h2><p>${esc(mapping.category_name||mapping.title)} · ${esc(state.project.template_name)}</p></div><button type="button" class="icon-button" data-action="close-modal" aria-label="关闭弹窗">${icon('close')}</button></div>${preserved?'':`<div class="location-selector"><label for="location-locator">模板插入位置</label><select id="location-locator"><option value="">未选择 · 先查看标书原页</option>${(state.project.headings||[]).map(h=>`<option value="${esc(h.id)}" ${h.id===mapping.locator_id?'selected':''}>${esc(h.title||h.preview||'未命名段落')}${h.kind==='placeholder'?' · 占位符':''}</option>`).join('')}</select><span class="muted small">改选后须返回核对页保存。</span></div>`}<div class="location-modal-content" aria-live="polite"></div><div class="modal-footer"><span class="muted small">最终页码以 Word 排版为准。</span><button type="button" class="btn" data-action="close-modal">关闭预览</button></div>`;
  document.body.append(modal);modal.showModal();
  modal.addEventListener('cancel',e=>{e.preventDefault();closeModal();});
  modal.addEventListener('click',e=>{if(e.target===modal)closeModal();});
  await loadLocationPreview(modal);
}
async function loadLocationPreview(modal) {
  const mapping=state.project?.mappings.find(m=>m.id===locationUi.mappingId);
  if(!mapping||!modal?.isConnected)return;
  const requestId=++locationUi.requestId;
  locationUi.result=null;
  $('.location-modal-content',modal).innerHTML='<div class="location-loading"><span class="spinner"></span><strong>正在生成标书原页预览…</strong></div>';
  try {
    const result=await api(`/api/projects/${encodeURIComponent(state.project.id)}/location-preview`,{method:'POST',body:{mapping_id:mapping.id,locator_id:mapping.locator_id,material_ids:mapping.material_ids||[],action:mapping.action||'fill'}});
    if(!modal.isConnected||requestId!==locationUi.requestId)return;
    locationUi.result=result;locationUi.page=result.initial_page;renderLocationPreview();
  } catch(error) {
    if(modal.isConnected&&requestId===locationUi.requestId)$('.location-modal-content',modal).innerHTML=`<div class="location-loading"><p class="form-error" role="alert">${esc(error.message)}</p>${button('重试预览','location-retry',{small:true})}</div>`;
  }
}
function renderLocationPreview() {
  const modal=$('.location-modal');if(!modal||!locationUi.result)return;
  const {result,page,zoom,showMarkers,projectId,preserveOriginal}=locationUi;
  const pageMarkers=result.markers.filter(m=>m.page===page);
  const focused=document.activeElement?.dataset?.action;
  $('.location-modal-content',modal).innerHTML=`
    <div class="location-summary"><strong>${esc(result.title)}</strong>${renderLocationDescription(result.description)}${result.target_pages.length?`<span class="muted small">${preserveOriginal?'原文位于':'插入位置在'}预览第 ${result.target_pages.join('、')} 页</span>`:''}</div>
    <div class="location-toolbar"><div class="actions">${button('上一页','location-prev',{small:true,disabled:page<=1})}<label class="location-page-input">预览页 <input id="location-page-number" type="number" min="1" max="${result.page_count}" value="${page}" aria-label="预览页码"> / ${result.page_count}</label>${button('下一页','location-next',{small:true,disabled:page>=result.page_count})}</div><div class="actions">${button(preserveOriginal?'回到原文':'回到插入位置','location-target',{small:true,disabled:preserveOriginal?!(result.target_pages[0]||result.initial_page):!result.markers.length})}${result.markers.length?button(showMarkers?'隐藏标记':'显示标记','location-toggle',{small:true}):''}${button('−','location-zoom-out',{small:true,disabled:zoom<=.75})}<span class="muted small">${Math.round(zoom*100)}%</span>${button('＋','location-zoom-in',{small:true,disabled:zoom>=2})}</div></div>
    <div class="location-viewport" role="region" aria-label="标书原页预览" tabindex="0"><div class="location-paper" style="width:${zoom*100}%">
    <img class="location-page-image" src="/api/projects/${encodeURIComponent(projectId)}/location-preview/${encodeURIComponent(result.preview_id)}/pages/${page}" alt="标书原页，预览第 ${page} 页">
    ${showMarkers?pageMarkers.map(m=>`<div class="location-marker ${m.kind==='cell'?'cell':'after'}" style="top:${m.y*100}%;${m.kind==='cell'?`left:${m.x*100}%;`:''}" role="note" aria-label="${esc(m.label)}"><span ${m.kind==='cell'&&m.x>.5?'style="left:auto;right:18px"':''}>${esc(m.label)}</span></div>`).join(''):''}
    </div><div class="location-image-error" role="alert" hidden><p>此页未能加载，预览可能已过期。</p>${button('重新生成预览','location-retry',{small:true})}</div></div>`;
  if(focused)$$('[data-action]',modal).find(b=>b.dataset.action===focused)?.focus();
}
function renderMappingPreview(mapping) {
  const selected=(mapping.material_ids||[]).map(material).filter(Boolean);
  const action=mapping.action||'fill';
  if(action!=='fill')return '';
  if(!selected.length)return '<div class="mapping-preview"><p class="muted small">暂无可预览材料，请先选择或添加材料。</p></div>';
  return `<div class="mapping-preview"><h4>已选材料预览</h4>${selected.map(m=>{
    const files=m.files||[], pages=files.length>2?[0,files.length-1]:files.map((_,i)=>i);
    return `<section class="mapping-preview-material" aria-label="${esc(m.name)}预览"><div class="mapping-preview-heading"><div><strong>${esc(m.name)}</strong><span class="muted small">${m.mode==='text'?'文字材料':`共 ${files.length} 页${files.length>2?' · 仅展示首页和末页':''}`}</span></div><button type="button" class="text-button" data-action="open-material" data-id="${esc(m.id)}">查看完整材料 →</button></div>${m.mode==='text'?`<div class="mapping-text-preview">${esc(m.content||'尚未填写文字正文')}</div>`:files.length?`<div class="mapping-preview-pages">${pages.map(i=>{
      const f=files[i], rotation=((Number(f.rotation)||0)%360+360)%360;
      return `<button type="button" class="mapping-preview-page" data-action="open-material" data-id="${esc(m.id)}" data-index="${i}" aria-label="查看${esc(m.name)}第 ${i+1} 页"><span class="mapping-preview-image"><img src="${esc(f.url||`/api/files/${encodeURIComponent(f.id)}`)}" alt="${esc(m.name)}，第 ${i+1} 页" loading="lazy" style="transform:rotate(${rotation}deg);${rotation%180?'max-width:220px;max-height:220px;':''}"><span class="mapping-preview-error" hidden>图片加载失败，请点击查看完整材料</span></span><span class="mapping-preview-caption">${files.length>2?(i===0?'首页':'末页'):'图片'} · 第 ${i+1} 页</span></button>`;
    }).join('')}</div>`:'<p class="muted small">尚未上传图片</p>'}</section>`;
  }).join('')}</div>`;
}
function renderExports(exports) {
  return `<section class="panel export-panel"><div class="panel-heading"><h2>导出历史</h2><span class="count-label">${exports.length} 份记录</span></div>${exports.length?`<div class="table-wrap"><table class="data-table"><thead><tr><th>导出文件</th><th>类型</th><th>结果</th><th>导出时间</th><th>下载</th></tr></thead><tbody>${exports.map(e=>`<tr><td class="table-title">${esc(e.filename)}</td><td>${statusBadge(e.mode,e.mode==='reviewed'?'核对版':'初稿')}</td><td class="muted">填入 ${e.filled_count||0} · 缺项 ${e.missing_count||0} · 跳过 ${e.skipped_count||0}</td><td class="muted">${date(e.created_at,true)}</td><td><a href="${esc(e.download_url || `/api/exports/${encodeURIComponent(e.id)}/download`)}" download>Word 文件</a> <span class="muted"> / </span> <a href="${esc(e.report_url || `/api/exports/${encodeURIComponent(e.id)}/report`)}" download>核对报告</a></td></tr>`).join('')}</tbody></table></div>`:emptyState('暂无导出记录','','',true,'download')}</section>`;
}

function openModal(title, subtitle, content, formName, submitLabel, options = {}) {
  closeModal(false); dialogReturnFocus=document.activeElement;
  const modal=document.createElement('dialog');modal.className='modal';modal.id='active-modal';modal.setAttribute('aria-labelledby','modal-title');
  modal.innerHTML=`<div class="modal-header"><div><h2 id="modal-title">${esc(title)}</h2>${subtitle?`<p>${esc(subtitle)}</p>`:''}</div><button type="button" class="icon-button" data-action="close-modal" aria-label="关闭弹窗">${icon('close')}</button></div><form data-form="${esc(formName)}" ${options.id?`data-id="${esc(options.id)}"`:''}><div class="modal-body">${content}<div class="form-error" role="alert"></div></div><div class="modal-footer"><button type="button" class="btn" data-action="close-modal">取消</button><button type="submit" class="btn primary">${esc(submitLabel)}</button></div></form>`;
  const form=modal.querySelector('form');
  if(options.id&&['material','append-files','remove-file','remove-files'].includes(formName))form.dataset.revision=String(material(options.id)?.revision||1);
  if(formName==='rescan')form.dataset.mappingRevision=state.project.mapping_revision;
  document.body.append(modal); modal.showModal();
  modal.addEventListener('cancel',e=>{e.preventDefault();closeModal();});
  modal.addEventListener('click',e=>{if(e.target===modal && !modal.querySelector('button[type=submit]:disabled'))closeModal();});
}
function closeModal(restore=true) {
  const modal=$('#active-modal');if(!modal)return;
  if(modal.querySelector('button[type=submit]:disabled'))return;
  modal.close();modal.remove();if(restore && dialogReturnFocus?.isConnected)dialogReturnFocus.focus();
}
function showNewProject() {
  openModal('新建标书项目','',`<div class="form-grid"><div class="form-field full"><label for="project-name">项目名称 <span class="required">*</span></label><input id="project-name" class="input" name="name" required maxlength="160" placeholder="例如：某项目投标文件"></div><div class="form-field full"><label>模板来源</label><div class="radio-options"><label class="radio-option"><input type="radio" name="source" value="upload" checked><span><strong>上传 Word 模板</strong></span></label><label class="radio-option"><input type="radio" name="source" value="sample"><span><strong>使用已有示例</strong><small>测试示例</small></span></label></div></div><div class="form-field full" id="template-upload-area"><div class="upload-zone">${icon('upload')}<label for="template-file">选择项目的 Word 模板</label><input id="template-file" name="file" type="file" accept=".doc,.docx,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"><small>支持 .doc / .docx，单个文件最大 30 MB；旧版 Word 自动转换，原文件保留。</small></div></div></div>`,'project','创建并扫描');
}
function showMaterialForm(id, category) {
  if(isCreditCategory(category)||id&&isCreditCategory(material(id)?.category_id)){toast('此栏目仅由全自动查询归档，不能手工添加或修改材料。',true);return;}
  const m=id?material(id):null,special=isSpecial(m?.category_id||category),cats=state.data.categories.filter(c=>c.layer===(special?'special':'common')&&!isCreditCategory(c.id));
  openModal(m?'编辑材料信息':special?'新增文字材料':'新增图片材料','',`<div class="form-grid">${special&&m?.category_id!=='text_template'&&category!=='text_template'?specialFormFields(m):''}<div class="form-field full"><label for="material-name">材料名称 *</label><input id="material-name" class="input" name="name" required maxlength="200" value="${esc(m?.name)}" placeholder="例如：2025 年财务审计报告"></div><div class="form-field"><label for="material-category">所属栏目 *</label><select id="material-category" name="category_id" required>${cats.map(c=>`<option value="${esc(c.id)}" ${c.id===(m?.category_id||category)?'selected':''}>${esc(c.name)}${!c.enabled?'（已停用）':''}</option>`).join('')}</select></div><div class="form-field"><label for="material-subject">材料主体 *</label><input id="material-subject" class="input" name="subject" required maxlength="160" value="${esc(m?.subject||'北京腾宝科技有限公司')}" placeholder="填写材料对应的投标主体"></div><div class="form-field"><label for="material-years">适用年度</label><input id="material-years" class="input" name="years" value="${esc(m?.years?.join('、'))}" placeholder="例如：2023、2024"><span class="form-hint" id="material-years-hint" ${((m?.category_id||category)==='audit_report')?'':'hidden'}>填写报告对应年度</span></div><div class="form-field"><label for="material-version">版本说明</label><input id="material-version" class="input" name="version" value="${esc(m?.version)}" placeholder="例如：2025 年更新版" maxlength="120"></div><div class="form-field"><label for="material-valid">有效期截止</label><input id="material-valid" class="input" type="text" name="valid_until" value="${esc(m?.valid_until)}" placeholder="YYYY-MM-DD、长期或下一年度更新"></div><div class="form-field"><label for="material-mode">内容与排版方式 *</label><select id="material-mode" name="mode"><option value="page" ${((!m&&!special)||m?.mode==='page')?'selected':''}>图片 · 每张独立一页</option><option value="side_by_side" ${m?.mode==='side_by_side'?'selected':''}>图片 · 双图并排</option><option value="text" ${(m?.mode==='text'||!m&&special)?'selected':''}>文字内容</option></select></div><div class="form-field full ${(m?.mode==='text'||!m&&special)?'':'hidden'}" id="material-text-area"><label for="material-content">材料正文</label><textarea id="material-content" name="content" rows="7" placeholder="填写可复用的公司介绍或说明正文">${esc(m?.content)}</textarea></div>${!m?`<div class="form-field full ${special?'hidden':''}" id="material-upload-area"><div class="upload-zone">${icon('upload')}<label for="material-files">添加材料图片，可一次选择多张</label><input id="material-files" name="files" type="file" multiple accept="image/png,image/jpeg,image/webp,image/bmp,image/tiff"><small>支持 PNG / JPEG / WebP 等图片</small></div></div>`:''}<div class="form-field full"><label for="material-description">使用说明 / 注意事项</label><textarea id="material-description" name="description" rows="3" placeholder="例如：证件附件，需核对模板证明或授权正文">${esc(m?.description)}</textarea></div></div>`,'material',m?'保存修改':'添加材料',{id});
  const form=$('form[data-form="material"]');
  $('#material-years-hint',form).hidden=form.elements.namedItem('category_id').value!=='audit_report';
  form.dataset.initialMaterialBody=JSON.stringify(materialFormBody(form));
  queueMaterialValidation(form);
}
let materialFeedbackTimer, materialFeedbackSequence=0;
function materialFormBody(form) {
  const fd=new FormData(form), raw=String(fd.get('years')||'').trim();
  return {material_id:form.dataset.id||null,name:String(fd.get('name')||'').trim(),subject:String(fd.get('subject')||'').trim(),category_id:fd.get('category_id'),years:raw?raw.split(/[\s,，、;；]+/).filter(Boolean).map(y=>/^\d{4}$/.test(y)?Number(y):null):[],valid_until:String(fd.get('valid_until')||'').trim(),mode:fd.get('mode'),content:String(fd.get('content')||''),description:String(fd.get('description')||''),upload_count:form.dataset.id?(material(form.dataset.id)?.files?.length||0):($('input[name=files]',form)?.files?.length||0)};
}
async function validateMaterialForm(form) {
  const sequence=++materialFeedbackSequence;
  const body=materialFormBody(form);
  const feedback=await api('/api/materials/validate',{method:'POST',body});
  if(!form.isConnected||sequence!==materialFeedbackSequence)return null;
  $$('.material-field-feedback',form).forEach(x=>x.remove());
  $$('[aria-invalid]',form).forEach(x=>x.removeAttribute('aria-invalid'));
  $$('[aria-describedby^="material-feedback-"]',form).forEach(x=>x.removeAttribute('aria-describedby'));
  if($('.form-error',form).textContent==='请修正上方标出的材料问题后再保存。')$('.form-error',form).textContent='';
  const files=$('input[name=files]',form)?.files||[];
  if([...files].some(f=>! /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(f.name)))feedback.errors.files='图片格式不支持，请选择 PNG、JPEG、WebP、BMP 或 TIFF。';
  if([...files].some(f=>f.size>15*1024*1024))feedback.errors.files='单张图片不能超过 15 MB。';
  if([...files].reduce((sum,f)=>sum+Math.ceil(f.size/3)*4,0)>79*1024*1024)feedback.errors.files='本批图片超出一次上传容量，请减少图片数量后分批追加。';
  const targets=new Set();
  for(const field of new Set([...Object.keys(feedback.errors),...Object.keys(feedback.warnings)])){
    const input=form.elements.namedItem(field), container=input?.closest('.form-field');
    if(!container)continue;
    targets.add(field);
    const note=document.createElement('div');note.className='material-field-feedback '+(feedback.errors[field]?'error':'warning');note.textContent=feedback.errors[field]||feedback.warnings[field];
    note.id=`material-feedback-${field}`;input.setAttribute('aria-describedby',note.id);
    if(feedback.errors[field])input.setAttribute('aria-invalid','true');
    container.append(note);
  }
  let panel=$('.material-save-feedback',form);
  if(!panel){panel=document.createElement('div');panel.className='material-save-feedback';panel.setAttribute('aria-live','polite');$('.modal-body',form).prepend(panel);}
  const changed=!!form.dataset.id&&JSON.stringify(body)!==form.dataset.initialMaterialBody;
  panel.innerHTML=materialFeedbackSummary(feedback,targets,changed);
  return feedback;
}
function materialFeedbackSummary(feedback,targets,changed) {
  const labels={name:'材料名称',subject:'材料主体',category_id:'所属栏目',years:'适用年度',valid_until:'有效期截止',mode:'内容与排版方式',content:'材料正文',files:'材料图片',description:'使用说明'};
  const group=(entries,kind)=>entries.length?`<div class="material-feedback-${kind}"><strong>${kind==='errors'?'请先修正':'请核对'} ${entries.length} 项${kind==='errors'?'问题':'提醒'}</strong><ul>${entries.map(([field,message])=>`<li>${targets.has(field)?`<button type="button" class="text-button" data-action="focus-material-field" data-id="${esc(field)}">定位：${esc(labels[field]||field)}</button>`:esc(message)}</li>`).join('')}</ul></div>`:'';
  const errors=Object.entries(feedback.errors),warnings=Object.entries(feedback.warnings).filter(([field])=>!feedback.errors[field]);
  const projects=changed?[...new Set(feedback.project_warnings||[])]:[];
  return group(errors,'errors')+group(warnings,'warnings')+(projects.length?`<div class="material-feedback-warnings"><strong>受影响项目需重新核对</strong><ul>${projects.map(message=>`<li>${esc(message)}</li>`).join('')}</ul></div>`:'');
}
function queueMaterialValidation(form) {
  clearTimeout(materialFeedbackTimer);++materialFeedbackSequence;
  materialFeedbackTimer=setTimeout(()=>validateMaterialForm(form).catch(e=>{if(form.isConnected)$('.form-error',form).textContent=e.message;}),300);
}
function showCategoryForm(id) {
  if(isCreditCategory(id)){toast('此栏目名称、别名与启用状态由全自动查询管理。',true);return;}
  const c=id?state.data.categories.find(x=>x.id===id):null;
  openModal(c?'编辑匹配栏目':'新增通用栏目','用于匹配原文标题',`<div class="form-grid"><div class="form-field full"><label for="category-name">栏目名称 *</label><input id="category-name" class="input" name="name" required value="${esc(c?.name)}" maxlength="160" placeholder="填写通用栏目名称"></div><div class="form-field full"><label for="category-aliases">匹配别名</label><textarea id="category-aliases" name="aliases" rows="5" placeholder="每行一个别名，也可用逗号分隔">${esc(c?.aliases?.join('\n'))}</textarea><span class="form-hint">使用原文标题，避免宽泛词</span></div><div class="form-field full"><label class="switch"><input type="checkbox" name="enabled" ${!c||c.enabled?'checked':''} ${isCreditCategory(c?.id)?'disabled':''}>启用此栏目</label>${isCreditCategory(c?.id)?'<span class="form-hint">此栏目由三项全自动验证条件管理，不能手动启用或停用。</span>':''}</div></div>`,'category',c?'保存修改':'新增栏目',{id});
}
function showIndustryForm() {
  openModal('新增行业分类','先建立业务分类，后续扩展文字材料与匹配规则。',`<div class="form-field"><label for="industry-name">行业分类名称 *</label><input id="industry-name" class="input" name="name" required maxlength="80" placeholder="例如：保险、银行、基金"><span class="form-hint">仅新增你需要的分类，不自动创建示例材料。</span></div>`,'industry','创建分类');
}
function showAppendFiles(id) {
  openModal('追加材料图片','新图片追加到本组末尾。',`<div class="upload-zone">${icon('upload')}<label for="append-files">选择需要追加的图片</label><input id="append-files" name="files" type="file" multiple required accept="image/png,image/jpeg,image/webp,image/bmp,image/tiff"><small>PNG / JPEG / WebP 等图片</small></div>`,'append-files','追加图片',{id});
}
function showRemoveFile() {
  const m=material(state.drawerId),f=m?.files?.[state.pageIndex];if(!f)return;
  openModal('将图片移出本组','移出后不再随本材料导出，原件保留。',`<div class="detail-description">${esc(f.name)}</div><input type="hidden" name="file_id" value="${esc(f.id)}">`,'remove-file','确认移出',{id:m.id});
}
function showBatchRemoveFiles() {
  const m=material(state.drawerId),files=m?.files||[];
  if(!files.length||isCreditCategory(m.category_id)||m.credit_evidence)return;
  openModal('批量移除图片','移出后不再随本材料导出，原件保留。',`<div class="batch-remove-toolbar"><label><input type="checkbox" id="batch-select-all">全选</label><span id="batch-selection-count" role="status">已选 0 / ${files.length} 张</span></div><div class="batch-remove-grid">${files.map((f,i)=>`<label class="batch-remove-item"><input type="checkbox" name="file_ids" value="${esc(f.id)}" aria-label="选择第 ${i+1} 张 ${esc(f.name)}"><img src="${esc(f.url||`/api/files/${encodeURIComponent(f.id)}`)}?thumb=1" alt="第 ${i+1} 张" loading="lazy"><span>第 ${i+1} 张 · ${esc(f.name)}</span></label>`).join('')}</div><div class="note-bar" id="batch-empty-warning" role="status" hidden>${icon('info')}<span>将清空本组图片。</span></div>`,'remove-files','确认移除',{id:m.id});
}
function updateBatchSelection() {
  const form=$('form[data-form="remove-files"]');if(!form)return;
  const boxes=$$('input[name="file_ids"]',form),count=boxes.filter(x=>x.checked).length;
  const all=$('#batch-select-all',form);all.checked=count===boxes.length;all.indeterminate=count>0&&count<boxes.length;
  $('.form-error',form).textContent='';
  $('#batch-selection-count',form).textContent=`已选 ${count} / ${boxes.length} 张`;
  $('#batch-empty-warning',form).hidden=count!==boxes.length||!count;
  $('button[type="submit"]',form).textContent=count?`确认移除 ${count} 张`:'确认移除';
}
async function openDrawer(id, pageIndex=0) {
  if(!state.data.materials.some(m=>m.id===id))specialState.cache.set(id,await api('/api/materials/'+encodeURIComponent(id)));
  state.drawerId=id;state.pageIndex=Math.max(0,Math.min(Number(pageIndex)||0,(material(id)?.files?.length||1)-1));drawerReturnFocus=document.activeElement;renderDrawer();
  $('.drawer [data-action="close-drawer"]')?.focus();
}
function closeDrawer() {
  $('#overlay-root').innerHTML='';state.drawerId=null;document.body.style.overflow='';if(drawerReturnFocus?.isConnected)drawerReturnFocus.focus();
}
function renderDrawer() {
  const m=material(state.drawerId);if(!m){closeDrawer();return;}
  const files=m.files||[];state.pageIndex=Math.max(0,Math.min(state.pageIndex,files.length-1));const f=files[state.pageIndex];
  const rotation=f?Number(f.rotation||0):0,creditManaged=isCreditCategory(m.category_id)||!!m.credit_evidence;
  document.body.style.overflow='hidden';
  $('#overlay-root').innerHTML=`<div class="overlay-backdrop" data-action="close-drawer"></div><aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title"><div class="drawer-header"><div class="drawer-title"><h2 id="drawer-title">${esc(m.name)}</h2><p>${esc(categoryName(m.category_id))} · ${m.mode==='text'?'文字材料':`${files.length} 张图片`}</p></div><button type="button" class="icon-button" data-action="close-drawer" aria-label="关闭材料详情">${icon('close')}</button></div><div class="drawer-body"><div class="actions">${validityBadge(m.valid_until)}${m.version?`<span class="muted small">版本 ${esc(m.version)}</span>`:''}</div><dl class="detail-meta"><div><dt>所属主体</dt><dd>${esc(m.subject||'未填写')}</dd></div><div><dt>适用年度</dt><dd>${esc(m.years?.join('、')||'未指定')}</dd></div><div><dt>排版方式</dt><dd>${{page:'每张独立一页',side_by_side:'双图并排',text:'文字内容'}[m.mode]||esc(m.mode)}</dd></div><div><dt>最近更新</dt><dd>${date(m.updated_at)}</dd></div></dl>${m.description&&!isSpecial(m.category_id)?`<p class="detail-description">${esc(m.description)}</p>`:''}${renderCreditEvidence(m.credit_evidence)}${['legal_identity','authorization'].includes(m.category_id)?`<div class="note-bar" style="margin:0 0 18px">${icon('info')}<span>证件图片是附件，需另外核对模板中的身份证明或授权委托正文、签字盖章与日期。</span></div>`:''}<div class="preview-stage">${m.mode==='text'?`<div class="text-preview">${esc(m.content||'尚未填写文字正文')}</div>`:f?`<img src="${esc(f.url||`/api/files/${encodeURIComponent(f.id)}`)}" alt="${esc(m.name)}，第 ${state.pageIndex+1} 张：${esc(f.name)}" style="transform:rotate(${rotation}deg);${rotation%180?'max-height:360px;max-width:78%;':''}">`:'<span class="muted small">尚未上传图片</span>'}</div>${m.mode!=='text'&&files.length?`<div class="preview-toolbar"><div class="preview-pagination"><button type="button" class="icon-button" data-action="preview-prev" aria-label="上一张" ${state.pageIndex===0?'disabled':''}>${icon('back')}</button><span>${state.pageIndex+1} / ${files.length}</span><button type="button" class="icon-button" data-action="preview-next" aria-label="下一张" ${state.pageIndex===files.length-1?'disabled':''}>${icon('chevron')}</button></div>${creditManaged?'':`<div class="actions"><button type="button" class="btn small" data-action="rotate-file">${icon('rotate')}旋转 90°</button><button type="button" class="icon-button" data-action="move-file-up" aria-label="将当前图片前移一位" title="前移一位" ${state.pageIndex===0?'disabled':''}>${icon('up')}</button><button type="button" class="icon-button" data-action="move-file-down" aria-label="将当前图片后移一位" title="后移一位" ${state.pageIndex===files.length-1?'disabled':''}>${icon('down')}</button></div>`}</div><div class="preview-filename">${esc(f.name)}${rotation?` · 顺时针 ${rotation}°`:''} ${f.width&&f.height?`<details><summary>图片详情</summary>${f.width} × ${f.height}</details>`:''} ${creditManaged?'':'<button type="button" class="text-button" data-action="remove-file" style="font-size:10px;margin-left:8px;color:#b17569">移出本组</button>'}</div><div class="preview-files" aria-label="材料图片列表">${files.map((x,i)=>`<button type="button" class="preview-thumb ${state.pageIndex===i?'active':''}" data-action="preview-page" data-index="${i}" aria-label="预览第 ${i+1} 张 ${esc(x.name)}" ${state.pageIndex===i?'aria-current="true"':''}><img src="${esc(x.url||`/api/files/${encodeURIComponent(x.id)}`)}?thumb=1" alt="" loading="lazy"><span>${i+1}</span></button>`).join('')}</div>`:''}</div><div class="drawer-footer">${creditManaged?'<span class="muted small">自动归档证据，不可编辑</span>':`${m.mode==='text'?'<span></span>':`<div class="actions">${button('追加图片','append-files',{icon:'plus',id:m.id})}${files.length?button('批量移除','batch-remove-files',{small:true}):''}</div>`}${button('编辑信息','edit-material',{primary:true,icon:'edit',id:m.id})}`}</div></aside>`;
}

async function toFiles(fileList) {
  const files=[...fileList];
  const total=files.reduce((n,f)=>n+f.size,0);if(total>100*1024*1024)throw new Error('本次选择的文件超过 100 MB，请分批添加。');
  return Promise.all(files.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:file.name,data:String(reader.result).split(',')[1]});reader.onerror=()=>reject(new Error(`无法读取文件：${file.name}`));reader.readAsDataURL(file);})));
}
async function submitForm(form) {
  const kind=form.dataset.form,id=form.dataset.id,fd=new FormData(form),submit=$('button[type=submit]',form),error=$('.form-error',form),label=submit.textContent;
  submit.disabled=true;form.inert=true;submit.innerHTML='<span class="spinner" style="width:14px;height:14px"></span> 正在保存…';error.textContent='';
  try {
    let result;
    if(kind==='project') {
      const body={name:String(fd.get('name')).trim()};
      if(fd.get('source')==='sample')body.use_sample=true;
      else {const f=$('input[name=file]',form).files[0];if(!f)throw new Error('请选择一个 .doc 或 .docx 模板，或使用已有示例。');if(!/\.docx?$/i.test(f.name))throw new Error('模板需为 .doc 或 .docx 格式，请先在 Word 中另存为对应格式。');if(f.size>30*1024*1024)throw new Error('Word 模板不能超过 30 MB。');if(!f.size)throw new Error('Word 模板不能为空，请重新选择文件。');const item=(await toFiles([f]))[0];body.filename=item.name;body.data=item.data;}
      submit.innerHTML='<span class="spinner" style="width:14px;height:14px"></span> 正在读取并扫描模板…';
      result=await api('/api/projects',{method:'POST',body});state.project=result;state.view='projects';state.query='';state.dirty=false;tenderFlow.url();
    } else if(kind==='material') {
      clearTimeout(materialFeedbackTimer);
      const feedback=await validateMaterialForm(form);
      if(!feedback||Object.keys(feedback.errors).length){$('.material-save-feedback',form)?.scrollIntoView({block:'nearest'});throw new Error('请修正上方标出的材料问题后再保存。');}
      const yearInput=String(fd.get('years')||'').trim(),years=yearInput?yearInput.split(/[\s,，、;；]+/).filter(Boolean):[];
      if(years.some(y=>!/^\d{4}$/.test(y)))throw new Error('年度请填写四位年份，多个年度用逗号或顿号分隔。');
      const body={name:String(fd.get('name')).trim(),category_id:fd.get('category_id'),subject:String(fd.get('subject')).trim(),version:String(fd.get('version')||'').trim(),years:[...new Set(years.map(Number))],valid_until:fd.get('valid_until')||'',mode:fd.get('mode'),content:String(fd.get('content')||''),description:String(fd.get('description')||'')};
      if(isSpecial(body.category_id))Object.assign(body,{client_name:String(fd.get('client_name')||''),case_name:String(fd.get('case_name')||''),subject_kind:fd.get('subject_kind'),linked_category_ids:fd.getAll('linked_category_ids')});
      if(!body.name||!body.subject)throw new Error('请填写材料名称与所属主体。');
      if(!id){body.files=await toFiles($('input[name=files]',form)?.files||[]);if(body.mode!=='text'&&!body.files.length)throw new Error('请至少添加一张图片，或选择文字内容。');}
      if(body.mode==='text'&&!body.content.trim())throw new Error('请填写文字材料正文。');
      if(id)body.revision=Number(form.dataset.revision);
      result=await api(id?`/api/materials/${encodeURIComponent(id)}`:'/api/materials',{method:id?'PATCH':'POST',body});
    } else if(kind==='append-files') {
      const files=await toFiles($('input[name=files]',form).files);if(!files.length)throw new Error('请至少选择一张图片。');
      result=await api(`/api/materials/${encodeURIComponent(id)}/files`,{method:'POST',body:{files,revision:Number(form.dataset.revision)}});
    } else if(kind==='remove-files') {
      const ids=fd.getAll('file_ids').map(String);
      if(!ids.length)throw new Error('请先勾选需要移除的图片。');
      result=await api(`/api/materials/${encodeURIComponent(id)}`,{method:'PATCH',body:{removed_file_ids:ids,revision:Number(form.dataset.revision)}});
    } else if(kind==='remove-file') {
      result=await api(`/api/materials/${encodeURIComponent(id)}`,{method:'PATCH',body:{removed_file_ids:[String(fd.get('file_id'))],revision:Number(form.dataset.revision)}});
    } else if(kind==='category') {
      const body={name:String(fd.get('name')).trim(),aliases:[...new Set(String(fd.get('aliases')||'').split(/[\n,，、;；]/).map(a=>a.trim()).filter(Boolean))],layer:'common',enabled:fd.has('enabled')};
      if(!body.name)throw new Error('请填写栏目名称。');if(isCreditCategory(id))delete body.enabled;
      result=await api(id?`/api/categories/${encodeURIComponent(id)}`:'/api/categories',{method:id?'PATCH':'POST',body});
    } else if(kind==='industry') {
      const name=String(fd.get('name')).trim();if(!name)throw new Error('请填写行业分类名称。');result=await api('/api/industries',{method:'POST',body:{name}});
    } else if(kind==='rescan') {
      result=await api(`/api/projects/${encodeURIComponent(state.project.id)}/scan`,{method:'POST',body:{mapping_revision:form.dataset.mappingRevision}});state.project=result;state.dirty=false;
    }
    await refreshData();if(state.project)await loadProjectPickers();if(kind==='rescan')projectUi.step='special';submit.disabled=false;closeModal();render();
    if(state.drawerId)renderDrawer();
    if(kind==='material'&&!id&&result?.id)openDrawer(result.id);
    toast(kind==='project'?'项目已创建':kind==='rescan'?'已重新扫描，请重新核对匹配结果。':kind==='category'?'栏目已保存，已有项目重新扫描后生效。':['remove-file','remove-files'].includes(kind)?'已移出本组':'已保存');
  } catch(e) {error.textContent=e.message;submit.disabled=false;submit.textContent=label;}
  finally {form.inert=false;}
}
async function saveProject(notify=true) {
  if(!state.project)return;
  if(state.busy)throw new Error('正在保存核对，请等待完成。');
  const mappings=state.project.mappings.map(m=>({id:m.id,locator_id:m.locator_id,material_ids:m.material_ids||[],action:m.action||'fill',confirmed:!!m.confirmed,note:m.note||''}));
  state.busy=true;$('#app').inert=true;
  try {state.project=await api(`/api/projects/${encodeURIComponent(state.project.id)}/mappings`,{method:'PATCH',body:{mappings,mapping_revision:state.project.mapping_revision}});state.dirty=false;projectUi.navigation=null;await refreshData({refreshProject:false});if(notify)toast('核对已保存');}
  finally {state.busy=false;render();}
}
async function navigate(view, category) {
  if(view==='dashboard'){
    closeDrawer();
    if(state.view==='dashboard'){
      $('.app-shell')?.classList.remove('nav-open');
      window.scrollTo({top:0});
      return;
    }
    return tenderFlow.enter({resume:true});
  }
  await tenderFlow.beforeLeave();
  if(state.dirty)await saveProject(false);
  closeDrawer();state.view=view;state.project=null;state.query='';state.category=category||'all';tenderFlow.url();if(view==='industries')await loadSpecial();render();window.scrollTo({top:0});
}
async function openProject(id) {
  if(state.dirty)await saveProject(false);
  await tenderFlow.beforeLeave();
  const project=await api(`/api/projects/${encodeURIComponent(id)}`);
  if(project.framework_id)return tenderFlow.resumeProject(project);
  state.view='projects';state.project=project;tenderFlow.url();state.dirty=false;resetProjectUi();await loadProjectPickers();render();window.scrollTo({top:0});
}
async function changeFile(action) {
  const m=material(state.drawerId),files=m.files||[],f=files[state.pageIndex];if(!f)return;
  let body;
  if(action==='rotate-file')body={rotations:{[f.id]:(Number(f.rotation||0)+90)%360}};
  else {const other=state.pageIndex+(action==='move-file-up'?-1:1);if(other<0||other>=files.length)return;const order=files.map(x=>x.id);[order[state.pageIndex],order[other]]=[order[other],order[state.pageIndex]];state.pageIndex=other;body={file_order:order};}
  body.revision=m.revision;
  $$('.drawer button').forEach(b=>b.disabled=true);
  try {await api(`/api/materials/${encodeURIComponent(m.id)}`,{method:'PATCH',body});await refreshData();render();renderDrawer();}
  catch(e){renderDrawer();throw e;}
}
let exportInFlight=false;
const exportTasks={jobs:[],timer:null,modalId:null,polling:false,loaded:false,hidden:new Set(),error:''};
function exportRemaining(job){
  if(job.status==='queued')return '排队中';
  if(job.status!=='running')return '';
  return '预计还需约 30 秒';
}
function exportTaskContent(job){
  const active=['queued','running'].includes(job.status),result=job.result;
  return `<div class="export-progress-heading"><strong role="status">${esc(job.stage)}</strong><span class="export-elapsed">${esc(exportRemaining(job))}</span></div><progress class="export-progress-bar" aria-label="导出进度" max="100" ${job.status==='queued'?'':`value="${job.progress}"`}></progress>${job.status==='failed'||active?`<p class="muted export-progress-detail">${esc(job.status==='failed'?job.error:'可继续处理其他项目。')}</p>`:''}${result?`<div class="actions"><a class="btn primary" href="${esc(result.download_url)}" download>下载 Word 文件</a><a class="btn" href="${esc(result.report_url)}" download>下载核对报告</a></div>`:''}`;
}
function paintExportTasks(){
  let dock=$('#export-task-dock');
  if(!dock){dock=document.createElement('aside');dock.id='export-task-dock';dock.setAttribute('aria-label','后台导出任务');document.body.append(dock);}
  const visible=exportTasks.jobs.filter(j=>!exportTasks.hidden.has(j.id));
  dock.hidden=!visible.length;
  dock.innerHTML=visible.map(j=>`<div class="export-task-item"><button class="text-button" data-action="export-task-open" data-id="${esc(j.id)}"><strong>${esc(j.project_name)}</strong><span>${esc(j.stage)}${exportRemaining(j)?' · '+esc(exportRemaining(j)):''}</span></button>${['queued','running'].includes(j.status)?'':`<button class="icon-button" data-action="export-task-dismiss" data-id="${esc(j.id)}" aria-label="收起导出任务">×</button>`}</div>`).join('')+(exportTasks.error?`<p role="status">${esc(exportTasks.error)}</p>`:'');
  const modal=$('#export-task-modal'),job=exportTasks.jobs.find(j=>j.id===exportTasks.modalId);
  if(modal&&job){$('.modal-body',modal).innerHTML=exportTaskContent(job)+(exportTasks.error?`<p role="status">${esc(exportTasks.error)}</p>`:'');$('#export-background',modal).textContent=['queued','running'].includes(job.status)?'后台运行':'关闭';}
}
function openExportTask(id){
  $('#export-task-modal')?.close();
  const job=exportTasks.jobs.find(j=>j.id===id);if(!job)return;
  exportTasks.modalId=id;
  const modal=document.createElement('dialog');modal.id='export-task-modal';modal.className='modal export-progress-modal';
  modal.setAttribute('aria-labelledby','export-task-title');
  modal.innerHTML=`<div class="modal-header"><div><h2 id="export-task-title">${job.mode==='reviewed'?'核对版':'初稿'}导出</h2><p>${esc(job.project_name)}</p></div></div><div class="modal-body">${exportTaskContent(job)}</div><div class="modal-footer"><button class="btn" id="export-background">${['queued','running'].includes(job.status)?'后台运行':'关闭'}</button></div>`;
  modal.addEventListener('close',()=>{modal.remove();exportTasks.modalId=null;});
  $('#export-background',modal).addEventListener('click',()=>modal.close());
  document.body.append(modal);modal.showModal();
}
async function pollExportTasks(){
  if(exportTasks.polling)return;exportTasks.polling=true;
  clearTimeout(exportTasks.timer);
  try{
    const jobs=await api('/api/export-jobs');
    if(!exportTasks.loaded){
      // Completed downloads are already available in each project's export history.
      for(const job of jobs){
        if(job.status==='completed'&&!exportTasks.jobs.some(current=>current.id===job.id))exportTasks.hidden.add(job.id);
      }
      exportTasks.loaded=true;
    }
    const newlyDone=jobs.filter(j=>j.status==='completed'&&exportTasks.jobs.some(old=>old.id===j.id&&['queued','running'].includes(old.status)));
    exportTasks.jobs=jobs;exportTasks.error='';paintExportTasks();
    if(newlyDone.length){
      if(newlyDone.some(job=>job.id!==exportTasks.modalId))toast('导出已完成，可在右下角任务中下载。');
      if(!state.dirty){await refreshData();render();}
    }
  }catch(error){exportTasks.error='暂时无法读取进度，正在重连；请勿重复提交。';paintExportTasks();}
  exportTasks.polling=false;
  exportTasks.timer=setTimeout(pollExportTasks,exportTasks.jobs.some(j=>['queued','running'].includes(j.status))?1000:10000);
}
async function exportProject(sourceButton){
  if(exportInFlight)return;
  exportInFlight=true;sourceButton.disabled=true;
  try{
    const mode='draft';
    const job=await api(`/api/projects/${encodeURIComponent(state.project.id)}/export-jobs`,{method:'POST',body:{mode,mapping_revision:state.project.mapping_revision}});
    exportTasks.hidden.delete(job.id);exportTasks.jobs=[job,...exportTasks.jobs.filter(j=>j.id!==job.id)];
    paintExportTasks();openExportTask(job.id);await pollExportTasks();
  }catch(error){toast(error.message,true);await pollExportTasks();}
  finally{exportInFlight=false;sourceButton.disabled=false;}
}

function mappingChange(id,field,value,rerender=true) {
  projectUi.navigation=null;
  $('.material-completion-notice')?.remove();
  $$('.mapping-needs-attention').forEach(card=>card.classList.remove('mapping-needs-attention'));
  const m=state.project?.mappings.find(x=>x.id===id);if(!m)return;
  m[field]=value;if(field==='locator_id'&&isSpecial(m.category_id))m.location_match={status:'manual',reason:'位置已由人工调整',candidates:[]};if(field!=='confirmed')m.confirmed=false;state.dirty=true;
  if(rerender){const scroll=window.scrollY;render();window.scrollTo(0,scroll);}
}

document.addEventListener('click',async e=>{
  const target=e.target.closest('[data-action]');if(!target||target.disabled)return;
  e.preventDefault();const action=target.dataset.action,id=target.dataset.id;
  try {
    if(action==='export-task-open')openExportTask(id);
    else if(action==='export-task-dismiss'){exportTasks.hidden.add(id);paintExportTasks();}
    else if(action==='navigate')await navigate(target.dataset.view);
    else if(action==='toggle-nav')$('.app-shell')?.classList.toggle('nav-open');
    else if(action==='new-framework'){
      target.disabled=true;
      try{await tenderFlow.enter();}finally{if(target.isConnected)target.disabled=false;}
    }
    else if(action==='new-project'){await tenderFlow.beforeLeave();if(state.dirty)await saveProject(false);showNewProject();}
    else if(action==='credit-check')await runCreditCheck();
    else if(action==='credit-archive'){if(state.data.credit_automation?.enabled===true&&state.data.credit_automation?.status==='verified')await runCreditCheck();}
    else if(action==='credit-retry-status'){creditUi.error='';await pollCreditStatus();}
    else if(action==='new-material')showMaterialForm(null,target.dataset.category);
    else if(action==='edit-material')showMaterialForm(id);
    else if(action==='focus-material-field'){
      const field=target.closest('form')?.elements.namedItem(id);
      field?.scrollIntoView({block:'center'});field?.focus();
    }
    else if(action==='open-material')await openDrawer(id,target.dataset.index);
    else if(action.startsWith('special-')){
      if(action==='special-industry'){specialState.industry=id;specialState.category='';specialState.page=1;}
      if(action==='special-category'){specialState.category=id==='all'?'':id;specialState.page=1;}
      if(action==='special-prev')specialState.page--;
      if(action==='special-next')specialState.page++;
      if(action==='special-restore-files'){const m=material(id);await api('/api/materials/'+encodeURIComponent(id),{method:'PATCH',body:{restore_file_ids:m.removed_file_ids,revision:m.revision}});await refreshData();renderDrawer();}
      await loadSpecial();render();
    }
    else if(action==='new-category')showCategoryForm();
    else if(action==='edit-category')showCategoryForm(id);
    else if(action==='new-industry')showIndustryForm();
    else if(action==='close-modal')closeModal();
    else if(action==='close-drawer')closeDrawer();
    else if(action==='append-files')showAppendFiles(id);
    else if(action==='remove-file')showRemoveFile();
    else if(action==='batch-remove-files')showBatchRemoveFiles();
    else if(action==='filter-category'){state.category=id;render();}
    else if(action==='open-project'){target.setAttribute('aria-busy','true');await openProject(id);}
    else if(action==='preview-location')await openLocationPreview(id);
    else if(action==='location-retry')await openLocationPreview(locationUi.mappingId);
    else if(action==='location-prev'||action==='location-next'){locationUi.page+=action==='location-prev'?-1:1;renderLocationPreview();}
    else if(action==='location-target'){locationUi.page=locationUi.preserveOriginal?(locationUi.result.target_pages[0]||locationUi.result.initial_page):locationUi.result.initial_page;renderLocationPreview();}
    else if(action==='location-zoom-in'||action==='location-zoom-out'){locationUi.zoom=Math.max(.75,Math.min(2,locationUi.zoom+(action==='location-zoom-in'?.25:-.25)));renderLocationPreview();}
    else if(action==='location-toggle'){locationUi.showMarkers=!locationUi.showMarkers;renderLocationPreview();}
    else if(action==='project-step')await projectStep(id);
    else if(action==='locate-material-issue')focusMaterialCompletionIssue(Number(id));
    else if(action==='project-picker-prev'||action==='project-picker-next'){
      const category=target.dataset.category,picker=projectUi.pickers[category];
      await loadProjectPicker(category,picker.page+(action==='project-picker-next'?1:-1));render();
    }
    else if(action==='project-materials')await navigate('materials',target.dataset.category);
    else if(action==='save-project'){target.disabled=true;await saveProject();}

    else if(action==='rescan')openModal('重新扫描模板','将按当前栏目与材料重新匹配。',`<div class="warning-box" style="margin-bottom:0">重新扫描会清除当前项目的人工确认与匹配调整。导出历史仍会保留，原始模板不会修改。</div>`,'rescan','确认重新扫描');
    else if(action==='export-project')await exportProject(target);
    else if(action==='preview-prev'){state.pageIndex--;renderDrawer();}
    else if(action==='preview-next'){state.pageIndex++;renderDrawer();}
    else if(action==='preview-page'){state.pageIndex=Number(target.dataset.index);renderDrawer();}
    else if(['rotate-file','move-file-up','move-file-down'].includes(action))await changeFile(action);
    else if(action==='retry-load')await init();
  } catch(err){toast(err.message,true);if(target.isConnected){target.disabled=false;target.removeAttribute('aria-busy');}}
});
document.addEventListener('submit',e=>{const form=e.target.closest('form[data-form]');if(form){e.preventDefault();submitForm(form);}});
document.addEventListener('change',async e=>{
  const el=e.target;
  if(el.id==='location-locator'){
    const mapping=state.project?.mappings.find(m=>m.id===locationUi.mappingId);
    if(!mapping)return;
    if(mapping.locator_id!==el.value){
      mappingChange(mapping.id,'locator_id',el.value);
      dialogReturnFocus=$$('[data-action="preview-location"]').find(b=>b.dataset.id===mapping.id);
    }
    await loadLocationPreview($('.location-modal'));return;
  }
  if(el.id==='location-page-number'){const page=Number(el.value);if(Number.isInteger(page)&&page>=1&&page<=locationUi.result.page_count){locationUi.page=page;renderLocationPreview();}else{el.value=locationUi.page;toast('请输入有效的预览页码。',true);}return;}
  if(el.id==='batch-select-all'){$$('form[data-form="remove-files"] input[name="file_ids"]').forEach(x=>x.checked=el.checked);updateBatchSelection();}
  if(el.name==='file_ids')updateBatchSelection();
  if(el.name==='source')$('#template-upload-area')?.classList.toggle('hidden',el.value==='sample');
  if(el.id==='material-category')$('#material-years-hint').hidden=el.value!=='audit_report';
  if(el.closest('form[data-form="material"]'))queueMaterialValidation(el.closest('form'));
  if(el.name==='mode'){$('#material-text-area')?.classList.toggle('hidden',el.value!=='text');$('#material-upload-area')?.classList.toggle('hidden',el.value==='text');}
  if(el.dataset.mapField)mappingChange(el.dataset.mapId,el.dataset.mapField,el.type==='checkbox'?el.checked:el.value,el.dataset.mapField!=='note');
  if(el.dataset.mapMaterial){
    const m=state.project.mappings.find(x=>x.id===el.dataset.mapMaterial);if(!m)return;
    const id=el.value,checked=el.checked;el.disabled=true;
    try{
      if(checked&&isSpecial(m.category_id))specialState.cache.set(id,await api('/api/materials/'+encodeURIComponent(id)));
      const ids=new Set(m.material_ids||[]);if(checked)ids.add(id);else ids.delete(id);
      if(checked)m.action='fill';mappingChange(m.id,'material_ids',[...ids]);
    }catch(err){el.checked=!checked;el.disabled=false;toast(err.message,true);}
  }
  if(el.dataset.categoryToggle){if(isCreditCategory(el.dataset.categoryToggle)){el.checked=!!state.data.categories.find(c=>c.id===el.dataset.categoryToggle)?.enabled;el.disabled=true;return;}el.disabled=true;try{await api(`/api/categories/${encodeURIComponent(el.dataset.categoryToggle)}`,{method:'PATCH',body:{enabled:el.checked}});await refreshData();render();toast('栏目状态已更新；已有项目可重新扫描以应用。');}catch(err){el.checked=!el.checked;el.disabled=false;toast(err.message,true);}}
});
let searchTimer;
document.addEventListener('input',e=>{
  const el=e.target;
  if(el.dataset.projectSearch){
    clearTimeout(searchTimer);const category=el.dataset.projectSearch,query=el.value,position=el.selectionStart,projectId=state.project.id;
    searchTimer=setTimeout(async()=>{try{
      await loadProjectPicker(category,1,query);if(state.project?.id!==projectId)return;
      render();const next=$$('input[data-project-search]').find(x=>x.dataset.projectSearch===category);next?.focus();try{next?.setSelectionRange(position,position);}catch{}
    }catch(err){toast(err.message,true);}},250);
  }
  if(el.closest('form[data-form="material"]'))queueMaterialValidation(el.closest('form'));
  if(el.id==='special-search'){clearTimeout(searchTimer);const position=el.selectionStart;specialState.query=el.value;specialState.page=1;searchTimer=setTimeout(async()=>{try{await loadSpecial();render();const next=$('#special-search');next?.focus();try{next?.setSelectionRange(position,position);}catch{}}catch(error){toast(error.message,true);}},250);}
  if(el.id==='material-search'||el.id==='project-search'){
    clearTimeout(searchTimer);const position=el.selectionStart;state.query=el.value;searchTimer=setTimeout(()=>{render();const next=$('#'+el.id);if(next){next.focus();try{next.setSelectionRange(position,position);}catch{}}},180);
  }
  if(el.dataset.mapField==='note'){
    mappingChange(el.dataset.mapId,'note',el.value,false);
    const card=el.closest('.mapping-card'),confirm=$('input[data-map-field=confirmed]',card);if(confirm)confirm.checked=false;$('.status.confirmed',card)?.remove();$('.confirm-label',card)?.classList.remove('checked');
    const existing=$('.sticky-save');if(!existing){const node=document.createElement('div');node.className='sticky-save';node.innerHTML=`<div><p>有未保存修改，请先保存</p></div>${button('保存核对','save-project',{primary:true,icon:'check'})}`;$('.mapping-list').after(node);}
    $$('[data-action=export-project],[data-action=confirm-ready]').forEach(b=>b.disabled=true);
  }
});
document.addEventListener('load',e=>{
  if(e.target.matches?.('.location-page-image')){const viewport=e.target.closest('.location-viewport'),marker=locationUi.result?.markers.find(m=>m.page===locationUi.page);if(viewport)viewport.scrollTop=marker?Math.max(0,marker.y*e.target.clientHeight-viewport.clientHeight*.4):0;}
},true);
document.addEventListener('error',e=>{
  if(e.target.matches?.('.location-page-image')){e.target.closest('.location-paper').hidden=true;$('.location-image-error')?.removeAttribute('hidden');}
  if(e.target.matches?.('.mapping-preview-image img')){e.target.hidden=true;e.target.nextElementSibling.hidden=false;}
},true);
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&!$('#active-modal')&&state.drawerId){e.preventDefault();closeDrawer();}
  if(e.key==='Tab'&&state.drawerId&&!$('#active-modal')){
    const nodes=$$('.drawer button:not(:disabled),.drawer a[href],.drawer input,.drawer textarea,.drawer select');const first=nodes[0],last=nodes[nodes.length-1];
    if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
  }
});
window.addEventListener('beforeunload',e=>{if(state.dirty){e.preventDefault();e.returnValue='';}});
async function init(){try{await refreshData();await tenderFlow.enter({fromUrl:true});pollExportTasks();}catch(e){$('#app').innerHTML=`<div class="initial-loading">${icon('alert')}<h2>暂时无法连接本地工作空间</h2><p class="muted small">${esc(e.message)}</p>${button('重试','retry-load',{primary:true,icon:'refresh'})}</div>`;}}
init();
