"""Source-backed outline editions and project calibration."""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import framework_review
import framework_service
import writing_model

KIMI = 'moonshotai/kimi-k3.1-pro'
OUTLINE_PROMPT_VERSION = 2
CHINESE = r'[一二三四五六七八九十百]+'
NUMBER = re.compile(rf'^(?:第{CHINESE}[章节篇部分]\s*|{CHINESE}[、.．]\s*|'
                    rf'[（(](?:{CHINESE}|\d+)[）)]\s*|\d+(?:[.．]\d+)*(?:[.．、)）]\s*|\s+))')
TOP = re.compile(rf'^{CHINESE}、\S')
CHILD = re.compile(r'^(\d{1,2})、(.+)')
PROJECT_SCOPE = r'(?:本项目|本标段|本标包|本包件|本采购|本次采购)'
NOT_APPLICABLE = re.compile(rf'(?:[（(]\s*(?:{PROJECT_SCOPE}\s*)?不(?:涉及|适用)\s*[）)]'
                            rf'|[\s：:—]+{PROJECT_SCOPE}\s*不(?:涉及|适用))\s*[。.]*$')
EXCLUSION_NOTE = re.compile(rf'[（(]?\s*{PROJECT_SCOPE}\s*不(?:涉及|适用)\s*[）)]?[。.]?')
FORM_TITLE = re.compile(r'投标函|承诺书|身份证明|授权委托书|声明函|申报表|(?:偏离|报价|一览|情况|汇总|简历|索引)表|信息表|授权说明')
FRONT_TITLES = {'投标文件封面格式', '响应文件封面格式', '初步评审索引表', '详细评审索引表'}
LICENSE_NOTICE = '目录未单列营业执照，请按采购要求核对其所在章节及应附材料。'


def title_key(value):
    value = NUMBER.sub('', value.strip())
    value = re.sub(r'[（(]负责人[）)]', '', value)
    value = re.sub(r'[★☆\s]*(?:[（(]必需[）)])?\s*$', '', value)
    return re.sub(r'[\s:：()（）]', '', value)


def procurement(source):
    framework_service.metric_module()
    from docx import Document
    from outline_inputs import source_numbered_paragraphs
    from plan_framework import Source
    document = Source(Path(source))
    start = next((n.index for n in document.nodes if n.tag == 'p' and
                  re.fullmatch(r'第[一二三四五六七八九十\d]+章\s+(?:投标|响应|参选|应答)文件格式', n.text)),
                 None)
    if start is None:
        start = document.chapter_start()
    for index, text in source_numbered_paragraphs(Document(source)).items():
        if index < document.end and index != start:
            document.nodes[index].text = text
    lower = start + 1 if start is not None else 0
    upper = next((node.index for node in document.nodes[lower:]
                  if start is not None and node.tag == 'p'
                  and re.match(r'^第[一二三四五六七八九十\d]+章\s', node.text)), document.end)
    nodes = document.nodes[lower:upper]
    return document, lower, upper, nodes


def source_units(nodes, catalog, required):
    units = []
    excluded_top = False
    for node in nodes:
        if node.tag not in {'p', 'tbl'} or not node.text:
            continue
        explicit = node.tag == 'p' and (catalog.get(node.index, {}).get('not_applicable')
                                        or bool(NOT_APPLICABLE.search(node.text)))
        numbered = node.index in required
        if numbered:
            excluded_top = explicit
        unit = {'index': node.index, 'text': node.text, 'kind': node.tag}
        if excluded_top or explicit:
            unit['not_applicable'] = True
        elif numbered:
            unit['prescribed_numbered_heading'] = True
        units.append(unit)
    return units


def indexed_catalog(document, nodes, automatic):
    # Source anchors for copying an original form are distinct from the AI outline.
    # A long, explicit Chinese-numbered list under the prescribed format chapter
    # also detects valid-looking model output which omits an entire buyer item.
    top = [n for n in nodes if n.tag == 'p' and TOP.match(n.text) and len(n.text) < 150]
    top = top if len(top) >= 3 and top[0].text.startswith('一、') else []
    anchors = {}
    for i, node in enumerate(top):
        anchors[node.index] = {'source_index': node.index, 'text': node.text, 'level': 1}
        stop = top[i + 1].index if i + 1 < len(top) else nodes[-1].index + 1
        if not re.search(r'资格证明|技术方案', node.text):
            continue
        next_number = 1
        for child in document.nodes[node.index + 1:stop]:
            match = CHILD.match(child.text) if child.tag == 'p' else None
            if match and int(match[1]) == next_number and len(child.text) < 170:
                anchors[child.index] = {'source_index': child.index, 'text': child.text, 'level': 2}
                next_number += 1
    anchors.update({item['source_index']: item for item in automatic})
    if not top:
        for node in nodes:
            if (node.tag == 'p' and 2 <= len(node.text) <= 110 and FORM_TITLE.search(node.text)
                    and (NUMBER.match(node.text) or any(next_node.tag == 'tbl' for next_node in
                           document.nodes[node.index + 1:node.index + 4]))):
                anchors.setdefault(node.index, {'source_index': node.index, 'text': node.text, 'level': 1})
    visible = [node for node in nodes if node.text or node.tag == 'tbl' or node.graphics]
    notes = {node.index for node, following in zip(visible, visible[1:])
             if following.tag == 'p' and EXCLUSION_NOTE.fullmatch(following.text.strip())}
    for index, anchor in anchors.items():
        if index in notes or NOT_APPLICABLE.search(anchor['text']):
            anchor['not_applicable'] = True
    return anchors, {n.index for n in top}


def bind_plan_requirements(plan, requirements):
    blocks = plan['blocks']
    headings = [b for b in blocks if b['kind'] == 'heading']
    tech = next((b for b in headings if '技术方案' in b['text']), None)
    for heading in headings:
        heading['requirement_ids'] = []
    for req in requirements:
        indices = {ref.get('index') for ref in req['references'] if ref.get('part') == '主件'}
        matches = [heading for heading in headings if indices.intersection(heading.get('source_indices', []))]
        if not matches and req['category'] in {'spec', 'score'} and tech:
            matches = [tech]
        for heading in matches:
            heading['requirement_ids'].append(req['id'])
    return plan


def parse_rows(record, body):
    import outline_inputs
    from server import check_docx, decode_upload

    supplied = sum(field in body for field in ('docx', 'text', 'items', 'automatic'))
    if supplied != 1:
        raise ValueError('请选择一种目录来源。')
    if 'docx' in body:
        name, raw = decode_upload(body['docx'], 30 * 1024 * 1024)
        if not name.lower().endswith('.docx'):
            raise ValueError('自定义目录请上传 DOCX 文件。')
        check_docx(name, raw, procurement=True)
        return outline_inputs.parse_docx(raw, include_positions=True), 'custom'
    if 'text' in body:
        return outline_inputs.parse_text(body['text']), 'custom'
    if 'automatic' in body:
        snapshot = current_automatic(record)
        if not body['automatic'] or not snapshot.get('items'):
            raise ValueError('请先完成 AI 目录识别。')
        return snapshot['items'], 'automatic'
    if record['outline_mode'] == 'pending':
        raise ValueError('请先导入或生成目录。')
    return body['items'], record['outline_mode']


def checked_rows(rows, previous, automatic, restorations):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError('目录须保留 1 至 1000 个章节。')
    before = 0
    seen = set()
    for row in rows:
        level = row.get('level') if isinstance(row, dict) else None
        if type(level) is not int or not 1 <= level <= min(9, before + 1):
            raise ValueError('目录层级须从 1 开始且不能跨级。')
        before = level
        text = framework_review.text(row.get('text'), '章节标题', 220, True)
        ident = row.get('id')
        if ident and (not isinstance(ident, str) or ident in seen or ident not in previous):
            raise ValueError('目录章节不存在或重复，请重新读取。')
        if ident:
            seen.add(ident)
        requested = row.get('form_source_index')
        restore = restorations.get(requested) if not ident and type(requested) is int else None
        if restore is not None:
            text = restore['text']
        index = (row.get('source_index') if automatic else restore['source_index'] if restore
                 else previous.get(ident, {}).get('source_index'))
        yield {'kind': 'heading', 'text': text, 'level': level, 'numbered': False,
               'source_index': index, **({'bookmark': ident} if ident else {})}


def assign_sources(headings, catalog):
    parents = []
    used = {b['source_index'] for b in headings if b['source_index'] is not None}
    for heading in headings:
        while parents and parents[-1]['level'] >= heading['level']:
            parents.pop()
        if heading['source_index'] is None:
            key = title_key(heading['text'])
            candidates = [i for i, anchor in catalog.items() if i not in used
                          and title_key(anchor['text']) == key]
            if len(candidates) > 1:
                parent = next((p['source_index'] for p in reversed(parents) if p['source_index'] in catalog), None)
                if parent is not None:
                    level = catalog[parent]['level']
                    end = min((i for i, item in catalog.items()
                               if i > parent and item['level'] <= level), default=float('inf'))
                    candidates = [i for i in candidates if parent < i < end]
            if len(candidates) == 1:
                heading['source_index'] = candidates[0]
                used.add(candidates[0])
        parents.append(heading)


def copy_blocks(document, start, end):
    return [{'kind': 'copy', 'start': n.index, 'preserve_format': True}
            for n in document.nodes[start:end]
            if n.tag in {'p', 'tbl', 'sdt'} and (n.full or n.graphics or n.tag == 'tbl')]


def prepare(record, source, body, requirements):
    rows, mode = parse_rows(record, body)
    old_plan = record['_plan']
    prefix, previous_sections = framework_review.sections(old_plan)
    previous = {section[0]['bookmark']: section[0] for section in previous_sections}
    document, lower, upper, nodes = procurement(source)
    automatic = record.get('_auto_outline', {}).get('items', []) if 'automatic' in body or mode == 'automatic' else []
    catalog, _ = indexed_catalog(document, nodes, automatic)
    # Sticky original anchors keep renamed/reordered chapters attached to their source.
    for heading in previous.values():
        i = heading.get('source_index')
        if 'items' in body and old_plan.get('preserve_outline') and isinstance(i, int) and lower <= i < upper:
            catalog.setdefault(i, {'source_index': i, 'text': document.nodes[i].text, 'level': heading['level']})
    restorations = {row['source_index']: row for row in record.get('outline_missing', [])}
    headings = list(checked_rows(rows, previous, 'automatic' in body, restorations))
    indices = [row['source_index'] for row in headings if row['source_index'] is not None]
    if len(indices) != len(set(indices)):
        raise ValueError('同一采购章节只可恢复一次，请重新读取目录。')
    assign_sources(headings, catalog)
    plan = {k: v for k, v in old_plan.items() if k not in {'blocks', 'parts', 'books', 'book_basis', 'review'}}
    plan.update(blocks=headings, preserve_outline=True, review=[])
    framework_review.assign_bookmarks(plan)

    source_to_heading = {h['source_index']: h for h in headings if h['source_index'] in catalog}
    by_bookmark = {h['bookmark']: h for h in headings}
    raw_assignments = body.get('form_assignments', record.get('outline_form_assignments', {}))
    if not isinstance(raw_assignments, dict):
        raise ValueError('采购原表关联格式无效。')
    assignments = {str(i): raw_assignments[str(i)] for i in catalog if str(i) in raw_assignments
                   and isinstance(raw_assignments[str(i)], str) and raw_assignments[str(i)] in by_bookmark}
    source_order = sorted(catalog)
    owners = {}
    missing = []
    inapplicable = []
    parents = []
    for pos, index in enumerate(source_order):
        anchor = catalog[index]
        while parents and parents[-1]['level'] >= anchor['level']:
            parents.pop()
        form_title = anchor['text']
        end = source_order[pos + 1] if pos + 1 < len(source_order) else upper
        source_blocks = copy_blocks(document, index + 1, end)
        has_table = any(document.nodes[block['start']].tag == 'tbl' for block in source_blocks)
        explicit_form = bool(FORM_TITLE.search(form_title) and len(form_title) <= 95)
        optional_label = anchor.get('not_applicable', False)
        optional = optional_label or bool(parents and parents[-1]['not_applicable'])
        target = source_to_heading.get(index) or by_bookmark.get(assignments.get(str(index)))
        if optional_label:
            inapplicable.append({'source_index': index, 'text': form_title})
        if target is None and not optional and (has_table or explicit_form):
            missing.append({'source_index': index, 'text': form_title, 'level': anchor['level']})
        elif target is None and not optional and parents:
            target = parents[-1]['target']
        parents.append({**anchor, 'not_applicable': optional, 'target': target})
        if target is None:
            continue
        if index != target['source_index']:
            # A user may host a missing fixed form under an existing chapter;
            # retain the original title and table without inserting a TOC node.
            source_blocks.insert(0, {'kind': 'copy', 'start': index, 'preserve_format': True,
                                     'plain_heading': True})
        target.setdefault('source_indices', []).extend(range(index, end))
        if has_table or explicit_form:
            target['locked_format'] = True
        if optional and index == target['source_index']:
            target['not_applicable'] = True
        owners.setdefault(target['bookmark'], []).extend(source_blocks)
    if not any('营业执照' in h['text'] for h in headings):
        plan['review'].append(LICENSE_NOTICE)
    body_blocks = []
    for heading in headings:
        body_blocks.append(heading)
        children = owners.get(heading['bookmark'], [])
        body_blocks.extend(children or ([] if heading.get('not_applicable') else
                           [{'kind': 'slot', 'text': '【待编】填写有依据的响应内容及证明材料。'}]))
    # Pre-outline buyer forms (for example the two evaluation indexes) are
    # independent of the numbered directory and stay in front of it.
    if lower and source_order:
        front = copy_blocks(document, lower, source_order[0])
    else:
        front = []
    cover = [block for block in prefix if block['kind'].startswith('cover') or block['kind'] == 'toc']
    plan['blocks'] = cover + front + body_blocks
    update = {'outline_mode': mode, 'outline_missing': missing, 'outline_form_assignments': assignments,
              'outline_inapplicable': inapplicable}
    return bind_plan_requirements(plan, requirements), update


def model_call(system, content, schema):
    return writing_model.complete_json(system, content, schema, model=KIMI, reasoning='low',
                                       timeout=75, chat_stream=True, max_tokens=12000)


def auto_outline(source, call=None):
    import outline_inputs
    document, lower, upper, nodes = procurement(source)
    catalog, required = indexed_catalog(document, nodes, [])
    units = source_units(nodes, catalog, required)
    items = outline_inputs.generate_outline(units, call or model_call)
    required = {unit['index'] for unit in units if unit.get('prescribed_numbered_heading')}
    retained = {item['source_index'] for item in items if item['level'] == 1}
    if required - retained:
        raise ValueError('Kimi 遗漏了采购原文中的正式目录项，请重试或使用自定义目录。')
    if required:
        first = min(required)
        items = [item for item in items if item['source_index'] >= first
                 or item['text'] not in FRONT_TITLES]
    return items


def _paths(items):
    parents = []
    for position, row in enumerate(items):
        level = row.get('level', 1)
        while parents and parents[-1]['level'] >= level:
            parents.pop()
        parents.append(row)
        yield {**row, 'position': position + 1, 'path': ' / '.join(p['text'] for p in parents)}


def comparison_rows(record, automatic):
    custom = list(_paths(record.get('outline', [])))
    auto = list(_paths(automatic))
    sources = {s['id']: s for s in record['sources']}
    requirements = framework_review.expand_requirements(record['requirements'], record['sources'])
    index_titles = {h['bookmark']: h for h in record['_plan']['blocks'] if h['kind'] == 'heading'}
    rows = []
    for requirement in requirements:
        refs = requirement['references']
        main = [r['index'] for r in refs if r.get('part') == '主件' and type(r.get('index')) is int]
        linked = []
        for index in main:
            previous = next((row for row in reversed(auto) if row['source_index'] <= index), None)
            if previous and previous not in linked:
                linked.append(previous)
        if not linked and requirement['category'] in {'spec', 'score'}:
            linked = [row for row in auto if '技术方案' in row['text'] and row['level'] == 1][:1]
        custom_matches = [row for row in custom if requirement['id'] in row.get('requirement_ids', [])]
        rows.append({'id': requirement['id'], 'text': requirement['text'],
                     'references': [sources.get(r.get('id') or r.get('source_id'), r) for r in refs],
                     'automatic': linked, 'custom': custom_matches})
    for row in auto:
        index = row['source_index']
        matches = [item for item in custom if index == index_titles[item['id']].get('source_index')
                   or index in index_titles[item['id']].get('source_indices', [])]
        rows.append({'id': f'format-{index}', 'text': row['text'], 'references': [],
                     'automatic': [row], 'custom': matches})
    for row in record.get('outline_inapplicable', []):
        index = row['source_index']
        matches = [item for item in custom if index == index_titles[item['id']].get('source_index')]
        rows.append({'id': f'format-{index}', 'text': row['text'], 'references': [],
                     'automatic': [], 'custom': matches, 'not_applicable': True})
    return rows


def basis(record):
    value = [OUTLINE_PROMPT_VERSION, record.get('outline_mode'), record.get('outline', []),
             record.get('requirements', [])]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def current_automatic(record):
    snapshot = record.get('_auto_outline', {})
    return snapshot if snapshot.get('prompt_version') == OUTLINE_PROMPT_VERSION else {}


class OutlineJobs:
    def __init__(self, store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='bid-outline')
        self.running = set()
        self.closed = False

    def close(self):
        with self.store.lock:
            self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)

    def describe(self, key, kind):
        record = self.store.require('framework', key)
        if not record.get('review_version'):
            raise ValueError('此项目使用历史目录流程。')
        snapshot = record.get('_calibration' if kind == 'calibration' else '_outline_job', {})
        if kind == 'automatic':
            automatic = current_automatic(record)
            if automatic.get('items'):
                return {'status': 'completed', **automatic}
            if snapshot.get('prompt_version') != OUTLINE_PROMPT_VERSION:
                snapshot = {}
        status = snapshot.get('status', 'idle')
        if key in self.running and kind == 'calibration' and record.get('outline_mode') == 'custom' and status == 'idle':
            status = 'running'
        elif key not in self.running and status == 'running':
            status = 'failed'
        elif kind == 'calibration' and snapshot.get('basis') != basis(record) and status in {'completed', 'failed'}:
            status = 'stale'
        result = {**snapshot, 'status': status}
        if kind == 'calibration' and status == 'stale':
            result.pop('rows', None)
        return result

    def start(self, key, kind, *, auto=False):
        store = self.store
        field = '_calibration' if kind == 'calibration' else '_outline_job'
        with store.lock:
            if self.closed:
                if auto:
                    return None
                raise ValueError('系统正在关闭，请重启后再生成目录。')
            record = store.require('framework', key)
            permitted = record.get('review_version') and record.get('requirements_confirmed')
            if kind == 'calibration':
                permitted = permitted and record.get('outline_mode') == 'custom'
            if not permitted:
                if auto:
                    return None
                raise ValueError('请先确认采购要求并选择相应目录路径。')
            if key in self.running:
                return self.describe(key, kind)
            if auto and record.get(field, {}).get('status') == 'completed' and record[field].get('basis') == basis(record):
                return self.describe(key, kind)
            snapshot = {'status': 'running', 'model': KIMI, 'basis': basis(record),
                        'prompt_version': OUTLINE_PROMPT_VERSION}
            record[field] = snapshot
            framework_service.save_record(store, record)
            store.db.commit()
            self.running.add(key)
            self.pool.submit(self.run, key, kind, record)
            return self.describe(key, kind)

    def run(self, key, kind, record):
        import tempfile
        import outline_inputs
        store = self.store
        field = '_calibration' if kind == 'calibration' else '_outline_job'
        source_auto = current_automatic(record).get('items')
        snapshot = record[field]
        latest_basis = snapshot['basis']
        try:
            if not source_auto:
                with tempfile.TemporaryDirectory(prefix='bid-outline-') as temporary:
                    source = framework_service.edition_source(store, record, Path(temporary))
                    source_auto = auto_outline(source)
            result = {'status': 'completed', 'model': KIMI}
            if kind == 'automatic':
                result['items'] = source_auto
            else:
                with store.lock:
                    current = store.require('framework', key)
                    latest_basis = basis(current)
                rows = comparison_rows(current, source_auto)
                ranked = sorted(rows, key=lambda r: (r.get('not_applicable', False),
                    bool(r['automatic']) == bool(r['custom']), not r['id'].startswith('format-')))
                inputs = [{'id': r['id'], 'text': r['text'][:450],
                           **{name: [{k: n[k] for k in ('path', 'position', 'level')} for n in r[name][:5]]
                              for name in ('automatic', 'custom')}}
                          for r in ranked if not r.get('not_applicable')][:24]
                try:
                    suggestions = outline_inputs.suggest_calibration(inputs, model_call)
                except ValueError as exc:
                    result.update(status='failed', error=str(exc))
                    suggestions = []
                by_id = {s['requirement_id']: s['suggestion'] for s in suggestions}
                result['rows'] = [{**row, 'suggestion': by_id.get(row['id'], '')} for row in rows]
        except Exception as exc:
            result = {'status': 'failed', 'model': KIMI, 'error': str(exc) if isinstance(exc, ValueError)
                      else 'Kimi 目录任务未完成，请重试。'}
        with store.lock:
            self.running.discard(key)
            current = store.get('framework', key)
            if current is None:
                return
            if source_auto:
                current['_auto_outline'] = {'items': source_auto, 'model': KIMI,
                                            'prompt_version': OUTLINE_PROMPT_VERSION}
            current[field] = {**result, 'basis': latest_basis, 'prompt_version': OUTLINE_PROMPT_VERSION}
            framework_service.save_record(store, current)
            store.db.commit()
            rerun = (kind == 'calibration' and current.get('outline_mode') == 'custom'
                     and latest_basis != basis(current))
            rerun |= (kind == 'automatic' and current.get('outline_mode') == 'custom'
                      and current.get('_calibration', {}).get('basis') != basis(current))
            if rerun:
                self.start(key, 'calibration', auto=True)
