"""Follow explicitly prescribed books and buyer response tables outside format chapters.

This adapter requires both an explicit file-composition section and separate
technical/commercial books. It never treats an arbitrary procurement table as a
response form. Continuation tables travel with their original technical item.
"""
import re

CN = '零〇一二三四五六七八九十百两'
CHAPTER = re.compile(rf'^第[{CN}\d]+(?:章|部分|篇)\s*(.+)$')
TOP_ITEM = re.compile(r'^\d+\s*[、.．]\s*([^。；]+)$')
BOOK = re.compile(r'^\d+\s*[)）]\s*((?:技术|商务|价格|报价|资格)(?:标|文件|标书))')
SUBPART = re.compile(rf'^第[{CN}\d]+部分\s*[：:]\s*(.+)$')
LETTER = re.compile(r'^[a-zA-Z]\s*[)）]\s*(.+)$')
FORM_NAME = re.compile(r'.{1,55}(?:承诺书|承诺函|授权书|委托书|声明函|声明书)$')


def compact(text):
    return re.sub(r'\s+', '', text)


def chapter_spans(src):
    headings = [n for n in src.nodes if n.tag == 'p' and len(n.text) < 70
                and CHAPTER.fullmatch(n.text) and not SUBPART.fullmatch(n.text)]
    return [(n.index, headings[j+1].index if j+1 < len(headings) else src.end,
             CHAPTER.fullmatch(n.text)[1]) for j, n in enumerate(headings)]


def named_form_ranges(src, chapters=None):
    """Locate unnumbered actual forms in an explicitly labelled format chapter."""
    chapters = chapters or chapter_spans(src)
    bounds = next(((a, b) for a, b, title in reversed(chapters)
                   if re.fullmatch(r'(?:相关)?表格|(?:投标|响应|应答)?文件格式|附件(?:及格式)?', compact(title))), None)
    if not bounds:
        return []
    a, b = bounds
    candidates = [n for n in src.nodes[a+1:b] if n.tag == 'p' and FORM_NAME.fullmatch(compact(n.text))
                  and not re.match(r'^[l•·●\d、.（(]', n.text)]
    forms = []
    for j, n in enumerate(candidates):
        end = candidates[j+1].index if j+1 < len(candidates) else b
        # Only a trailing list of empty material captions is separate. A caption
        # followed by a box, image, note or signature remains inside its form.
        captions = [x for x in src.nodes[n.index+1:end] if x.tag == 'p'
                    and re.fullmatch(r'.{2,45}(?:复印件|扫描件)', compact(x.text))]
        for caption in captions:
            suffix = [x for x in src.nodes[caption.index:end] if x.full or x.graphics or x.rows]
            if all(x.tag == 'p' and not x.graphics and re.fullmatch(r'.{2,45}(?:复印件|扫描件)', compact(x.text)) for x in suffix):
                end = caption.index
                break
        contents = [x for x in src.nodes[n.index+1:end] if x.full or x.graphics or x.rows]
        if not contents or sum(len(x.full) for x in contents) < 25:
            continue
        forms.append({'title': n.text, 'source_index': n.index, 'source_start': n.index,
                      'content_start': n.index+1, 'source_end': end,
                      'front_required': any(re.search(r'(?:装订|置|放).{0,8}(?:首页|最前)', x.text) for x in contents)})
    return forms


def book_structure(src):
    composition = next((n for n in src.nodes if n.tag == 'p'
                        and re.fullmatch(r'\d+\s*[、.．]\s*(?:投标|响应|应答)文件组成', n.text)), None)
    if not composition:
        return None
    stop = next((n.index for n in src.nodes[composition.index+1:]
                 if n.tag == 'p' and TOP_ITEM.fullmatch(n.text)), src.end)
    books = [n for n in src.nodes[composition.index+1:stop]
             if n.tag == 'p' and BOOK.match(n.text) and re.search(r'独立成册|分别成册|单独成册', compact(n.text))]
    if not (any('技术' in n.text for n in books) and any(re.search('商务|报价|价格', n.text) for n in books)):
        return None
    chapters = chapter_spans(src)
    demands = next(((a, b) for a, b, title in chapters if re.search(r'(?:招标|采购|技术).*(?:内容|需求|要求)', title)), None)
    if not demands:
        return None
    sections = []
    for n in src.nodes[demands[0]+1:demands[1]]:
        match = re.fullmatch(rf'[{CN}]+\s*[、.．]\s*((?:技术|商务|报价|价格).{{0,25}})', n.text)
        if n.tag == 'p' and match:
            sections.append((n, match[1]))
    requirements = [(n.index, sections[j+1][0].index if j+1 < len(sections) else demands[1], title)
                    for j, (n, title) in enumerate(sections)]
    tech = next(((a, b, title) for a, b, title in requirements if title.startswith('技术')), None)
    commercial = next(((a, b, title) for a, b, title in requirements if re.match('商务|价格|报价', title)), None)
    # Explicit response columns prove these are supplier-completed tables.
    if not tech or not commercial or not any(n.rows and any(re.search(r'(?:供应商|投标人|响应人).*(?:反馈|响应|应答)', compact(c))
            for c in n.rows[0]) for n in src.nodes[tech[0]:tech[1]]):
        return None
    forms = named_form_ranges(src, chapters)
    declarations = []
    if forms:
        area = next(((a, b) for a, b, _ in chapters if a < forms[0]['source_start'] < b), None)
        if area:
            for n in src.nodes[area[0]+1:forms[0]['source_start']]:
                title = re.sub(r'^[l•·●]\s*', '', n.text)
                if n.tag == 'p' and FORM_NAME.fullmatch(compact(title)):
                    declarations.append((n.index, title))
            for n in src.nodes[forms[-1]['source_end']:area[1]]:
                if n.tag == 'p' and re.fullmatch(r'.{2,45}(?:复印件|扫描件)', compact(n.text)):
                    declarations.append((n.index, n.text))
    return {'composition': composition.index, 'books': books, 'composition_end': stop,
            'technical': tech, 'commercial': commercial, 'forms': forms,
            'form_declarations': declarations}


def try_plan(src):
    from plan_framework import Plan
    structure = book_structure(src)
    if not structure:
        return None
    plan = Plan(src)
    forms = structure['forms']
    used = set()
    material_titles = []
    books = structure['books']
    book_ends = [books[j+1].index if j+1 < len(books) else structure['composition_end'] for j in range(len(books))]
    kinds = [BOOK.match(book.text)[1] for book in books]
    technical_book = next(j for j, title in enumerate(kinds) if '技术' in title)
    commercial_book = next(j for j, title in enumerate(kinds) if re.search('商务|报价|价格', title))
    administrative_book = next((j for j, title in enumerate(kinds) if '资格' in title), technical_book)

    def owner(title):
        explicit = [j for j, book in enumerate(books) if any(compact(title) in compact(n.full)
                    for n in src.nodes[book.index+1:book_ends[j]])]
        if len(explicit) == 1:
            return explicit[0]
        if re.search('报价|价格|费用|计价', title):
            return commercial_book
        return administrative_book

    form_owners = {form['source_start']: owner(form['title']) for form in forms}
    from plan_framework import add_standard_cover, extract_project_id, extract_package_name
    add_standard_cover(
        plan, project=plan.project, doc_title='投标文件', ref=structure['composition'],
        project_id=extract_project_id(src.alltext), package=extract_package_name(src.alltext))
    plan.add('break'); plan.add('toc')

    def heading(title, level, ref, **metadata):
        plan.add('heading', title, ref, level=level, numbered=False, **metadata)

    def form_block(form, level=3):
        heading(form['title'], level, form['source_index'], locked_format=True,
                source_start=form['source_start'], source_end=form['source_end'], page_before=True)
        plan.add('copy', start=form['content_start'], end=form['source_end']-1, preserve_format=True)
        used.add(form['source_start'])

    def remaining_forms(level, book_index):
        for form in forms:
            if form['source_start'] not in used and form_owners[form['source_start']] == book_index:
                form_block(form, level)
        for ref, title in structure['form_declarations']:
            if owner(title) != book_index:
                continue
            if any(compact(title) in compact(t) for t in material_titles + [f['title'] for f in forms]):
                continue
            heading(title, level, ref)
            plan.add('slot', '【待补】本项为采购方明确列明的材料；未提供正文模板的，请补充原模板或核对适用格式。', ref)
            material_titles.append(title)

    def technical_contents(level):
        a, b, _ = structure['technical']
        nodes = src.nodes[a+1:b]
        numbered = [n for n in nodes if n.tag == 'p' and len(n.text) < 65
                    and re.fullmatch(r'\d+\s*(?:[.．、]|[)）])\s*[^。；：:]+', n.text)]
        positions = {n.index: j for j, n in enumerate(numbered)}
        cursor = a+1
        group_seen = False
        for n in numbered:
            if cursor < n.index:
                plan.add('copy', start=cursor, end=n.index-1, preserve_format=True)
            end = numbered[positions[n.index]+1].index if positions[n.index]+1 < len(numbered) else b
            nested = bool(re.match(r'^\d+\s*[)）]', n.text)) and group_seen
            if not re.match(r'^\d+\s*[)）]', n.text):
                group_seen = True
            fixed = any(x.rows for x in src.nodes[n.index+1:end])
            heading(n.text, min(level+int(nested), 9), n.index, locked_format=fixed,
                    source_start=n.index, source_end=end)
            cursor = n.index+1
        if cursor < b:
            plan.add('copy', start=cursor, end=b-1, preserve_format=True)

    for j, book in enumerate(books):
        end = book_ends[j]
        is_tech = '技术' in BOOK.match(book.text)[1]
        is_commercial = bool(re.search('商务|价格|报价', BOOK.match(book.text)[1]))
        heading(BOOK.match(book.text)[1], 1, book.index, page_before=True,
                book_role='technical' if is_tech else 'commercial' if is_commercial else 'qualification')
        plan.add('text', book.text, book.index)
        plan.note(book.text, book.index)
        if is_commercial:
            a, b, title = structure['commercial']
            heading(title, 2, a)
            plan.add('copy', start=a+1, end=b-1, preserve_format=True)
            if any('附件' in n.text for n in src.nodes[a+1:b]) and not any(n.rows for n in src.nodes[a+1:b]):
                message = '【待补】采购原文引用的商务报价表附件未包含在本文件中，请补充原附件后按其格式填写。'
                plan.add('slot', message, a)
                plan.note(message, a)
            else:
                plan.add('slot', '【待补】按采购要求填写商务报价。', a)
            remaining_forms(2, j)
            continue
        # A buyer's explicit front-page placement instruction takes precedence
        # over the order of its material checklist.
        for form in forms:
            if form['front_required'] and form_owners[form['source_start']] == j:
                form_block(form, 2)
        current_level = 2
        attached_technical = False
        for n in src.nodes[book.index+1:end]:
            if n.tag == 'tbl':
                plan.add('copy', start=n.index, end=n.index, preserve_format=True)
                continue
            if n.tag != 'p' or not n.text:
                continue
            part = SUBPART.fullmatch(n.text)
            requirement = LETTER.fullmatch(n.text)
            if part:
                if is_tech and '技术' in part[1]:
                    remaining_forms(current_level, j)
                heading(n.text, 2, n.index)
                current_level = 3
                if is_tech and '技术' in part[1]:
                    technical_contents(3)
                    attached_technical = True
            elif requirement:
                text = requirement[1]
                material_titles.append(text)
                form = next((f for f in forms if compact(f['title']) in compact(text)), None)
                if form:
                    if form['source_start'] not in used:
                        form_block(form, current_level)
                    continue
                heading(re.sub(r'[（(].*$', '', text).strip(), current_level, n.index)
                plan.add('text', text, n.index)
                plan.add('slot', '【待补】按上述采购要求提供材料；原文未附指定表单正文的，请补充采购方模板。', n.index)
            elif not re.search(r'根据本.*(?:第三|第\S+).*(?:技术要求|技术标响应)', compact(n.text)):
                plan.add('text', n.text, n.index)
        remaining_forms(2, j)
        if is_tech and not attached_technical:
            heading(structure['technical'][2], 2, structure['technical'][0])
            technical_contents(3)
    return {'source_sha256': src.digest, 'project': plan.project,
            'strategy': 'prescribed-response-books', 'forms': forms,
            'blocks': plan.blocks, 'review': plan.review}
