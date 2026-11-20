'use strict';

// The preparation workspace uses the same project and matching controls as the material library.
const tenderFlow = (() => {
  // Keep stored step IDs and old links compatible while combining setup with upload.
  const steps = ['招标文件解读', '目录编写', '系统填充'];
  const writingStep = '编写投标文件';
  const categories = [
    ['redline','risk','废标红线条款','条'], ['qualification','qualification','强制资格要求','项'],
    ['score','score','评分细则要点','条'], ['spec','spec','商务技术规范','项'],
    ['format','format','指定格式','项']
  ];
  const fresh = () => ({
    step:1, name:'', record:null, library:[], archive:null, busy:'', error:'',
    editor:null, chapter:'', edits:new Map(), writing:null, writingError:'', writingModel:'', businessNotes:'', businessNotesDirty:false, requirements:[], requirementKey:'', requirementCategory:'redline',
    requirementEditing:false, outlineEditing:false, requirementsDirty:false, outlineDraft:[], outlineKey:'', outlineDirty:false, previewLoaded:false, sourceQuery:'', draftSequence:0,
    notesAcknowledged:false, removedAcknowledged:new Set(), redline:null, redlineError:'', redlineAiBusy:false, redlineAiOutboundAck:false, redlinePendingOnly:false,
    librarySelection:null, libraryOpen:false, viewingEditionId:'', generationNoticeId:'',
    outlineText:'', formAssignments:{}, outlineSelection:new Set(), automatic:null,
    calibrationOpen:false, calibration:null, calibrationRow:'', saveCase:false, caseName:''
  });
  let ui = fresh();
  let writingTimer=null, writingEpoch=0, writingRequest=0, redlineRequest=0, redlineLoading=false, redlineRefreshPending=false, outlineTimer=null;
  const base = id => '/api/frameworks/' + encodeURIComponent(id);
  const projectUrl = id => '/api/projects/' + encodeURIComponent(id);
  const draftsUrl = id => projectUrl(id) + '/chapter-drafts';
  const writingUrl = id => projectUrl(id) + '/writing';
  const redlineUrl = id => projectUrl(id) + '/redline-scan';
  const files = () => ui.record?.artifacts || [];
  const html = () => files().find(f=>f.kind==='html');
  const pendingNotes = () => [...new Set(ui.record?.notes||[])].filter(note=>typeof note==='string'&&note.trim());
  const removedColumnRequirements = () => (ui.record?.requirements||[]).filter(row=>['qualification','format'].includes(row.category)&&!ui.requirements.some(item=>item.id&&item.id===row.id));
  function wfButton(label, action, options={}) {
    return `<button type="button" class="wf-button ${options.primary?'primary':''} ${options.subtle?'subtle':''}" data-wf="${esc(action)}" ${options.id!==undefined?`data-id="${esc(options.id)}"`:''} ${options.disabled||ui.busy?'disabled':''}>${esc(label)}</button>`;
  }
  function fileLink(label, url, external=false) {
    return `<a class="wf-button subtle" href="${esc(url)}" ${external?'target="_blank" rel="noopener"':'download'}>${esc(label)}</a>`;
  }
  function title(heading, lead='') {
    return `<header class="wf-heading"><h2 class="wf-title">${esc(heading)}</h2>${lead?`<p class="wf-lead">${esc(lead)}</p>`:''}</header>`;
  }
  function actions(previous, next, label, enabled=true) {
    return `<div class="wf-actions">${previous?wfButton('上一步','step',{id:previous}):''}${wfButton(label||'下一步',next,{primary:true,disabled:!enabled})}</div>`;
  }
  function hasProject() {
    return Boolean(state.project && ui.record && state.project.framework_id===ui.record.id && state.project.framework_artifact_id==='word' && state.project.id===ui.record.projects?.word);
  }
  function available(n) {
    if (n<=2) return true;
    if (n===3) return !!ui.record && ui.record.requirements_confirmed!==false && !ui.requirementsDirty;
    if (n===4) return hasProject() && ui.record.requirements_confirmed!==false && !ui.requirementsDirty && !ui.outlineDirty;
    return hasProject() && ui.record.requirements_confirmed!==false && !ui.requirementsDirty && !ui.outlineDirty && projectReviewed();
  }
  // Earliest unfinished gate before entering step n; clicking ahead jumps here.
  function earliestBlocker(n) {
    if (n<=2) return null;
    if (!ui.record) {
      return {step:2, message:'请先上传并解析招标文件。', selector:'[data-wf="upload"]'};
    }
    if (ui.requirementsDirty || ui.record.requirements_confirmed===false) {
      return {
        step:2,
        message:ui.requirementsDirty?'采购要求有未保存修改，请先保存并确认要求。':'请先核对并确认采购要求。',
        selector:ui.requirementsDirty?'[data-wf="save-requirements"]':'[data-wf="confirm-requirements"]',
      };
    }
    if (n<=3) return null;
    if (ui.outlineDirty) {
      return {
        step:3, outlineEditing:true,
        message:'目录有未保存修改，请先保存目录并核对预览。',
        selector:'[data-wf="save-outline"]',
      };
    }
    if (!hasProject()) {
      return {
        step:3,
        message:'请先核对目录，并点击“确认目录，进入系统填充”。',
        selector:'[data-wf="confirm"]',
      };
    }
    if (n<=4||projectReviewed()) return null;
    return {material:true};
  }
  function resolveStep(n) {
    const blocker=earliestBlocker(n);
    if (!blocker) return n;
    if (blocker.material) return 4;
    return blocker.step;
  }
  function stepper() {
    const current=Math.max(2,ui.step), exportReady=hasProject()&&projectReviewed();
    return `<nav class="wf-stepper wf-stepper-fork" aria-label="投标文件编制步骤">${steps.map((label,i)=>{
      const n=i+2;
      // Keep later steps clickable: setStep sends the user back to the unfinished gate.
      return `<button type="button" data-wf="step" data-id="${n}" class="wf-step ${n===current?'is-current':available(n)&&n<current?'is-done':''}" ${n===current?'aria-current="step"':''} ${ui.busy?'disabled':''}><span class="wf-number">${i+1}</span><span class="wf-label">${label}</span></button>`;
    }).join('')}<div class="wf-fork ${ui.step===5?'is-b-current':''}" role="group" aria-label="系统填充后的两条路径">
      <button type="button" data-wf="export" class="wf-step wf-branch wf-branch-a ${exportReady?'is-available':''}" ${!exportReady||ui.busy?'disabled':''} title="${exportReady?'直接导出半成品标书':'完成文字和图片材料核对并保存后可导出半成品标书'}"><span class="wf-number">A</span><span class="wf-label">导出半成品标书</span></button>
      <button type="button" data-wf="step" data-id="5" class="wf-step wf-branch wf-branch-b ${ui.step===5?'is-current':''}" ${ui.step===5?'aria-current="step"':''} ${ui.busy?'disabled':''}><span class="wf-number">B</span><span class="wf-label">${writingStep}</span></button>
    </div></nav>`;
  }
  function settings() {
    const history = ui.library.filter(r=>r.id!==ui.record?.id);
    const templates=(state.data.projects||[]).filter(p=>!p.framework_id);
    const entries=[
      ...history.map(r=>`<li><div><strong>${esc(r.name)}</strong><small class="wf-file-meta">${esc(r.filename)} · ${date(r.created_at)}</small></div>${wfButton('继续编制','resume',{id:r.id,subtle:true})}</li>`),
      ...templates.map(p=>`<li><div><strong>${esc(p.name)}</strong><small class="wf-file-meta">${esc(p.template_name||'已有模板')} · ${date(p.updated_at||p.created_at)}</small></div><button type="button" class="wf-button subtle" data-action="open-project" data-id="${esc(p.id)}">继续编制</button></li>`)
    ];
    return analysis()+`<div class="wf-file-actions wf-template-entry"><button type="button" class="text-button" data-action="new-project">已有投标模板，直接填充材料</button></div>
      ${entries.length?`<section class="wf-section wf-history"><h2 class="wf-section-title">继续已有项目</h2><ul class="wf-list">${entries.slice(0,5).join('')}</ul>${entries.length>5?`<details class="wf-note"><summary>其余 ${entries.length-5} 个项目</summary><ul class="wf-list">${entries.slice(5).join('')}</ul></details>`:''}</section>`:''}`;
  }
  function analysis() {
    const r=ui.record, preview=html();
    const drop = `<input id="wf-upload" type="file" hidden accept=".docx,.doc">
      <button type="button" data-wf="upload" class="wf-dropzone " ${ui.busy?'disabled':''} aria-label="选择招标文件上传"><span class="wf-drop-icon">${icon('upload')}</span><strong class="wf-drop-title">${ui.busy||'点击或拖拽上传招标文件'}</strong><small class="wf-drop-help">支持 DOC、DOCX，单文件 ≤ 50 MB</small></button>`;
    const list = r ? `<div class="wf-file"><div class="wf-file-info"><strong class="wf-file-name">${esc(r.filename)}</strong>${r.created_at?`<details class="wf-file-meta"><summary>文件详情</summary><span>上传时间　${date(r.created_at,true)}</span></details>`:''}</div><span class="wf-status">解析完成</span><div class="wf-file-actions">${preview?fileLink('预览',preview.url,true):''}${Object.keys(r.projects||{}).length||r.project_id?fileLink('原件',base(r.id)+'/source'):wfButton('删除','remove',{subtle:true})}</div></div>`
      : `<div class="wf-empty">${ui.busy?'正在读取采购文件，完成后可核对识别结果。':'暂无文件。上传后可预览目录，回查每项要求的采购原文。'}</div>`;
    if(!r)return title('上传招标文件')+`<label class="wf-field" for="wf-name">项目名称（选填）<input class="input" id="wf-name" maxlength="150" value="${esc(ui.name)}" placeholder="留空时使用文件名"></label>`+drop;
    // Step 1 only surfaces redlines; other categories stay in the saved payload for outline.
    const visibleCategories=categories.filter(([key])=>key==='redline');
    const cards = visibleCategories.map(([key,kind,label,unit])=>{
      const metric=r?.metrics?.find(m=>m.key===key), number=r?.review_editable?ui.requirements.filter(item=>item.category===key).length:metric?.count;
      return `<button type="button" class="wf-metric" data-wf="metric" data-id="${key}" data-kind="${kind}" ${!r||!metric?'disabled':''} aria-pressed="${ui.requirementCategory===key}" aria-label="${label}，${metric?number+unit+'，核对条款':'待解析'}"><span class="wf-metric-label">${label}</span><strong class="wf-metric-count">${metric?esc(number)+`<small>${unit}</small>`:'—'}</strong></button>`;
    }).join('');
    return title('招标文件解读')+
      (r?'':drop)+`<section class="wf-files">${list}</section>
      <div class="wf-metrics" aria-label="废标红线条款">${cards}</div>`+coverage()+requirementReview()+confirmationChecklist()+downloads()+
      (needsRequirementsConfirm()?actions(0,'confirm-requirements',r.outline_mode?'保存并确认要求，进入目录':'保存并确认要求，生成目录',!!r):actions(0,'to-outline','下一步',!!r));
  }
  function downloads() {
    const word=files().find(f=>f.kind==='word'), preview=html();
    if(!word||!preview)return '';
    return `<details class="wf-downloads"><summary>下载编制文件</summary><div class="wf-file-actions">${fileLink('整本 Word 框架',word.url)}${fileLink('目录与要求 HTML',preview.url+'?download=1')}</div></details>`;
  }
  function coverage() {
    const notes=pendingNotes();
    if(!notes.length)return '';
    const open=needsRequirementsConfirm()||!ui.notesAcknowledged;
    return `<details class="wf-note" ${open?'open':''}><summary>待核对事项（${notes.length}）</summary><ul>${notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>
      <p class="wf-help">确认要求前须在下方“确认前防漏核对”中勾选已逐条核对这些事项。</p></details>`;
  }
  function confirmationChecklist() {
    if(!needsRequirementsConfirm())return '';
    const notes=pendingNotes(), removed=removedColumnRequirements();
    if(!removed.length&&!notes.length)return '';
    return `<section class="wf-section wf-confirm-gate" aria-label="确认前核对"><h2 class="wf-section-title">确认前防漏核对</h2>
      <p class="wf-help">界面只审废标红线；资格与格式等仍自动生成目录栏目。待核对事项未勾选可能漏掉材料位置${removed.length?'；若曾删除资格/格式要求须逐项确认，否则会去掉对应响应栏目':''}。</p>
      ${notes.length?`<label class="wf-ack"><input type="checkbox" id="wf-notes-ack" ${ui.notesAcknowledged?'checked':''}>我已逐条核对待核对事项（${notes.length}），确认未遗漏必交材料与响应栏目</label>`:''}
      ${removed.length?`<div class="wf-removed-list"><strong>已删除的资格/格式要求（${removed.length}）</strong>${removed.map(row=>`<label class="wf-ack"><input type="checkbox" data-wf="ack-removed" data-id="${esc(row.id)}" ${ui.removedAcknowledged.has(row.id)?'checked':''}>确认删除：${esc(row.label||row.text||row.id)}</label>`).join('')}</div>`:''}
    </section>`;
  }
  const headingPrefix=/^(?:第\s*[一二三四五六七八九十百零〇两\d]{1,5}\s*[章节部分篇卷册]\s*|[（(]\s*[一二三四五六七八九十百零〇两\d]{1,4}\s*[）)]\s*|[一二三四五六七八九十百零〇两]{1,4}\s*[、.．]\s*|[①-⑳]\s*|\d{1,3}(?:[.．]\d{1,3})*(?:[.．、]\s*|\s+)(?![\d\s.．%％万元亿]|年(?!度|报)|月(?!度|报)|日(?!期|常|报)))/;
  function headingTitle(value) {
    let title=String(value||'').trim();
    while(headingPrefix.test(title)){
      const rest=title.replace(headingPrefix,'').trim();
      if(!rest)break;
      title=rest;
    }
    return title;
  }
  function hierarchy(rows) {
    const parents=[], counts=[];
    return rows.map(row=>{
      const level=Math.max(1,Number(row.level)||1);
      while(parents.length && parents[parents.length-1].level>=level)parents.pop();
      const depth=parents.length;
      counts.length=depth+1;counts[depth]=(counts[depth]||0)+1;
      const title=headingTitle(row.title??row.text);
      parents.push({level,title});
      return {...row,depth,title,number:counts.join('.')+(depth===0?'.':''),path:parents.map(p=>p.title).join(' / ')};
    });
  }
  function archiveLinks(history=ui.record?.legacy_projects||[]) {
    if(!history.length)return '';
    return `<details class="wf-note"><summary>历史工作稿（${history.length} 份）</summary><ul class="wf-list">${history.map(p=>`<li><div><strong>${esc(p.name)}</strong>${p.pending_chapters?`<small class="wf-file-meta">有正文需对照整本目录人工核对</small>`:''}</div>${wfButton('查看历史工作稿','archive',{id:p.id,subtle:true})}</li>`).join('')}</ul></details>`;
  }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function editable() { return ui.record?.review_editable===true; }
  // Confirm again only when still pending or local edits must be republished.
  function needsRequirementsConfirm() {
    return editable() && !!ui.record && (!ui.record.requirements_confirmed || ui.requirementsDirty);
  }
  function newKey() { return 'new-'+(++ui.draftSequence); }
  function requirement() { return ui.requirements.find(item=>item._key===ui.requirementKey); }
  function redlineListTitle(item) {
    // Prefer operative text; short labels like「否决响应」are not actionable for reviewers.
    const text=String(item?.text||'').replace(/\s+/g,' ').trim();
    const label=String(item?.label||'').replace(/\s+/g,' ').trim();
    const weak=/^(否决响应|否决投标|废标|无效(投标|响应|标书)?|(废标|否决)(情形|情况|条件|条款))?$/.test(label);
    const title=(!weak&&label&&(!text||label.length>=Math.min(24,text.length)))?label:text||label||'新增红线';
    return title.length>90?title.slice(0,90)+'…':title;
  }
  function sourceLabel(source) {
    return [source.part,source.locator,(source.text||'').replace(/\s+/g,' ').slice(0,80)].filter(Boolean).join(' · ');
  }
  function sourceMatches(source) {
    const query=ui.sourceQuery.trim().toLocaleLowerCase();
    return !query||[source.part,source.locator,source.context,source.text].filter(Boolean).join(' ').toLocaleLowerCase().includes(query);
  }
  function requirementReview() {
    if(!ui.record)return '';
    if(!editable())return '<p class="wf-note">历史版本只读；重新上传可编辑。</p>';
    ui.requirementCategory='redline';
    const rows=ui.requirements.filter(item=>item.category==='redline'), selected=requirement();
    const label='废标红线条款';
    const references=selected?.references||[];
    const matchingSources=(ui.record.sources||[]).filter(sourceMatches), sources=matchingSources.slice(0,100);
    return `<section class="wf-section wf-review" aria-label="废标红线核对"><div class="wf-review-heading"><h2 class="wf-section-title">${esc(label)} · ${rows.length}</h2><div class="wf-file-actions">${wfButton('新增红线','add-requirement',{subtle:true})}${wfButton('保存修改','save-requirements',{subtle:true,disabled:!ui.requirementsDirty})}${ui.error?wfButton('重新读取','reload-review',{subtle:true}):''}</div></div>
      <p class="wf-help" id="wf-requirements-status" role="status">${ui.requirementsDirty?'有未保存修改；保存后须重新确认':ui.record.requirements_confirmed?'红线已确认':'红线待确认'}。资格、格式等仍自动参与目录生成，本页不展示。</p>
      <div class="wf-review-grid"><div class="wf-requirement-list" aria-label="红线列表">${rows.map((item,i)=>`<button type="button" class="wf-requirement-choice ${item._key===ui.requirementKey?'is-selected':''}" data-wf="select-requirement" data-id="${esc(item._key)}" aria-pressed="${item._key===ui.requirementKey}"><span>${i+1}</span><span>${esc(redlineListTitle(item))}</span></button>`).join('')||'<p class="wf-empty">未识别到废标红线</p>'}</div>
      <div class="wf-requirement-editor">${selected?`<div class="wf-requirement-reading"><h3>${esc(redlineListTitle(selected))}</h3><p>${esc(selected.text||'请填写要求内容')}</p>${selected.scope?`<p class="wf-help">适用范围：${esc(selected.scope)}</p>`:''}${selected.note?`<p class="wf-help">备注：${esc(selected.note)}</p>`:''}</div>
      <details class="wf-source-evidence" open><summary>采购原文依据（${references.length} 处）</summary>${references.map(ref=>`<div class="wf-source-reference"><p class="wf-help">${esc([ref.part,ref.context,ref.locator].filter(Boolean).join(' · '))}</p><blockquote>${esc(ref.original_text||ref.text||'')}</blockquote></div>`).join('')||'<p class="wf-note">新增要求须选择采购原文出处。</p>'}</details>
      <details class="wf-requirement-edit" ${ui.requirementEditing?'open':''}><summary>修改解读</summary><div class="wf-review-fields"><input type="hidden" id="wf-requirement-category" value="redline"><label class="wf-field">适用范围<input class="input" id="wf-requirement-scope" maxlength="200" value="${esc(selected.scope||'')}" placeholder="例如：包件二；共同适用"></label></div>
      <label class="wf-field">要求名称<input class="input" id="wf-requirement-label" maxlength="300" value="${esc(selected.label||'')}"></label>
      <label class="wf-field">要求内容<textarea class="input" id="wf-requirement-text" rows="5" maxlength="30000">${esc(selected.text||'')}</textarea></label>
      <label class="wf-field">核对备注<input class="input" id="wf-requirement-note" maxlength="2000" value="${esc(selected.note||'')}" placeholder="记录解释、待补附件或核对结论"></label>
      ${references.length>1?`<div class="wf-source-reference-tools">${references.map((ref,i)=>`<p>${esc([ref.part,ref.locator].filter(Boolean).join(' · '))} ${wfButton('移除此出处','remove-reference',{id:i,subtle:true})}</p>`).join('')}</div>`:''}
      <details class="wf-source-picker" ${references.length?'':'open'}><summary>从采购原文添加出处</summary><label class="wf-field">搜索原文<input class="input" id="wf-source-query" value="${esc(ui.sourceQuery)}" placeholder="输入关键词筛选"></label><label class="wf-field">选择出处<select class="input" id="wf-requirement-source"><option value="">请选择原文位置</option>${sources.map(source=>`<option value="${esc(source.id)}">${esc(sourceLabel(source))}</option>`).join('')}</select></label><p class="wf-help" id="wf-source-count" ${matchingSources.length>sources.length?'':'hidden'}>${matchingSources.length>sources.length?`共 ${matchingSources.length} 条匹配原文，仅显示前 ${sources.length} 条。`:''}</p>${wfButton('添加选中出处','add-reference',{subtle:true})}</details>
      <div class="wf-file-actions wf-review-delete">${wfButton('删除本条要求','delete-requirement',{id:selected._key,subtle:true})}</div></details>`:''}</div></div></section>`;
  }
  function treeEnd(index) {
    let end=index+1;
    while(end<ui.outlineDraft.length&&ui.outlineDraft[end].level>ui.outlineDraft[index].level)end++;
    return end;
  }
  function previousSibling(index) {
    const level=ui.outlineDraft[index]?.level;
    for(let i=index-1;i>=0;i--){if(ui.outlineDraft[i].level<level)return -1;if(ui.outlineDraft[i].level===level)return i;}
    return -1;
  }
  function outlineEditor() {
    if(!editable())return '';
    const index=ui.outlineDraft.findIndex(row=>row._key===ui.outlineKey), selected=ui.outlineDraft[index];
    const end=index>=0?treeEnd(index):0, previous=index>=0?previousSibling(index):-1;
    const next=end<ui.outlineDraft.length&&ui.outlineDraft[end].level===selected?.level;
    const containsForms=index>=0&&ui.outlineDraft.slice(index,end).some(row=>row.locked_format);
    return `<details class="wf-outline-editor" ${ui.outlineEditing?'open':''}><summary>修改目录 · ${ui.outlineDraft.length} 个章节</summary>
      <div class="wf-outline-grid"><div class="wf-outline-list" aria-label="可编辑目录">${hierarchy(ui.outlineDraft).map(row=>`<div class="wf-outline-row">${ui.record.outline_mode?`<input type="checkbox" aria-label="选择 ${esc(row.text)}" data-outline-select="${esc(row._key)}" ${ui.outlineSelection.has(row._key)?'checked':''}>`:''}<button type="button" class="wf-outline-choice ${row._key===ui.outlineKey?'is-selected':''}" style="--depth:${Math.min(8,row.level-1)}" data-wf="select-outline" data-id="${esc(row._key)}" aria-pressed="${row._key===ui.outlineKey}"><span class="wf-outline-number">${esc(row.number)}</span><span>${esc(row.title||'新增章节')}</span></button></div>`).join('')||'<p class="wf-empty">尚无目录，可新增章节。</p>'}</div>
      <div class="wf-outline-properties">${selected?`<label class="wf-field">章节名称<input class="input" id="wf-outline-title" maxlength="${ui.record.outline_mode?220:150}" value="${esc(headingTitle(selected.text))}"></label><p class="wf-help">第 ${selected.level} 级${end>index+1?'；调整会同时移动下属章节。':''}${selected.locked_format?'<span class="wf-status">采购方指定格式</span>':''}</p>${containsForms?'<p class="wf-help">采购方指定的原表随所属章节移动。</p>':''}<div class="wf-file-actions">${wfButton('上移','outline-up',{disabled:previous<0,subtle:true})}${wfButton('下移','outline-down',{disabled:!next,subtle:true})}${wfButton('升一级','outline-outdent',{disabled:selected.level<=1,subtle:true})}${wfButton('降一级','outline-indent',{disabled:previous<0||Math.max(...ui.outlineDraft.slice(index,end).map(row=>row.level))>=9,subtle:true})}</div>
`:''}
      <div class="wf-file-actions">${wfButton(selected?'新增同级':'新增章节','outline-add-sibling',{subtle:true})}${selected?wfButton('新增子级','outline-add-child',{subtle:true,disabled:selected.level>=9})+wfButton('删除章节','outline-delete',{subtle:true}):''}${ui.record.outline_mode?wfButton('删除所选章节','delete-outline-selection',{subtle:true,disabled:!ui.outlineSelection.size}):''}</div>
      ${ui.record.outline_mode?`<details class="wf-note"><summary>批量新增章节</summary><label class="wf-field">每行一个章节<textarea id="wf-outline-batch" class="input" rows="4"></textarea></label><div class="wf-file-actions">${wfButton('批量新增同级','add-outline-batch',{subtle:true})}${selected&&selected.level<9?wfButton('批量新增子级','add-outline-batch',{id:'child',subtle:true}):''}</div></details>`:''}</div></div>
      <div class="wf-review-heading wf-outline-save"><p class="wf-help" id="wf-outline-status" role="status" ${ui.outlineDirty?'':'hidden'}>${ui.outlineDirty?'有未保存修改；下方预览仍为已保存版本':''}</p><div class="wf-file-actions">${ui.error?wfButton('重新读取','reload-review',{subtle:true}):''}${wfButton('保存目录并更新预览','save-outline',{primary:true,disabled:!ui.outlineDirty})}</div></div></details>`;
  }
  function outline() {
    const preview=html(), word=files().find(f=>f.kind==='word');
    if(ui.record.outline_mode==='pending')return title('选择目录编排方式')+outlineSources();
    if(!preview||!word)return `<p class="wf-error" role="alert">${esc(ui.record.migration_error||'无法读取整本预览。请返回招标文件解读，核对采购原件。')}</p>`;
    const url=preview.url+(preview.url.includes('?')?'&':'?')+'revision='+encodeURIComponent(ui.record.revision||0);
    const books=ui.record.books||[];
    const summary=books.length?`<section class="wf-section" aria-label="分册目录"><h2 class="wf-section-title">分册目录</h2><div class="wf-file-actions">${books.map(book=>wfButton(book.name+'目录 · '+book.chapters.length+' 个章节','select-outline',{id:book.id,subtle:true})).join('')}</div></section>`:'';
    return summary+outlineForms()+outlineEditor()+(editable()?`<details class="wf-note"><summary>更换目录来源</summary>${outlineSources()}</details>`:'')+`<iframe class="wf-preview-frame" title="投标文件目录预览" src="${esc(url)}" sandbox="allow-same-origin"></iframe>
      <p class="wf-help wf-preview-state" role="status">正在读取目录预览…</p>
      <div class="wf-actions">${wfButton('上一步','step',{id:2})}${wfButton('确认目录，进入系统填充','confirm',{primary:true,disabled:!ui.previewLoaded||ui.outlineDirty||ui.record.requirements_confirmed===false||outlineMissingBlocked()})}</div>`;
  }
  function filling() {
    return title('系统填充')+(!isRemote()?`<div class="wf-stage-tools">${wfButton('查看阶段预览','stage-preview',{subtle:true})} ${wfButton('去核对红线与导出','step',{id:5,subtle:true})}</div>`:'')+renderProject(true);
  }
  function automaticControls() {
    const job=ui.automatic||{}, running=job.status==='running';
    return `${wfButton(running?'Kimi 正在识别…':job.status==='failed'?'重试 AI 目录':'AI 自动生成目录','generate-outline',{disabled:running,primary:!job.items?.length})}
      ${job.items?.length?wfButton('使用 AI 目录（'+job.items.length+' 个章节）','use-ai-outline',{primary:true}):''}
      ${job.error?`<p class="wf-help" role="status">${esc(job.error)}</p>`:''}`;
  }
  function outlineSources() {
    return `<div class="wf-source-grid"><section class="wf-section">
      <h3 class="wf-section-title">AI 自动生成</h3><p class="wf-help">Kimi 将读取采购原文中的投标文件格式和相关要求。</p>
      <div class="wf-file-actions" id="wf-automatic-outline">${automaticControls()}</div></section>
      <section class="wf-section"><h3 class="wf-section-title">使用自定义目录</h3>
      <p class="wf-help">导入或粘贴后可直接核对整本预览。系统会使用采购相关文字与目录在后台生成校准建议。</p>
      <label class="wf-field">粘贴目录<textarea id="wf-outline-text" class="input" rows="5" maxlength="200000" placeholder="每行一个章节，可用缩进或层级编号">${esc(ui.outlineText)}</textarea></label>
      <input id="wf-outline-upload" type="file" accept=".docx" hidden>
      <div class="wf-file-actions">${wfButton('使用粘贴目录','use-pasted-outline',{primary:true})}${wfButton('导入 DOCX','pick-outline',{subtle:true})}</div>
      </section></div>`;
  }
  function outlineMissingBlocked() {
    return ui.record?.outline_mode!=='custom'&&!!ui.record?.outline_missing?.length;
  }
  function outlineForms() {
    const missing=ui.record.outline_missing||[];
    if(!missing.length)return '';
    if(ui.record.outline_mode==='custom'){
      // 自定义目录以上传内容为准：只提示未收录的采购原表，不再要求逐项关联或恢复。
      return `<section class="wf-section wf-outline-forms" aria-label="采购原表提示"><h3 class="wf-section-title">采购原表提示（${missing.length}）</h3>
        <p class="wf-help">上传目录未包含以下采购原表，系统将完全按上传目录生成，不再要求关联。若采购文件要求必交，请把它们补入上传目录，或在导出后人工补入。</p>
        <ul>${missing.map(item=>`<li>${esc(item.text)}</li>`).join('')}</ul></section>`;
    }
    return `<section class="wf-section wf-outline-forms" aria-label="待关联采购原表"><h3 class="wf-section-title">待关联采购原表（${missing.length}）</h3>
      <p class="wf-help">恢复原章节，或指定现有章节保留原表，再保存目录。</p>
      ${missing.map(item=>`<div class="wf-file"><div class="wf-file-info"><strong>${esc(item.text)}</strong>
        <label class="wf-field">保留到现有章节<select class="input" data-form-assignment="${item.source_index}"><option value="">请选择章节</option>
        ${hierarchy(ui.outlineDraft.filter(r=>r.id)).map(row=>`<option value="${esc(row.id)}" ${ui.formAssignments[item.source_index]===row.id?'selected':''}>${esc(row.path)}</option>`).join('')}</select></label></div>
        ${wfButton('恢复原章节','restore-outline-form',{id:item.source_index,subtle:true})}</div>`).join('')}</section>`;
  }
  function calibrationPanel() {
    const data=ui.calibration||{}, rows=data.rows||[], selected=rows.find(row=>row.id===ui.calibrationRow)||rows[0];
    const nodes=items=>items?.length?`<ul class="wf-calibration-nodes">${items.map(row=>`<li><strong>${esc(row.path||row.text)}</strong><small>第 ${row.position} 项 · 第 ${row.level} 级</small></li>`).join('')}</ul>`:'<p class="wf-help">未关联对应章节</p>';
    return title('目录校准',ui.record.name)+`<div class="wf-stage-tools">${wfButton('返回项目','project-main',{subtle:true})}</div>`+(selected?`<div class="wf-calibration-grid">
      <section><label class="wf-field">选择采购原文要求<select id="wf-calibration-row" class="input">
      ${rows.map(row=>`<option value="${esc(row.id)}" ${selected.id===row.id?'selected':''}>${esc(row.text.slice(0,110))}</option>`).join('')}</select></label>
      <blockquote>${esc(selected.text)}</blockquote>${(selected.references||[]).map(ref=>`<details class="wf-note"><summary>${esc([ref.part,ref.locator].filter(Boolean).join(' · ')||'原文出处')}</summary><p>${esc(ref.original_text||ref.text||'')}</p></details>`).join('')}
      ${selected.not_applicable?'<p class="wf-help">采购原文标记为本项目不涉及。</p>':''}</section>
      <section><h3 class="wf-section-title">Kimi 自动目录</h3>${nodes(selected.automatic)}</section>
      <section><h3 class="wf-section-title">自定义目录</h3>${nodes(selected.custom)}</section>
      <section class="wf-calibration-suggestion"><h3 class="wf-section-title">AI 优化建议</h3><p>${esc(selected.suggestion||'本条暂无优化建议。')}</p>
      ${data.error?`<p class="wf-error" role="status">${esc(data.error)}</p>`:''}</section></div>`:
      `<p class="wf-help" role="status">${esc(data.error||({running:'正在后台对照目录与采购原文…',stale:'目录版本已变化，等待重新校准。',idle:'校准结果待生成。'}[data.status]||'正在读取校准结果…'))}</p>`)+
      (data.status==='running'?'':`<div class="wf-file-actions">${wfButton(data.status==='failed'?'重试校准':'重新生成校准','rerun-calibration',{subtle:true})}</div>`);
  }
  function calibrationState() {
    return {available: !ui.archive && ui.record?.outline_mode === 'custom', active: !ui.archive && ui.calibrationOpen};
  }
  function calibrationProjects() {
    const projects=ui.library.filter(record=>record.outline_mode==='custom');
    return projects.length
      ? `<ul class="wf-list">${projects.map(record=>`<li><div><strong>${esc(record.name)}</strong><small class="wf-file-meta">${esc(record.filename)}</small></div><button type="button" class="wf-button subtle" data-action="open-calibration" data-id="${esc(record.id)}">查看校准</button></li>`).join('')}</ul>`
      : '<p class="wf-help">暂无自定义目录项目。上传或粘贴自定义目录后，就能在这里查看校准。</p>';
  }
  async function openCalibration(id) {
    if(ui.busy||ui.pending||ui.savingDrafts)return;
    if(id){
      const record=await api(base(id));
      if(record.outline_mode!=='custom')throw new Error('该项目当前未使用自定义目录，请返回标书项目查看。');
      await restore(id,0,record);
      state.view='dashboard';
    }else if(!calibrationState().available)return;
    ui.calibrationOpen=true;
    paint(true);
    await loadOutlineJob('calibration');
  }
  function writingRunning() { return ['queued','running'].includes(ui.writing?.job?.status); }
  function writingStale(id) {
    const job=ui.writing?.job, stale=job?.stale_chapter_ids;
    return state.dirty || (id?ui.edits.has(id):!!ui.edits.size) ||
      (Array.isArray(stale)?id?stale.includes(id):stale.length>0:!!job?.stale);
  }
  function writingChapterBusy(id) { return writingRunning()&&ui.writing?.job?.target_chapter_ids?.includes(id); }
  function unacceptedChapters() {
    const job=ui.writing?.job;
    return (job?.chapters||[]).filter(c=>!job.accepted_chapter_ids?.includes(c.chapter_id));
  }
  function writingChapterStatus(id) {
    const job=activeWritingJob();
    if(job?.accepted_chapter_ids?.includes(id))return ' · 已采纳进正文';
    if(job?.chapters?.some(candidate=>candidate.chapter_id===id)){
      if(writingStale(id))return ' · 候选待更新';
      return viewingHistoricalEdition()?' · 历史候选':' · 有候选·未采纳';
    }
    return '';
  }
  function activeWritingJob() {
    if(ui.viewingEditionId&&ui.writing?.edition_job?.id===ui.viewingEditionId)return ui.writing.edition_job;
    return ui.writing?.job||null;
  }
  function viewingHistoricalEdition() {
    const current=ui.writing?.job?.id;
    return Boolean(ui.viewingEditionId&&current&&ui.viewingEditionId!==current);
  }
  function formatEditionTime(value) {
    const stamp=Number(value);
    if(!Number.isFinite(stamp)||stamp<=0)return '';
    try{return new Date(stamp*1000).toLocaleString();}catch{return '';}
  }
  function writingMessages(items) {
    return (items||[]).map(item=>typeof item==='string'?item:item.message||item.description||item.text||item.reason||'请核对该项资料。');
  }
  function writingStatus() {
    const writing=ui.writing, job=writing?.job;
    if(ui.writingError)return `<div class="wf-error" role="alert">${esc(ui.writingError)}<div class="wf-file-actions">${wfButton('重新读取写作状态','reload-writing',{subtle:true})}</div></div>`;
    if(!writing)return '<p class="wf-note">正在读取案例与写作状态…</p>';
    const warnings=writingMessages(writing.warnings);
    const warningHtml=warnings.length?`<details class="wf-note"><summary>案例读取提示（${warnings.length}）</summary><ul>${warnings.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></details>`:'';
    if(!job)return warningHtml;
    if(writingRunning()){
      const progress=Math.max(0,Math.min(100,Number(job.progress)||0));
      const batch=/第\s*\d+\s*\/\s*\d+\s*组/.test(job.message||'')?esc(job.message)+'。':'';
      const label=job.label?`<span class="wf-help">${esc(job.label)}${job.model?` · ${esc(job.model)}`:''}</span>`:'';
      return warningHtml+`<div class="wf-writing-progress" role="status"><div><strong>${job.status==='queued'?'等待开始写作':'正在生成初稿正文'}</strong><span>${progress}%</span></div>${label}<progress max="100" value="${progress}" aria-label="初稿正文生成进度">${progress}%</progress><p class="wf-help">${batch}离开本页不影响生成。</p></div>`;
    }
    if(job.status==='failed'){
      return warningHtml+generationResultCard(job,'failed')+`<p class="wf-error" role="alert">生成失败：${esc((job.error||job.message||'请稍后重试').replace(/[。；;]+$/,''))}。已保存正文保留，可重试。</p>`;
    }
    const notes=writingMessages(job.notes);
    const pending=(job.pending_chapter_ids||unacceptedChapters().map(c=>c.chapter_id)).length;
    const stale=unacceptedChapters().filter(candidate=>writingStale(candidate.chapter_id)).length;
    return warningHtml+generationResultCard(job,'completed')+
      `<p class="wf-note" role="status">${esc(job.label||'本批候选')}：已生成 ${job.chapters?.length||0} 个章节候选，待采纳 ${pending} 个，已采纳 ${job.accepted_chapter_ids?.length||0} 个${stale?`，${stale} 个候选待更新`:''}。</p>`+
      (notes.length?`<details class="wf-note"><summary>本次写作说明（${notes.length}）</summary><ul>${notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></details>`:'');
  }
  function generationResultCard(job, status) {
    if(!job?.id||ui.generationNoticeId===job.id+':'+status)return '';
    const when=formatEditionTime(job.completed_at||job.created_at);
    if(status==='failed'){
      return `<div class="wf-generation-result is-failed" role="status"><strong>${esc(job.label||'正文批次')} 生成失败</strong><p class="wf-help">${when?esc(when)+' · ':''}${esc(job.model||'')}</p></div>`;
    }
    const pending=(job.pending_chapter_ids||[]).length;
    return `<div class="wf-generation-result" role="status"><strong>${esc(job.label||'正文批次')} 已生成</strong><p class="wf-help">${when?esc(when)+' · ':''}${esc(job.model||'')} · ${job.chapters?.length||0} 章候选${pending?` · ${pending} 章待采纳`:''}</p><div class="wf-file-actions">${wfButton('去核对本批候选','review-generation',{primary:true,id:job.id})}</div></div>`;
  }
  function writingEditionBar() {
    const editions=ui.writing?.writing_editions||[];
    if(!editions.length)return '';
    const current=ui.writing?.job?.id||'';
    const viewing=ui.viewingEditionId||current;
    return `<section class="wf-writing-editions" aria-label="正文版本"><h3 class="wf-section-title">正文版本</h3>
      <p class="wf-help">每次生成是一批候选；采纳后才进入已保存正文、红线核对和导出。</p>
      <div class="wf-edition-list" role="list">${editions.map(edition=>{
        const selected=edition.id===viewing, live=edition.id===current;
        const meta=[formatEditionTime(edition.completed_at||edition.created_at),edition.model,
          edition.status==='completed'?`${edition.chapter_count} 章候选`:'',
          edition.status==='failed'?'失败':'',
          edition.status==='running'||edition.status==='queued'?'生成中':'',
          live?'当前':'历史'].filter(Boolean).join(' · ');
        return `<button type="button" class="wf-edition-chip ${selected?'is-selected':''} ${live?'is-current':''}" data-wf="select-edition" data-id="${esc(edition.id)}" role="listitem" ${ui.busy||writingRunning()?'disabled':''}><strong>${esc(edition.label)}</strong><small>${esc(meta)}</small></button>`;
      }).join('')}</div>
      ${viewingHistoricalEdition()?'<p class="wf-help">正在查看历史批次（只读）。回到当前批次后才能采纳。</p>':''}
    </section>`;
  }
  function writingSources(candidate) {
    const writing=ui.writing||{}, sources=new Map([...(writing.case_sources||[]),...(writing.procurement_sources||[]),...(ui.record?.sources||[])].map(source=>[source.id,source]));
    const materials=new Map([...(state.data.materials||[]),...(state.project?.selected_special_materials||[]),...(writing.materials||[])].map(item=>[item.id,item]));
    const references=(candidate.source_refs||[]).map(ref=>{
      const source=sources.get(typeof ref==='string'?ref:ref.source_id||ref.id);
      if(!source)return '<li>来源暂时无法定位，请重新读取写作状态后核对。</li>';
      const label=[source.file_name||source.filename||source.part||'采购文件',source.location||source.locator||source.context].filter(Boolean).join(' · ');
      return `<li><strong>${esc(label)}</strong>${source.text?`<blockquote>${esc(source.text)}</blockquote>`:''}</li>`;
    });
    for(const ref of candidate.material_refs||[]){
      const material=materials.get(typeof ref==='string'?ref:ref.material_id||ref.id);
      references.push(`<li><strong>已选材料 · ${esc(material?.name||'请回到系统填充核对材料')}</strong></li>`);
    }
    const requirements=(candidate.requirement_ids||[]).map(id=>ui.requirements.find(r=>r.id===id)).filter(Boolean);
    return `<details class="wf-source-evidence"><summary>查看写作依据（${references.length} 处）</summary>${references.length?`<ul class="wf-writing-sources">${references.join('')}</ul>`:'<p class="wf-help">本章未返回可定位的案例或材料出处，请重点核对事实表述。</p>'}${requirements.length?`<p class="wf-help">对应采购要求：${requirements.map(r=>esc(r.label||r.text)).join('；')}</p>`:''}</details>`;
  }
  function writingCandidate() {
    const job=activeWritingJob(), historical=viewingHistoricalEdition();
    const candidate=job?.chapters?.find(c=>c.chapter_id===ui.chapter);
    const remaining=historical?[]:unacceptedChapters().filter(c=>!writingStale(c.chapter_id)&&!writingChapterBusy(c.chapter_id));
    const generate=wfButton(candidate?'重新生成本章':'生成本章正文','generate-chapter-writing',{subtle:true,
      disabled:historical||writingRunning()||!ui.writing?.files?.length||ui.writing?.model?.ready!==true});
    const fixedForm=ui.record?.outline?.find(row=>row.id===ui.chapter)?.locked_format;
    if(!candidate)return `<h3 class="wf-section-title">AI 候选正文</h3><p class="wf-help">${writingChapterBusy(ui.chapter)?'正在生成':job?.status==='completed'?fixedForm?'本章为采购原表，请在 Word 中填写。':'本章无候选，可手工填写；请选择“有候选”章节查看生成结果。':'尚未生成候选正文'}</p><div class="wf-candidate-actions">${generate}${remaining.length?wfButton('采纳全部可用候选（'+remaining.length+'）','accept-all-writing',{subtle:true}):''}</div>`;
    const accepted=job.accepted_chapter_ids?.includes(ui.chapter), stale=writingStale(ui.chapter), generating=writingChapterBusy(ui.chapter);
    const current=ui.edits.has(ui.chapter)?ui.edits.get(ui.chapter):ui.editor?.chapters.find(c=>c.id===ui.chapter)?.text||'';
    const missing=writingMessages(candidate.missing_items), conflicts=writingMessages(candidate.conflicts);
    const badge=historical?'<span class="wf-status">历史批次 · 只读</span>':accepted?'<span class="wf-status">已采纳进正文</span>':'<span class="wf-status is-pending">有候选·未采纳</span>';
    return `<div class="wf-review-heading"><h3 class="wf-section-title">AI 候选正文</h3>${badge}</div>
      ${job.label?`<p class="wf-help">${esc(job.label)}${job.model?` · ${esc(job.model)}`:''}</p>`:''}
      <div class="wf-candidate-text" tabindex="0" aria-label="当前章节候选正文">${chapterPreview(candidate)}</div>
      ${missing.length?`<div class="wf-note"><strong>待补资料</strong><ul>${missing.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></div>`:''}
      ${conflicts.length?`<div class="wf-error"><strong>需要核对的冲突</strong><ul>${conflicts.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></div>`:''}
      ${writingSources(candidate)}<div class="wf-file-actions wf-candidate-actions">${wfButton(accepted?'本章已采纳':current.trim()?'采纳并替换本章正文':'采纳本章正文','accept-writing',{primary:true,disabled:historical||accepted||stale||generating})}${wfButton('采纳全部可用候选（'+remaining.length+'）','accept-all-writing',{subtle:true,disabled:historical||!remaining.length})}${generate}</div>${historical?'<p class="wf-help">历史批次仅供对照；请切回当前批次后再采纳。</p>':generating?'<p class="wf-help">正在重新生成本章；其他章节候选仍可采纳。</p>':!accepted&&stale?'<p class="wf-help">本章正文或依据已变化，请重新生成本章。</p>':!accepted&&!current.trim()?'<p class="wf-help">采纳后进入已保存正文，才会用于红线核对和导出。</p>':''}`;
  }
  function libraryCasesPanel(disabled) {
    // Reviewed colleague cases from the material library; selecting them replaces the package.
    const library=ui.writing?.library_cases||[], chosen=ui.librarySelection||new Set(ui.writing?.library_case_ids||[]);
    const rows=library.map(c=>`<label class="wf-library-case"><input type="checkbox" name="wf-library-case" value="${esc(c.id)}" ${chosen.has(c.id)?'checked':''} ${disabled?'disabled':''}><span><strong>${esc(c.name)}</strong><small>${esc([c.client_name,`${c.file_count} 个文件`,c.contributor?'提交人 '+c.contributor:''].filter(Boolean).join(' · '))}</small></span></label>`).join('');
    return `<details class="wf-library-cases" ${ui.libraryOpen?'open':''}><summary>从案例库选择${library.length?`（${library.length} 个可用）`:''}</summary>${library.length?`<div class="wf-library-list">${rows}</div><div class="wf-file-actions">${wfButton('使用所选案例','use-library-cases',{disabled})}<span class="wf-help">所选案例将追加到当前案例包。</span></div>`:'<p class="wf-help">案例库暂无案例。同事可在材料库“案例库”提交案例，经本机核对后即可在此选用。</p>'}</details>`;
  }
  async function useLibraryCases() {
    const ids=[...document.querySelectorAll('input[name="wf-library-case"]:checked')].map(el=>el.value);
    if(!ids.length)throw new Error('请先勾选案例库中的案例。');
    if(!ui.writing||ui.writingError)throw new Error('请先重新读取案例写作状态。');
    if(writingRunning())throw new Error('请等待本次写作完成后再替换案例包。');
    const local=ui,key=state.project.id;
    if(!ui.chat||ui.chatBusy||ui.chat.turns.some(turn=>['queued','running'].includes(turn.status)))return;
    const {case_revision,mapping_revision}=ui.chat;
    ui.chatBusy=true;paintChat();
    try{
      await api(writingUrl(key)+'/cases',{method:'POST',body:{library_case_ids:ids,append:true,case_revision,mapping_revision}});
      if(ui===local&&state.project?.id===key){ui.librarySelection=null;await readChat();}
    }finally{if(ui===local){ui.chatBusy=false;paintChat();}}
  }
  function writingInputs() {
    const writing=ui.writing, running=writingRunning(), uploaded=writing?.files||[], ready=writing?.model?.ready===true;
    const disabled=!writing||!!ui.writingError||running;
    return `<section class="wf-writing-inputs" aria-label="项目案例与 AI 写作"><h3 class="wf-section-title">1. 上传相关项目案例</h3><p class="wf-help">支持多文件或 ZIP，也可从案例库选择。</p>
      <input id="wf-case-upload" type="file" hidden multiple accept=".doc,.docx,.pdf,.pptx,.txt,.md,.zip,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff"><button type="button" class="wf-dropzone wf-case-dropzone" data-wf="upload-cases" ${disabled?'disabled':''} aria-label="上传相关项目案例"><span class="wf-drop-icon">${icon('upload')}</span><strong class="wf-drop-title">${uploaded.length?'点击或拖拽替换案例包':'点击或拖拽上传相关项目案例'}</strong><small class="wf-drop-help">DOC、DOCX、PDF、PPTX、TXT、MD、ZIP 或图片 · 合计 ≤ 50 MB</small></button>
      ${uploaded.length?`<div class="wf-case-files" aria-label="已上传案例文件">${uploaded.map(file=>`<div class="wf-file"><div class="wf-file-info"><strong class="wf-file-name">${esc(file.name)}</strong><small class="wf-file-meta">${(Number(file.size||0)/1024/1024).toFixed(2)} MB</small></div><span class="wf-status">${String(file.name).startsWith('案例库/')?'案例库':'已上传'}</span></div>`).join('')}</div>`:''}
      ${!isRemote()?`<label class="wf-ack"><input type="checkbox" id="wf-save-case" ${ui.saveCase?'checked':''}>上传时同步保存到案例库</label>
        ${ui.saveCase?`<label class="wf-field">案例名称<input id="wf-case-name" class="input" maxlength="200" value="${esc(ui.caseName)}"></label>`:''}
        ${writing?.saved_library_case_id?'<p class="wf-help">此次上传已保存到案例库。</p>':''}`:''}
      ${libraryCasesPanel(disabled)}
      <label class="wf-field" for="wf-business-notes">业务补充信息（选填）<textarea class="input" id="wf-business-notes" rows="3" maxlength="10000" placeholder="填写已确认的范围、团队安排或案例说明；不确定处标注待确认。">${esc(ui.businessNotes)}</textarea></label><p class="wf-help" id="wf-business-notes-status" role="status" ${ui.businessNotesDirty?'':'hidden'}>补充信息已修改，将用于下次生成。</p>
      ${writingModelField(disabled||!ready)}
      <div class="wf-file-actions wf-writing-generate">${wfButton(running?'正在生成初稿正文…':writing?.job?'重新生成初稿正文':'生成初稿正文','generate-writing',{primary:true,disabled:disabled||!uploaded.length||!ready||!ui.editor?.chapters?.length})}${!uploaded.length?'<span class="wf-help">请先上传案例包。</span>':!ready?`<span class="wf-help">${esc(writing?.model?.message||'AI 写作暂不可用，请检查项目模型配置。')}</span>`:'<span class="wf-help">生成结果为候选，采纳后才进入红线与导出。</span>'}</div><div id="wf-writing-status">${writingStatus()}</div></section>`;
  }
  function bodyEditionStamp() {
    const body=ui.writing?.body_edition;
    if(!body)return '<p class="wf-help" id="wf-body-edition-stamp">正在读取已保存正文版本…</p>';
    const latest=body.latest_accept;
    const when=latest?formatEditionTime(latest.accepted_at):'';
    const accept=latest?`${esc(latest.label||'正文批次')}${when?` · ${esc(when)}`:''}`:'尚未采纳 AI 候选（可能含手工正文）';
    const pending=body.has_unaccepted_candidates?'<p class="wf-help">下方红线不检查未采纳的 AI 候选；请先采纳或确认当前已保存正文。</p>':'';
    const expired=ui.redline?.ai_review_stale?'<span class="wf-redline-expired"> · 已过期，请重新扫描</span>':'';
    return `<div class="wf-body-edition-stamp" id="wf-body-edition-stamp" role="status"><strong>核对正文：已保存正文</strong><span> · 全书修订 r${esc(String(body.chapter_revision||0))} · 已填 ${esc(String(body.filled_chapter_count||0))} 章 · 最近采纳 ${accept}</span>${expired}</div>${pending}`;
  }
  function writingModelOptions() {
    const models=ui.writing?.model?.models;
    if(Array.isArray(models)&&models.length)return models;
    const current=ui.writing?.model?.model;
    return current?[{id:current,label:current}]:[];
  }
  function preferredWritingModel() {
    const options=writingModelOptions(), ids=new Set(options.map(row=>row.id));
    let saved='';
    try{saved=localStorage.getItem('bid-writing-model')||'';}catch(_){saved='';}
    if(saved&&ids.has(saved))return saved;
    if(ui.writingModel&&ids.has(ui.writingModel))return ui.writingModel;
    return ui.writing?.model?.model||options[0]?.id||'';
  }
  function writingModelField(disabled) {
    const options=writingModelOptions();
    if(!options.length)return '';
    const selected=preferredWritingModel();
    ui.writingModel=selected;
    return `<label class="wf-field" for="wf-writing-model">写作模型<select class="input" id="wf-writing-model" ${disabled||options.length<2?'disabled':''}>${options.map(row=>`<option value="${esc(row.id)}" ${row.id===selected?'selected':''}>${esc(row.label||row.id)}</option>`).join('')}</select></label>`;
  }
  function redlinePanel() {
    if(ui.redlineError)return `<section class="wf-section wf-redline-scan" id="wf-redline-scan"><h3 class="wf-section-title">废标红线核对</h3>${bodyEditionStamp()}<p class="wf-error" role="alert">${esc(ui.redlineError)}</p><div class="wf-file-actions">${wfButton('重新扫描','reload-redline',{subtle:true})}</div></section>`;
    const scan=ui.redline;
    if(!scan)return `<section class="wf-section wf-redline-scan" id="wf-redline-scan"><h3 class="wf-section-title">废标红线核对</h3>${bodyEditionStamp()}<p class="wf-note">正在读取废标红线…</p><div class="wf-file-actions">${wfButton('重新读取','reload-redline',{subtle:true})}</div></section>`;
    if(!scan.items?.length)return `<section class="wf-section wf-redline-scan" id="wf-redline-scan"><h3 class="wf-section-title">废标红线核对</h3>${bodyEditionStamp()}<p class="wf-help">本项目未识别到废标红线条款；若采购原文确有否决条款，请返回招标解读补充后再扫描。</p></section>`;
    const pending=scan.pending_count||0;
    const unsaved=redlineUnsaved();
    const chapters=ui.editor?.chapters||[];
    const ai=scan.ai_review&&typeof scan.ai_review==='object'?scan.ai_review:{};
    const aiRows=Object.values(ai);
    const aiLabels={satisfied:'通过',risk:'有风险',unclear:'需确认'};
    const aiCounts=aiRows.reduce((acc,row)=>{const v=row?.verdict;if(v in acc)acc[v]+=1;else acc.unclear+=1;return acc;},{satisfied:0,risk:0,unclear:0});
    const aiConflicts=scan.items.filter(item=>ai[item.id]?.verdict==='satisfied'&&item.status==='risk').length;
    const aiUnverified=aiRows.filter(row=>row?.verdict==='satisfied'&&row.evidence_verified!==true).length;
    const aiSummary=aiRows.length?`AI 核对：通过 ${aiCounts.satisfied}${aiUnverified?`（${aiUnverified} 条依据未核实）`:''} · 有风险 ${aiCounts.risk} · 需确认 ${aiCounts.unclear}${aiConflicts?`；${aiConflicts} 条与系统提示不一致，请人工复核`:''}${scan.ai_reviewed_at?`（${date(scan.ai_reviewed_at,true)}）`:''}`:'';
    const aiEligible=aiPassedEligible(scan).length;
    const shown=ui.redlinePendingOnly?scan.items.filter(item=>!item.acknowledged):scan.items;
    const fixLinks=item=>{
      const links=[];
      if(item.status==='risk'&&(item.material_category_ids||[]).length)links.push(wfButton('去材料核对处理','step',{id:4,subtle:true}));
      for(const id of item.empty_chapter_ids||[]){
        const chapter=chapters.find(c=>c.id===id);
        if(chapter&&!String(chapter.text||'').trim())links.push(wfButton(`去编写「${chapter.title||chapter.path||id}」`,'redline-chapter',{id,subtle:true}));
      }
      return links.length?`<div class="wf-file-actions wf-redline-fix">${links.join('')}</div>`:'';
    };
    const redlineItem=item=>{
      const row=ai[item.id];
      const risky=item.status==='risk'||row?.verdict==='risk';
      const passed=item.acknowledged?'is-passed':'';
      const badges=[
        row?`<span class="wf-ai-verdict wf-ai-${esc(row.verdict||'unclear')}" title="${esc(row.reason||'')}">AI：${esc(aiLabels[row.verdict]||'需确认')}</span>`:'',
        item.status==='risk'?'<span class="wf-redline-tag">规则提示</span>':''
      ].filter(Boolean).join('');
      const head=`<div class="wf-redline-head"><label class="wf-ack"><input type="checkbox" data-wf="ack-redline" data-id="${esc(item.id)}" aria-label="确认已核对：${esc(item.label)}" ${item.acknowledged?'checked':''}><strong title="${esc(item.text)}">${esc(item.label)}</strong></label>${badges?`<span class="wf-redline-badges">${badges}</span>`:''}</div>`;
      const expired=item.ack_expired?'<p class="wf-redline-expired">相关正文或材料已变化，之前的确认已失效，请重新核对。</p>':'';
      const conflict=row&&row.verdict==='satisfied'&&item.status==='risk'?'<p class="wf-redline-expired">AI 判定通过，但系统规则仍发现风险，请人工复核后再勾选。</p>':'';
      const quote=row&&row.evidence
        ?`“${esc(row.evidence)}”${row.evidence_verified!==true?' <span class="wf-redline-unverified">（未能逐字核对，请人工确认）</span>':''}`
        :'正文中未找到可直接引用的原文。';
      const aiDetail=row?`<div class="wf-redline-line"><span class="wf-redline-line-label" title="从投标正文逐字摘录">对应正文</span><p>${quote}</p></div>
        <div class="wf-redline-line"><span class="wf-redline-line-label">AI 建议</span><p>${esc(row.suggestion||'—')}</p></div>`:'';
      const findings=(item.findings||[]).length?`<ul>${item.findings.map(note=>`<li>${esc(note)}</li>`).join('')}</ul>`:'';
      const hints=(item.hints||[]).length?`<ul class="wf-redline-hints">${item.hints.map(note=>`<li>${esc(note)}</li>`).join('')}</ul>`:'';
      return `<article class="wf-redline-item ${risky?'is-risk':''} ${passed}" data-redline="${esc(item.id)}">${head}${expired}${conflict}<p>${esc(item.text)}</p>${aiDetail}${findings}${hints}${fixLinks(item)}</article>`;
    };
    return `<section class="wf-section wf-redline-scan" id="wf-redline-scan"><h3 class="wf-section-title">废标红线核对</h3>
      ${bodyEditionStamp()}
      <p class="wf-help">${esc(scan.scope||'自动检查：材料栏目是否缺漏或待补，关联章节是否已有正文。')}已保存正文可发起 AI 核对。人工必核和本地未覆盖项须逐条确认；材料缺漏或空章须先修复。正文或材料变化后，受影响条目需重新过关。</p>
      <p class="wf-help" role="status" id="wf-redline-status">${redlineStatusText()}</p>
      ${aiSummary?`<p class="wf-help" role="status">${esc(aiSummary)}</p>`:''}
      ${scan.ai_review_stale?'<p class="wf-help">正文或材料已变化，之前的 AI 核对结果已过期，请重新核对。</p>':''}
      ${!aiRows.length&&!scan.ai_review_stale?'<p class="wf-help">保存正文后可手动发起 AI 逐条核对（正文会发送到本项目配置的模型服务）。</p>':''}
      <div class="wf-redline-bulk"><div class="wf-redline-bulk-info"><span class="wf-help">勾选已逐条人工核对的项目。</span><label class="wf-ack wf-redline-filter"><input type="checkbox" id="wf-redline-pending-only" ${ui.redlinePendingOnly?'checked':''}>仅显示待过关（${pending}）</label></div><div class="wf-file-actions">${aiEligible?wfButton(`采纳 AI 通过项（${aiEligible}）`,'adopt-ai-redline',{subtle:true}):''}</div></div>
      <div class="wf-redline-list">${shown.length?shown.map(redlineItem).join(''):'<p class="wf-help">没有待过关的红线。</p>'}</div>
      <div class="wf-file-actions">${wfButton(ui.redlineAiBusy?'正在 AI 逐条核对…':'AI 逐条核对','ai-redline',{primary:true,disabled:ui.redlineAiBusy})}${wfButton('重新扫描','reload-redline',{subtle:true})}${wfButton('保存人工核对','save-redline',{subtle:true,disabled:!unsaved||!!ui.busy})}</div></section>`;
  }
  function chapterPreview(row, projectId=state.project.id) {
    const lines=row.text.split('\n'), parts=[], blocks=new Map((row.visuals||[]).map(b=>[b.start,b]));
    for(let i=0;i<lines.length;i++){
      const block=blocks.get(i);
      if(!block){parts.push(`<p>${esc(lines[i])}</p>`);continue;}
      const caption=esc(block.caption);
      if(block.kind==='image'){
        const url=writingUrl(projectId)+'/images/'+encodeURIComponent(block.image_id);
        parts.push(`<figure class="wf-case-picture">${isRemote()?'<p>请在本机入口查看原图。</p>':`<img loading="lazy" src="${esc(url)}" alt="${caption}">`}
          <figcaption>${caption}<small>来源：${esc(block.source)}</small></figcaption></figure>`);
      }else if(block.kind==='flow'){
        parts.push(`<figure class="wf-flow"><figcaption>${caption}</figcaption><ol>${block.steps.map(v=>`<li>${esc(v)}</li>`).join('')}</ol></figure>`);
      }else if(block.kind==='table'){
        parts.push(`<div class="wf-delivery-table"><table><caption>${caption}</caption><thead><tr>${block.headers.map(v=>`<th>${esc(v)}</th>`).join('')}</tr></thead>
          <tbody>${block.rows.map(r=>`<tr>${r.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
      }
      i+=block.lines.length-1;
    }
    return parts.join('');
  }
  function chatPreview(turn) {
    const job=ui.chatPreview;
    if(job?.id!==turn.id)return wfButton('预览章节正文','chat-preview',{id:turn.id,subtle:true});
    const paths=hierarchy((ui.record?.outline||[]).map(r=>({...r,title:r.text})));
    const titles=new Map(paths.map(r=>[r.id,r.path||r.title]));
    return wfButton('收起预览','chat-preview',{id:turn.id,subtle:true})+job.chapters.map(row=>{
      const id=row.chapter_id, accepted=job.accepted_chapter_ids?.includes(id), tooLong=job.over_capacity_ids?.includes(id);
      return `<details class="wf-chat-chapter"><summary>${esc(titles.get(id)||id)}</summary>
        <div class="wf-visual-body">${chapterPreview(row)}</div>${accepted?'<small>已采纳</small>':
          ui.chatPreviewCurrent&&job.pending_chapter_ids?.includes(id)?(tooLong?
            `<p class="wf-help">采纳本章会超出正文容量。</p>${wfButton('缩写本章重试','chat-shorten',{id,subtle:true})}`
            :wfButton('采纳本章正文','chat-adopt',{id,subtle:true})):''}</details>`;
    }).join('');
  }
  async function openChatPreview(id) {
    const local=ui,key=state.project.id;
    const result=await api(writingUrl(key)+'?view=job&edition='+encodeURIComponent(id));
    if(local!==ui||key!==state.project?.id||ui.step!==5)return;
    ui.chatPreview=result.edition_job;
    ui.chatPreviewCurrent=result.job?.id===id;
    paint();
  }
  async function adoptChatChapter(id) {
    const key=state.project.id,local=ui,job=ui.chatPreview;
    if(!job||!ui.chatPreviewCurrent||!job.pending_chapter_ids?.includes(id))return;
    const {case_revision,mapping_revision,revision}=ui.chat;
    if(!window.confirm('将本章图文保存到项目正文，已有本章正文会被替换。确认采纳？'))return;
    await api(writingUrl(key)+'/accept',{method:'POST',body:{job_id:job.id,chapter_ids:[id],case_revision,mapping_revision,revision}});
    if(local!==ui||key!==state.project?.id)return;
    await readChat();
    if(local!==ui||key!==state.project?.id)return;
    state.project.mapping_revision=ui.chat.mapping_revision;
    await openChatPreview(job.id);
    toast('本章正文已采纳。');
  }
  function chatMessages() {
    const turns=ui.chat?.turns||[];
    return turns.length?turns.map(turn=>`<article class="wf-chat-turn">
      <div class="wf-chat-user"><p>${esc(turn.business_notes)}</p><small>${esc(turn.model)}
        ${turn.files.length?` · ${esc(turn.files.slice(0,3).join('、'))}${turn.files.length>3?' 等 '+turn.files.length+' 份':''}`:''}</small></div>
      <div class="wf-chat-reply">${turn.status==='completed'
        ? `<p>${turn.pending_count?`初稿和编制说明已生成，还有 ${turn.pending_count} 项待人工核对。`:'初稿和编制说明已生成。'}</p>
           ${turn.pending?.length?`<ul>${turn.pending.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`:''}
           ${chatPreview(turn)}${isRemote()?'<p class="wf-help">请在本机入口下载 Word。</p>':`<div class="wf-chat-downloads">${turn.downloads.map(file=>`<a class="btn" href="${esc(file.url)}" download>${esc(file.name)}</a>`).join('')}</div>`}`
        : turn.status==='failed'?`<p role="alert">${esc(turn.error)}</p>${wfButton('重试','chat-retry',{subtle:true,id:turn.id})}`
        : '<p role="status">正在编写…</p>'}</div></article>`).join('')
      : '<p class="wf-chat-empty">上传相关项目案例，然后告诉我这份投标文件怎么写。</p>';
  }
  function writing() {
    const chat=ui.chat, busy=ui.chatBusy||chat?.turns.some(turn=>['queued','running'].includes(turn.status));
    return title('编写投标文件')+`<div class="wf-chat">
      <p class="wf-chat-project">${esc(state.project.name)}</p>
      <div id="wf-chat-log" class="wf-chat-log" role="log" aria-live="polite">${chatMessages()}</div>
      <div class="wf-chat-compose">
        ${chat?.files.length?`<div class="wf-chat-files" aria-label="本项目案例附件">${chat.files.map(file=>`<span>${esc(file.name)}</span>`).join('')}</div>`:''}
        ${chat?.warnings?.length?`<details><summary>案例读取提示（${chat.warnings.length}）</summary><ul>${chat.warnings.map(v=>`<li>${esc(v)}</li>`).join('')}</ul></details>`:''}
        <textarea id="wf-chat-message" maxlength="10000" rows="3" aria-label="编写指令" placeholder="输入编写或修改要求">${esc(ui.chatMessage||'')}</textarea>
        <div class="wf-chat-tools">
          <input id="wf-chat-upload" type="file" hidden multiple accept=".doc,.docx,.pdf,.pptx,.txt,.md,.zip,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff">
          <button class="btn wf-chat-add" type="button" data-wf="chat-upload" aria-label="上传案例文件" ${!chat||busy?'disabled':''}>+</button>
          ${chat?writingModelField(!!busy||!chat.model.ready):''}
          ${wfButton('发送','chat-send',{primary:true,disabled:!chat||!!busy||!chat.model.ready})}
        </div>
        ${ui.chatError?`<p class="wf-error" role="alert">${esc(ui.chatError)}</p>`:''}
        ${chat&&!chat.model.ready?`<p class="wf-help">${esc(chat.model.message)}</p>`:''}
      </div>
    </div>`+`<div class="wf-file-actions">${wfButton('编辑已保存正文','edit-saved-chapters',{subtle:true})}</div>`
      +libraryCasesPanel(!chat||!!busy)+redlinePanel()
      +(!isRemote()?`<section class="wf-section" aria-label="正式导出">
        <h3 class="wf-section-title">正式导出</h3>
        ${ui.redline?.pending_count?`<p class="wf-help">还有 ${ui.redline.pending_count} 条废标红线待过关。请处理风险提示、完成 AI 核对，再逐条确认并保存其余待核对项。期间可先导出仅供内部核对的阶段稿。</p>`:''}
        <div class="wf-file-actions">
          ${wfButton('查看内部阶段预览','stage-preview',{subtle:true})}
          ${wfButton('导出待补初稿','formal-export',{id:'draft',subtle:true})}
          ${wfButton('导出已核对版','formal-export',{id:'reviewed',primary:true})}
        </div>${(state.project.exports||[]).length?`<ul class="wf-export-list">${state.project.exports.map(item=>`<li><a href="${esc(item.download_url)}" download>${esc(item.filename)}</a> <a href="${esc(item.report_url)}">核对报告</a> ${deleteExportButton(item)}</li>`).join('')}</ul>`:''}
      </section>`:'');
  }
  let chatTimer=null;
  let chatRequest=0;
  async function readChat({poll=false}={}) {
    clearTimeout(chatTimer);
    const local=ui,key=state.project?.id,request=++chatRequest;
    if(!key)return;
    try{
      const result=await api(writingUrl(key)+'?view=chat');
      if(local!==ui||key!==state.project?.id||ui.step!==5||request!==chatRequest)return;
      const changed=JSON.stringify(ui.chat?.turns)!==JSON.stringify(result.turns);
      ui.chat=result;
      ui.writing={model:result.model,library_cases:result.library_cases,files:result.files,body_edition:result.body_edition};
      ui.chatError='';
      if(!poll||changed){
        paintChat();
        if(!ui.redline){await ensureRedlineLoaded();}
        else if(!redlineUnsaved()&&(ui.redline.mapping_revision!==result.mapping_revision||changed)){
          await loadRedline();paintRedline();
        }
      }
      if(result.turns.some(turn=>['queued','running'].includes(turn.status))){
        chatTimer=setTimeout(()=>{if(ui===local&&ui.step===5&&state.view==='dashboard')readChat({poll:true});},1800);
      }
    }catch(err){
      if(local!==ui||key!==state.project?.id||request!==chatRequest)return;
      ui.chatError=err.message;
      paintChat();
      if(poll&&local===ui&&ui.step===5&&state.view==='dashboard')chatTimer=setTimeout(()=>readChat({poll:true}),3000);
    }
  }
  function paintChat() {
    if(ui.step!==5||state.view!=='dashboard')return;
    const field=$('#wf-chat-message'), focused=field&&document.activeElement===field;
    const selection=focused?[field.selectionStart,field.selectionEnd]:null;
    paint();
    const next=$('#wf-chat-message'), log=$('#wf-chat-log');
    if(selection&&next){next.focus({preventScroll:true});next.setSelectionRange(...selection);}
    if(log)log.scrollTop=log.scrollHeight;
  }
  async function chatUpload(files) {
    if(!ui.chat||ui.chatBusy||!files.length)return;
    const local=ui,key=state.project.id;
    const {case_revision,mapping_revision}=ui.chat;
    ui.chatBusy=true;paintChat();
    try{
      await api(writingUrl(key)+'/cases',{method:'POST',body:{files:await toFiles(files),case_revision,mapping_revision,append:true}});
      if(ui===local&&state.project?.id===key)await readChat();
    }finally{if(ui===local&&state.project?.id===key){ui.chatBusy=false;paintChat();}}
  }
  async function chatSend(chapterId=null) {
    const chat=ui.chat, text=chapterId?'请缩写这个章节，保留必要的采购响应与引用，遵守章节字数上限。':(ui.chatMessage||'').trim();
    if(!chat||ui.chatBusy||chat.turns.some(turn=>['queued','running'].includes(turn.status)))return;
    if(!chat.files.length)throw new Error('请先用加号上传相关项目案例。');
    if(!text)throw new Error('请输入本轮编写或修改要求。');
    const key=state.project.id,local=ui;
    const {case_revision,mapping_revision,revision}=chat;
    const body={chat:true,case_revision,mapping_revision,revision,business_notes:text,model:preferredWritingModel(),...(chapterId?{chapter_ids:[chapterId]}:{})};
    ui.chatBusy=true;ui.chatError='';paintChat();
    try{
      const outbound=await api(writingUrl(key)+'/outbound',{method:'POST',body});
      if(ui!==local||key!==state.project?.id)return;
      const message=outboundSummary(outbound).replace('确认发送并开始生成？','')
        +'正文编写完成后，本轮正文和材料核对摘要也会发送至同一模型服务，必要时回查案例摘录。确认发送？';
      if(!window.confirm(message))return;
      await api(writingUrl(key)+'/generate',{method:'POST',body});
      if(ui===local&&key===state.project?.id){ui.chatMessage='';await readChat();}
    }catch(err){
      if(ui===local&&key===state.project?.id){await readChat();ui.chatError=err.message;}
    }finally{if(ui===local&&key===state.project?.id){ui.chatBusy=false;paintChat();}}
  }
  function archiveView() {
    const {project,chapters}=ui.archive;
    const filled=hierarchy(chapters).filter(c=>c.text);
    const materials=new Map([...(state.data.materials||[]),...(project.selected_special_materials||[])].map(m=>[m.id,m.name]));
    const rows=(project.mappings||[]).map(m=>{
      const names=(m.material_ids||[]).map(id=>materials.get(id)||id);
      const choice={fill:'填充',missing:'待补',skip:'跳过'}[m.action]||'待核对';
      return `<li><div><strong>${esc(categoryName(m.category_id))}</strong><small class="wf-file-meta">${esc([choice,...names,m.note].filter(Boolean).join(' · '))}</small></div></li>`;
    }).join('');
    return `<section class="wf-workspace"><div class="wf-card">${ui.error?`<p class="wf-error" role="alert">${esc(ui.error)}</p>`:''}${title('历史工作稿',project.name+' · 只读')}
      <section class="wf-section"><h2 class="wf-section-title">已保存的章节正文（${filled.length}）</h2>${filled.map(c=>`<details class="wf-note"><summary>${esc(c.path)}</summary><div class="wf-visual-body">${chapterPreview(c,project.id)}</div></details>`).join('')||'<p class="wf-help">该工作稿未保存在线正文。</p>'}</section>
      <details class="wf-note"><summary>材料核对记录（${project.mappings?.length||0}）</summary><ul class="wf-list">${rows}</ul></details>${renderExports(project.exports||[])}
      <div class="wf-actions">${wfButton('返回整本项目','resume',{id:project.framework_id,primary:true})}</div></div></section>`;
  }
  function view() {
    if(ui.archive)return archiveView();
    const bodies=[settings,()=>ui.record?analysis():settings(),outline,filling,writing];
    return `<div class="wf-workspace" ${ui.busy||ui.pending||ui.savingDrafts?'inert':''}>${stepper()}<section class="wf-card" aria-busy="${!!(ui.busy||ui.pending||ui.savingDrafts)}">${ui.error?`<p class="wf-error" role="alert">${esc(ui.error)}</p>`:''}${ui.calibrationOpen?calibrationPanel():bodies[ui.step-1]()}</section></div>`;
  }
  function url() {
    const u=new URL(location.href);
    for(const key of ['workflow','step','part','legacy_project'])u.searchParams.delete(key);
    if(state.view==='dashboard'){
      if(ui.archive)u.searchParams.set('legacy_project',ui.archive.project.id);
      else if(ui.record || ui.step===2){
        u.searchParams.set('workflow',ui.record?.id || 'analysis');
        if(ui.record)u.searchParams.set('step',String(ui.step));
      }
    }
    history.replaceState(null,'',u);
  }
  function paint(top=false) {
    if(state.view!=='dashboard')return;
    url();render();
    if(top)window.scrollTo({top:0});
  }
  async function loadLibrary() {
    const data=await api('/api/frameworks');
    ui.library=Array.isArray(data)?data:data.items||[];
  }
  function bindRecord(record) {
    stopWritingPoll();
    clearTimeout(chatTimer);chatRequest++;
    ui.chat=null;ui.chatMessage='';ui.chatError='';ui.chatBusy=false;
    const previousRequirement=ui.requirementKey, previousOutline=ui.outlineKey;
    if(ui.record?.id!==record.id){ui.automatic=null;ui.calibration=null;ui.calibrationOpen=false;ui.outlineText='';}
    ui.record=record;ui.name=record.name;ui.archive=null;
    ui.library=[record,...ui.library.filter(r=>r.id!==record.id)];
    ui.editor=null;ui.edits.clear();ui.chapter='';ui.writing=null;ui.writingError='';ui.writingModel='';ui.businessNotes='';ui.businessNotesDirty=false;
    ui.redline=null;ui.redlineError='';redlineRequest++;
    ui.requirements=clone(record.requirements||[]).map(item=>({...item,_key:item.id||newKey()}));
    ui.requirementCategory='redline';
    ui.requirementKey=ui.requirements.find(item=>item._key===previousRequirement&&item.category==='redline')?previousRequirement:ui.requirements.find(item=>item.category==='redline')?._key||'';
    ui.outlineDraft=clone(record.outline||[]).map(item=>({...item,_key:item.id||newKey(),level:Number(item.level)||1}));
    ui.formAssignments={...record.outline_form_assignments};
    ui.outlineSelection.clear();
    ui.outlineKey=ui.outlineDraft.some(item=>item._key===previousOutline)?previousOutline:ui.outlineDraft[0]?._key||'';
    ui.requirementsDirty=false;ui.outlineDirty=false;ui.previewLoaded=false;ui.sourceQuery='';
    ui.notesAcknowledged=false;ui.removedAcknowledged=new Set();
    if(state.project?.framework_id===record.id&&record.projects?.word!==state.project.id){state.project=null;state.dirty=false;}
  }
  async function restore(id, step=0, record=null) {
    record=record||await api(base(id));
    bindRecord(record);
    state.project=null;state.dirty=false;
    const linked=record.projects?.word;
    if(linked){
      const project=await api(projectUrl(linked));
      if(project.framework_id!==record.id||project.framework_artifact_id!=='word')throw new Error('整本工作稿与框架关联不一致，请重新打开项目核对。');
      state.project=project;resetProjectUi();await loadProjectPickers();
    }
    const requested=step>=2&&step<=5?step:0;
    ui.step=requested?resolveStep(requested):linked&&available(4)?(projectReviewed()?5:4):available(3)?3:2;
    if(ui.step===4 && projectUi.step==='export')projectUi.step=commonReviewed()?'common':'special';
    if(ui.step===5)await readChat();
    if(ui.step===3&&record.outline_mode)await loadOutlineJob('automatic');
  }
  async function enter({fromUrl=false, resume=false}={}) {
    if(!fromUrl){
      if(ui.busy||ui.pending||ui.savingDrafts)throw new Error('请等待当前操作完成。');
      if(!resume&&ui.businessNotesDirty&&!window.confirm('业务补充信息尚未用于生成，新建项目将放弃这些修改。继续新建？'))return;
      await beforeLeave();if(state.dirty)await saveProject(false);
    }
    const query=new URLSearchParams(location.search);
    const entry=fromUrl?query.get('workflow'):resume?ui.record?.id:null;
    const step=fromUrl?Number(query.get('step')):ui.step;
    const archived=fromUrl&&!entry?query.get('legacy_project'):null;
    const name=resume?ui.name:'';
    state.project=null;state.dirty=false;state.view='dashboard';state.query='';
    stopWritingPoll();ui=fresh();ui.name=name;
    try{
      await loadLibrary();
      if(archived){await resumeProject(await api(projectUrl(archived)));return;}
      if(entry==='analysis'||(resume&&!entry&&step===2))ui.step=2;
      else if(entry && entry!=='new')await restore(entry,step>=1&&step<=5?step:0);
    }catch(err){ui.error=err.message;}
    paint(true);
  }
  async function resumeProject(project) {
    if(!project.framework_id)throw new Error('该项目不属于招标文件编制流程。');
    const record=await api(base(project.framework_id));
    if(record.projects?.word===project.id){
      state.view='dashboard';await restore(record.id);paint(true);return;
    }
    if(!(record.legacy_projects||[]).some(p=>p.id===project.id))throw new Error('该项目与采购原件的历史记录不符，请从项目列表重新进入。');
    const archive=await api(draftsUrl(project.id));
    state.view='dashboard';state.project=null;state.dirty=false;
    bindRecord(record);ui.error='';ui.archive={project,chapters:archive.chapters};
    paint(true);
  }
  async function upload(file) {
    if (!file||ui.busy||ui.record)return;
    if (!/\.docx?$/i.test(file.name)) throw new Error('请选择 DOCX 或 DOC 招标文件。PDF 或扫描件请先转为可核对的 Word。');
    if (!file.size||file.size>50*1024*1024) throw new Error('采购文件须大于 0 字节且不超过 50 MB。');
    ui.busy='正在解析招标文件…';ui.error='';paint();
    try{
      const [{name:filename,data}]=await toFiles([file]);
      const name=ui.name.trim()||filename.replace(/\.docx?$/i,'').slice(0,150)||'标书项目';
      const result=await api('/api/frameworks',{method:'POST',body:{name,filename,data,outline_choice:true}});
      bindRecord(result);ui.step=2;
    } finally {ui.busy='';paint();}
  }
  async function loadEditor() {
    ui.editor=await api(draftsUrl(state.project.id));
    if(!ui.editor.chapters.some(c=>c.id===ui.chapter))ui.chapter=ui.editor.chapters[0]?.id||'';
    await loadWriting({poll:!!ui.writing});
  }
  function stopWritingPoll() {
    clearTimeout(writingTimer);writingTimer=null;writingEpoch++;writingRequest++;
  }
  function updateWritingPanels() {
    if(state.view!=='dashboard'||ui.step!==5)return;
    const status=$('#wf-writing-status'),candidate=$('#wf-writing-candidate'),editions=$('#wf-writing-editions');
    const stamp=$('#wf-body-edition-stamp')?.closest('.wf-body-edition-stamp')||$('#wf-body-edition-stamp');
    if(status)status.innerHTML=writingStatus();
    if(candidate)candidate.innerHTML=writingCandidate();
    if(editions)editions.innerHTML=writingEditionBar();
    const stampHost=document.querySelector('#wf-redline-scan .wf-body-edition-stamp')||document.querySelector('#wf-body-edition-stamp');
    if(stampHost){
      const wrap=document.createElement('div');
      wrap.innerHTML=bodyEditionStamp();
      stampHost.replaceWith(...wrap.childNodes);
    }
    const selector=$('#wf-chapter');
    if(selector&&ui.editor)for(const chapter of hierarchy(ui.editor.chapters)){
      const option=[...selector.options].find(option=>option.value===chapter.id);
      if(option)option.textContent=chapter.number+'　'+chapter.path+writingChapterStatus(chapter.id);
    }
    const generate=document.querySelector('[data-wf="generate-writing"]'),upload=document.querySelector('[data-wf="upload-cases"]');
    const disabled=!!ui.busy||!!ui.pending||!ui.writing||!!ui.writingError||writingRunning();
    if(generate){generate.disabled=disabled||!ui.writing?.files?.length||ui.writing?.model?.ready!==true||!ui.editor?.chapters?.length;generate.textContent=writingRunning()?'正在生成初稿正文…':ui.writing?.job?'重新生成初稿正文':'生成初稿正文';}
    if(upload)upload.disabled=disabled;
    const modelSelect=$('#wf-writing-model');
    if(modelSelect)modelSelect.disabled=disabled||writingModelOptions().length<2||ui.writing?.model?.ready!==true;
    for(const control of document.querySelectorAll('[data-wf="use-library-cases"],input[name="wf-library-case"]'))control.disabled=disabled;
  }
  function bindWriting(result) {
    // A newer query or completed edit supersedes every earlier in-flight read.
    writingRequest++;
    ui.writing={...ui.writing,...result};ui.writingError='';
    if(result.model)ui.writingModel=preferredWritingModel();
    if(!ui.businessNotesDirty)ui.businessNotes=result.job?.business_notes||result.business_notes||'';
    if(!ui.viewingEditionId&&result.job?.id)ui.viewingEditionId=result.job.id;
    if(ui.viewingEditionId===result.job?.id)ui.writing.edition_job=result.job;
  }
  async function loadWriting({poll=false, editionId=null}={}) {
    const currentUi=ui,projectId=state.project?.id,request=++writingRequest;
    const current=()=>request===writingRequest&&ui===currentUi&&state.project?.id===projectId;
    if(!projectId)return;
    const withEdition=(base,id)=>{
      if(!id)return base;
      return base+(base.includes('?')?'&':'?')+'edition='+encodeURIComponent(id);
    };
    try{
      let result;
      if(poll){
        result=await api(writingUrl(projectId)+'?view=status');
        if(!current())return;
        const previous=ui.writing, previousStatus=previous?.job?.status;
        if(!previous||JSON.stringify(result.source_revision)!==JSON.stringify(previous.source_revision)){
          result=await api(withEdition(writingUrl(projectId), ui.viewingEditionId||result.job?.id));
        }else if(result.state_revision!==previous.state_revision||result.job?.id!==previous.job?.id||
          result.job?.version!==previous.job?.version||
          (['completed','failed'].includes(result.job?.status)&&result.job.status!==previous.job?.status)){
          result=await api(withEdition(writingUrl(projectId)+'?view=job', result.job?.id||ui.viewingEditionId));
        }else{
          result.job=result.job?{...previous.job,...result.job}:previous.job;
          result.writing_editions=result.writing_editions||previous.writing_editions;
          result.body_edition=result.body_edition||previous.body_edition;
          if(previous.edition_job)result.edition_job=previous.edition_job;
        }
        if(!current())return;
        bindWriting(result);
        if(previousStatus&&['queued','running'].includes(previousStatus)&&['completed','failed'].includes(result.job?.status)){
          ui.viewingEditionId=result.job.id;
          ui.generationNoticeId='';
          if(result.job.status==='completed'){
            const pending=result.job.pending_chapter_ids||[];
            if(pending[0])ui.chapter=pending[0];
          }
          paint(true);
          scheduleWritingPoll();
          return;
        }
      }else{
        const target=editionId||ui.viewingEditionId||'';
        result=await api(withEdition(writingUrl(projectId), target));
        if(!current())return;
        if(!result||!Array.isArray(result.files)||!Number.isInteger(result.case_revision))throw new Error('案例写作状态不完整，请重新读取。');
        bindWriting(result);
        if(editionId)ui.viewingEditionId=editionId;
        else if(!ui.viewingEditionId&&result.job?.id)ui.viewingEditionId=result.job.id;
      }
    }catch(err){
      if(!current())return;
      ui.writingError='暂时无法读取案例写作状态：'+err.message;
    }
    scheduleWritingPoll();
  }
  async function selectWritingEdition(id) {
    if(!id||writingRunning())return;
    ui.viewingEditionId=id;
    ui.busy='正在读取正文批次…';ui.error='';paint();
    try{
      await loadWriting({editionId:id});
      const job=activeWritingJob();
      const pending=(job?.pending_chapter_ids||[]).find(Boolean)||job?.chapters?.[0]?.chapter_id;
      if(pending)ui.chapter=pending;
    }finally{ui.busy='';paint(true);}
  }
  function reviewGeneration(id) {
    ui.generationNoticeId=(id||ui.writing?.job?.id||'')+':completed';
    ui.viewingEditionId=id||ui.writing?.job?.id||'';
    const job=ui.writing?.job;
    const pending=(job?.pending_chapter_ids||unacceptedChapters().map(c=>c.chapter_id))[0];
    if(pending)ui.chapter=pending;
    paint(true);
    setTimeout(()=>{
      document.querySelector('#wf-writing-review')?.scrollIntoView({block:'start',behavior:'smooth'});
      $('#wf-chapter')?.focus({preventScroll:true});
    },0);
  }
  function scheduleWritingPoll() {
    clearTimeout(writingTimer);writingTimer=null;
    if(!writingRunning()||!hasProject()||state.view!=='dashboard')return;
    const epoch=writingEpoch,currentUi=ui,projectId=state.project.id;
    writingTimer=setTimeout(async()=>{
      writingTimer=null;
      if(epoch!==writingEpoch||ui!==currentUi||state.project?.id!==projectId||state.view!=='dashboard')return;
      if(ui.pending||ui.busy||ui.savingDrafts){scheduleWritingPoll();return;}
      await loadWriting({poll:true});
      if(epoch!==writingEpoch||ui!==currentUi||state.project?.id!==projectId)return;
      updateWritingPanels();
    },2200);
  }
  async function saveWritingInputs() {
    if(state.dirty)await saveProject(false);
    await saveDrafts();
  }
  async function uploadCases(fileList) {
    const selected=[...fileList];
    if(!selected.length||ui.busy||ui.pending)return;
    if(!ui.writing||ui.writingError)throw new Error('请先重新读取案例写作状态。');
    if(writingRunning())throw new Error('请等待本次写作完成后再替换案例包。');
    if(selected.some(file=>! /\.(docx?|pdf|pptx|txt|md|zip|png|jpe?g|webp|gif|bmp|tiff?)$/i.test(file.name)))throw new Error('案例支持 DOC、DOCX、PDF、PPTX、TXT、MD、ZIP 或图片。');
    if(selected.some(file=>!file.size)||selected.reduce((sum,file)=>sum+file.size,0)>50*1024*1024)throw new Error('案例文件须大于 0 字节，合计不超过 50 MB。');
    const save_to_library=ui.saveCase&&!isRemote()?{name:ui.caseName.trim()}:undefined;
    if(save_to_library&&!save_to_library.name)throw new Error('请填写要保存到案例库的案例名称。');
    if(ui.writing.files.length&&!window.confirm('本次选择的文件将替换整个案例包，之前生成的候选将失效。已保存的正文保留。继续上传？'))return;
    ui.busy='正在读取相关项目案例…';ui.error='';stopWritingPoll();paint();
    try{
      await saveWritingInputs();
      const files=await toFiles(selected);
      bindWriting(await api(writingUrl(state.project.id)+'/cases',{method:'POST',body:{files,save_to_library,case_revision:ui.writing.case_revision,mapping_revision:state.project.mapping_revision}}));
      ui.saveCase=false;ui.caseName='';
    }finally{ui.busy='';paint();scheduleWritingPoll();}
  }
  async function generateWriting(chapterOnly=false) {
    if(writingRunning())return;
    if(!ui.writing?.files?.length)throw new Error('请先上传相关项目案例。');
    if(ui.writing.model?.ready!==true)throw new Error(ui.writing.model?.message||'AI 写作暂不可用。');
    const replaced=unacceptedChapters().filter(c=>!chapterOnly||c.chapter_id===ui.chapter);
    if(replaced.length&&!window.confirm(chapterOnly?'重新生成会替换本章尚未采纳的候选，已保存正文和其他章节候选保留。继续生成？':'重新生成会替换本次尚未采纳的候选，已保存正文保留。继续生成？'))return;
    stopWritingPoll();
    try{
      await saveWritingInputs();
      const business_notes=ui.businessNotes;
      const model=preferredWritingModel();
      ui.writingModel=model;
      const request={case_revision:ui.writing.case_revision,mapping_revision:state.project.mapping_revision,revision:ui.editor.revision,business_notes,model,...(chapterOnly?{chapter_ids:[ui.chapter]}:{})};
      const outbound=await api(writingUrl(state.project.id)+'/outbound',{method:'POST',body:request});
      if(!window.confirm(outboundSummary(outbound)))return;
      const job=await api(writingUrl(state.project.id)+'/generate',{method:'POST',body:request});
      const queued=job.job||job;
      ui.viewingEditionId=queued.id||'';
      ui.generationNoticeId='';
      bindWriting({job:queued, writing_editions:ui.writing?.writing_editions, body_edition:ui.writing?.body_edition});
      ui.businessNotesDirty=false;
      // The generation request only queues work. Keep navigation and hand editing available.
      paint();
    }finally{scheduleWritingPoll();}
  }
  function outboundSummary(info) {
    const list=(items,limit=8)=>items.length?items.slice(0,limit).join('、')+(items.length>limit?` 等 ${items.length} 项`:''):'无';
    return ['生成初稿将把以下资料发送至所配置的 AI 模型服务'+(info.model?`（${info.model}）`:'')+'：','',
      `· 待编写章节：${info.chapter_count} 章，分 ${info.group_count} 次发送`,
      `· 采购要求：${info.requirement_count} 条，相关采购原文 ${info.procurement_source_count} 段`,
      `· 案例资料：${list(info.case_files||[])}（每次都会发送）`,
      `· 发送正文的材料：${list(info.materials_with_text||[])}`,
      `· 只发送名称的材料：${list(info.materials_name_only||[])}（证件、授权书等正文不发送）`,
      `· 业务补充信息：${info.business_notes_length?info.business_notes_length+' 字':'无'}`,'',
      '营业执照、身份证等图片不会发送。确认发送并开始生成？'].join('\n');
  }
  async function acceptWriting(all=false) {
    const job=ui.writing?.job;
    if(!job)throw new Error('当前没有可采纳的候选正文。');
    const candidates=unacceptedChapters().filter(c=>(all||c.chapter_id===ui.chapter)&&!writingStale(c.chapter_id)&&!writingChapterBusy(c.chapter_id));
    if(!candidates.length)return;
    const replacements=candidates.filter(c=>ui.editor.chapters.some(chapter=>chapter.id===c.chapter_id&&chapter.text?.trim()));
    if(replacements.length&&!window.confirm(`采纳将替换 ${replacements.length} 个章节已有的正文（包括手工编写内容）。请先核对候选与当前正文。确认替换？`))return;
    stopWritingPoll();
    try{
      await saveWritingInputs();
      let result;
      try{result=await api(writingUrl(state.project.id)+'/accept',{method:'POST',body:{job_id:job.id,chapter_ids:candidates.map(c=>c.chapter_id),case_revision:ui.writing.case_revision,mapping_revision:state.project.mapping_revision,revision:ui.editor.revision}});}
      catch(err){await loadWriting();throw err;}
      ui.editor=result.editor;
      if(ui.editor.mapping_revision!==undefined)state.project.mapping_revision=ui.editor.mapping_revision;
      bindWriting(result.writing);paint();redlineRefreshPending=true;backgroundRedlineRefresh();if(candidates.length>1)toast(`已采纳并保存 ${candidates.length} 个章节正文。`);
    }finally{scheduleWritingPoll();}
  }
  async function saveDrafts() {
    if(!ui.edits.size)return;
    if(ui.savingDrafts)throw new Error('正在保存正文，请等待完成。');
    const chapters=[...ui.edits].map(([id,text])=>({id,text}));
    if(!hasProject())throw new Error('请从整本项目重新进入正文编写。');
    stopWritingPoll();
    ui.savingDrafts=true;
    const workspace=document.querySelector('.wf-workspace');if(workspace)workspace.inert=true;
    try{
      ui.editor=await api(draftsUrl(state.project.id),{method:'PATCH',body:{chapters,revision:ui.editor.revision,mapping_revision:state.project.mapping_revision}});
      if(ui.editor.mapping_revision)state.project.mapping_revision=ui.editor.mapping_revision;
      for(const {id,text} of chapters)if(ui.edits.get(id)===text)ui.edits.delete(id);
      redlineRefreshPending=true;
      if(ui.writing?.job){
        const changed=chapters.filter(({id})=>ui.writing.job.chapter_revisions?.[id]!==ui.editor.chapters.find(c=>c.id===id)?.revision).map(c=>c.id);
        ui.writing.job.stale_chapter_ids=[...new Set([...(ui.writing.job.stale_chapter_ids||[]),...changed])];
        ui.writing.job.stale=ui.writing.job.stale_chapter_ids.length>0;
      }
    }finally{
      writingRequest++;
      ui.savingDrafts=false;
      if(workspace?.isConnected)workspace.inert=!!(ui.busy||ui.pending);
      scheduleWritingPoll();
    }
  }
  function reviewDirty() { return ui.requirementsDirty||ui.outlineDirty; }
  function syncReviewStatus() {
    const r=$('#wf-requirements-status'),o=$('#wf-outline-status');
    if(r)r.textContent=ui.requirementsDirty?'有未保存修改；保存后须重新确认':ui.record.requirements_confirmed?'要求已确认':'要求待确认';
    if(o){o.textContent=ui.outlineDirty?'有未保存修改；下方预览仍为已保存版本':'';o.hidden=!ui.outlineDirty;}
    const rs=document.querySelector('[data-wf="save-requirements"]'),os=document.querySelector('[data-wf="save-outline"]'),confirm=document.querySelector('[data-wf="confirm"]');
    if(rs)rs.disabled=!ui.requirementsDirty||!!ui.busy;
    if(os)os.disabled=!ui.outlineDirty||!!ui.busy;
    if(confirm)confirm.disabled=ui.outlineDirty||!ui.previewLoaded||ui.record.requirements_confirmed===false||!!ui.busy||outlineMissingBlocked();
  }
  function discardReviewEdits() {
    if(!reviewDirty())return true;
    if(!window.confirm('存在尚未保存的要求或目录修改。放弃这些修改并离开？'))return false;
    bindRecord(ui.record);return true;
  }
  async function saveRequirements(confirmed=false) {
    if(!editable())throw new Error('历史结果不能在线修订，请重新上传采购原件。');
    if(ui.requirements.some(item=>!item.text?.trim()||!item.references?.length))throw new Error('每条要求须填写内容并选择至少一处采购原文依据。');
    const items=ui.requirements.map(({_key,...item})=>item);
    const body={revision:ui.record.revision,items,confirmed};
    if(confirmed){
      const notes=pendingNotes(), removed=removedColumnRequirements();
      ui.notesAcknowledged=!!$('#wf-notes-ack')?.checked||ui.notesAcknowledged||!notes.length;
      if(notes.length&&!ui.notesAcknowledged){
        guideWorkflowCompletion(2,'请先展开并核对待核对事项，勾选确认未遗漏必交材料与栏目后再确认要求。','#wf-notes-ack');
        return;
      }
      const missing=removed.filter(row=>!ui.removedAcknowledged.has(row.id));
      if(missing.length){
        guideWorkflowCompletion(2,'请逐项勾选已删除的资格/格式要求，确认不会漏掉响应栏目；或先恢复这些要求。',
          `[data-wf="ack-removed"][data-id="${missing[0].id}"]`);
        return;
      }
      body.notes_acknowledged=notes.length?true:false;
      body.acknowledged_removed_ids=removed.map(row=>row.id);
    }
    ui.busy=confirmed?'正在确认要求并生成目录…':'正在保存要求…';ui.error='';paint();
    try{
      const record=await api(base(ui.record.id)+'/requirements',{method:'PATCH',body});
      bindRecord(record);
      if(confirmed)ui.step=3;
      toast(confirmed?(record.outline_mode==='pending'?'采购要求已确认，请选择目录编排方式。':'采购要求已确认，目录已更新。'):'要求修改已保存。');
    }finally{ui.busy='';paint();}
  }
  function bindRedline(scan, previous=null) {
    // 服务端确认状态单独保存；页面勾选只是本地草稿，导出前必须保存到服务端。
    scan.saved_ids=[...(scan.acknowledged_ids||[])];
    if(previous?.items){
      // 重新扫描时保留尚未保存、且依据未变化的本地勾选。
      const local=new Map(previous.items.map(item=>[item.id,item]));
      for(const item of scan.items||[]){
        const old=local.get(item.id);
        if(old&&old.basis===item.basis&&old.acknowledged)item.acknowledged=true;
      }
    }
    syncRedlineCounts(scan);
    ui.redline=scan;ui.redlineError='';
  }
  function syncRedlineCounts(scan=ui.redline) {
    if(!scan?.items)return;
    scan.acknowledged_ids=scan.items.filter(row=>row.acknowledged).map(row=>row.id);
    scan.pending_count=scan.items.filter(row=>!row.acknowledged).length;
    scan.complete=!scan.pending_count;
  }
  function aiPassedEligible(scan=ui.redline) {
    const ai=scan?.ai_review&&typeof scan.ai_review==='object'?scan.ai_review:{};
    return (scan?.items||[]).filter(item=>!item.acknowledged
      &&ai[item.id]?.verdict==='satisfied'&&ai[item.id]?.evidence_verified===true
      &&item.status!=='risk'&&!item.manual_check&&item.auto_checked===true);
  }
  function adoptAiRedline() {
    const scan=ui.redline;
    if(!scan?.items?.length)return;
    const eligible=aiPassedEligible(scan);
    if(!eligible.length)return;
    eligible.forEach(item=>{item.acknowledged=true;});
    syncRedlineCounts(scan);
    paintRedline();
    toast(scan.pending_count
      ?`已采纳 AI 通过的 ${eligible.length} 条；尚有 ${scan.pending_count} 条待确认。`
      :`已采纳 AI 通过的 ${eligible.length} 条，红线已全部通过。`);
  }
  function redlineUnsaved() {
    const scan=ui.redline;
    if(!scan?.items)return false;
    const saved=new Set(scan.saved_ids||[]);
    return scan.items.some(item=>item.acknowledged!==saved.has(item.id));
  }
  function redlineStatusText() {
    const scan=ui.redline;
    if(!scan)return '';
    const items=scan.items||[];
    const total=items.length;
    const pending=scan.pending_count||0;
    const passed=Math.max(0,total-pending);
    const ai=scan.ai_review&&typeof scan.ai_review==='object'?scan.ai_review:{};
    const flagged=items.filter(item=>item.status==='risk'||ai[item.id]?.verdict==='risk').length;
    const unsaved=redlineUnsaved()?' 有未保存的人工覆盖。':'';
    return (scan.complete?`全部红线已确认${flagged?`（其中 ${flagged} 条仍有规则风险，导出将被拦截）`:''}。`
      :`共 ${total} 条红线，已过关 ${passed} 条，尚有 ${pending} 条待修复或 AI 核对。`)+unsaved;
  }
  function syncRedlineUi() {
    const status=document.querySelector('#wf-redline-status');
    if(status)status.textContent=redlineStatusText();
    const save=document.querySelector('[data-wf="save-redline"]');
    if(save)save.disabled=!!ui.busy||!redlineUnsaved();
    const adopt=document.querySelector('[data-wf="adopt-ai-redline"]');
    if(adopt){const count=aiPassedEligible().length;adopt.textContent=count?`采纳 AI 通过项（${count}）`:'采纳 AI 通过项';adopt.disabled=!!ui.busy||!count;}
  }
  function hasSavedChapters() {
    return !!ui.chat?.body_edition?.filled_chapter_count || (ui.editor?.chapters||[]).some(c=>String(c.text||'').trim())
      || Object.values(state.project?.chapter_texts||{}).some(text=>String(text||'').trim());
  }
  async function ensureRedlineAi({quiet=false}={}) {
    if(!hasProject()||ui.redlineAiBusy)return false;
    if(!hasSavedChapters())return false;
    const scan=ui.redline;
    if(scan?.ai_review&&!scan.ai_review_stale)return true;
    if(!ui.redlineAiOutboundAck){
      if(quiet)return false;
      if(!window.confirm('AI 逐条核对会把已保存的全部投标正文和材料核对摘要发送到本项目配置的模型服务。确认继续？'))return false;
      ui.redlineAiOutboundAck=true;
    }
    await reviewRedlineAi();
    return !!(ui.redline?.ai_review&&!ui.redline.ai_review_stale);
  }
  async function loadRedline({keepLocal=false}={}) {
    if(!hasProject()){ui.redline=null;ui.redlineError='';return;}
    // 红线读取使用独立序号，避免写作轮询或记录刷新把结果丢弃后一直停在“读取中”。
    const request=++redlineRequest, previous=keepLocal?ui.redline:null;
    try{
      const scan=await api(redlineUrl(state.project.id));
      if(request!==redlineRequest)return;
      bindRedline(scan,previous);
    }catch(err){
      if(request!==redlineRequest)return;
      ui.redline=null;ui.redlineError=err.message;
    }
  }
  async function ensureRedlineLoaded() {
    // 页面重绘时若红线面板可见但数据为空（例如保存目录会清空缓存），补发一次读取。
    if(ui.step!==5||ui.redline||ui.redlineError||redlineLoading)return;
    redlineLoading=true;
    try{
      await loadRedline();
      if(ui.redlineAiOutboundAck)await ensureRedlineAi({quiet:true});
    }
    finally{redlineLoading=false;}
    paintRedline();
  }
  async function reviewRedlineAi() {
    if(!hasProject()||ui.busy||ui.redlineAiBusy)return;
    if(!ui.redlineAiOutboundAck){
      if(!window.confirm('AI 逐条核对会把已保存的全部投标正文和材料核对摘要发送到本项目配置的模型服务。确认继续？'))return;
      ui.redlineAiOutboundAck=true;
    }
    const currentUi=ui;
    ui.redlineAiBusy=true;ui.error='';paint();
    try{
      if(ui.edits.size)await saveDrafts();
      const scan=await api(redlineUrl(state.project.id)+'/ai-review',{method:'POST',body:{}});
      if(ui!==currentUi)return;
      // A status read started before this review may still be in flight; it must not overwrite the result.
      redlineRequest++;
      bindRedline(scan,ui.redline);
      toast(scan.complete?'AI 红线核对完成，合格项已自动过关。'
        :`AI 红线核对完成；仍有 ${scan.pending_count||0} 条待修复材料、补正文或人工覆盖。`);
    }catch(err){
      if(ui!==currentUi)return;
      ui.error=err.message;toast(err.message,true);
    }finally{
      if(ui===currentUi){ui.redlineAiBusy=false;paint();}
    }
  }
  async function refreshRedline() {
    // 正文或材料保存后重新扫描，避免用过期的依据保存确认。
    if(ui.redline||ui.redlineError)await loadRedline({keepLocal:true});
  }
  async function saveRedline({quiet=false}={}) {
    if(!ui.redline)throw new Error('请先重新扫描废标红线。');
    if(ui.edits.size)await saveDrafts();
    if(!ui.redline)throw new Error(ui.redlineError||'请先重新扫描废标红线。');
    const acknowledged=Object.fromEntries(ui.redline.items.filter(item=>item.acknowledged&&(!item.auto_acknowledged||item.manually_acknowledged)).map(item=>[item.id,item.basis]));
    ui.busy='正在保存红线核对…';ui.error='';paint();
    try{
      const scan=await api(redlineUrl(state.project.id),{method:'POST',body:{
        mapping_revision:ui.redline.mapping_revision,review_revision:ui.redline.review_revision,acknowledged}});
      redlineRequest++;
      bindRedline(scan);
      if(!quiet)toast(ui.redline.complete?'废标红线已全部通过。':`已保存；仍有 ${ui.redline.pending_count} 条红线待确认。`);
    }catch(err){
      await loadRedline();
      throw err;
    }finally{ui.busy='';paint();}
  }
  function paintRedline() {
    // 只替换红线面板，避免整页重绘打断用户正在输入的其他字段。
    const panel=document.querySelector('#wf-redline-scan');
    if(panel&&state.view==='dashboard'&&ui.step===5)panel.outerHTML=redlinePanel();
  }
  function backgroundRedlineRefresh() {
    // 不阻塞当前操作：操作期间页面处于锁定状态，等待网络会让用户的其他输入被忽略。
    flushRedlineRefresh().then(paintRedline).catch(()=>{});
  }
  async function flushRedlineRefresh() {
    if(!redlineRefreshPending)return;
    redlineRefreshPending=false;
    await refreshRedline();
  }
  async function ensureRedlineForExport() {
    // 导出硬拦：须红线全部过关；有正文时先自动跑 AI。
    if(!hasProject())return true;
    await saveDrafts();
    if(!ui.redline&&!ui.redlineError)await loadRedline();
    else await refreshRedline();
    redlineRefreshPending=false;
    paint();
    if(ui.redlineError){
      toast('无法读取废标红线状态，已阻止导出：'+ui.redlineError,true);
      return false;
    }
    if(ui.redline?.items?.length){
      try{await ensureRedlineAi();}
      catch(err){toast('红线 AI 核对失败，已阻止导出：'+err.message,true);return false;}
      await refreshRedline();
      paint();
    }
    const scan=ui.redline;
    if(scan?.items?.length&&redlineUnsaved()){
      try{await saveRedline({quiet:true});}
      catch(err){toast('人工覆盖未能保存，已阻止导出：'+err.message,true);return false;}
    }
    const current=ui.redline;
    if(current?.items?.length&&!current.complete){
      toast(`尚有 ${current.pending_count||0} 条废标红线未过关，请补材料/正文或完成 AI 核对后再导出。`,true);
      document.querySelector('#wf-redline-scan')?.scrollIntoView({block:'start',behavior:'smooth'});
      return false;
    }
    const ai=current?.ai_review&&typeof current.ai_review==='object'?current.ai_review:{};
    const risks=(current?.items||[]).filter(item=>item.status==='risk'
      ||(!item.acknowledged&&['risk','unclear'].includes(ai[item.id]?.verdict)));
    if(risks.length){
      toast(`尚有 ${risks.length} 条废标红线存在风险或需确认，已阻止导出。`,true);
      document.querySelector('#wf-redline-scan')?.scrollIntoView({block:'start',behavior:'smooth'});
      return false;
    }
    if(current?.items?.length&&!Object.keys(ai).length&&hasSavedChapters()){
      toast('请先完成废标红线 AI 逐条核对后再导出。',true);
      return false;
    }
    return true;
  }
  async function loadOutlineJob(kind, start=false) {
    const recordId=ui.record?.id, currentUi=ui;
    if(!recordId)return;
    clearTimeout(outlineTimer);
    const field=kind==='automatic'?'automatic':'calibration';
    try {
      const result=await api(base(recordId)+'/'+kind,start?{method:'POST',body:{}}:{});
      if(ui!==currentUi||ui.record?.id!==recordId)return;
      ui[field]=result;
    } catch(err) {
      if(start)throw err;
      if(ui!==currentUi||ui.record?.id!==recordId)return;
      ui[field]={status:'failed',error:err.message};
    }
    if(ui!==currentUi||ui.record?.id!==recordId)return;
    if(kind==='automatic'){
      const host=$('#wf-automatic-outline');if(host)host.innerHTML=automaticControls();
    }else if(ui.calibrationOpen)paint();
    if(ui[field].status==='running'){
      outlineTimer=setTimeout(()=>{
        if(ui===currentUi&&ui.record?.id===recordId&&state.view==='dashboard'&&
            (kind==='calibration'?ui.calibrationOpen:ui.step===3&&!ui.calibrationOpen)){
          loadOutlineJob(kind).catch(error);
        }
      },1700);
    }
  }
  async function replaceOutline(payload) {
    if(ui.outlineDirty&&!window.confirm('更换目录将放弃本页未保存的目录修改。继续？'))return;
    if(hasProject()&&!window.confirm('更换目录将创建新的工作稿，当前稿会归入历史。继续？'))return;
    ui.busy='正在准备整本预览…';ui.error='';paint();
    try{
      const record=await api(base(ui.record.id)+'/outline',{method:'PATCH',body:{revision:ui.record.revision,...payload}});
      bindRecord(record);ui.outlineText='';ui.outlineEditing=!!record.outline_missing?.length;
      toast(record.outline_mode==='custom'?'自定义目录已导入。':'AI 目录已保存。');
    }finally{ui.busy='';paint();}
  }
  async function importOutline(file) {
    if(!file)return;
    if(!/\.docx$/i.test(file.name)||!file.size||file.size>30*1024*1024)throw new Error('请选择 30 MB 以内的 DOCX 目录。');
    const [docx]=await toFiles([file]);
    await replaceOutline({docx});
  }
  function deleteOutlineRows(keys) {
    const removed=new Set();
    ui.outlineDraft.forEach((row,i)=>{
      if(keys.has(row._key))for(const node of ui.outlineDraft.slice(i,treeEnd(i)))removed.add(node._key);
    });
    if(removed.size>=ui.outlineDraft.length)throw new Error('请至少保留一个目录章节。');
    const ids=ui.outlineDraft.filter(row=>removed.has(row._key)&&row.id).map(row=>row.id);
    const used=hasProject()&&(state.project.mappings.some(row=>ids.includes(row.chapter_id)&&row.confirmed)
      ||state.project.written_chapter_ids?.some(id=>ids.includes(id)));
    if((used||!ui.record.outline_mode)&&!window.confirm('删除所选章节及下属章节？已填写内容可从历史稿查看。'))return;
    ui.outlineDraft=ui.outlineDraft.filter(row=>!removed.has(row._key));ui.outlineSelection.clear();
    ui.outlineKey=ui.outlineDraft.find(row=>row._key===ui.outlineKey)?._key||ui.outlineDraft[0]._key;
    ui.outlineDirty=true;paint();
  }
  function addOutlineBatch(child) {
    const lines=$('#wf-outline-batch').value.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
    if(!lines.length||lines.some(line=>line.length>220)||ui.outlineDraft.length+lines.length>1000)throw new Error('请逐行输入章节标题，目录总数不超过 1000。');
    const index=ui.outlineDraft.findIndex(row=>row._key===ui.outlineKey);
    const level=(ui.outlineDraft[index]?.level||1)+(child?1:0);
    if(level>9)return;
    const at=index<0?ui.outlineDraft.length:treeEnd(index);
    const items=lines.map(text=>({id:'',_key:newKey(),text,level,requirement_ids:[]}));
    ui.outlineDraft.splice(at,0,...items);ui.outlineDirty=true;ui.outlineKey=items[0]._key;paint();
  }
  function restoreOutlineForm(sourceIndex) {
    const row=ui.record.outline_missing.find(item=>item.source_index===sourceIndex);
    if(!row||ui.outlineDraft.some(item=>item.form_source_index===sourceIndex))return;
    let index=ui.outlineDraft.findIndex(item=>item.source_index>sourceIndex&&item.level<=row.level);
    if(index<0)index=ui.outlineDraft.length;
    const level=Math.min(row.level,(ui.outlineDraft[index-1]?.level||0)+1);
    const item={id:'',_key:newKey(),text:row.text,level,source_index:sourceIndex,form_source_index:sourceIndex,locked_format:true};
    ui.outlineDraft.splice(index,0,item);ui.outlineKey=item._key;ui.outlineEditing=true;ui.outlineDirty=true;paint();
  }
  async function saveOutline() {
    if(!editable())throw new Error('历史目录不能在线修订，请重新上传采购原件。');
    if(!ui.outlineDraft.length||ui.outlineDraft.some(row=>!row.text.trim()))throw new Error('目录不能为空，且每个章节须填写名称。');
    const items=ui.outlineDraft.map(({id,text,level,form_source_index})=>({id:id||'',text,level,form_source_index}));
    ui.busy='正在保存目录并更新预览…';ui.error='';paint();
    try{
      const record=await api(base(ui.record.id)+'/outline',{method:'PATCH',body:{revision:ui.record.revision,items,form_assignments:ui.formAssignments}});
      bindRecord(record);toast('目录及整本文件已更新。');
    }finally{ui.busy='';paint();}
  }
  function editOutline(action) {
    const i=ui.outlineDraft.findIndex(row=>row._key===ui.outlineKey), row=ui.outlineDraft[i];
    if(action==='outline-add-sibling'||action==='outline-add-child'){
      if(action==='outline-add-child'&&(!row||row.level>=9))return;
      const item={id:'',_key:newKey(),text:'新增章节',level:row?row.level+(action==='outline-add-child'?1:0):1,requirement_ids:[]};
      ui.outlineDraft.splice(row?treeEnd(i):ui.outlineDraft.length,0,item);ui.outlineKey=item._key;
    }else{
      if(!row)return;
      const end=treeEnd(i), count=end-i, previous=previousSibling(i);
      if(action==='outline-delete'){
        return deleteOutlineRows(new Set([row._key]));
      }else if(action==='outline-up'){
        if(previous<0)return;
        const moved=ui.outlineDraft.splice(i,count);ui.outlineDraft.splice(previous,0,...moved);
      }else if(action==='outline-down'){
        if(end>=ui.outlineDraft.length||ui.outlineDraft[end].level!==row.level)return;
        const nextEnd=treeEnd(end),moved=ui.outlineDraft.splice(i,count);ui.outlineDraft.splice(nextEnd-count,0,...moved);
      }else if(action==='outline-indent'){
        if(previous<0||Math.max(...ui.outlineDraft.slice(i,end).map(n=>n.level))>=9)return;
        for(const node of ui.outlineDraft.slice(i,end))node.level++;
      }else if(action==='outline-outdent'){
        if(row.level<=1)return;
        let parent=i-1;while(parent>=0&&ui.outlineDraft[parent].level>=row.level)parent--;
        if(parent<0)return;
        const parentEnd=treeEnd(parent),moved=ui.outlineDraft.splice(i,count);
        for(const node of moved)node.level--;
        ui.outlineDraft.splice(parentEnd-count,0,...moved);
      }else return;
    }
    ui.outlineDirty=true;paint();
    if(action.startsWith('outline-add'))$('#wf-outline-title')?.select();
  }
  async function beforeLeave() {
    if(state.view!=='dashboard')return;
    if(ui.busy||ui.pending)throw new Error('请等待当前操作完成。');
    if(!discardReviewEdits())throw new Error('已保留未保存修改，请保存后继续。');
    await saveDrafts();
    if(ui.redline&&redlineUnsaved())await saveRedline({quiet:true});
    stopWritingPoll();
  }
  function guideWorkflowCompletion(step, message, selector) {
    ui.step=step;ui.error=message;paint();toast(message);
    // The click handler releases the workspace's inert state after navigation.
    setTimeout(()=>{
      const target=document.querySelector(selector);
      target?.scrollIntoView({block:'center',behavior:'smooth'});
      target?.focus({preventScroll:true});
    },0);
  }
  async function setStep(n) {
    if(!Number.isInteger(n)||n<2||n>5)return;
    if(ui.busy||ui.savingDrafts||(n===5&&state.busy))return;
    const blocker=earliestBlocker(n);
    if(blocker){
      if(blocker.outlineEditing)ui.outlineEditing=true;
      if(blocker.material){
        ui.step=4;
        if(projectUi.step==='export')projectUi.step=commonReviewed()?'common':'special';
        if(await guideMaterialCompletion('export'))return;
        await saveDrafts();
        ui.step=5;ui.error='';
        await readChat();
        paint(true);return;
      }
      guideWorkflowCompletion(blocker.step,blocker.message,blocker.selector);
      return;
    }
    if(n!==ui.step&&!discardReviewEdits())return;
    ui.calibrationOpen=false;
    await saveDrafts();
    if(state.dirty)await saveProject(false);
    if(n===5){
      ui.step=5;ui.error='';
      await readChat();
      paint(true);return;
    }
    if(n===4 && projectUi.step==='export')projectUi.step=commonReviewed()?'common':'special';
    ui.step=n;ui.error='';paint(true);
  }
  function error(err) {
    ui.error=err.message;paint();document.querySelector('.wf-error')?.scrollIntoView({block:'nearest'});
    toast(err.message,true);
  }
  async function action(target) {
    const action=target.dataset.wf, id=target.dataset.id;
    if (action==='upload')return $('#wf-upload').click();
    if (action==='pick-outline')return $('#wf-outline-upload').click();
    if (action==='generate-outline')return loadOutlineJob('automatic',true);
    if (action==='use-ai-outline')return replaceOutline({automatic:true});
    if (action==='use-pasted-outline')return replaceOutline({text:ui.outlineText});
    if (action==='restore-outline-form')return restoreOutlineForm(Number(id));
    if (action==='add-outline-batch')return addOutlineBatch(id==='child');
    if (action==='delete-outline-selection')return deleteOutlineRows(ui.outlineSelection);
    if (action==='project-main'){ui.calibrationOpen=false;paint();if(ui.step===3)return loadOutlineJob('automatic');return;}
    if (action==='rerun-calibration')return loadOutlineJob('calibration',true);
    if (action==='stage-preview')return showStagePreview();
    if (action==='formal-export'&&!isRemote()){
      if(ui.busy)return;
      if(redlineUnsaved())await saveRedline({quiet:true});
      const project=await api('/api/projects/'+encodeURIComponent(state.project.id));
      state.project=project;
      await queueExport(project.id,id,project.mapping_revision);
      return;
    }
    if (action==='edit-saved-chapters'||action==='redline-chapter'){
      const key=state.project.id,editor=await api(draftsUrl(key));
      const row=editor.chapters.find(item=>item.id===id)||editor.chapters[0];
      if(!row)return;
      openModal('编辑已保存正文','',`<label>章节<select name="chapter">${editor.chapters.map(item=>`<option value="${esc(item.id)}" ${item.id===row.id?'selected':''}>${esc(item.title)}</option>`).join('')}</select></label><label>正文<textarea class="input" name="text" rows="14" maxlength="50000">${esc(row.text)}</textarea></label>`,'wf-saved-chapter','保存正文');
      const form=$('#active-modal form');
      form.elements.chapter.onchange=()=>{form.elements.text.value=editor.chapters.find(item=>item.id===form.elements.chapter.value)?.text||'';};
      form.onsubmit=async event=>{
        event.preventDefault();event.stopPropagation();
        const submit=form.querySelector('button[type=submit]');submit.disabled=true;
        try{
          await api(draftsUrl(key),{method:'PATCH',body:{revision:editor.revision,mapping_revision:ui.chat?.mapping_revision||state.project.mapping_revision,chapters:[{id:form.elements.chapter.value,text:form.elements.text.value}]}});
          submit.disabled=false;closeModal();
          // Confirm the save at once; a later toast (e.g. an export blocked by redlines) must not be overwritten by it.
          toast('正文已保存。');await readChat();
        }catch(err){toast(err.message,true);}
        finally{submit.disabled=false;}
      };
      return;
    }
    if (action==='chat-upload')return $('#wf-chat-upload').click();
    if (action==='chat-send')return chatSend();
    if (action==='chat-shorten')return chatSend(id);
    if (action==='chat-preview'){
      if(ui.chatPreview?.id===id){ui.chatPreview=null;paint();return;}
      return openChatPreview(id);
    }
    if (action==='chat-adopt')return adoptChatChapter(id);
    if (action==='chat-retry'){
      ui.chatMessage=ui.chat?.turns.find(turn=>turn.id===id)?.business_notes||'';
      paintChat();$('#wf-chat-message')?.focus();return;
    }
    if (action==='upload-cases')return $('#wf-case-upload').click();
    if (action==='reload-writing'){await loadWriting();paint();return;}
    if (action==='select-edition')return selectWritingEdition(id);
    if (action==='review-generation'){reviewGeneration(id);return;}
    if (action==='reload-redline'){await loadRedline();paint();return;}
    if (action==='save-redline')return saveRedline();
    if (action==='adopt-ai-redline')return adoptAiRedline();
    if (action==='ai-redline')return reviewRedlineAi();
    if (action==='use-library-cases')return useLibraryCases();
    if (action==='generate-writing')return generateWriting();
    if (action==='generate-chapter-writing')return generateWriting(true);
    if (action==='accept-writing'||action==='accept-all-writing')return acceptWriting(action==='accept-all-writing');
    if (action==='metric'){
      if(id!=='redline'){
        if(!editable())return window.open(html().url+'#'+encodeURIComponent(id),'_blank','noopener');
        return;
      }
      if(!editable())return window.open(html().url+'#'+encodeURIComponent(id),'_blank','noopener');
      ui.requirementEditing=false;ui.requirementCategory='redline';ui.requirementKey=ui.requirements.find(item=>item.category==='redline')?._key||'';ui.sourceQuery='';paint();
      document.querySelector('.wf-review')?.scrollIntoView({block:'start',behavior:'smooth'});return;
    }
    if (action==='to-outline')return setStep(3);
    if (action==='step')return setStep(Number(id));
    if (action==='new')return enter();
    if (action==='confirm') {
      if(ui.outlineDirty)throw new Error('请先保存目录并核对更新后的预览。');
      if(ui.record.requirements_confirmed===false)throw new Error('请先确认采购要求。');
      if(!ui.previewLoaded)throw new Error('请等待整本预览加载完成。');
      if(ui.record.outline_mode!=='custom'&&ui.record.outline_missing?.length)throw new Error('请先处理待关联的采购原表。');
      if(state.dirty)await saveProject(false);
      const project=await api(base(ui.record.id)+'/confirm',{method:'POST',body:{artifact_id:'word',revision:ui.record.revision}});
      if(project.framework_id!==ui.record.id||project.framework_artifact_id!=='word')throw new Error('未能取得此框架的整本项目。');
      state.project=project;state.dirty=false;
      state.data.projects=[project,...state.data.projects.filter(p=>p.id!==project.id)];
      ui.record.projects={word:project.id};
      resetProjectUi();projectUi.step='special';await loadProjectPickers();
      ui.step=4;paint(true);return;
    }
    if(action==='select-requirement'){ui.requirementEditing=false;ui.requirementKey=id;ui.sourceQuery='';paint();return;}
    if(action==='add-requirement'){
      const item={id:'',_key:newKey(),category:'redline',label:'',text:'',scope:'',references:[],note:''};
      ui.requirementEditing=true;ui.requirementCategory='redline';ui.requirements.push(item);ui.requirementKey=item._key;ui.requirementsDirty=true;ui.sourceQuery='';paint();$('#wf-requirement-label')?.focus();return;
    }
    if(action==='delete-requirement'){
      if(!window.confirm('删除本条废标红线？请确认其不适用或属于重复识别。'))return;
      ui.requirements=ui.requirements.filter(item=>item._key!==id);ui.requirementKey=ui.requirements.find(item=>item.category==='redline')?._key||'';ui.requirementsDirty=true;paint();return;
    }
    if(action==='add-reference'){
      const source=ui.record.sources.find(item=>item.id===$('#wf-requirement-source').value),item=requirement();
      if(!source||!item)throw new Error('请先选择一处采购原文。');
      if(!item.references.some(ref=>(ref.source_id||ref.id)===source.id))item.references.push({...clone(source),source_id:source.id});
      if(!item.text.trim())item.text=source.text;
      if(!item.label.trim())item.label=source.text.replace(/\s+/g,' ').slice(0,100);
      ui.requirementsDirty=true;paint();return;
    }
    if(action==='remove-reference'){
      const item=requirement();if(!item||item.references.length<=1)return;
      item.references.splice(Number(id),1);ui.requirementsDirty=true;paint();return;
    }
    if(action==='save-requirements'){await saveRequirements(false);return;}
    if(action==='confirm-requirements'){await saveRequirements(true);return;}
    if(action==='select-outline'){ui.outlineEditing=true;ui.outlineKey=id;paint();return;}
    if(action.startsWith('outline-')){editOutline(action);return;}
    if(action==='save-outline'){await saveOutline();return;}
    if(action==='reload-review'){
      if(reviewDirty()&&!window.confirm('放弃尚未保存的要求和目录修改，重新读取最新版本？'))return;
      const step=ui.step;await restore(ui.record.id,step);ui.error='';paint();return;
    }
    if(action==='remove'){
      if(!window.confirm('删除这次上传的采购文件及其生成的框架？'))return;
      await api(base(ui.record.id)+'/remove',{method:'POST',body:{}});
      ui.library=ui.library.filter(r=>r.id!==ui.record.id);ui.record=null;paint();return;
    }
    if(action==='archive'){await resumeProject(await api(projectUrl(id)));return;}
    if(action==='resume'){await restore(id);paint(true);return;}
    if(action==='reload-editor'){
      if(ui.edits.size&&!window.confirm('放弃本页尚未保存的正文修改，重新读取已存版本？'))return;
      await loadEditor();ui.edits.clear();paint();return;
    }
    if(action==='save'){await saveDrafts();paint();backgroundRedlineRefresh();return;}
    if(action==='redline-chapter'){
      if(ui.editor?.chapters?.some(c=>c.id===id)){ui.chapter=id;paint();document.querySelector('#wf-body')?.scrollIntoView({block:'center'});document.querySelector('#wf-body')?.focus({preventScroll:true});}
      return;
    }
    if(action==='export'){await showStagePreview();return;}
  }
  function cleanEmbeddedOutline(contents) {
    // Only remove known display notes in the embedded copy; source HTML stays intact.
    for(const note of contents.querySelectorAll('p.desc,p.wf-help')){
      const text=note.textContent.trim();
      if(text==='按采购原文分册'||text==='采购原文未要求商务标、技术标分册，保留统一目录；营业执照与公司介绍相邻排列。')note.remove();
      else if(text==='按内容编排，递交方式以采购原文为准')note.textContent='递交方式以采购原文为准';
    }
    const outline=hierarchy(ui.record.outline||[]);
    let cursor=0;
    for(const item of contents.querySelectorAll('ul.outline li')){
      if(item.querySelector(':scope > ul'))continue;
      const title=headingTitle(item.textContent);
      const i=outline.findIndex((row,index)=>index>=cursor&&row.title===title);
      if(i<0)continue;
      cursor=i+1;
      item.textContent=outline[i].number+' '+outline[i].title;
    }
  }
  document.addEventListener('load',e=>{
    const frame=e.target;
    if(!frame.matches?.('.wf-preview-frame'))return;
    const panel=frame.closest('.wf-card');
    const status=panel.querySelector('.wf-preview-state'), confirm=panel.querySelector('[data-wf="confirm"]');
    const report=frame.contentDocument;
    if(!report?.querySelector('main#top #redline')){
      status.textContent='预览未能载入，请返回招标文件解读，重新打开本项目。';
      return;
    }
    const contents=report.querySelector('ul.outline')?.closest('details');
    if(!contents){status.textContent='目录预览不完整，请重新打开本项目。';return;}
    cleanEmbeddedOutline(contents);
    // This step shows the directory only; the full source report remains in Downloads.
    const main=report.querySelector('main#top');
    main.replaceChildren(contents);contents.open=true;
    const style=report.createElement('style');
    style.textContent='body{background:#fff!important}main#top{max-width:none;margin:0;padding:20px}main#top>details{margin:0;border:0;box-shadow:none}main#top>details>summary{display:none}';
    report.head?.append(style);
    frame.style.height=Math.min(Math.max(contents.getBoundingClientRect().height+60,260),Math.max(350,innerHeight*.65))+'px';
    status.hidden=true;ui.previewLoaded=true;confirm.disabled=ui.outlineDirty||ui.record.requirements_confirmed===false||!!ui.busy||outlineMissingBlocked();
  },true);
  document.addEventListener('click',async e=>{
    const target=e.target.closest('[data-wf]');if(!target||target.disabled||ui.busy)return;
    const actionName=target.dataset.wf;
    if(['upload','upload-cases','chat-upload','metric','new'].includes(actionName)){
      try{await action(target);}catch(err){error(err);}return;
    }
    // Disable the clicked control until state is refreshed, including double-clicked confirm.
    if(ui.pending)return;
    ui.pending=true;ui.error='';target.disabled=true;
    const panel=target.closest('.wf-workspace');
    if(panel)panel.inert=true;
    try{await action(target);}catch(err){error(err);}
    finally{ui.pending=false;const current=document.querySelector('.wf-workspace');if(current)current.inert=!!(ui.busy||ui.savingDrafts);if(target.isConnected)target.disabled=false;syncReviewStatus();if(actionName.includes('writing'))updateWritingPanels();}
  });
  document.addEventListener('toggle',e=>{
    if(!e.target.isConnected)return;
    if(e.target.matches('.wf-outline-editor'))ui.outlineEditing=e.target.open;
    if(e.target.matches('.wf-library-cases'))ui.libraryOpen=e.target.open;
    if(e.target.matches('.wf-requirement-edit')){
      const wasEditing=ui.requirementEditing;ui.requirementEditing=e.target.open;
      if(wasEditing&&!e.target.open)paint();
    }
  },true);
  document.addEventListener('change',async e=>{
    const el=e.target;
    try{
      if(el.id==='wf-upload')await upload(el.files?.[0]);
      if(el.id==='wf-outline-upload')await importOutline(el.files?.[0]);
      if(el.id==='wf-calibration-row'){ui.calibrationRow=el.value;paint();$('#wf-calibration-row')?.focus();return;}
      if(el.dataset.outlineSelect){
        if(el.checked)ui.outlineSelection.add(el.dataset.outlineSelect);else ui.outlineSelection.delete(el.dataset.outlineSelect);
        const remove=$('[data-wf="delete-outline-selection"]');if(remove)remove.disabled=!ui.outlineSelection.size;
        return;
      }
      if(el.dataset.formAssignment){
        ui.formAssignments[el.dataset.formAssignment]=el.value;ui.outlineDirty=true;
        const save=$('[data-wf="save-outline"]'), confirm=$('[data-wf="confirm"]');
        if(save)save.disabled=false;if(confirm)confirm.disabled=true;
        return;
      }
      if(el.id==='wf-chat-upload'){await chatUpload([...el.files]);return;}
      if(el.id==='wf-case-upload'){await uploadCases(el.files||[]);el.value='';}
      if(el.id==='wf-save-case'){ui.saveCase=el.checked;paint();if(ui.saveCase)$('#wf-case-name')?.focus();return;}
      if(el.name==='wf-library-case'){ui.librarySelection=new Set([...document.querySelectorAll('input[name="wf-library-case"]:checked')].map(x=>x.value));return;}
      if(el.id==='wf-chapter'){ui.chapter=el.value;paint();}
      if(el.id==='wf-writing-model'){
        ui.writingModel=el.value;
        try{localStorage.setItem('bid-writing-model',ui.writingModel);}catch(_){/* ignore quota / private mode */}
        return;
      }
      if(el.id==='wf-requirement-category'&&requirement()){requirement().category='redline';ui.requirementCategory='redline';ui.requirementsDirty=true;paint();}
      if(el.id==='wf-notes-ack'){ui.notesAcknowledged=el.checked;return;}
      if(el.matches?.('[data-wf="ack-removed"]')){
        if(el.checked)ui.removedAcknowledged.add(el.dataset.id);else ui.removedAcknowledged.delete(el.dataset.id);
        return;
      }
      if(el.id==='wf-redline-pending-only'){
        ui.redlinePendingOnly=el.checked;
        paintRedline();
        return;
      }
      if(el.matches?.('[data-wf="ack-redline"]')&&ui.redline?.items){
        const item=ui.redline.items.find(row=>row.id===el.dataset.id);
        if(!item)return;
        item.acknowledged=el.checked;
        syncRedlineCounts();
        if(ui.redlinePendingOnly){paintRedline();return;}
        const card=el.closest?.('.wf-redline-item');
        card?.classList.toggle('is-passed',el.checked);
        syncRedlineUi();
        return;
      }
    }catch(err){error(err);}
  });
  document.addEventListener('keydown',event=>{
    if(event.target.id==='wf-chat-message'&&event.key==='Enter'&&!event.shiftKey&&!event.isComposing){
      event.preventDefault();
      chatSend().catch(error);
    }
  });
  document.addEventListener('input',e=>{
    if(e.target.id==='wf-chat-message'){ui.chatMessage=e.target.value;return;}
    if(e.target.id==='wf-name')ui.name=e.target.value;
    if(e.target.id==='wf-outline-text')ui.outlineText=e.target.value;
    if(e.target.id==='wf-case-name')ui.caseName=e.target.value;
    if(e.target.id==='wf-business-notes'){
      ui.businessNotes=e.target.value;
      ui.businessNotesDirty=ui.businessNotes!==(ui.writing?.job?.business_notes||'');
      const status=$('#wf-business-notes-status');if(status)status.hidden=!ui.businessNotesDirty;
    }
    const key={'wf-requirement-label':'label','wf-requirement-text':'text','wf-requirement-scope':'scope','wf-requirement-note':'note'}[e.target.id];
    if(key&&requirement()){
      requirement()[key]=e.target.value;ui.requirementsDirty=true;syncReviewStatus();
      if(key==='label'||key==='text'){const choice=[...document.querySelectorAll('[data-wf=select-requirement]')].find(el=>el.dataset.id===ui.requirementKey);if(choice)choice.lastElementChild.textContent=requirement().label||requirement().text||'新增要求';}
    }
    if(e.target.id==='wf-outline-title'){
      const row=ui.outlineDraft.find(item=>item._key===ui.outlineKey);if(row){row.text=e.target.value;ui.outlineDirty=true;syncReviewStatus();const choice=[...document.querySelectorAll('[data-wf=select-outline]')].find(el=>el.dataset.id===ui.outlineKey);if(choice)choice.lastElementChild.textContent=headingTitle(row.text)||'新增章节';}
    }
    if(e.target.id==='wf-source-query'){
      ui.sourceQuery=e.target.value;
      const matches=(ui.record.sources||[]).filter(sourceMatches),sources=matches.slice(0,100);
      $('#wf-requirement-source').innerHTML='<option value="">请选择原文位置</option>'+sources.map(source=>`<option value="${esc(source.id)}">${esc(sourceLabel(source))}</option>`).join('');
      const count=$('#wf-source-count');count.hidden=matches.length<=sources.length;
      count.textContent=count.hidden?'':`共 ${matches.length} 条匹配原文，仅显示前 ${sources.length} 条。`;
    }
    if(e.target.id==='wf-body'){
      const chapter=ui.editor?.chapters.find(c=>c.id===ui.chapter);if(!chapter)return;
      const wasDirty=!!ui.edits.size;
      if(e.target.value===(chapter.text||''))ui.edits.delete(chapter.id);else ui.edits.set(chapter.id,e.target.value);
      $('#wf-save-status').textContent=ui.edits.size?'有未保存修改':'已保存';
      if(wasDirty!==!!ui.edits.size)updateWritingPanels();
    }
  });
  for(const event of ['dragover','dragleave','drop'])document.addEventListener(event,async e=>{
    const zone=e.target.closest('.wf-dropzone');if(!zone)return;
    e.preventDefault();
    zone.classList.toggle('is-dragging',event==='dragover'&&!zone.disabled);
    if(event==='drop'&&!zone.disabled){
      try{
        if(zone.matches('.wf-case-dropzone'))await uploadCases(e.dataTransfer.files);
        else{
          if(e.dataTransfer.files.length!==1)throw new Error('每次解析一份采购文件，请选择一个文件。');
          await upload(e.dataTransfer.files[0]);
        }
      }catch(err){error(err);}
    }
  });
  window.addEventListener('beforeunload',e=>{
    if(ui.edits.size||reviewDirty()||ui.busy||ui.businessNotesDirty||redlineUnsaved()){e.preventDefault();e.returnValue='';}
  });
  function projectArchives() {
    return archiveLinks(ui.library.flatMap(r=>r.legacy_projects||[]));
  }
  function forgetExport(id){
    if(ui.archive?.project?.exports)ui.archive.project.exports=ui.archive.project.exports.filter(item=>item.id!==id);
  }
  return {view,enter,url,resumeProject,beforeLeave,setStep,projectArchives,isPreview:()=>ui.step===3&&!ui.archive&&!ui.calibrationOpen,calibrationState,loadLibrary,calibrationProjects,openCalibration,ensureRedlineForExport,forgetExport};
})();
