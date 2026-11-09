"""Use an explicit document-composition checklist and its actual attachments.

Scanned attachment images are preserved as source media, without claiming OCR
or deriving requirements from pixels. Exempt materials stay exempt.
"""
import re

CN = '零〇一二三四五六七八九十百两'
COMPOSITION = re.compile(r'^(\d+)[.．、]\s*(?:投标|响应|应答|报价)文件(?:的)?组成$')
GROUP = re.compile(r'^(\d+)[.．](\d+)\s*((?:资质|资格|商务|技术|报价|价格)(?:部分|文件|标|册))(.*)$')
SIBLING = re.compile(r'^\d+[.．、]\s*\D')
ATTACHMENT = re.compile(rf'^附[件表]\s*(?:[{CN}\d]+)?\s*[：:、.．]\s*(.{{2,80}})$')
ITEM = re.compile(r'^[（(]\d+[）)]\s*(.+)$')
EXEMPT = re.compile(r'本项目(?:无[须需]|不[须需])提供|无需提供|不适用|不要求提供')
PRICE_FORM = re.compile(r'报价.*(?:单|表|明细|清单)|(?:价格|费用).*表')


def compact(text):
    return re.sub(r'\s+', '', text)


def attachment_ranges(src):
    markers = [(n, ATTACHMENT.fullmatch(n.text)) for n in src.nodes
               if n.tag == 'p' and ATTACHMENT.fullmatch(n.text)]
    forms = []
    for j, (node, match) in enumerate(markers):
        end = markers[j+1][0].index if j+1 < len(markers) else src.end
        start = node.index if node.graphics else node.index+1
        if start < end and compact(src.nodes[start].text) == compact(match[1]) and not src.nodes[start].graphics:
            start += 1
        contents = src.nodes[start:end]
        if not any(n.full or n.graphics or n.rows for n in contents):
            continue
        forms.append({'title': match[1], 'source_index': node.index, 'source_start': node.index,
                      'content_start': start, 'source_end': end,
                      'has_images': any(n.graphics for n in src.nodes[node.index:end])})
    return forms


def structure(src):
    composition = next((n for n in src.nodes if n.tag == 'p' and COMPOSITION.fullmatch(n.text)), None)
    if not composition:
        return None
    number = COMPOSITION.fullmatch(composition.text)[1]
    stop = next((n.index for n in src.nodes[composition.index+1:]
                 if n.tag == 'p' and (SIBLING.match(n.text) or re.match(rf'^[{CN}]+、', n.text))), src.end)
    groups = [(n, GROUP.fullmatch(n.text)) for n in src.nodes[composition.index+1:stop]
              if n.tag == 'p' and GROUP.fullmatch(n.text) and GROUP.fullmatch(n.text)[1] == number]
    if len(groups) < 2 or not any(re.search('商务|报价|价格', match[3]) for _, match in groups):
        return None
    if not any(re.search('资质|资格', match[3]) for _, match in groups):
        return None
    result = []
    for j, (n, match) in enumerate(groups):
        end = groups[j+1][0].index if j+1 < len(groups) else stop
        role = 'commercial' if re.search('商务|报价|价格', match[3]) else 'technical' if '技术' in match[3] else 'qualification'
        result.append({'title': match[3], 'source_index': n.index, 'source_start': n.index,
                       'source_end': end, 'book_role': role})
    # A composition adapter is used only when its checklist has actual entries.
    if not any(ITEM.fullmatch(n.text) for n in src.nodes[composition.index+1:stop]):
        return None
    return {'source_index': composition.index, 'source_end': stop, 'groups': result,
            'forms': attachment_ranges(src)}


def try_plan(src):
    from plan_framework import Plan
    spec = structure(src)
    if not spec:
        return None
    plan = Plan(src)
    from plan_framework import add_standard_cover, extract_project_id, extract_package_name
    doc_title = '报价文件' if '报价文件' in src.nodes[spec['source_index']].text else '响应文件'
    role = '报价人' if doc_title == '报价文件' else '响应单位'
    add_standard_cover(
        plan, project=plan.project, doc_title=doc_title, ref=spec['source_index'],
        project_id=extract_project_id(src.alltext), package=extract_package_name(src.alltext),
        fields=((f'{role}名称：', '________'), (f'{role}地址：', '________'), ('日期：', '________')))
    plan.add('break'); plan.add('toc'); plan.add('break')
    groups, forms = spec['groups'], spec['forms']
    used = set()
    administrative = next(i for i, group in enumerate(groups) if group['book_role'] == 'qualification')
    commercial = next(i for i, group in enumerate(groups) if group['book_role'] == 'commercial')

    def owner(form):
        explicit = [i for i, group in enumerate(groups)
                    if any(compact(form['title']) in compact(n.full) for n in src.nodes[group['source_start']+1:group['source_end']])]
        if len(explicit) == 1:
            return explicit[0]
        return commercial if PRICE_FORM.search(form['title']) else administrative

    def form_block(form):
        plan.add('heading', form['title'], form['source_index'], level=2, numbered=False,
                 locked_format=True, source_start=form['source_start'], source_end=form['source_end'])
        plan.add('copy', start=form['content_start'], end=form['source_end']-1, preserve_format=True)
        if form['has_images']:
            message = '【需人工核对】本附件含图片，已保留原图；未进行 OCR，图中条款、字段和格式须对照原件人工核对。'
            plan.add('text', message, form['source_index'])
            plan.note(message, form['source_index'])
        for n in src.nodes[:form['source_start']]:
            for row in n.rows:
                if form['title'] in ''.join(row) and re.search(r'反馈|提交|递交|发送', ''.join(row)):
                    plan.add('text', '递交要求（采购原文）：' + row[-1], n.index)
                    plan.note(row[-1], n.index)
        used.add(form['source_start'])

    for i, group in enumerate(groups):
        start, end = group['source_start'], group['source_end']
        plan.add('heading', group['title'], start, level=1, numbered=False, page_before=True,
                 book_role=group['book_role'], source_start=start, source_end=end)
        plan.add('text', src.nodes[start].text, start)
        for n in src.nodes[start+1:end]:
            if n.tag == 'tbl':
                plan.add('copy', start=n.index, end=n.index, preserve_format=True)
                continue
            if not n.text:
                continue
            item = ITEM.fullmatch(n.text)
            if item:
                text = item[1]
                if EXEMPT.search(text):
                    plan.add('text', '不适用（采购原文）：' + text, n.index)
                    plan.note(text, n.index)
                    continue
                form = next((f for f in forms if compact(f['title']) in compact(text) and owner(f) == i), None)
                if form:
                    if form['source_start'] not in used:
                        form_block(form)
                    continue
                title = re.sub(r'[（(].*$', '', text).strip()
                plan.add('heading', title, n.index, level=2, numbered=False,
                         source_start=n.index, source_end=n.index+1)
                plan.add('text', text, n.index)
                plan.add('slot', '【待补】按采购要求提供本项材料。', n.index)
            else:
                plan.add('text', n.text, n.index)
        for form in forms:
            if owner(form) == i and form['source_start'] not in used:
                form_block(form)
        if group['book_role'] == 'qualification':
            for n in src.nodes:
                if n.tag == 'p' and re.search(r'授权.{0,12}代理人.{0,20}(?:签字|盖章).{0,20}(?:须|应).{0,20}授权委托书', n.text):
                    plan.add('heading', '法定代表人授权委托书（授权代理人签署时适用）', n.index,
                             level=2, numbered=False, source_start=n.index, source_end=n.index+1)
                    plan.add('text', n.text, n.index)
                    plan.add('slot', '【待补】仅适用上述授权签署情形时提供。', n.index)
        if group['book_role'] == 'commercial':
            following = src.nodes[spec['source_end']] if spec['source_end'] < src.end else None
            if following and re.fullmatch(r'\d+[.．、]\s*报价', following.text):
                stop = next((n.index for n in src.nodes[following.index+1:] if n.tag == 'p'
                             and re.match(rf'^[{CN}]+、|^第[{CN}\d]+(?:章|部分)', n.text)), src.end)
                plan.add('copy', start=following.index+1, end=stop-1, preserve_format=True)
            if not any(owner(f) == i for f in forms):
                plan.add('slot', '【待补】按本节采购要求提供报价；如另有采购方报价模板，请补充原附件。', start)
    for n in src.nodes:
        if n.tag == 'p' and re.search('分别.*(?:装订|密封)|除报价单外|封面.*盖章', n.text):
            plan.note(n.text, n.index)
    return {'source_sha256': src.digest, 'project': plan.project,
            'strategy': 'composition-checklist-forms', 'forms': forms,
            'blocks': plan.blocks, 'review': plan.review}
