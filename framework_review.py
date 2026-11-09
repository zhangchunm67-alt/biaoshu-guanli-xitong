"""Review state and stable, editable response sections; no external I/O."""
import copy
import re
import uuid

CATEGORIES = ('redline', 'qualification', 'score', 'spec', 'format')
# Qualification and format drive response chapters / fixed forms; deleting them
# without an explicit ack is how material columns silently disappear.
COLUMN_CATEGORIES = ('qualification', 'format')
COLUMN_LABELS = {'qualification': '强制资格要求', 'format': '指定格式'}
GROUP_TITLES = {'qualification': '资格证明与准入响应', 'score': '评分要求响应',
                'spec': '商务技术要求响应', 'format': '指定格式与待补附件'}


def text(value, name, limit, required=False):
    if (not isinstance(value, str) or len(value) > limit
            or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', value)):
        raise ValueError(f'{name}格式不正确或超过 {limit} 字')
    value = value.strip()
    if required and not value:
        raise ValueError(f'请填写{name}')
    return value


def normalize_sources(payload):
    for source in payload['sources']:
        source['source_id'] = source['id']
    sources = {s['id']: s for s in payload['sources']}
    for row in payload['requirements']:
        row['references'] = [copy.deepcopy(sources.get(r.get('id', r.get('source_id')), r))
                             for r in row['references']]
        for ref in row['references']:
            ref['source_id'] = ref.get('id', ref.get('source_id'))
        row.setdefault('note', '')
        row.setdefault('origin', 'rule')
    return payload


def expand_requirements(items, sources):
    """Read both historical embedded references and the canonical ID-only form."""
    registry = {s['id']: s for s in sources}
    result = copy.deepcopy(items)
    for row in result:
        row.setdefault('note', '')
        row.setdefault('origin', 'rule')
        references = []
        for ref in row.get('references', []):
            ident = ref.get('id', ref.get('source_id')) if isinstance(ref, dict) else ref
            if ident not in registry:
                raise ValueError('要求引用的采购原文不存在，请核对已存框架')
            references.append(dict(copy.deepcopy(registry[ident]), source_id=ident))
        row['references'] = references
    return result


def compact_requirements(items):
    """Keep evidence content solely in the immutable source registry."""
    result = copy.deepcopy(items)
    for row in result:
        row.setdefault('note', '')
        row.setdefault('origin', 'rule')
        row['references'] = [{'id': ref.get('id', ref.get('source_id')) if isinstance(ref, dict) else ref}
                             for ref in row.get('references', [])]
    return result


def validate_requirements(items, sources, previous):
    if not isinstance(items, list) or len(items) > 2000:
        raise ValueError('要求清单须为列表且不超过 2000 项')
    by_source = {s['id']: s for s in sources}
    previous_by_id = {r['id']: r for r in previous}
    allowed = set(previous_by_id)
    seen, result = set(), []
    for row in items:
        if not isinstance(row, dict):
            raise ValueError('要求条目格式不正确')
        ident = row.get('id') or ('manual_' + uuid.uuid4().hex[:20])
        if not isinstance(ident, str) or ident in seen or (row.get('id') and ident not in allowed):
            raise ValueError('要求编号重复或不属于当前版本')
        seen.add(ident)
        category = row.get('category')
        if category not in CATEGORIES:
            raise ValueError('请选择五类要求之一')
        refs = row.get('references')
        if not isinstance(refs, list) or not refs or len(refs) > 100:
            raise ValueError('每条要求必须关联采购原文')
        ids = [r.get('source_id', r.get('id')) if isinstance(r, dict) else r for r in refs]
        if any(not isinstance(i, str) or i not in by_source for i in ids):
            raise ValueError('所选原文位置不属于本次采购文件')
        unchanged = ident in allowed and all(row.get(k, '') == previous_by_id[ident].get(k, '')
            for k in ('label', 'text', 'category', 'scope', 'note'))
        if unchanged:
            unchanged = set(ids) == {r.get('source_id', r.get('id')) for r in previous_by_id[ident]['references']}
        # User edits are an interpretation. Original evidence always comes from the stored source.
        result.append({'id': ident, 'category': category,
            'label': text(row.get('label', ''), '要求标题', 300),
            'text': text(row.get('text', ''), '要求内容', 30000, True),
            'scope': text(row.get('scope', ''), '适用范围', 300),
            'note': text(row.get('note', ''), '核对备注', 3000),
            'references': [copy.deepcopy(by_source[i]) for i in dict.fromkeys(ids)],
            'origin': previous_by_id[ident].get('origin', 'rule') if unchanged else 'manual'})
    return result


def removed_column_requirements(previous, current):
    """Qualification/format rows dropped since the last saved interpretation."""
    kept = {row['id'] for row in current}
    return [row for row in previous if row.get('category') in COLUMN_CATEGORIES and row['id'] not in kept]


def require_confirmation_acks(requirements, previous, notes, body):
    """Block confirmations that would rubber-stamp missing response columns."""
    if body.get('confirmed') is not True:
        return
    pending_notes = [note for note in (notes or []) if isinstance(note, str) and note.strip()]
    if pending_notes and body.get('notes_acknowledged') is not True:
        raise ValueError('请先展开并核对待核对事项，勾选确认未遗漏必交材料与栏目后再确认要求。')
    removed = removed_column_requirements(previous, requirements)
    acknowledged = body.get('acknowledged_removed_ids', [])
    if not isinstance(acknowledged, list) or any(not isinstance(item, str) for item in acknowledged):
        raise ValueError('删除栏目确认格式不正确')
    if len(acknowledged) != len(set(acknowledged)):
        raise ValueError('删除栏目确认存在重复项')
    expected = {row['id'] for row in removed}
    if set(acknowledged) != expected:
        if not removed:
            raise ValueError('未删除资格或格式要求，无须提交删除确认。')
        labels = '、'.join((row.get('label') or row.get('text') or '')[:40] for row in removed[:5])
        raise ValueError(
            '确认前须逐项勾选已删除的资格/格式要求，避免漏掉响应栏目：'
            + labels + ('…' if len(removed) > 5 else '')
            + '。也可先恢复这些要求后再确认。')


def sections(plan):
    prefix, result = [], []
    for block in plan['blocks']:
        if block['kind'] == 'heading':
            result.append([block])
        elif result:
            result[-1].append(block)
        else:
            prefix.append(block)
    return prefix, result


def assign_bookmarks(plan):
    used = {b['bookmark'] for b in plan['blocks'] if b['kind'] == 'heading' and b.get('bookmark')}
    counter = max([int(i.removeprefix('BidSection')) for i in used] + [plan.get('chapter_counter', 0)])
    for block in plan['blocks']:
        if block['kind'] == 'heading' and not block.get('bookmark'):
            counter += 1
            block['bookmark'] = f'BidSection{counter}'
    plan['chapter_counter'] = counter


def outline(plan):
    return [{'id': b['bookmark'], 'text': b['text'], 'level': b.get('level', 1),
             'requirement_ids': b.get('requirement_ids', []),
             'locked_format': bool(b.get('locked_format')), 'part': ''}
            for b in plan['blocks'] if b['kind'] == 'heading']


def bind_requirements(plan, requirements):
    """Preserve buyer forms; give unmapped obligations a real response location."""
    plan = copy.deepcopy(plan)
    assign_bookmarks(plan)
    prefix, content = sections(plan)
    by_id = {r['id']: r for r in requirements}
    linked = set()
    generated = {}
    for section in content:
        heading = section[0]
        if heading.get('review_group'):
            generated[heading['review_group']] = section
            heading['requirement_ids'] = []
            continue
        # Direct evidence-to-form mapping is more reliable than matching a short label.
        indices = {heading.get('source_index')}
        for block in section[1:]:
            if block['kind'] == 'copy':
                indices.update(range(block['start'], block.get('end', block['start']) + 1))
            elif block['kind'] == 'score':
                indices.add(block['index'])
        ids = [i for i in heading.get('requirement_ids', []) if i in by_id]
        for requirement in requirements:
            if any(r.get('part') == '主件' and r.get('index') in indices
                   for r in requirement['references']):
                ids.append(requirement['id'])
        heading['requirement_ids'] = list(dict.fromkeys(ids))
        linked.update(ids)
    groups = {}
    group_locations = {}
    books = {s[0]['book_role']: s[0] for s in content if s[0].get('book_role')}

    def response_book(req):
        if not books:
            return None
        if any(b.get('book_origin') == 'inferred' or b.get('book_assignment') == 'content'
               for b in books.values()):
            # Inferred commercial books contain qualifications and administrative
            # documents; keep prices and filing instructions out of technical responses.
            from response_books import content_role
            role = content_role(req.get('label', '') + req['text'], req['category']) or 'commercial'
            if req['category'] == 'qualification' and books.get('qualification'):
                return books['qualification']
            return books.get(role) or books.get('commercial')
        if req['category'] == 'qualification':
            return books.get('qualification') or books.get('technical')
        # A procurement that separates prices must never receive price-related
        # response prompts in its technical book. Evidence already copied into
        # a prescribed form remains bound there above.
        if re.search(r'报价|价格|金额|预算|税率|费用|付款|结算|单价|免费', req['text']):
            return books.get('commercial')
        return books.get('technical') or books.get('qualification')

    for req in requirements:
        if req['id'] in linked:
            continue
        if req['category'] == 'redline' and content:
            # Red lines remain internal review evidence, not extra supplier declarations.
            target = next((s for s in content if s[0].get('origin') != 'user_policy'), content[0])
            target[0]['requirement_ids'].append(req['id'])
            linked.add(req['id'])
            continue
        group = req['category'] + ':' + req.get('scope', '')
        parent = response_book(req)
        if parent:
            group += '@book:' + parent['bookmark']
        group_locations[group] = (req['category'], req.get('scope', ''), parent)
        groups.setdefault(group, []).append(req)
    for group, rows in groups.items():
        category, scope, parent = group_locations[group]
        old = generated.get(group)
        heading = copy.deepcopy(old[0]) if old else {
            'kind': 'heading', 'level': parent.get('level', 1) + 1 if parent else 1, 'review_group': group,
            'text': (scope + ' · ' if scope else '') + GROUP_TITLES.get(category, '采购要求响应')}
        heading['requirement_ids'] = [r['id'] for r in rows]
        # Do not inject review summaries into any buyer-owned fixed form.
        body = [{'kind': 'text', 'text': '按以下采购要求逐项编制响应，保留适用条件并附对应证明。'}]
        for i, row in enumerate(rows, 1):
            label = row.get('label', '').strip()
            caption = label + '：' if label and label not in row['text'] else ''
            body.append({'kind': 'text', 'text': f'{i}. ' + caption + row['text'], 'requirement_id': row['id']})
            body.append({'kind': 'slot', 'text': '【待编】响应说明及证明材料位置：________'})
        section = [heading, *body]
        if old:
            content[content.index(old)] = section
        elif parent:
            location = next(i for i, s in enumerate(content) if s[0].get('bookmark') == parent['bookmark']) + 1
            while location < len(content) and content[location][0].get('level', 1) > parent.get('level', 1):
                location += 1
            content.insert(location, section)
        else:
            content.append(section)
    # Keep stable containers even when their last requirement was removed. A user
    # may have nested custom chapters or authored text below that heading.
    for i, section in enumerate(content):
        group = section[0].get('review_group')
        if group and group not in groups:
            content[i] = [section[0], {'kind': 'slot',
                'text': '【待编】本章关联要求已调整，请核对是否保留本章及其下级内容。'}]
    plan['blocks'] = prefix + [b for s in content for b in s]
    plan['reviewed_requirements'] = copy.deepcopy(requirements)
    assign_bookmarks(plan)
    return plan


def edit_outline(plan, items, requirements):
    if not isinstance(items, list) or not items or len(items) > 1000:
        raise ValueError('目录须保留至少一个章节，且不超过 1000 个章节')
    plan = copy.deepcopy(plan)
    prefix, existing = sections(plan)
    by_id = {s[0]['bookmark']: s for s in existing}
    req_ids = {r['id'] for r in requirements}
    required_formats = {r['id'] for r in requirements if r['category'] == 'format'}
    new, seen, covered = [], set(), set()
    previous_level = 0
    counter = plan.get('chapter_counter', 0)
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('目录条目格式不正确')
        ident = item.get('id')
        if ident:
            if ident not in by_id or ident in seen:
                raise ValueError('目录章节不存在或重复')
            section = copy.deepcopy(by_id[ident])
        else:
            counter += 1
            ident = f'BidSection{counter}'
            section = [{'kind': 'heading', 'bookmark': ident},
                       {'kind': 'slot', 'text': '【待编】填写有依据的响应内容及证明材料。'}]
        seen.add(ident)
        level = item.get('level')
        if type(level) is not int or not 1 <= level <= 9 or level > previous_level + 1:
            raise ValueError('目录层级须从 1 开始，且不能跨级缩进')
        previous_level = level
        # The directory editor only edits titles and hierarchy. Keep requirement
        # coverage attached to stable chapters without asking users to map IDs.
        ids = item.get('requirement_ids', section[0].get('requirement_ids', []))
        if not isinstance(ids, list) or any(i not in req_ids for i in ids):
            raise ValueError('关联要求不存在，请重新读取目录')
        heading = section[0]
        if heading.get('locked_format') and (set(heading.get('requirement_ids', [])) & required_formats) - set(ids):
            raise ValueError('指定原表的要求关联不能移除；请先在招标解读中核对该格式是否适用')
        heading.update(text=text(item.get('text'), '章节名称', 150, True), level=level,
                       requirement_ids=list(dict.fromkeys(ids)))
        covered.update(ids)
        new.extend(section)
    for ident, section in by_id.items():
        if ident not in seen and section[0].get('locked_format') and required_formats & set(section[0].get('requirement_ids', [])):
            raise ValueError('该章节保留采购指定原表；请先在招标解读中确认相应格式不适用，再删除章节')
    if req_ids - covered:
        missing = next(r for r in requirements if r['id'] in req_ids - covered)
        label = missing.get('label') or missing['text']
        raise ValueError(f'该章节仍需响应采购要求“{label[:80]}”，请保留；若要求不适用，请先在招标文件解读中修正')
    plan.update(blocks=prefix + new, chapter_counter=counter)
    return plan
