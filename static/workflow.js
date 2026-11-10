'use strict';

// The preparation workspace uses the same project and matching controls as the material library.
const tenderFlow = (() => {
  // Keep stored step IDs and old links compatible while combining setup with upload.
  const steps = ['招标文件解读', '目录编写', '系统填充', '编写投标文件'];
  const categories = [
    ['redline','risk','废标红线条款','条'], ['qualification','qualification','强制资格要求','项'],
    ['score','score','评分细则要点','条'], ['spec','spec','商务技术规范','项'],
    ['format','format','指定格式','项']
  ];
  const fresh = () => ({
    step:1, name:'', record:null, library:[], archive:null, busy:'', error:'',
    editor:null, chapter:'', edits:new Map(), writing:null, writingError:'', businessNotes:'', businessNotesDirty:false, requirements:[], requirementKey:'', requirementCategory:'redline',
    requirementEditing:false, outlineEditing:false, requirementsDirty:false, outlineDraft:[], outlineKey:'', outlineDirty:false, previewLoaded:false, sourceQuery:'', draftSequence:0,
    notesAcknowledged:false, removedAcknowledged:new Set(), redline:null, redlineError:'', outlineCheckAck:false,
    librarySelection:null, libraryOpen:false
  });
  let ui = fresh();
  let writingTimer=null, writingEpoch=0, writingRequest=0, redlineRefreshPending=false;
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
    if (n===3) return !!ui.record && ui.record.requirements_confirmed!==false;
    if (n===4) return hasProject() && ui.record.requirements_confirmed!==false;
    return hasProject() && ui.record.requirements_confirmed!==false && projectReviewed();
  }
  function stepper() {
    return `<nav class="wf-stepper" aria-label="投标文件编制步骤">${steps.map((label,i)=>{
      const n=i+2, current=Math.max(2,ui.step);
      return `<button type="button" data-wf="step" data-id="${n}" class="wf-step ${n===current?'is-current':n<current?'is-done':''}" ${n===current?'aria-current="step"':''} ${(n!==5&&!available(n))||ui.busy?'disabled':''}><span class="wf-number">${i+1}</span><span class="wf-label">${label}</span></button>`;
    }).join('')}</nav>`;
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
    const cards = categories.map(([key,kind,label,unit])=>{
      const metric=r?.metrics?.find(m=>m.key===key), number=r?.review_editable?ui.requirements.filter(item=>item.category===key).length:metric?.count;
      return `<button type="button" class="wf-metric" data-wf="metric" data-id="${key}" data-kind="${kind}" ${!r||!metric?'disabled':''} aria-pressed="${ui.requirementCategory===key}" aria-label="${label}，${metric?number+unit+'，核对条款':'待解析'}"><span class="wf-metric-label">${label}</span><strong class="wf-metric-count">${metric?esc(number)+`<small>${unit}</small>`:'—'}</strong></button>`;
    }).join('');
    return title('招标文件解读')+
      (r?'':drop)+`<section class="wf-files">${list}</section>
      <div class="wf-metrics" aria-label="五类要求">${cards}</div>`+coverage()+requirementReview()+confirmationChecklist()+downloads()+
      (needsRequirementsConfirm()?actions(0,'confirm-requirements','保存并确认要求，生成目录',!!r):actions(0,'to-outline','下一步',!!r));
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
      <p class="wf-help">删除资格/格式要求会去掉对应响应栏目；待核对事项未勾选也可能漏掉材料位置。</p>
      ${notes.length?`<label class="wf-ack"><input type="checkbox" id="wf-notes-ack" ${ui.notesAcknowledged?'checked':''}>我已逐条核对待核对事项（${notes.length}），确认未遗漏必交材料与响应栏目</label>`:''}
      ${removed.length?`<div class="wf-removed-list"><strong>已删除的资格/格式要求（${removed.length}）</strong>${removed.map(row=>`<label class="wf-ack"><input type="checkbox" data-wf="ack-removed" data-id="${esc(row.id)}" ${ui.removedAcknowledged.has(row.id)?'checked':''}>确认删除：${esc(row.label||row.text||row.id)}</label>`).join('')}</div>`:''}
    </section>`;
  }
  function hierarchy(rows) {
    const parents=[], counts=[];
    return rows.map(row=>{
      const level=Math.max(1,Number(row.level)||1);
      while(parents.length && parents[parents.length-1].level>=level)parents.pop();
      const depth=parents.length;
      counts.length=depth+1;counts[depth]=(counts[depth]||0)+1;
      parents.push({level,title:row.title??row.text});
      return {...row,depth,number:counts.join('.'),path:parents.map(p=>p.title).join(' / ')};
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
    const rows=ui.requirements.filter(item=>item.category===ui.requirementCategory), selected=requirement();
    const label=categories.find(c=>c[0]===ui.requirementCategory)?.[2]||'采购要求';
    const references=selected?.references||[];
    const matchingSources=(ui.record.sources||[]).filter(sourceMatches), sources=matchingSources.slice(0,100);
    return `<section class="wf-section wf-review" aria-label="采购要求核对"><div class="wf-review-heading"><h2 class="wf-section-title">${esc(label)} · ${rows.length}</h2><div class="wf-file-actions">${wfButton('新增要求','add-requirement',{subtle:true})}${wfButton('保存修改','save-requirements',{subtle:true,disabled:!ui.requirementsDirty})}${ui.error?wfButton('重新读取','reload-review',{subtle:true}):''}</div></div>
      <p class="wf-help" id="wf-requirements-status" role="status">${ui.requirementsDirty?'有未保存修改；保存后须重新确认':ui.record.requirements_confirmed?'要求已确认':'要求待确认'}</p>
      <div class="wf-review-grid"><div class="wf-requirement-list" aria-label="要求列表">${rows.map((item,i)=>`<button type="button" class="wf-requirement-choice ${item._key===ui.requirementKey?'is-selected':''}" data-wf="select-requirement" data-id="${esc(item._key)}" aria-pressed="${item._key===ui.requirementKey}"><span>${i+1}</span><span>${esc(item.label||item.text||'新增要求')}</span></button>`).join('')||'<p class="wf-empty">未识别到条目</p>'}</div>
      <div class="wf-requirement-editor">${selected?`<div class="wf-requirement-reading"><h3>${esc(selected.label||'要求内容')}</h3><p>${esc(selected.text||'请填写要求内容')}</p>${selected.scope?`<p class="wf-help">适用范围：${esc(selected.scope)}</p>`:''}${selected.note?`<p class="wf-help">备注：${esc(selected.note)}</p>`:''}</div>
      <details class="wf-source-evidence" open><summary>采购原文依据（${references.length} 处）</summary>${references.map(ref=>`<div class="wf-source-reference"><p class="wf-help">${esc([ref.part,ref.context,ref.locator].filter(Boolean).join(' · '))}</p><blockquote>${esc(ref.original_text||ref.text||'')}</blockquote></div>`).join('')||'<p class="wf-note">新增要求须选择采购原文出处。</p>'}</details>
      <details class="wf-requirement-edit" ${ui.requirementEditing?'open':''}><summary>修改解读</summary><div class="wf-review-fields"><label class="wf-field">类别<select class="input" id="wf-requirement-category">${categories.map(([key,,name])=>`<option value="${key}" ${selected.category===key?'selected':''}>${name}</option>`).join('')}</select></label><label class="wf-field">适用范围<input class="input" id="wf-requirement-scope" maxlength="200" value="${esc(selected.scope||'')}" placeholder="例如：包件二；共同适用"></label></div>
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
      <div class="wf-outline-grid"><div class="wf-outline-list" aria-label="可编辑目录">${hierarchy(ui.outlineDraft).map(row=>`<button type="button" class="wf-outline-choice ${row._key===ui.outlineKey?'is-selected':''}" style="--depth:${Math.min(8,row.level-1)}" data-wf="select-outline" data-id="${esc(row._key)}" aria-pressed="${row._key===ui.outlineKey}"><span class="wf-outline-number">${esc(row.number)}</span><span>${esc(row.text||'新增章节')}</span></button>`).join('')||'<p class="wf-empty">尚无目录，可新增章节。</p>'}</div>
      <div class="wf-outline-properties">${selected?`<label class="wf-field">章节名称<input class="input" id="wf-outline-title" maxlength="150" value="${esc(selected.text)}"></label><p class="wf-help">第 ${selected.level} 级${end>index+1?'；调整会同时移动下属章节。':''}${selected.locked_format?'<span class="wf-status">采购方指定格式</span>':''}</p>${containsForms?'<p class="wf-help">采购方指定的原表随所属章节移动。</p>':''}<div class="wf-file-actions">${wfButton('上移','outline-up',{disabled:previous<0,subtle:true})}${wfButton('下移','outline-down',{disabled:!next,subtle:true})}${wfButton('升一级','outline-outdent',{disabled:selected.level<=1,subtle:true})}${wfButton('降一级','outline-indent',{disabled:previous<0||Math.max(...ui.outlineDraft.slice(index,end).map(row=>row.level))>=9,subtle:true})}</div>
`:''}
      <div class="wf-file-actions">${wfButton(selected?'新增同级':'新增章节','outline-add-sibling',{subtle:true})}${selected?wfButton('新增子级','outline-add-child',{subtle:true,disabled:selected.level>=9})+wfButton('删除章节','outline-delete',{subtle:true}):''}</div></div></div>
      <div class="wf-review-heading wf-outline-save"><p class="wf-help" id="wf-outline-status" role="status" ${ui.outlineDirty?'':'hidden'}>${ui.outlineDirty?'有未保存修改；下方预览仍为已保存版本':''}</p><div class="wf-file-actions">${ui.error?wfButton('重新读取','reload-review',{subtle:true}):''}${wfButton('保存目录并更新预览','save-outline',{primary:true,disabled:!ui.outlineDirty})}</div></div></details>`;
  }
  function outlineIssues() { return ui.record?.outline_check?.issues||[]; }
  function outlineCheckPanel() {
    const check=ui.record?.outline_check;
    if(!check)return '';
    const issues=check.issues||[];
    if(!issues.length)return `<section class="wf-section wf-outline-check is-ok" aria-label="目录校验"><p class="wf-help">目录校验通过：${Number(check.checked)||0} 个章节，未发现序号乱序、重复、跳号或编号混用。</p></section>`;
    const titles=new Map((ui.record.outline||[]).map(row=>[row.id,row.text]));
    const canEdit=editable();
    return `<section class="wf-section wf-outline-check" aria-label="目录校验"><h3 class="wf-section-title">目录校验 · ${issues.length} 项需核对</h3>
      <p class="wf-help">${ui.outlineDirty?'以下为已保存目录的校验结果；保存目录后会重新校验。':'从招标文件拆出的标题常带原文序号，请核对后再进入系统填充。可按建议修正，修正后须保存目录。'}</p>
      <ol class="wf-outline-issues">${issues.map((issue,i)=>`<li><p>${esc(issue.message)}</p><div class="wf-outline-issue-chapters">${(issue.chapter_ids||[]).slice(0,12).map(id=>canEdit?`<button type="button" class="wf-chip" data-wf="select-outline" data-id="${esc(id)}">${esc(titles.get(id)||id)}</button>`:`<span class="wf-chip">${esc(titles.get(id)||id)}</span>`).join('')}${(issue.chapter_ids||[]).length>12?`<span class="wf-help">等 ${issue.chapter_ids.length} 个章节</span>`:''}</div>${canEdit&&issue.fixes?.length?`<details class="wf-outline-fix"><summary>查看建议修正（${issue.fixes.length} 处）</summary><ul>${issue.fixes.map(fix=>`<li>${esc(titles.get(fix.id)||fix.id)} → <strong>${esc(fix.text)}</strong></li>`).join('')}</ul>${wfButton('按建议修正','outline-fix',{id:String(i),subtle:true})}</details>`:''}</li>`).join('')}</ol>
      ${ui.outlineDirty?'':`<label class="wf-ack"><input type="checkbox" id="wf-outline-check-ack" ${ui.outlineCheckAck?'checked':''}>已逐项核对目录校验提示，确认无需再修改（例如附件号须与采购原件一致）</label>`}</section>`;
  }
  function outline() {
    const preview=html(), word=files().find(f=>f.kind==='word');
    if(!preview||!word)return `<p class="wf-error" role="alert">${esc(ui.record.migration_error||'无法读取整本预览。请返回招标文件解读，核对采购原件。')}</p>`;
    const url=preview.url+(preview.url.includes('?')?'&':'?')+'revision='+encodeURIComponent(ui.record.revision||0);
    const books=ui.record.books||[];
    const summary=books.length?`<section class="wf-section" aria-label="分册目录"><h2 class="wf-section-title">分册目录</h2><div class="wf-file-actions">${books.map(book=>wfButton(book.name+'目录 · '+book.chapters.length+' 个章节','select-outline',{id:book.id,subtle:true})).join('')}</div></section>`:'';
    return summary+outlineCheckPanel()+outlineEditor()+`<iframe class="wf-preview-frame" title="投标文件目录预览" src="${esc(url)}" sandbox="allow-same-origin"></iframe>
      <p class="wf-help wf-preview-state" role="status">正在读取目录预览…</p>
      <div class="wf-actions">${wfButton('上一步','step',{id:2})}${wfButton('确认目录，进入系统填充','confirm',{primary:true,disabled:!ui.previewLoaded||ui.outlineDirty||ui.record.requirements_confirmed===false})}</div>`;
  }
  function filling() {
    return title('系统填充')+renderProject(true);
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
    const job=ui.writing?.job;
    return job?.accepted_chapter_ids?.includes(id)?' · 已采纳':job?.chapters?.some(candidate=>candidate.chapter_id===id)?writingStale(id)?' · 候选待更新':' · 有候选':'';
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
      return warningHtml+`<div class="wf-writing-progress" role="status"><div><strong>${job.status==='queued'?'等待开始写作':'正在生成初稿正文'}</strong><span>${progress}%</span></div><progress max="100" value="${progress}" aria-label="初稿正文生成进度">${progress}%</progress><p class="wf-help">${batch}离开本页不影响生成。</p></div>`;
    }
    if(job.status==='failed')return warningHtml+`<p class="wf-error" role="alert">生成失败：${esc((job.error||job.message||'请稍后重试').replace(/[。；;]+$/,''))}。已保存正文保留，可重试。</p>`;
    const notes=writingMessages(job.notes);
    const stale=unacceptedChapters().filter(candidate=>writingStale(candidate.chapter_id)).length;
    return warningHtml+`<p class="wf-note" role="status">已生成 ${job.chapters?.length||0} 个章节候选，已采纳 ${job.accepted_chapter_ids?.length||0} 个${stale?`，${stale} 个候选待更新`:''}。</p>`+(notes.length?`<details class="wf-note"><summary>本次写作说明（${notes.length}）</summary><ul>${notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></details>`:'');
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
    const job=ui.writing?.job, candidate=job?.chapters?.find(c=>c.chapter_id===ui.chapter);
    const remaining=unacceptedChapters().filter(c=>!writingStale(c.chapter_id)&&!writingChapterBusy(c.chapter_id));
    const generate=wfButton(candidate?'重新生成本章':'生成本章正文','generate-chapter-writing',{subtle:true,
      disabled:writingRunning()||!ui.writing?.files?.length||ui.writing?.model?.ready!==true});
    const fixedForm=ui.record?.outline?.find(row=>row.id===ui.chapter)?.locked_format;
    if(!candidate)return `<h3 class="wf-section-title">AI 候选正文</h3><p class="wf-help">${writingChapterBusy(ui.chapter)?'正在生成':job?.status==='completed'?fixedForm?'本章为采购原表，请在 Word 中填写。':'本章无候选，可手工填写；请选择“有候选”章节查看生成结果。':'尚未生成候选正文'}</p><div class="wf-candidate-actions">${generate}${remaining.length?wfButton('采纳全部可用候选（'+remaining.length+'）','accept-all-writing',{subtle:true}):''}</div>`;
    const accepted=job.accepted_chapter_ids?.includes(ui.chapter), stale=writingStale(ui.chapter), generating=writingChapterBusy(ui.chapter);
    const current=ui.edits.has(ui.chapter)?ui.edits.get(ui.chapter):ui.editor?.chapters.find(c=>c.id===ui.chapter)?.text||'';
    const missing=writingMessages(candidate.missing_items), conflicts=writingMessages(candidate.conflicts);
    return `<div class="wf-review-heading"><h3 class="wf-section-title">AI 候选正文</h3>${accepted?'<span class="wf-status">已采纳</span>':'<span class="wf-status is-pending">待核对</span>'}</div><div class="wf-candidate-text" tabindex="0" aria-label="当前章节候选正文">${esc(candidate.text)}</div>
      ${missing.length?`<div class="wf-note"><strong>待补资料</strong><ul>${missing.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></div>`:''}
      ${conflicts.length?`<div class="wf-error"><strong>需要核对的冲突</strong><ul>${conflicts.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></div>`:''}
      ${writingSources(candidate)}<div class="wf-file-actions wf-candidate-actions">${wfButton(accepted?'本章已采纳':current.trim()?'采纳并替换本章正文':'采纳本章正文','accept-writing',{primary:true,disabled:accepted||stale||generating})}${wfButton('采纳全部可用候选（'+remaining.length+'）','accept-all-writing',{subtle:true,disabled:!remaining.length})}${generate}</div>${generating?'<p class="wf-help">正在重新生成本章；其他章节候选仍可采纳。</p>':!accepted&&stale?'<p class="wf-help">本章正文或依据已变化，请重新生成本章。</p>':!accepted&&!current.trim()?'<p class="wf-help">采纳后自动保存。</p>':''}`;
  }
  function libraryCasesPanel(disabled) {
    // Reviewed colleague cases from the material library; selecting them replaces the package.
    const library=ui.writing?.library_cases||[], chosen=ui.librarySelection||new Set(ui.writing?.library_case_ids||[]);
    const rows=library.map(c=>`<label class="wf-library-case"><input type="checkbox" name="wf-library-case" value="${esc(c.id)}" ${chosen.has(c.id)?'checked':''} ${disabled?'disabled':''}><span><strong>${esc(c.name)}</strong><small>${esc([c.client_name,`${c.file_count} 个文件`,c.contributor?'提交人 '+c.contributor:''].filter(Boolean).join(' · '))}</small></span></label>`).join('');
    return `<details class="wf-library-cases" ${ui.libraryOpen?'open':''}><summary>从案例库选择${library.length?`（${library.length} 个可用）`:''}</summary>${library.length?`<div class="wf-library-list">${rows}</div><div class="wf-file-actions">${wfButton('使用所选案例','use-library-cases',{disabled})}<span class="wf-help">所选案例将替换当前案例包。</span></div>`:'<p class="wf-help">案例库暂无案例。同事可在材料库“案例库”提交案例，经本机核对后即可在此选用。</p>'}</details>`;
  }
  async function useLibraryCases() {
    const ids=[...document.querySelectorAll('input[name="wf-library-case"]:checked')].map(el=>el.value);
    if(!ids.length)throw new Error('请先勾选案例库中的案例。');
    if(!ui.writing||ui.writingError)throw new Error('请先重新读取案例写作状态。');
    if(writingRunning())throw new Error('请等待本次写作完成后再替换案例包。');
    if(ui.writing.files.length&&!window.confirm('所选案例将替换整个案例包，之前生成的候选将失效。已保存的正文保留。继续？'))return;
    ui.busy='正在读取案例库案例…';ui.error='';stopWritingPoll();paint();
    try{
      await saveWritingInputs();
      bindWriting(await api(writingUrl(state.project.id)+'/cases',{method:'POST',body:{library_case_ids:ids,case_revision:ui.writing.case_revision,mapping_revision:state.project.mapping_revision}}));
      ui.librarySelection=null;
      toast(`已使用 ${ids.length} 个案例库案例。`);
    }finally{ui.busy='';paint();scheduleWritingPoll();}
  }
  function writingInputs() {
    const writing=ui.writing, running=writingRunning(), uploaded=writing?.files||[], ready=writing?.model?.ready===true;
    const disabled=!writing||!!ui.writingError||running;
    return `<section class="wf-writing-inputs" aria-label="项目案例与 AI 写作"><h3 class="wf-section-title">1. 上传相关项目案例</h3><p class="wf-help">支持多文件或 ZIP，也可从案例库选择。</p>
      <input id="wf-case-upload" type="file" hidden multiple accept=".doc,.docx,.pdf,.pptx,.txt,.md,.zip"><button type="button" class="wf-dropzone wf-case-dropzone" data-wf="upload-cases" ${disabled?'disabled':''} aria-label="上传相关项目案例"><span class="wf-drop-icon">${icon('upload')}</span><strong class="wf-drop-title">${uploaded.length?'点击或拖拽替换案例包':'点击或拖拽上传相关项目案例'}</strong><small class="wf-drop-help">DOC、DOCX、PDF、PPTX、TXT、MD、ZIP · 合计 ≤ 50 MB</small></button>
      ${uploaded.length?`<div class="wf-case-files" aria-label="已上传案例文件">${uploaded.map(file=>`<div class="wf-file"><div class="wf-file-info"><strong class="wf-file-name">${esc(file.name)}</strong><small class="wf-file-meta">${(Number(file.size||0)/1024/1024).toFixed(2)} MB</small></div><span class="wf-status">${String(file.name).startsWith('案例库/')?'案例库':'已上传'}</span></div>`).join('')}</div>`:''}
      ${libraryCasesPanel(disabled)}
      <label class="wf-field" for="wf-business-notes">业务补充信息（选填）<textarea class="input" id="wf-business-notes" rows="3" maxlength="10000" placeholder="填写已确认的范围、团队安排或案例说明；不确定处标注待确认。">${esc(ui.businessNotes)}</textarea></label><p class="wf-help" id="wf-business-notes-status" role="status" ${ui.businessNotesDirty?'':'hidden'}>补充信息已修改，将用于下次生成。</p>
      <div class="wf-file-actions wf-writing-generate">${wfButton(running?'正在生成初稿正文…':writing?.job?'重新生成初稿正文':'生成初稿正文','generate-writing',{primary:true,disabled:disabled||!uploaded.length||!ready||!ui.editor?.chapters?.length})}${!uploaded.length?'<span class="wf-help">请先上传案例包。</span>':!ready?`<span class="wf-help">${esc(writing?.model?.message||'AI 写作暂不可用，请检查项目模型配置。')}</span>`:''}</div><div id="wf-writing-status">${writingStatus()}</div></section>`;
  }
  function redlinePanel() {
    if(ui.redlineError)return `<section class="wf-section wf-redline-scan" id="wf-redline-scan"><h3 class="wf-section-title">3. 废标红线扫描</h3><p class="wf-error" role="alert">${esc(ui.redlineError)}</p><div class="wf-file-actions">${wfButton('重新扫描','reload-redline',{subtle:true})}</div></section>`;
    const scan=ui.redline;
    if(!scan)return `<section class="wf-section wf-redline-scan" id="wf-redline-scan"><h3 class="wf-section-title">3. 废标红线扫描</h3><p class="wf-note">正在读取废标红线…</p></section>`;
    if(!scan.items?.length)return `<section class="wf-section wf-redline-scan" id="wf-redline-scan"><h3 class="wf-section-title">3. 废标红线扫描</h3><p class="wf-help">本项目未识别到废标红线条款；若采购原文确有否决条款，请返回招标解读补充后再扫描。</p></section>`;
    const pending=scan.pending_count||0;
    const unsaved=redlineUnsaved();
    const chapters=ui.editor?.chapters||[];
    const fixLinks=item=>{
      const links=[];
      if(item.status==='risk'&&(item.material_category_ids||[]).length)links.push(wfButton('去材料核对处理','step',{id:4,subtle:true}));
      for(const id of item.empty_chapter_ids||[]){
        const chapter=chapters.find(c=>c.id===id);
        if(chapter&&!String(chapter.text||'').trim())links.push(wfButton(`去编写「${chapter.title||chapter.path||id}」`,'redline-chapter',{id,subtle:true}));
      }
      return links.length?`<div class="wf-file-actions wf-redline-fix">${links.join('')}</div>`:'';
    };
    return `<section class="wf-section wf-redline-scan" id="wf-redline-scan"><h3 class="wf-section-title">3. 废标红线扫描</h3>
      <p class="wf-help">${esc(scan.scope||'自动检查：材料栏目是否缺漏或待补，关联章节是否已有正文；其余为人工核对提示。')}只有正文或材料与某条红线相关时，才需要重新确认该条。</p>
      <p class="wf-help" role="status" id="wf-redline-status">${redlineStatusText()}</p>
      <div class="wf-redline-list">${scan.items.map(item=>`<article class="wf-redline-item ${item.status==='risk'?'is-risk':''}" data-redline="${esc(item.id)}"><label class="wf-ack"><input type="checkbox" data-wf="ack-redline" data-id="${esc(item.id)}" ${item.acknowledged?'checked':''}><strong>${esc(item.label)}</strong>${item.status==='risk'?'<span class="wf-redline-tag">有风险</span>':''}</label>${item.ack_expired?'<p class="wf-redline-expired">相关正文或材料已变化，之前的确认已失效，请重新核对。</p>':''}<p>${esc(item.text)}</p>${(item.findings||[]).length?`<ul>${item.findings.map(note=>`<li>${esc(note)}</li>`).join('')}</ul>`:''}${(item.hints||[]).length?`<ul class="wf-redline-hints">${item.hints.map(note=>`<li>${esc(note)}</li>`).join('')}</ul>`:''}${fixLinks(item)}</article>`).join('')}</div>
      <div class="wf-file-actions">${wfButton(unsaved?'保存红线核对（有未保存勾选）':'保存红线核对','save-redline',{primary:true})}${wfButton('重新扫描','reload-redline',{subtle:true})}</div></section>`;
  }
  function writing() {
    const editor=ui.editor, chapter=editor?.chapters.find(c=>c.id===ui.chapter);
    const draft=ui.edits.has(ui.chapter)?ui.edits.get(ui.chapter):chapter?.text||'';
    return title('编写投标文件')+
      `<div class="wf-summary"><strong>${esc(state.project.name)}</strong><small class="wf-file-meta">${esc(state.project.template_name)}</small></div>
       ${writingInputs()}
       ${chapter?`<section class="wf-settings wf-writing-review"><h3 class="wf-section-title">2. 核对并采纳正文</h3><label class="wf-field" for="wf-chapter">编写章节<select class="input" id="wf-chapter">${hierarchy(editor.chapters).map(c=>`<option value="${esc(c.id)}" ${c.id===chapter.id?'selected':''}>${esc(c.number)}　${esc(c.path)}${writingChapterStatus(c.id)}</option>`).join('')}</select></label><div class="wf-writing-grid"><div id="wf-writing-candidate" class="wf-candidate">${writingCandidate()}</div><div class="wf-saved-draft"><label class="wf-field" for="wf-body">已保存 / 手工编辑的章节正文<textarea class="input" id="wf-body" rows="14" maxlength="50000" placeholder="可直接填写正文。">${esc(draft)}</textarea></label><div class="wf-file-actions">${wfButton('保存正文','save',{subtle:true})}${ui.error?wfButton('重新读取已存正文','reload-editor',{subtle:true}):''}<span class="wf-help" id="wf-save-status" role="status">${ui.edits.size?'有未保存修改':'已保存'}</span></div></div></div></section>`:'<p class="wf-note">这份 Word 没有可在线编写的章节，可下载 Word 后填写采购原表。</p>'}
       ${redlinePanel()}
       <div class="wf-actions">${wfButton('上一步','step',{id:4})}${wfButton('导出 Word 初稿','export',{primary:true})}</div>
       <p class="wf-help">交付前请在 Word 中更新目录，并人工复核待补项、报价、签章和页码。</p>${renderExports(state.project.exports||[])}`;
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
      <section class="wf-section"><h2 class="wf-section-title">已保存的章节正文（${filled.length}）</h2>${filled.map(c=>`<details class="wf-note"><summary>${esc(c.path)}</summary><p class="wf-archived-text">${esc(c.text)}</p></details>`).join('')||'<p class="wf-help">该工作稿未保存在线正文。</p>'}</section>
      <details class="wf-note"><summary>材料核对记录（${project.mappings?.length||0}）</summary><ul class="wf-list">${rows}</ul></details>${renderExports(project.exports||[])}
      <div class="wf-actions">${wfButton('返回整本项目','resume',{id:project.framework_id,primary:true})}</div></div></section>`;
  }
  function view() {
    if(ui.archive)return archiveView();
    const bodies=[settings,()=>ui.record?analysis():settings(),outline,filling,writing];
    return `<div class="wf-workspace" ${ui.busy||ui.pending||ui.savingDrafts?'inert':''}>${stepper()}<section class="wf-card" aria-busy="${!!(ui.busy||ui.pending||ui.savingDrafts)}">${ui.error?`<p class="wf-error" role="alert">${esc(ui.error)}</p>`:''}${bodies[ui.step-1]()}</section></div>`;
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
    const previousRequirement=ui.requirementKey, previousOutline=ui.outlineKey;
    ui.record=record;ui.name=record.name;ui.archive=null;
    ui.library=[record,...ui.library.filter(r=>r.id!==record.id)];
    ui.editor=null;ui.edits.clear();ui.chapter='';ui.writing=null;ui.writingError='';ui.businessNotes='';ui.businessNotesDirty=false;
    ui.redline=null;ui.redlineError='';
    ui.requirements=clone(record.requirements||[]).map(item=>({...item,_key:item.id||newKey()}));
    ui.requirementKey=ui.requirements.find(item=>item._key===previousRequirement&&item.category===ui.requirementCategory)?previousRequirement:ui.requirements.find(item=>item.category===ui.requirementCategory)?._key||'';
    ui.outlineDraft=clone(record.outline||[]).map(item=>({...item,_key:item.id||newKey(),level:Number(item.level)||1}));
    ui.outlineKey=ui.outlineDraft.some(item=>item._key===previousOutline)?previousOutline:ui.outlineDraft[0]?._key||'';
    ui.requirementsDirty=false;ui.outlineDirty=false;ui.previewLoaded=false;ui.sourceQuery='';ui.outlineCheckAck=false;
    ui.notesAcknowledged=false;ui.removedAcknowledged=new Set();
    if(state.project?.framework_id===record.id&&record.projects?.word!==state.project.id){state.project=null;state.dirty=false;}
  }
  async function restore(id, step=0) {
    const record=await api(base(id));
    bindRecord(record);
    state.project=null;state.dirty=false;
    const linked=record.projects?.word;
    if(linked){
      const project=await api(projectUrl(linked));
      if(project.framework_id!==record.id||project.framework_artifact_id!=='word')throw new Error('整本工作稿与框架关联不一致，请重新打开项目核对。');
      state.project=project;resetProjectUi();await loadProjectPickers();
    }
    ui.step=step&&available(step)?step:linked&&available(4)?(projectReviewed()?5:4):available(3)?3:2;
    if(ui.step===4 && projectUi.step==='export')projectUi.step='special';
    if(ui.step===5){await loadEditor();await loadRedline();}
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
      const result=await api('/api/frameworks',{method:'POST',body:{name,filename,data}});
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
    const status=$('#wf-writing-status'),candidate=$('#wf-writing-candidate');
    if(status)status.innerHTML=writingStatus();
    if(candidate)candidate.innerHTML=writingCandidate();
    const selector=$('#wf-chapter');
    if(selector&&ui.editor)for(const chapter of hierarchy(ui.editor.chapters)){
      const option=[...selector.options].find(option=>option.value===chapter.id);
      if(option)option.textContent=chapter.number+'　'+chapter.path+writingChapterStatus(chapter.id);
    }
    const generate=document.querySelector('[data-wf="generate-writing"]'),upload=document.querySelector('[data-wf="upload-cases"]');
    const disabled=!!ui.busy||!!ui.pending||!ui.writing||!!ui.writingError||writingRunning();
    if(generate){generate.disabled=disabled||!ui.writing?.files?.length||ui.writing?.model?.ready!==true||!ui.editor?.chapters?.length;generate.textContent=writingRunning()?'正在生成初稿正文…':ui.writing?.job?'重新生成初稿正文':'生成初稿正文';}
    if(upload)upload.disabled=disabled;
    for(const control of document.querySelectorAll('[data-wf="use-library-cases"],input[name="wf-library-case"]'))control.disabled=disabled;
  }
  function bindWriting(result) {
    // A newer query or completed edit supersedes every earlier in-flight read.
    writingRequest++;
    ui.writing={...ui.writing,...result};ui.writingError='';
    if(!ui.businessNotesDirty)ui.businessNotes=result.job?.business_notes||result.business_notes||'';
  }
  async function loadWriting({poll=false}={}) {
    const currentUi=ui,projectId=state.project?.id,request=++writingRequest;
    const current=()=>request===writingRequest&&ui===currentUi&&state.project?.id===projectId;
    if(!projectId)return;
    try{
      let result=await api(writingUrl(projectId)+(poll?'?view=status':''));
      if(!current())return;
      if(poll){
        const previous=ui.writing;
        if(!previous||JSON.stringify(result.source_revision)!==JSON.stringify(previous.source_revision)){
          result=await api(writingUrl(projectId));
        }else if(result.state_revision!==previous.state_revision||result.job?.id!==previous.job?.id||
          result.job?.version!==previous.job?.version||
          (['completed','failed'].includes(result.job?.status)&&result.job.status!==previous.job?.status)){
          result=await api(writingUrl(projectId)+'?view=job');
        }else if(result.job){result.job={...previous.job,...result.job};}
        if(!current())return;
      }
      if(!result||(!poll&&!Array.isArray(result.files))||!Number.isInteger(result.case_revision))throw new Error('案例写作状态不完整，请重新读取。');
      bindWriting(result);
    }catch(err){
      if(!current())return;
      ui.writingError='暂时无法读取案例写作状态：'+err.message;
    }
    scheduleWritingPoll();
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
    if(selected.some(file=>! /\.(docx?|pdf|pptx|txt|md|zip)$/i.test(file.name)))throw new Error('案例支持 DOC、DOCX、PDF、PPTX、TXT、MD、ZIP。');
    if(selected.some(file=>!file.size)||selected.reduce((sum,file)=>sum+file.size,0)>50*1024*1024)throw new Error('案例文件须大于 0 字节，合计不超过 50 MB。');
    if(ui.writing.files.length&&!window.confirm('本次选择的文件将替换整个案例包，之前生成的候选将失效。已保存的正文保留。继续上传？'))return;
    ui.busy='正在读取相关项目案例…';ui.error='';stopWritingPoll();paint();
    try{
      await saveWritingInputs();
      const files=await toFiles(selected);
      bindWriting(await api(writingUrl(state.project.id)+'/cases',{method:'POST',body:{files,case_revision:ui.writing.case_revision,mapping_revision:state.project.mapping_revision}}));
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
      const request={case_revision:ui.writing.case_revision,mapping_revision:state.project.mapping_revision,revision:ui.editor.revision,business_notes,...(chapterOnly?{chapter_ids:[ui.chapter]}:{})};
      const outbound=await api(writingUrl(state.project.id)+'/outbound',{method:'POST',body:request});
      if(!window.confirm(outboundSummary(outbound)))return;
      const job=await api(writingUrl(state.project.id)+'/generate',{method:'POST',body:request});
      bindWriting({job:job.job||job});ui.businessNotesDirty=false;
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
    if(confirm)confirm.disabled=ui.outlineDirty||!ui.previewLoaded||ui.record.requirements_confirmed===false||!!ui.busy;
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
      toast(confirmed?'采购要求已确认，目录已更新。':'要求修改已保存。');
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
  function redlineUnsaved() {
    const scan=ui.redline;
    if(!scan?.items)return false;
    const saved=new Set(scan.saved_ids||[]);
    return scan.items.some(item=>item.acknowledged!==saved.has(item.id));
  }
  function redlineStatusText() {
    const scan=ui.redline;
    if(!scan)return '';
    const unsaved=redlineUnsaved()?'勾选尚未保存，导出时会自动保存。':'';
    return (scan.complete?'全部红线已勾选核对。':`尚有 ${scan.pending_count} 条红线待人工勾选确认。`)+unsaved;
  }
  async function loadRedline({keepLocal=false}={}) {
    if(!hasProject()){ui.redline=null;ui.redlineError='';return;}
    const request=++writingRequest, previous=keepLocal?ui.redline:null;
    try{
      const scan=await api(redlineUrl(state.project.id));
      if(request!==writingRequest)return;
      bindRedline(scan,previous);
    }catch(err){
      if(request!==writingRequest)return;
      ui.redline=null;ui.redlineError=err.message;
    }
  }
  async function refreshRedline() {
    // 正文或材料保存后重新扫描，避免用过期的依据保存确认。
    if(ui.redline||ui.redlineError)await loadRedline({keepLocal:true});
  }
  async function saveRedline({quiet=false}={}) {
    if(!ui.redline)throw new Error('请先重新扫描废标红线。');
    if(ui.edits.size)await saveDrafts();
    await refreshRedline();
    if(!ui.redline)throw new Error(ui.redlineError||'请先重新扫描废标红线。');
    const acknowledged=Object.fromEntries(ui.redline.items.filter(item=>item.acknowledged).map(item=>[item.id,item.basis]));
    ui.busy='正在保存红线核对…';ui.error='';paint();
    try{
      const scan=await api(redlineUrl(state.project.id),{method:'POST',body:{
        mapping_revision:state.project.mapping_revision,acknowledged}});
      bindRedline(scan);
      if(!quiet)toast(ui.redline.complete?'废标红线已全部勾选确认。':`已保存；仍有 ${ui.redline.pending_count} 条红线待确认。`);
    }catch(err){
      await loadRedline({keepLocal:true});
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
  function guideRedlineCompletion() {
    if(!ui.redline){ui.error='请先完成废标红线扫描。';paint();document.querySelector('#wf-redline-scan')?.scrollIntoView({block:'center'});return true;}
    if(ui.redline.items?.length&&!ui.redline.complete){
      ui.error=`尚有 ${ui.redline.pending_count} 条废标红线未勾选确认，请完成扫描后再导出。`;
      paint();
      const pending=document.querySelector('.wf-redline-item input:not(:checked)')?.closest('.wf-redline-item')||document.querySelector('#wf-redline-scan');
      pending?.scrollIntoView({block:'center'});
      pending?.querySelector('input')?.focus({preventScroll:true});
      toast(ui.error,true);
      return true;
    }
    return false;
  }
  async function saveOutline() {
    if(!editable())throw new Error('历史目录不能在线修订，请重新上传采购原件。');
    if(!ui.outlineDraft.length||ui.outlineDraft.some(row=>!row.text.trim()))throw new Error('目录不能为空，且每个章节须填写名称。');
    const items=ui.outlineDraft.map(({id,text,level})=>({id:id||'',text,level}));
    ui.busy='正在保存目录并更新预览…';ui.error='';paint();
    try{
      const record=await api(base(ui.record.id)+'/outline',{method:'PATCH',body:{revision:ui.record.revision,items}});
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
        if(!window.confirm(`删除“${row.text}”及其 ${count-1} 个下属章节？涉及采购原表和必交项时，须先在招标解读中核对其适用性。`))return;
        ui.outlineDraft.splice(i,count);ui.outlineKey=ui.outlineDraft[Math.min(i,ui.outlineDraft.length-1)]?._key||'';
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
    if(n===5){
      if(ui.busy||ui.savingDrafts||state.busy)return;
      if(!ui.record){
        guideWorkflowCompletion(2,'请先上传并解析招标文件。','[data-wf="upload"]');return;
      }
      if(ui.requirementsDirty||ui.record.requirements_confirmed===false){
        guideWorkflowCompletion(2,ui.requirementsDirty?'采购要求有未保存修改，请先保存并确认要求。':'请先核对并确认采购要求。',
          ui.requirementsDirty?'[data-wf="save-requirements"]':'[data-wf="confirm-requirements"]');return;
      }
      if(ui.outlineDirty){
        ui.outlineEditing=true;
        guideWorkflowCompletion(3,'目录有未保存修改，请先保存目录并核对预览。','[data-wf="save-outline"]');return;
      }
      if(!hasProject()){
        guideWorkflowCompletion(3,'请先核对目录，并点击“确认目录，进入系统填充”。','[data-wf="confirm"]');return;
      }
      ui.step=4;
      if(await guideMaterialCompletion('export'))return;
      await saveDrafts();
      await loadEditor();
      ui.step=5;ui.error='';
      await loadRedline();
      paint(true);return;
    }
    if(!Number.isInteger(n)||!available(n))return;
    if(n!==ui.step&&!discardReviewEdits())return;
    await saveDrafts();
    if(state.dirty)await saveProject(false);
    if(n===4 && projectUi.step==='export')projectUi.step='special';
    ui.step=n;ui.error='';paint(true);
  }
  function error(err) {
    ui.error=err.message;paint();document.querySelector('.wf-error')?.scrollIntoView({block:'nearest'});
    toast(err.message,true);
  }
  async function action(target) {
    const action=target.dataset.wf, id=target.dataset.id;
    if (action==='upload')return $('#wf-upload').click();
    if (action==='upload-cases')return $('#wf-case-upload').click();
    if (action==='reload-writing'){await loadWriting();paint();return;}
    if (action==='reload-redline'){await loadRedline();paint();return;}
    if (action==='save-redline')return saveRedline();
    if (action==='use-library-cases')return useLibraryCases();
    if (action==='generate-writing')return generateWriting();
    if (action==='generate-chapter-writing')return generateWriting(true);
    if (action==='accept-writing'||action==='accept-all-writing')return acceptWriting(action==='accept-all-writing');
    if (action==='metric'){
      if(!editable())return window.open(html().url+'#'+encodeURIComponent(id),'_blank','noopener');
      ui.requirementEditing=false;ui.requirementCategory=id;ui.requirementKey=ui.requirements.find(item=>item.category===id)?._key||'';ui.sourceQuery='';paint();
      document.querySelector('.wf-review')?.scrollIntoView({block:'start',behavior:'smooth'});return;
    }
    if (action==='to-outline')return setStep(3);
    if (action==='step')return setStep(Number(id));
    if (action==='new')return enter();
    if (action==='confirm') {
      if(ui.outlineDirty)throw new Error('请先保存目录并核对更新后的预览。');
      if(ui.record.requirements_confirmed===false)throw new Error('请先确认采购要求。');
      if(!ui.previewLoaded)throw new Error('请等待整本预览加载完成。');
      if(outlineIssues().length&&!ui.outlineCheckAck&&!ui.record.projects?.word){
        guideWorkflowCompletion(3,`目录校验有 ${outlineIssues().length} 项需核对：请按建议修正后保存目录，或逐项核对后勾选确认。`,'#wf-outline-check-ack');return;
      }
      if(state.dirty)await saveProject(false);
      const project=await api(base(ui.record.id)+'/confirm',{method:'POST',body:{artifact_id:'word',revision:ui.record.revision,outline_check_acknowledged:!!ui.outlineCheckAck}});
      if(project.framework_id!==ui.record.id||project.framework_artifact_id!=='word')throw new Error('未能取得此框架的整本项目。');
      state.project=project;state.dirty=false;
      state.data.projects=[project,...state.data.projects.filter(p=>p.id!==project.id)];
      ui.record.projects={word:project.id};
      resetProjectUi();projectUi.step='special';await loadProjectPickers();
      ui.step=4;paint(true);return;
    }
    if(action==='select-requirement'){ui.requirementEditing=false;ui.requirementKey=id;ui.sourceQuery='';paint();return;}
    if(action==='add-requirement'){
      const item={id:'',_key:newKey(),category:ui.requirementCategory,label:'',text:'',scope:'',references:[],note:''};
      ui.requirementEditing=true;ui.requirements.push(item);ui.requirementKey=item._key;ui.requirementsDirty=true;ui.sourceQuery='';paint();$('#wf-requirement-label')?.focus();return;
    }
    if(action==='delete-requirement'){
      if(!window.confirm('删除本条要求？请确认其不适用或属于重复识别，避免遗漏必交材料。'))return;
      ui.requirements=ui.requirements.filter(item=>item._key!==id);ui.requirementKey=ui.requirements.find(item=>item.category===ui.requirementCategory)?._key||'';ui.requirementsDirty=true;paint();return;
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
    if(action==='outline-fix'){
      const issue=outlineIssues()[Number(id)];
      if(!issue)return;
      let applied=0;
      for(const fix of issue.fixes||[]){
        const row=ui.outlineDraft.find(item=>item.id===fix.id);
        if(row&&row.text!==fix.text){row.text=fix.text;applied++;}
      }
      if(!applied){toast('这些章节已按建议修改，或已在目录编辑中调整。');return;}
      ui.outlineDirty=true;ui.outlineEditing=true;ui.outlineCheckAck=false;paint();
      toast(`已按建议修改 ${applied} 个章节名称，请核对后点“保存目录并更新预览”。`);
      document.querySelector('[data-wf="save-outline"]')?.scrollIntoView({block:'center'});return;
    }
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
    if(action==='export'){
      await saveDrafts();
      if(!ui.redline&&!ui.redlineError)await loadRedline();
      else await refreshRedline();
      redlineRefreshPending=false;
      paint();
      if(ui.redline?.items?.length){
        if(!ui.redline.complete){guideRedlineCompletion();return;}
        // 页面勾选须先保存到服务端；服务端也会在导出时再次校验。
        if(redlineUnsaved()||(ui.redline.saved_ids||[]).length!==ui.redline.items.length){
          await saveRedline({quiet:true});
          if(!ui.redline.complete||redlineUnsaved()){guideRedlineCompletion();return;}
        }
        const risks=ui.redline.items.filter(item=>item.status==='risk');
        if(risks.length&&!window.confirm(`以下 ${risks.length} 条废标红线仍有风险提示，导出后请在递交前处理：\n\n${risks.map(item=>`· ${item.label}：${(item.findings||[])[0]||''}`).join('\n')}\n\n确认已知晓风险并继续导出？`))return;
      }else if(guideRedlineCompletion())return;
      await exportProject(target);return;
    }
  }
  function cleanEmbeddedOutline(contents) {
    // Only remove known display notes in the embedded copy; source HTML stays intact.
    for(const note of contents.querySelectorAll('p.desc,p.wf-help')){
      const text=note.textContent.trim();
      if(text==='按采购原文分册'||text==='采购原文未要求商务标、技术标分册，保留统一目录；营业执照与公司介绍相邻排列。')note.remove();
      else if(text==='按内容编排，递交方式以采购原文为准')note.textContent='递交方式以采购原文为准';
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
    status.hidden=true;ui.previewLoaded=true;confirm.disabled=ui.outlineDirty||ui.record.requirements_confirmed===false||!!ui.busy;
  },true);
  document.addEventListener('click',async e=>{
    const target=e.target.closest('[data-wf]');if(!target||target.disabled||ui.busy)return;
    const actionName=target.dataset.wf;
    if(['upload','upload-cases','metric','new'].includes(actionName)){
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
      if(el.id==='wf-case-upload'){await uploadCases(el.files||[]);el.value='';}
      if(el.name==='wf-library-case'){ui.librarySelection=new Set([...document.querySelectorAll('input[name="wf-library-case"]:checked')].map(x=>x.value));return;}
      if(el.id==='wf-chapter'){ui.chapter=el.value;paint();}
      if(el.id==='wf-requirement-category'&&requirement()){requirement().category=el.value;ui.requirementCategory=el.value;ui.requirementsDirty=true;paint();}
      if(el.id==='wf-notes-ack'){ui.notesAcknowledged=el.checked;return;}
      if(el.id==='wf-outline-check-ack'){ui.outlineCheckAck=el.checked;return;}
      if(el.matches?.('[data-wf="ack-removed"]')){
        if(el.checked)ui.removedAcknowledged.add(el.dataset.id);else ui.removedAcknowledged.delete(el.dataset.id);
        return;
      }
      if(el.matches?.('[data-wf="ack-redline"]')&&ui.redline?.items){
        const item=ui.redline.items.find(row=>row.id===el.dataset.id);
        if(!item)return;
        item.acknowledged=el.checked;
        syncRedlineCounts();
        const status=document.querySelector('#wf-redline-status');
        if(status)status.textContent=redlineStatusText();
        const save=document.querySelector('[data-wf="save-redline"]');
        if(save)save.textContent=redlineUnsaved()?'保存红线核对（有未保存勾选）':'保存红线核对';
        return;
      }
    }catch(err){error(err);}
  });
  document.addEventListener('input',e=>{
    if(e.target.id==='wf-name')ui.name=e.target.value;
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
      const row=ui.outlineDraft.find(item=>item._key===ui.outlineKey);if(row){row.text=e.target.value;ui.outlineDirty=true;syncReviewStatus();const choice=[...document.querySelectorAll('[data-wf=select-outline]')].find(el=>el.dataset.id===ui.outlineKey);if(choice)choice.lastElementChild.textContent=row.text||'新增章节';}
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
    if(ui.edits.size||reviewDirty()||ui.busy||ui.businessNotesDirty){e.preventDefault();e.returnValue='';}
  });
  function projectArchives() {
    return archiveLinks(ui.library.flatMap(r=>r.legacy_projects||[]));
  }
  return {view,enter,url,resumeProject,beforeLeave,setStep,projectArchives,isPreview:()=>ui.step===3&&!ui.archive};
})();
