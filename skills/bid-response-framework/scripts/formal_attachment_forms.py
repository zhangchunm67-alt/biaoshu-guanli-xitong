"""Plan actual numbered buyer forms without rebuilding their tables or captions.

Attachment lists are discovery hints. Only the later, non-empty form bodies are
copied; repeated attachment numbers in a catalogue never become content spans.
"""
import re

NUM = '零〇一二三四五六七八九十百两0-9０-９'
ATTACHMENT = re.compile(
    rf'^附\s*[件表录]\s*[（(]?([{NUM}]+(?:\s*[-–—－]\s*[{NUM}]+)*)[）)]?'
    r'\s*(?:[、.．：:]|[-—－]{1,2})?\s*(.*?)\s*$')
CHAPTER = re.compile(rf'^第[{NUM}]+(?:章|部分|篇|卷)\s*(.+)$')
FORM_CHAPTER = re.compile(r'格式|模[板版]|范本|^附件$')
FILE = re.compile(r'\.(?:docx?|xlsx?|pdf|pptx?)(?![a-z0-9])', re.I)
LOT = re.compile(rf'^(?:(?:[（(][{NUM}]+[)）]|[{NUM}]+[、.．])\s*)?'
                 rf'((?:标包|包件|标段|采购包)\s*[{NUM}]+|第[{NUM}]+(?:标包|包件|标段|包))'
                 r'(?:名称)?\s*(?:[：:、]\s*([^。；;]{1,65}))?$')


def compact(text):
    return re.sub(r'\s+', '', text)


def marker(node):
    if node.tag != 'p' or not node.text or len(node.text) > 190:
        return None
    match = ATTACHMENT.fullmatch(node.text)
    if not match or FILE.search(match[2]):
        return None
    code = re.sub(r'\s+', '', match[1]).translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    code = re.sub(r'[–—－]', '-', code)
    title = match[2].lstrip('—－-：:、.． ')
    # A list reference with a page-number leader is not a form caption.
    if re.search(r'(?:\.{2,}|…+|\t)\s*\d+$', title):
        return None
    return code, title


def _regions(src):
    """Find explicit format chapters; each chapter owns its attachment numbers."""
    found = []
    for node in src.nodes:
        if node.tag != 'p' or len(node.text) > 80:
            continue
        heading = CHAPTER.fullmatch(node.text)
        if heading and FORM_CHAPTER.search(heading[1]) and not re.search(r'\d$', heading[1]):
            found.append(node.index)
    return [(lower, next((n.index for n in src.nodes[lower+1:]
                          if n.tag == 'p' and CHAPTER.fullmatch(n.text) and len(n.text) < 70), src.end))
            for lower in found]


def _region(src):
    regions = _regions(src)
    return regions[-1][0] if regions else None


def chapter_keys(src, regions):
    # A procurement catalogue may repeat the same chapter number with a shorter
    # generic caption. Unify only an unambiguous expansion of that caption;
    # distinct technical/commercial format chapters may reuse a chapter number.
    headings = {}
    for start, _ in regions:
        heading = CHAPTER.fullmatch(src.nodes[start].text)
        headings[start] = compact(src.nodes[start].text[:heading.start(1)]), compact(heading[1])
    result = {}
    for start, (prefix, title) in headings.items():
        if title in ('附件', '文件格式', '响应文件格式', '投标文件格式'):
            expanded = {other for number, other in headings.values()
                        if number == prefix and other != title and other.startswith(title)}
            if len(expanded) == 1:
                title = expanded.pop()
        result[start] = prefix + title
    return result


def lot_heading(node):
    if node.tag != 'p' or len(node.text) > 85:
        return None
    match = LOT.fullmatch(node.text)
    if not match or re.search(r'须提供|应提交|必须|不得|最高限价', match[2] or ''):
        return None
    return compact(match[1]), node.text, node.index


def scope_boundaries(src, lower, end):
    # A lot heading immediately before its own format chapter also owns that chapter.
    previous = next((n for n in reversed(src.nodes[:lower]) if n.text), None)
    inherited = lot_heading(previous) if previous else None
    result = [(lower, inherited or ('', '', lower))]
    result.extend((n.index, scope) for n in src.nodes[lower+1:end]
                  if (scope := lot_heading(n)))
    return result


def attachment_ranges(src):
    """Return actual form spans, with exclusive source_end and stable buyer codes.

    Indices are direct word/document.xml body child ordinals, including blanks.
    A source with no explicit format chapter is left to the specialized planners.
    """
    last = {}
    regions = _regions(src)
    chapters = chapter_keys(src, regions)
    for lower, end in regions:
        chapter = chapters[lower]
        boundaries = scope_boundaries(src, lower, end)
        candidates = [(n, marker(n)) for n in src.nodes[lower+1:end] if marker(n)]
        for j, (node, (code, title)) in enumerate(candidates):
            scope = next(info for index, info in reversed(boundaries) if index <= node.index)
            stop = candidates[j+1][0].index if j+1 < len(candidates) else end
            stop = min(stop, next((index for index, _ in boundaries if index > node.index), end))
            contents = [n for n in src.nodes[node.index+1:stop]
                        if n.tag in ('p', 'sdt', 'tbl') and (n.full or n.graphics or n.rows)]
            if not contents:
                continue
            start = node.index + 1
            if not title and contents[0].tag == 'p' and len(contents[0].text) <= 90:
                title = contents[0].text
                start = contents[0].index + 1
                contents = contents[1:]
            if not title or not contents:
                continue
            # Keep the later body over its catalogue only inside the same chapter/lot.
            last[(chapter, scope[0], code)] = {
                'code': code, 'title': title, 'source_start': node.index,
                'source_index': node.index, 'content_start': start, 'source_end': stop,
                'parent_code': code.rsplit('-', 1)[0] if '-' in code else None,
                'format_start': lower, 'format_scope': chapter,
                'scope': scope[0], 'scope_title': scope[1], 'scope_index': scope[2],
            }
    forms = sorted(last.values(), key=lambda x: x['source_start'])
    groups = {(x['format_scope'], x['scope']) for x in forms}
    if len(forms) < 2 or (len({x['code'].split('-')[0] for x in forms}) < 2 and len(groups) < 2):
        return []
    # Discard a catalogue-only parent when its actual children exist later.
    kept = []
    for group in groups:
        own = [x for x in forms if (x['format_scope'], x['scope']) == group]
        first = next((x for x in own if x['code'] in ('1', '一')), None)
        roots = [x['source_start'] for x in own if '-' not in x['code']]
        actual_start = first['source_start'] if first else min(roots or [x['source_start'] for x in own])
        kept.extend(x for x in own if x['source_start'] >= actual_start)
    return sorted(kept, key=lambda x: x['source_start'])


def try_plan(src):
    from plan_framework import Plan, add_standard_cover, extract_project_id, extract_package_name
    forms = attachment_ranges(src)
    if not forms:
        return None
    plan = Plan(src)
    lower = forms[0]['format_start']
    add_standard_cover(
        plan, project=plan.project, doc_title='响应文件', ref=lower,
        project_id=extract_project_id(src.alltext), package=extract_package_name(src.alltext),
        fields=(('响应单位名称：', '________'), ('响应单位地址：', '________'), ('日期：', '________')))
    plan.add('break')
    plan.scoring(lower)
    if plan.score:
        plan.add('title', '评分索引表（按采购要求填写页码）', plan.score[0][0])
        for index, _ in plan.score:
            plan.add('copy', start=index, end=index, preserve_format=True)
        plan.add('break')
    plan.add('toc')
    plan.add('break')
    captions = {}
    regions = _regions(src)
    chapters = chapter_keys(src, regions)
    for start, end in regions:
        boundaries = scope_boundaries(src, start, end)
        for n in src.nodes[start+1:end]:
            found = marker(n)
            if found:
                scope = next(info[0] for index, info in reversed(boundaries) if index <= n.index)
                captions[(chapters[start], scope, found[0])] = (n.index, found[1])
    seen = set()
    active_scope = None
    multiple_regions = len({f['format_start'] for f in forms}) > 1
    for form in forms:
        group = (form['format_start'], form['scope'])
        nested = bool(form['scope'] or multiple_regions)
        if group != active_scope:
            seen = set()
            active_scope = group
            if nested:
                plan.add('heading', form['scope_title'] or src.nodes[form['format_start']].text,
                         form['scope_index'], level=1, numbered=False, page_before=True)
        parts = form['code'].split('-')
        added_parent = False
        for depth in range(1, len(parts)):
            parent = '-'.join(parts[:depth])
            if parent in seen:
                continue
            ref, title = captions.get((form['format_scope'], form['scope'], parent), (form['source_index'], ''))
            plan.add('heading', ('附件' + parent + ' ' + title).strip(), ref, level=min(depth+int(nested), 9),
                     numbered=False, page_before=not added_parent, form_id=parent, locked_format=True,
                     source_start=form['source_start'], source_end=form['source_start'])
            seen.add(parent)
            added_parent = True
        level = len(parts) + int(nested)
        plan.add('heading', '附件' + form['code'] + ' ' + form['title'], form['source_index'],
                 level=min(level, 9), numbered=False, page_before=not added_parent, form_id=form['code'], locked_format=True,
                 source_start=form['source_start'], source_end=form['source_end'])
        seen.add(form['code'])
        # Include all empty response spaces, original notes, tables and graphics.
        # The builder separates original bookmarks from generated navigation.
        if form['content_start'] < form['source_end']:
            plan.add('copy', start=form['content_start'], end=form['source_end']-1,
                     preserve_format=True, form_id=form['code'])
        conditional = re.search(r'[【（(]([^】）)]*(?:适用|如有|若有|仅当|非必选)[^】）)]*)[】）)]', form['title'])
        if conditional:
            plan.note('附件' + form['code'] + '：' + conditional[1], form['source_index'])
    if any(re.search('单独密封|另行密封|单独提交|另行提交|U盘', n.full, re.I) for n in src.nodes[:forms[0]['source_start']]):
        plan.note('请按采购原件核对另交、单独密封与 U 盘材料；本工作稿不替代另交要求。', lower)
    return {'source_sha256': src.digest, 'project': plan.project,
            'strategy': 'formal-attachment-forms', 'forms': forms,
            'blocks': plan.blocks, 'review': plan.review}
