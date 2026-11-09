"""Split response chapters only when the procurement source requires books."""
import copy
import re

LABELS = {'commercial': '商务标', 'technical': '技术标', 'qualification': '资格标'}
POLICY_NOTE = '采购原文未明确完整的商务标、技术标分册结构；本次按内容生成两份编制目录，属于系统编排，正式递交方式仍以采购原件为准。'


def requires_split(source, roots):
    if source is None:
        roles = {n['heading'].get('book_role') or book_role(n['heading']['text'])
                 for n in roots if n['heading'].get('book_origin') != 'inferred'}
        return {'commercial', 'technical'} <= roles
    roles = {book_role(n.text) for n in source.nodes if n.tag == 'p'}
    if {'commercial', 'technical'} <= roles:
        return True
    for n in source.nodes:
        text = re.sub(r'\s+', '', n.full)
        if re.search(r'无需|无须|不要求|不分(?:商务|技术)|不必|不需要', text):
            continue
        if (re.search(r'商务(?:标|文件|部分|册)|报价(?:标|文件|部分|册)', text)
                and re.search(r'技术(?:标|文件|部分|册)', text)
                and re.search(r'分别(?:编制|成册|装订|密封|提交)|独立成册|单独成册|分册|分为|组成|包括.{0,16}两部分', text)):
            return True
    return False


def without_inferred_books(plan, prefix, roots):
    """Remove only system-created book containers when the buyer did not request them."""
    flattened = []
    for root in roots:
        if root['heading'].get('book_origin') == 'inferred':
            flattened.extend(root['children'])
        else:
            flattened.append(root)
    # Rejoin review rows split by a previous inferred layout; retain the first ID.
    groups = {}
    merged = []
    for node in flattened:
        h = node['heading']
        group = h.get('review_group', '').split('@book:')[0]
        if group:
            h['review_group'] = group
        if group and group in groups:
            target = groups[group]
            target['body'].extend(node['body'])
            target['children'].extend(node['children'])
            ids = target['heading'].get('requirement_ids', []) + h.get('requirement_ids', [])
            target['heading']['requirement_ids'] = list(dict.fromkeys(ids))
            if group.startswith('spec:'):
                target['heading']['text'] = '商务技术要求响应'
        else:
            merged.append(node)
            if group:
                groups[group] = node
    pair_identity(merged)
    result = list(prefix)
    def emit(nodes, level):
        for node in nodes:
            node['heading']['level'] = level
            result.extend([node['heading'], *node['body']])
            emit(node['children'], level + 1)
    emit(merged, 1)
    plan['blocks'] = result
    plan.pop('book_layout_version', None)
    plan['book_basis'] = 'none'
    plan['review'] = [n for n in plan.get('review', []) if n != POLICY_NOTE
                      and not (isinstance(n, str) and n.startswith('以下章节暂列商务标'))]
    return plan


def book_role(title):
    value = re.sub(r'\s+', '', title)
    value = re.sub(r'^(?:第[一二三四五六七八九十\d]+[章部分册卷篇]+|[一二三四五六七八九十\d]+[、.．）)])', '', value)
    match = re.fullmatch(r'(商务|技术|报价|价格|经济|资格|资信)(?:标书|标|册文件|册|部分|文件)(?:格式|目录)?', value)
    if not match:
        return None
    return 'technical' if match[1] == '技术' else 'qualification' if match[1] in ('资格', '资信') else 'commercial'


def content_role(text, category=''):
    if category == 'qualification':
        return 'commercial'
    if re.fullmatch(r'.{0,12}商务[与和及、/]?技术(?:部分|文件|要求响应|标)?', text):
        return None
    if re.search(r'报价|价格|金额|预算|税率|费用|付款|结算|单价|免费|商务|营业执照|公司介绍|公司简介|企业概况|授权|身份证|承诺|声明|资格|资质|财务|审计|信用|纳税|社保|历史项目|业绩|递交|截止|加密|邮箱|签章|盖章|保密', text):
        return 'commercial'
    if category == 'score' or re.search(r'技术|方案|服务|实施|人员|团队|培训|进度|运维|售后|质量|验收|内容生产|媒介|数据监测|投放|应急|案例|功能|性能|响应时间', text):
        return 'technical'
    return None


def trees(blocks):
    prefix, roots, stack = [], [], []
    for block in blocks:
        if block['kind'] == 'heading':
            node = {'heading': block, 'body': [], 'children': []}
            while stack and stack[-1]['heading'].get('level', 1) >= block.get('level', 1):
                stack.pop()
            (stack[-1]['children'] if stack else roots).append(node)
            stack.append(node)
        elif stack:
            stack[-1]['body'].append(block)
        else:
            prefix.append(block)
    return prefix, roots


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node['children'])


def company(title):
    return bool(re.fullmatch(r'(?:公司|企业|单位)(?:介绍|简介|概况)', title.strip()))


def license_title(title):
    return '营业执照' in title and not re.search(r'会计师|事务所|审计机构|制造商|生产厂家', title)


def pair_identity(roots):
    """Move the complete license section, retaining forms and stable bookmarks."""
    parents, owners, ancestors = {}, {}, {}
    def collect(nodes, owner=None, chain=()):
        for n in nodes:
            parents[id(n)] = nodes
            h = n['heading']
            role = h.get('book_role') or book_role(h['text'])
            current = (role, h.get('book_origin', 'source')) if role else owner
            owners[id(n)] = current
            ancestors[id(n)] = chain
            collect(n['children'], current, (*chain, id(n)))
    collect(roots)
    profiles = [n for n in walk(roots) if company(n['heading']['text'])]
    licenses = [n for n in walk(roots) if license_title(n['heading']['text'])]
    for profile in profiles:
        siblings = parents[id(profile)]
        match = next((n for n in licenses if parents[id(n)] is siblings), None)
        if match is None and len(profiles) == len(licenses) == 1:
            match = licenses[0]
        if match is not None and id(match) in ancestors[id(profile)]:
            continue
        if match is not None:
            original = match
            source_owner, target_owner = owners[id(match)], owners[id(profile)]
            if (source_owner and target_owner and source_owner != target_owner
                    and source_owner[1] == target_owner[1] == 'source'
                    and ('source_index' in match['heading'] or match['heading'].get('locked_format'))
                    and match['heading'].get('origin') != 'user_policy'):
                # Keep the explicitly required original in its book. The adjacent
                # copy is a user-requested supplement, with its own stable IDs.
                match = copy.deepcopy(match)
                for node in walk([match]):
                    node['heading'].pop('bookmark', None)
                    node['heading']['origin'] = 'user_policy'
                match['heading']['pairing_note'] = '采购原文将营业执照与公司介绍分列不同分册；原营业执照位置保留，并在公司介绍前增列一份补充材料。'
            else:
                parents[id(match)].remove(match)
            siblings.insert(siblings.index(profile), match)
            parents[id(match)] = siblings
            # The pair follows the company's explicitly prescribed book.
            match['paired_with'] = profile
            licenses.remove(original)


def organize(plan, source=None, requirements=()):
    """Return a plan with two navigable directories; never rewrite buyer forms."""
    plan = copy.deepcopy(plan)
    prefix, roots = trees(plan['blocks'])
    if not roots:
        return plan
    if not requires_split(source, roots):
        return without_inferred_books(plan, prefix, roots)
    pair_identity(roots)
    source_roles = {}
    current = None
    if source is not None:
        for node in source.nodes:
            role = book_role(node.text) if node.tag == 'p' else None
            if role:
                current = role
            elif node.tag == 'p' and re.match(r'^第[一二三四五六七八九十\d]+章', node.text):
                current = None
            if current:
                source_roles[node.index] = current
    by_req = {r['id']: r for r in requirements}
    existing = {}
    for n in roots:
        role = n['heading'].get('book_role') or book_role(n['heading']['text'])
        if role:
            existing.setdefault(role, []).append(n)
    prescribed = bool(existing) and not plan.get('book_layout_version')
    inferred = not {'commercial', 'technical'} <= existing.keys() or any(n['heading'].get('book_origin') == 'inferred'
                                  for ns in existing.values() for n in ns)
    assignments = {}
    pending = []

    def owner(n, inherited=None):
        h = n['heading']
        if 'paired_with' in n:
            return owner(n['paired_with'], inherited)
        role = h.get('book_role') or book_role(h['text'])
        if role:
            return role
        if h.get('source_index') in source_roles:
            return source_roles[h['source_index']]
        return content_role(h['text']) or inherited

    def partition(n, inherited=None):
        h = n['heading']
        role = owner(n, inherited)
        # A generated review section can contain commercial and technical rows.
        # Split its row blocks with their following material slots, never raw forms.
        if h.get('review_group') and not inherited:
            grouped = {}
            last = None
            for b in n['body']:
                req = by_req.get(b.get('requirement_id'))
                if req:
                    last = content_role(req.get('label', '') + req['text'], req['category']) or 'commercial'
                if last:
                    grouped.setdefault(last, []).append(b)
            if len(grouped) > 1 and not n['children']:
                result = {}
                for i, (r, body) in enumerate(grouped.items()):
                    head = copy.deepcopy(h)
                    if i:
                        head.pop('bookmark', None)
                    head['requirement_ids'] = [b['requirement_id'] for b in body if b.get('requirement_id')]
                    head['text'] = '商务要求响应' if r == 'commercial' else '技术要求响应'
                    result[r] = {'heading': head, 'body': body, 'children': []}
                return result
        children = {}
        for child in n['children']:
            for r, sub in partition(child, role).items():
                children.setdefault(r, []).append(sub)
        role = role or (next(iter(children)) if len(children) == 1 else 'commercial')
        if not children and content_role(h['text']) is None and not inherited and h.get('source_index') not in source_roles:
            pending.append(h['text'])
        result = {}
        for r in dict.fromkeys([role, *children]):
            if r != role and not children.get(r):
                continue
            head = copy.deepcopy(h)
            if r != role:
                head.pop('bookmark', None)
                head['requirement_ids'] = []
                head['partition_container'] = True
            result[r] = {'heading': head, 'body': n['body'] if r == role else [],
                         'children': children.get(r, [])}
        return result

    groups = {r: [] for r in ('commercial', 'technical')}
    # Explicit book containers remain authoritative, including separate qualification books.
    for root in roots:
        explicit = root['heading'].get('book_role') or book_role(root['heading']['text'])
        if explicit:
            root['heading'].update(book_role=explicit, book_origin=root['heading'].get('book_origin', 'source'))
            assignments.setdefault(explicit, []).append(root)
        else:
            for role, section in partition(root).items():
                groups.setdefault(role, []).append(section)
    ordered = []
    # Retain the buyer's book order; inferred directories use commercial first.
    roles = list(dict.fromkeys([*(existing if prescribed or not inferred else []), *groups]))
    for role in roles:
        books = assignments.get(role, [])
        if books:
            books[0]['children'].extend(groups.get(role, []))
            ordered.extend(books)
        elif groups.get(role) or role in ('commercial', 'technical'):
            ordered.append({'heading': {'kind': 'heading', 'text': LABELS[role], 'level': 1,
                'book_role': role, 'book_origin': 'inferred', 'book_assignment': 'content',
                'requirement_ids': [], 'page_before': True},
                'body': [] if groups.get(role) else [{'kind': 'slot',
                    'text': '【待核对】当前未识别到本部分的独立应答章节，请结合采购原件确认适用范围。'}],
                'children': groups.get(role, [])})
    pair_identity(ordered)
    blocks = list(prefix)
    def emit(nodes, level):
        if level > 9:
            raise ValueError('分册后目录超过九级，请先合并过深的目录层级')
        for n in nodes:
            h = n['heading']
            h['level'] = level
            blocks.append(h)
            blocks.extend(n['body'])
            if n['children']:
                emit(n['children'], level + 1)
    emit(ordered, 1)
    plan['blocks'] = blocks
    plan['book_layout_version'] = 1
    plan['book_basis'] = 'source'
    for node in ordered:
        # The source explicitly requires book separation; synthesized containers
        # reflect that requirement, while chapter assignment may still need review.
        node['heading']['book_origin'] = 'source'
    notes = plan.setdefault('review', [])
    for node in walk(ordered):
        note = node['heading'].get('pairing_note')
        if note and note not in notes:
            notes.append(note)
    plan['review'] = notes = [n for n in notes if n != POLICY_NOTE]
    if pending:
        note = '以下章节暂列商务标，请按采购原件核对归属：' + '、'.join(dict.fromkeys(pending)) + '。'
        if note not in notes:
            notes.append(note)
    return plan


def book_directories(plan):
    """Metadata for independently displaying each book's actual saved outline."""
    if not plan.get('book_layout_version'):
        return []
    _, roots = trees(plan['blocks'])
    result = []
    for root in roots:
        h = root['heading']
        if not h.get('book_role'):
            continue
        result.append({'id': h['bookmark'], 'name': h['text'], 'role': h['book_role'],
                       'basis': h.get('book_origin', 'source'), 'chapters': [
                           {'id': n['heading']['bookmark'], 'text': n['heading']['text'],
                            'level': n['heading']['level'] - h['level']}
                           for n in walk(root['children'])]})
    return result
