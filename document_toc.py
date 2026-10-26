"""Exclude Word TOCs and evidenced manual contents lists from write targets."""
from __future__ import annotations

import re
from collections import defaultdict

from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph


def _text_after(paragraph, marker):
    seen = False
    for node in paragraph.iter():
        if node is marker:
            seen = True
        elif seen and node.tag == qn('w:t') and (node.text or '').strip():
            return True
    return False


def excluded_paragraphs(document):
    import engine
    import recognition

    body = document.element.body
    excluded = {p for p in body.iter(qn('w:p')) if engine._is_toc(p, Paragraph(p, document._body))}

    # A TOC field can span several paragraphs without TOC styles or page numbers.
    stack = []
    current = None
    # Deleting the blank line after a TOC merges the field end into the first
    # body heading, which may also inherit a TOC style; that heading stays.
    body_after_field = set()
    for node in body.iter():
        if node.tag == qn('w:p'):
            current = node
            for frame in stack:
                frame['paragraphs'].add(node)
        elif node.tag == qn('w:fldSimple') and re.search(r'\bTOC\b', node.get(qn('w:instr'), ''), re.I):
            excluded.update(node.iter(qn('w:p')))
            if current is not None:
                excluded.add(current)
        elif node.tag == qn('w:fldChar'):
            kind = node.get(qn('w:fldCharType'))
            if kind == 'begin':
                stack.append({'instruction': '', 'paragraphs': {current} if current is not None else set()})
            elif kind == 'end' and stack:
                frame = stack.pop()
                if re.search(r'\bTOC\b', frame['instruction'], re.I):
                    excluded.update(frame['paragraphs'])
                    if current is not None and _text_after(current, node):
                        body_after_field.add(current)
        elif node.tag == qn('w:instrText') and stack:
            stack[-1]['instruction'] += node.text or ''
    for frame in stack:
        if re.search(r'\bTOC\b', frame['instruction'], re.I):
            excluded.update(frame['paragraphs'])

    def key(text):
        value = engine._normal(text).strip('；;：:、.．。')
        value = re.sub(r'[，,]?(?:包括|包含)(?:以下|如下)?$', '', value)
        return re.sub(r'(?:格式|范本)$', '', value)

    def label(text, paragraph):
        value = recognition.compact(text).strip('；;。')
        value = re.sub(r'[，,]?(?:包括|包含)(?:以下|如下)?[：:]?$', '', value)
        if not value or len(value) > 90 or engine._drawing(paragraph._p) or engine._TOKEN.search(value):
            return False
        if re.search(r'[。！？!?，,：:]', value):
            return False
        if re.search(r'^(?:本人|本授权|兹|现委托|现授权|注|说明|日期)', value):
            return False
        return bool(engine._style_level(paragraph) or recognition.structural(text) or
                    recognition.title_topics(text) or
                    re.search(r'(?:部分|文件|目录|目次|表|书|函|证明|证书|资质|简介|概况|情况|记录|材料|资料|状况|凭证|说明)$', value))

    # A manual list is identified by its surrounding structure and later body
    # headings, never by selecting the last occurrence of a material name.
    rows = []
    positions = defaultdict(list)
    directory_heading = False
    for index, element in enumerate(body):
        if element.tag != qn('w:p'):
            if element.tag == qn('w:tbl'):
                # Some manual directories are two-column title/page tables.
                # The explicit directory heading and page column together
                # distinguish them from material lists and financial tables.
                if directory_heading:
                    table_rows = []
                    for tr in element.findall(qn('w:tr')):
                        cells = [engine._text(tc).strip() for tc in tr.findall(qn('w:tc'))]
                        if any(cells):
                            table_rows.append(cells)
                    entries = [cells for cells in table_rows if len(cells) >= 2 and
                               re.fullmatch(r'(?:\d+(?:[-－—]\d+)?|[ivxlcdm]+)', cells[-1], re.I)]
                    headers = [cells for cells in table_rows if cells[-1] in {'页码', '页次'}]
                    if entries and len(entries) + len(headers) == len(table_rows):
                        excluded.update(element.iter(qn('w:p')))
                rows.append(dict(index=index, element=element, key='', label=False, marker=False))
                directory_heading = False
            continue
        paragraph = Paragraph(element, document._body)
        text = engine._text(element).strip()
        if not text and not engine._drawing(element):
            continue
        marker = bool(re.fullmatch(r'(?:投标文件|响应文件|商务部分|技术部分)?(?:目录|目次)', key(text)))
        directory_heading = marker
        row = dict(index=index, element=element, key=key(text),
                   label=label(text, paragraph), marker=marker,
                   numbered=bool(engine._number_level(text)), region=recognition.region_title(text),
                   chapter=bool(re.match(r'^第' + recognition._NUMBER + r'[章节部分篇]', recognition.compact(text))))
        positions[row['key']].append(len(rows))
        rows.append(row)

    start = 0
    while start < len(rows):
        if not (rows[start]['label'] or rows[start]['marker']):
            start += 1
            continue
        end = start
        while end < len(rows) and (rows[end]['label'] or rows[end]['marker']):
            end += 1
        run = rows[start:end]
        explicit = run[0]['marker']
        # For an explicit contents heading, even a one-entry directory is valid.
        minimum = 1 if explicit else 2
        prefix_keys = set()
        prefix_pairs = set()
        for offset in range(1, len(run)):
            boundary = start + offset
            previous = run[offset - 1]
            if not previous['marker']:
                prefix_keys.add(previous['key'])
            if offset >= 2:
                prefix_pairs.add((run[offset - 2]['key'], previous['key']))
            current_key = run[offset]['key']
            # Real body content/table after this heading is strong evidence;
            # an explicit directory may instead end at a repeated title sequence.
            has_body = (current_key in prefix_keys and boundary + 1 < len(rows) and
                        not rows[boundary + 1]['label'] and not rows[boundary + 1]['marker'])
            repeated_pair = (offset + 1 < len(run) and current_key != run[offset + 1]['key'] and
                             (current_key, run[offset + 1]['key']) in prefix_pairs)
            # The last heading of an empty framework can also be the repeated
            # body title, but this is only unambiguous after an explicit 目录.
            empty_body = explicit and current_key in prefix_keys and boundary == len(rows) - 1
            if not (has_body or (explicit and repeated_pair) or empty_body):
                continue
            repeated = {key for key in prefix_keys if positions[key][-1] >= boundary}
            if len(repeated) < minimum:
                continue
            cut = offset
            if (offset >= 2 and run[offset - 1]['key'] == current_key and
                    run[offset - 1].get('numbered') and not run[offset].get('numbered') and
                    any(r['key'] == current_key for r in run[:offset - 1])):
                # A numbered section header followed by the same form title
                # belongs to the body; keep both headings after the directory.
                cut -= 1
            if not explicit:
                sections = {r['key'] for r in run[:cut + 1] if not r.get('region') and
                            re.search(r'(?:部分|篇)$', r['key'])}
                if len(sections) > 1:
                    # Blank material sections in one part remain real targets
                    # even when another part repeats them with existing text.
                    continue
                if any(r.get('chapter') and positions[r['key']][-1] < boundary for r in run[1:cut]):
                    # A chapter heading that is not repeated later separates
                    # same-named blank sections; it is body structure, not a list.
                    continue
            for row in run[:cut]:
                if row['marker'] or (not row.get('region') and
                        (explicit or not re.search(r'(?:部分|篇)$', row['key']))):
                    excluded.add(row['element'])
            break
        else:
            # Explicit directory with no following body: its entries must not
            # become fallback material entries. Leave a final heading owning
            # substantive content available when no repeated title proves a TOC.
            if explicit:
                limit = len(run) - (1 if end < len(rows) and len(run) > 1 else 0)
                excluded.update(r['element'] for r in run[:limit])
            else:
                # Some frameworks put a short contents list before an evaluation
                # page-index form, then start the real attachments later. The
                # index/page labels plus repeated attachment titles are evidence;
                # repeated titles alone must not erase empty body sections.
                nearby = rows[start:min(len(rows), end + 12)]
                has_index = any(r['key'] in {'评审索引表', '评标索引表'} for r in nearby)
                has_pages = any(r['key'] in {'对应页码', '所在页码', '页码'} for r in nearby)
                repeats = {r['key'] for r in run if positions[r['key']][-1] >= end}
                if has_index and has_pages and len(repeats) >= 2:
                    for row in run:
                        if row['key'] in {'评审索引表', '评标索引表'}:
                            break
                        if not row.get('region') and not re.search(r'(?:部分|篇)$', row['key']):
                            excluded.add(row['element'])
        start = end
    return excluded - body_after_field


def excluded_locator_ids(document, paragraphs, categories):
    import engine

    locators = set()
    for paragraph in paragraphs:
        path = engine._xml_path(paragraph, document.element.body)
        locator = 'p:' + '.'.join(map(str, path)) + ':' + engine._digest(paragraph)
        locators.add(locator)
        locators.update('at:' + c['id'] + ':' + locator for c in categories)
        for occurrence, _ in enumerate(engine._TOKEN.finditer(engine._text(paragraph))):
            locators.add(locator.replace('p:', 'ph:', 1) + f':{occurrence}')
    return sorted(locators)
