/* 废标红线评测台 —— 本地前端（无构建依赖） */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const S = {
  docs: [],
  doc: null,
  units: [],
  predictions: [],
  gold: [],
  metrics: null,
  details: [],
  reasons: [],
  dirty: false,
  editVersion: 0,
  filter: '',
};

const KIND_LABEL = { TP: '命中', FN: '漏报', FP: '误报', FP_NA: '误报', FRAG: '误报', DUP: '重复', IGN: '忽略' };
const STATUSES = ['有效', '不适用', '存疑'];
const CONSEQUENCES = ['', '否决/无效', '不予受理/拒收', '取消资格', '视为放弃/撤回', '其他'];
const CERTAINTY = ['', '一律', '可能'];

let saveTimer = null;
let toastTimer = null;
let busyCount = 0;

/* ------------------------------------------------------------------ utils */

function normText(value) {
  return String(value || '')
    .normalize('NFKC')
    .replace(/\s+/g, '')
    .replace(/[\p{P}\p{S}]/gu, '')
    .toLowerCase();
}

function pct(value) {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function toast(message, kind = '') {
  const box = $('#toast');
  box.textContent = message;
  box.className = `toast ${kind}`.trim();
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, kind === 'error' ? 6000 : 3200);
}

function setBusy(on, text = '处理中…') {
  busyCount = Math.max(0, busyCount + (on ? 1 : -1));
  $('#busy').hidden = busyCount === 0;
  $('#busyText').textContent = text;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const type = response.headers.get('content-type') || '';
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    if (type.includes('json')) {
      try { message = (await response.json()).error || message; } catch (_) { /* ignore */ }
    }
    throw new Error(message);
  }
  return type.includes('json') ? response.json() : response.text();
}

/* ------------------------------------------------------------------ state */

async function loadState() {
  const data = await api('/api/state');
  S.docs = data.docs;
  $('#corpusPath').textContent = data.corpus;
  renderDocList();
  if (!S.doc && S.docs.length) {
    await openDoc(S.docs[0].id);
  } else if (S.doc && S.docs.some((d) => d.id === S.doc.id)) {
    await openDoc(S.doc.id, { keepEval: true, keepScroll: true });
  } else if (S.doc) {
    S.doc = null;
    $('#docView').hidden = true;
    $('#welcome').hidden = false;
  }
}

function renderDocList() {
  const list = $('#docList');
  list.textContent = '';
  if (!S.docs.length) {
    list.append(el('p', 'muted small', '还没有文件。点击右上角「＋ 导入招标文件」。'));
    return;
  }
  for (const doc of S.docs) {
    const button = el('button', 'doc-item');
    if (S.doc && S.doc.id === doc.id) button.classList.add('active');
    button.append(el('span', 'name', doc.id));
    const sub = el('span', 'sub');
    const dot = el('i', `dot${doc.gold ? ' done' : ''}`);
    sub.append(dot, el('span', null, `金标 ${doc.gold} 条`));
    button.append(sub);
    button.addEventListener('click', async () => {
      if (S.doc && S.doc.id !== doc.id && S.dirty) {
        try { await flushSave(); } catch (_) { return; }
      }
      openDoc(doc.id);
    });
    list.append(button);
  }
}

async function openDoc(id, options = {}) {
  setBusy(true, '读取招标文件…');
  try {
    const doc = await api(`/api/docs/${encodeURIComponent(id)}`);
    if (!options.keepScroll) window.scrollTo({ top: 0 });
    S.doc = doc;
    S.units = doc.units || [];
    S.predictions = doc.predictions || [];
    S.gold = (doc.gold || []).map((row) => ({ ...row }));
    S.metrics = null;
    S.details = [];
    S.reasons = [];
    S.dirty = false;
    S.editVersion = 0;
    S.filter = '';
    $('#searchInput').value = '';
    $('#showSystem').checked = false;
    $('#systemSection').hidden = true;
    $('#resultSection').hidden = true;
    $('#metrics').hidden = true;
    $('#metrics').textContent = '';
    $('#welcome').hidden = true;
    $('#docView').hidden = false;
    renderDocHeader();
    renderAlert();
    renderUnits();
    renderGold();
    setMeta('');
    $('#staleNote').hidden = true;
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    setBusy(false);
  }
}

function renderDocHeader() {
  const doc = S.doc;
  $('#docTitle').textContent = doc.id.replace(/^D\d+-/, '');
  renderDocList();
}

function setMeta(extra = '') {
  if (!S.doc) return;
  const system = S.predictions.filter((p) => p.category === 'redline').length;
  const parts = [S.doc.source, `原文 ${S.units.length} 段`, `系统红线 ${system} 条`];
  if (extra) parts.push(extra);
  $('#docMeta').textContent = parts.join(' · ');
}

function renderAlert() {
  const box = $('#alert');
  if (S.doc && S.doc.error) {
    box.hidden = false;
    box.className = 'alert error';
    box.textContent = `系统提取失败：${S.doc.error}。本次评测会把所有金标记为漏报，请先确认文件能否被系统正常读取。`;
  } else {
    box.hidden = true;
    box.textContent = '';
  }
}

/* ------------------------------------------------------------------ units */

function isMarked(unitText, goldKeys) {
  const key = normText(unitText);
  if (!key) return false;
  return goldKeys.some((gold) => {
    if (!gold) return false;
    if (gold === key) return true;
    return gold.length >= 8 && (key.includes(gold) || gold.includes(key));
  });
}

function renderUnits() {
  const container = $('#units');
  container.textContent = '';
  const filter = S.filter.trim();
  const goldKeys = S.gold.map((row) => normText(row.text)).filter(Boolean);
  const fragment = document.createDocumentFragment();
  let shown = 0;

  for (const unit of S.units) {
    if (filter && !unit.text.includes(filter) && !(unit.locator || '').includes(filter)) continue;
    shown += 1;
    const marked = isMarked(unit.text, goldKeys);
    const card = el('article', `unit${unit.is_heading ? ' heading' : ''}${marked ? ' marked' : ''}`);
    card.dataset.text = unit.text;

    const meta = el('div', 'unit-meta');
    const location = [unit.part, unit.locator, unit.context].filter(Boolean).join(' · ');
    meta.append(el('span', 'loc', location || '正文'));
    const button = el('button', `mark${marked ? ' done' : ''}`, marked ? '已标' : '＋ 标为红线');
    meta.append(button);

    const body = el('div', 'unit-text', unit.text);
    card.append(meta, body);

    body.addEventListener('mouseup', () => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed) return;
      const text = selection.toString().trim();
      if (!text) return;
      if (!body.contains(selection.anchorNode) || !body.contains(selection.focusNode)) return;
      card.dataset.selection = text;
      button.classList.add('has-selection');
      button.textContent = '＋ 标为红线（选中）';
    });

    button.addEventListener('click', () => {
      const text = (card.dataset.selection || unit.text).trim();
      delete card.dataset.selection;
      button.classList.remove('has-selection');
      button.textContent = '＋ 标为红线';
      window.getSelection()?.removeAllRanges();
      addGold(text, {
        scope: unit.scope || '',
        location: [unit.part, unit.locator].filter(Boolean).join(' · '),
      });
    });

    fragment.append(card);
  }

  if (!shown) {
    fragment.append(el('p', 'gold-empty', filter ? '没有匹配的原文段落。' : '没有读到原文段落。'));
  }
  container.append(fragment);
  $('#unitCount').textContent = filter ? `${shown} / ${S.units.length} 段` : `共 ${S.units.length} 段`;
}

function syncMarks() {
  const goldKeys = S.gold.map((row) => normText(row.text)).filter(Boolean);
  for (const card of $$('.unit', $('#units'))) {
    const marked = isMarked(card.dataset.text || '', goldKeys);
    card.classList.toggle('marked', marked);
    const button = $('.mark', card);
    if (!button) continue;
    button.classList.toggle('done', marked);
    if (!card.dataset.selection) button.textContent = marked ? '已标' : '＋ 标为红线';
  }
}

/* ------------------------------------------------------------------- gold */

function goldSummary() {
  const counts = { 有效: 0, 不适用: 0, 存疑: 0 };
  for (const row of S.gold) counts[row.status] = (counts[row.status] || 0) + 1;
  $('#goldSummary').textContent =
    `有效 ${counts['有效']} · 不适用 ${counts['不适用']} · 存疑 ${counts['存疑']} · 共 ${S.gold.length} 条`;
}

function renderGold() {
  const list = $('#goldList');
  list.textContent = '';
  goldSummary();

  if (!S.gold.length) {
    list.append(el('p', 'gold-empty',
      '还没有标记红线。\n在左侧原文里选中触发条件所在的一句，点「＋ 标为红线」；\n整段都是红线时直接点，不用先选中。'));
    return;
  }

  S.gold.forEach((row, index) => {
    const card = el('article', 'gold-item');
    card.dataset.status = row.status;
    card.dataset.index = String(index);

    const head = el('div', 'gold-head');
    head.append(el('span', 'gid', `G${String(index + 1).padStart(3, '0')}`));
    const select = el('select', 'status');
    select.dataset.v = row.status;
    for (const status of STATUSES) {
      const option = el('option', null, status);
      option.value = status;
      if (status === row.status) option.selected = true;
      select.append(option);
    }
    head.append(select);
    const remove = el('button', 'icon del', '×');
    remove.title = '删除这条';
    head.append(remove);

    const textarea = el('textarea', 'gtext');
    textarea.value = row.text;
    textarea.rows = Math.min(5, Math.max(2, Math.ceil(row.text.length / 38)));

    const fields = el('div', 'gold-fields');
    fields.append(
      field('包件', row.scope, (value) => { row.scope = value; markDirty(); }),
      field('位置', row.location, (value) => { row.location = value; markDirty(); }),
      field('备注', row.note, (value) => { row.note = value; markDirty(); }),
    );

    card.append(head, textarea, fields);
    list.append(card);

    select.addEventListener('change', () => {
      row.status = select.value;
      select.dataset.v = row.status;
      card.dataset.status = row.status;
      goldSummary();
      markDirty();
    });

    textarea.addEventListener('input', () => {
      row.text = textarea.value;
      textarea.rows = Math.min(6, Math.max(2, Math.ceil(textarea.value.length / 38)));
      goldSummary();
      markDirty();
    });
    textarea.addEventListener('blur', syncMarks);

    remove.addEventListener('click', () => {
      S.gold.splice(index, 1);
      renderGold();
      syncMarks();
      markDirty();
    });
  });
}

function field(label, value, onInput) {
  const wrapper = el('label');
  wrapper.append(document.createTextNode(label));
  const input = el('input');
  input.type = 'text';
  input.value = value || '';
  input.addEventListener('input', () => onInput(input.value));
  wrapper.append(input);
  return wrapper;
}

function addGold(text, meta = {}, options = {}) {
  const value = String(text || '').trim();
  if (!value) return;
  S.gold.push({
    id: '', text: value, status: '有效',
    consequence: '', certainty: '',
    scope: meta.scope || '', location: meta.location || '', note: '',
  });
  renderGold();
  syncMarks();
  markDirty();
  const cards = $$('.gold-item', $('#goldList'));
  const last = cards[cards.length - 1];
  if (last) {
    last.scrollIntoView({ block: 'nearest' });
    const textarea = $('textarea', last);
    if (textarea) {
      textarea.focus({ preventScroll: true });
      if (options.select) textarea.select();
    }
  }
}

/* ------------------------------------------------------------------- save */

function markDirty() {
  S.dirty = true;
  S.editVersion += 1;
  setMeta('未保存');
  if (S.metrics) $('#staleNote').hidden = false;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveGold().catch(() => {}), 900);
}

async function saveGold() {
  if (!S.doc) return null;
  clearTimeout(saveTimer);
  const version = S.editVersion;
  const rows = S.gold.map((row) => ({ ...row }));
  const response = await api(`/api/docs/${encodeURIComponent(S.doc.id)}/gold`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ gold: rows }),
  });
  if (version === S.editVersion) {
    S.dirty = false;
    if (response.gold.length === S.gold.length) {
      // Keep the same row objects the editor is bound to; only refresh ids.
      response.gold.forEach((saved, index) => { S.gold[index].id = saved.id; });
    } else {
      S.gold = response.gold;
      renderGold();
    }
    setMeta('已保存');
    const doc = S.docs.find((item) => item.id === S.doc.id);
    if (doc) doc.gold = S.gold.length;
    renderDocList();
  }
  return response;
}

async function flushSave() {
  clearTimeout(saveTimer);
  if (S.dirty) await saveGold();
}

/* --------------------------------------------------------------- evaluate */

async function evaluateDoc() {
  if (!S.doc) return;
  if (!S.gold.length) {
    toast('请先标出至少一条红线，再计算指标。');
    return;
  }
  if (!S.gold.some((row) => row.status === '有效')) {
    toast('当前金标都处于「不适用」或「存疑」，没有可计入的有效红线。');
    return;
  }
  await flushSave();
  setBusy(true, '正在对照系统识别结果…');
  try {
    const data = await api('/api/evaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: S.doc.id, gold: S.gold }),
    });
    S.metrics = data.metrics;
    S.details = data.details || [];
    S.reasons = data.reasons || [];
    renderMetrics(data);
    renderDetails();
    renderReasons();
    if ($('#showSystem').checked) renderSystem();
    $('#staleNote').hidden = true;
    $('#resultSection').hidden = false;
    $('#resultSection').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    setBusy(false);
  }
}

function renderMetrics(data) {
  const m = S.metrics;
  const box = $('#metrics');
  box.textContent = '';
  box.hidden = false;

  const cards = [
    ['金标条数', m['金标条数'], ''],
    ['系统条数', m['系统条数'], ''],
    ['命中', m['命中'], 'good'],
    ['漏报', m['漏报'], m['漏报'] ? 'bad' : ''],
    ['误报', m['误报'], m['误报'] ? 'warn' : ''],
    ['精确率', pct(m['精确率']), m['精确率'] == null ? '' : (m['精确率'] >= 0.85 ? 'good' : 'bad')],
    ['召回率', pct(m['召回率']), m['召回率'] == null ? '' : (m['召回率'] >= 0.95 ? 'good' : 'bad')],
    ['F1', pct(m['F1']), ''],
  ];

  for (const [label, value, tone] of cards) {
    const card = el('div', `metric ${tone}`.trim());
    card.append(el('span', null, label), el('b', null, String(value)));
    box.append(card);
  }

  const enough = m['精确率'] != null && m['召回率'] != null;
  const pass = enough && m['召回率'] >= 0.95 && m['精确率'] >= 0.85;
  const gate = el('div', 'gate');
  const pill = el('span', `pill ${enough ? (pass ? 'pass' : 'fail') : 'na'}`,
    enough ? (pass ? '达到参考门槛' : '未达参考门槛') : '暂无数据');
  gate.append(pill);
  const detail = el('span', null,
    `参考门槛：召回率 ≥ 95%，精确率 ≥ 85%。宽松口径 精确 ${pct(m['宽松精确率'])} / 召回 ${pct(m['宽松召回率'])}；` +
    `重复 ${m['重复']} 条，存疑忽略 ${m['忽略']} 条` +
    (data.merged ? `，金标重复合并 ${data.merged} 条` : '') + '。');
  gate.append(detail);
  box.append(gate);
}

function renderDetails() {
  const tbody = $('#detailTable tbody');
  tbody.textContent = '';
  for (const row of S.details) {
    const tr = el('tr');
    const first = el('td');
    first.append(el('span', `badge ${row.kind}`, KIND_LABEL[row.kind] || row.kind));
    tr.append(first);

    tr.append(el('td', null, row.gold ? row.gold.text : '—'));
    tr.append(el('td', null, row.prediction ? row.prediction.text : '—'));
    tr.append(el('td', null, row.reason || '—'));
    tr.append(el('td', 'cov', row.coverage == null ? '—' : String(row.coverage)));
    tbody.append(tr);
  }
}

function renderReasons() {
  const box = $('#reasons');
  box.textContent = '';
  for (const item of S.reasons) {
    box.append(el('span', 'reason-chip',
      `${KIND_LABEL[item.kind] || item.kind} · ${item.reason || '未注明'} ${item.count}`));
  }
}

/* ----------------------------------------------------------------- system */

function renderSystem() {
  const redlines = S.predictions.filter((p) => p.category === 'redline');
  $('#sysCount').textContent = `共 ${redlines.length} 条`;
  $('#sysNote').textContent = '仅显示废标红线；其他四类不参与本次评测';
  const list = $('#systemList');
  list.textContent = '';

  const status = new Map();
  for (const row of S.details) {
    if (row.prediction) status.set(row.prediction.id, row.kind);
  }

  if (!redlines.length) {
    list.append(el('p', 'muted small', '系统没有识别到废标红线。'));
    return;
  }

  for (const item of redlines) {
    const card = el('div', 'sys-item');
    const meta = el('div', 'sys-meta');
    const kind = status.get(item.id);
    if (kind) meta.append(el('span', `badge ${kind}`, KIND_LABEL[kind] || kind));
    if (item.label) meta.append(el('span', 'chip', item.label));
    if (item.scope) meta.append(el('span', 'chip', item.scope));
    if (item.location) meta.append(el('span', null, item.location));
    card.append(meta, el('div', 'sys-text', item.text));
    list.append(card);
  }
}

/* ---------------------------------------------------------------- overview */

async function openOverview() {
  await flushSave();
  setBusy(true, '正在评测全部文件…');
  try {
    const data = await api('/api/evaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const body = renderOverview(data);
    const report = el('a', null, '下载 report.md');
    report.href = '/api/report.md';
    const details = el('a', null, '下载 details.csv');
    details.href = '/api/details.csv';
    openModal('全部评测', body, [report, details]);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    setBusy(false);
  }
}

function renderOverview(data) {
  const wrapper = document.createDocumentFragment();
  if (!data.documents.length) {
    wrapper.append(el('p', 'muted', '评测集里还没有文件。'));
    return wrapper;
  }

  const micro = data.micro;
  const summary = el('div', 'metrics');
  const cards = [
    ['文件数', data.documents.length, ''],
    ['金标条数', micro['金标条数'], ''],
    ['命中', micro['命中'], 'good'],
    ['漏报', micro['漏报'], micro['漏报'] ? 'bad' : ''],
    ['误报', micro['误报'], micro['误报'] ? 'warn' : ''],
    ['精确率', pct(micro['精确率']), ''],
    ['召回率', pct(micro['召回率']), ''],
    ['F1', pct(micro['F1']), ''],
  ];
  for (const [label, value, tone] of cards) {
    const card = el('div', `metric ${tone}`.trim());
    card.append(el('span', null, label), el('b', null, String(value)));
    summary.append(card);
  }
  wrapper.append(summary);

  const macro = el('p', 'muted small',
    `宏平均：精确率 ${pct(data.macro['精确率'])}，召回率 ${pct(data.macro['召回率'])}。` +
    '微平均按所有条款合并计算，为主指标。');
  wrapper.append(macro);

  const table = el('table', 'overview-table');
  const thead = el('thead');
  const headRow = el('tr');
  for (const title of ['文件', '金标', '系统', '命中', '漏报', '误报', '精确率', '召回率', '备注']) {
    headRow.append(el('th', null, title));
  }
  thead.append(headRow);
  const tbody = el('tbody');

  for (const doc of data.documents) {
    const m = doc.metrics;
    const tr = el('tr');
    tr.append(el('td', null, doc.name));
    tr.append(el('td', null, String(m['金标条数'])));
    tr.append(el('td', null, String(m['系统条数'])));
    tr.append(el('td', null, String(m['命中'])));
    tr.append(el('td', null, String(m['漏报'])));
    tr.append(el('td', null, String(m['误报'])));
    tr.append(el('td', 'num-strong', pct(m['精确率'])));
    tr.append(el('td', 'num-strong', pct(m['召回率'])));
    tr.append(el('td', 'muted', doc.error ? `提取失败：${doc.error}` : (doc.merged ? `合并 ${doc.merged} 条` : '')));
    tr.style.cursor = 'pointer';
    tr.title = '打开这份文件';
    tr.addEventListener('click', () => {
      closeModal();
      openDoc(doc.id);
    });
    tbody.append(tr);
  }

  const total = el('tr', 'total');
  total.append(el('td', null, '合计（微平均）'));
  total.append(el('td', null, String(micro['金标条数'])));
  total.append(el('td', null, String(micro['系统条数'])));
  total.append(el('td', null, String(micro['命中'])));
  total.append(el('td', null, String(micro['漏报'])));
  total.append(el('td', null, String(micro['误报'])));
  total.append(el('td', null, pct(micro['精确率'])));
  total.append(el('td', null, pct(micro['召回率'])));
  total.append(el('td', null, ''));
  tbody.append(total);
  table.append(thead, tbody);
  const wrap = el('div', 'table-wrap');
  wrap.append(table);
  wrapper.append(wrap);
  return wrapper;
}

/* ------------------------------------------------------------------ modal */

function openModal(title, body, actions = []) {
  $('#modalTitle').textContent = title;
  const container = $('#modalBody');
  container.textContent = '';
  container.append(body);
  const bar = $('#modalActions');
  bar.textContent = '';
  for (const action of actions) bar.append(action);
  $('#overlay').hidden = false;
}

function closeModal() {
  $('#overlay').hidden = true;
  $('#modalBody').textContent = '';
  $('#modalActions').textContent = '';
}

function openHelp() {
  const body = el('div');
  const sections = [
    ['怎么数', '以招标文件原件为准通读全文（含表格、附件和补遗）。标的是“原文写明会或可能导致否决投标、响应无效、不予受理/拒收、取消资格、视为放弃”的一个触发条件。'],
    ['标几条', '列表式“有下列情形之一的”每个子项算 1 条，引导语本身不算；一句里用“或”“、”并列多个条件但只有一个后果的，算 1 条；同一条文多处出现只算 1 条；分别适用于不同包件的各算 1 条。'],
    ['不标什么', '标题和引导语、程序性说明（如评委会对否决情况作出书面说明）、否定或豁免表述（如“不作为否决依据”）、兜底条款、评分扣分、合同履约违约、项目整体废标（如有效供应商不足三家）。'],
    ['状态怎么选', '有效：计入精确率和召回率。不适用：原文有、但本项目设置排除了（如写明不收保证金时的保证金条款）；系统列出即算误报。存疑：确实判断不了，不计入指标，只单独统计。'],
    ['指标怎么算', '精确率 = 命中 ÷ 系统条数；召回率 = 命中 ÷ 有效金标条数；F1 为两者调和平均。匹配按金标文字在系统条目中的连续覆盖率 ≥ 0.8，一对一配对。'],
    ['怎么避免自证', '建议先盲标（不要打开“显示系统结果”），标完再计算；条件允许时两人背靠背标注，用 scripts/eval_redline.py agree 算一致性 ≥ 0.90 后再作为基线。'],
  ];
  for (const [title, text] of sections) {
    const block = el('div', 'help-block');
    block.append(el('h3', null, title), el('p', null, text));
    body.append(block);
  }
  openModal('使用说明', body);
}

/* ------------------------------------------------------------------- bind */

function bind() {
  $('#helpBtn').addEventListener('click', openHelp);
  $('#overviewBtn').addEventListener('click', openOverview);
  $('#refreshBtn').addEventListener('click', async () => {
    try {
      await flushSave();
      await loadState();
      toast('已刷新', 'ok');
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  $('#modalClose').addEventListener('click', closeModal);
  $('#overlay').addEventListener('click', (event) => {
    if (event.target === $('#overlay')) closeModal();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeModal();
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
      event.preventDefault();
      flushSave().then(() => toast('已保存', 'ok')).catch((e) => toast(e.message, 'error'));
    }
  });

  $('#uploadBtn').addEventListener('click', () => $('#fileInput').click());
  $('#fileInput').addEventListener('change', uploadFile);

  $('#calcBtn').addEventListener('click', evaluateDoc);
  $('#xlsxBtn').addEventListener('click', async () => {
    await flushSave();
    window.location.href = `/api/docs/${encodeURIComponent(S.doc.id)}/annotation.xlsx`;
  });
  $('#deleteBtn').addEventListener('click', async () => {
    if (!confirm(`将「${S.doc.id}」整个目录移入 _回收站？`)) return;
    try {
      await api(`/api/docs/${encodeURIComponent(S.doc.id)}`, { method: 'DELETE' });
      S.doc = null;
      $('#docView').hidden = true;
      $('#welcome').hidden = false;
      await loadState();
      toast('已移入回收站', 'ok');
    } catch (error) {
      toast(error.message, 'error');
    }
  });

  $('#addGoldBtn').addEventListener('click', () => {
    addGold('（请填写红线原文）', {}, { select: true });
  });

  $('#showSystem').addEventListener('change', (event) => {
    $('#systemSection').hidden = !event.target.checked;
    if (event.target.checked) renderSystem();
  });

  let searchTimer = null;
  $('#searchInput').addEventListener('input', (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      S.filter = event.target.value;
      renderUnits();
      syncMarks();
    }, 160);
  });

  window.addEventListener('beforeunload', (event) => {
    if (S.dirty) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
}

async function uploadFile(event) {
  const file = event.target.files && event.target.files[0];
  event.target.value = '';
  if (!file) return;
  const form = new FormData();
  form.append('file', file, file.name);
  setBusy(true, '正在导入并解析…');
  try {
    const response = await fetch('/api/import', { method: 'POST', body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '导入失败');
    await loadState();
    await openDoc(data.id);
    toast(`已导入：${file.name}`, 'ok');
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    setBusy(false);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  bind();
  loadState().catch((error) => toast(error.message, 'error'));
});
