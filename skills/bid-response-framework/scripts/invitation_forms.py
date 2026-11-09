"""Build a response to a procurement invitation from its requirements and actual forms."""
import re


PREFIX = re.compile(r'^\s*(?:第[一二三四五六七八九十0-9]+[章节]\s*|[（(]?[一二三四五六七八九十0-9]+[）)、.．]\s*)')
INVITATION = re.compile(r'采购邀[请约]|采购.{0,8}邀请函|(?:征集|遴选|比选|询价|应询|报价)邀请|供应商邀约|'
                        r'(?:邀请|诚邀|特邀|邀约).{0,30}(?:贵|供应商|服务商|合作方).{0,28}(?:报名|参与|参加|应征|提供|响应)')
FIXED = re.compile(r'(?:委托书|授权书|承诺(?:书|函|与声明)|(?:声明|证明)书|报价[单表]|'
                   r'偏离表|偏差表|报名表|响应函|应答函|报价函|基本情况表|业绩表|人员情况表)$')
ADMIN = re.compile(r'联系(?:方式|人)|答疑|特别声明|版权|(?:截止|报名|应标|开标|递交)时间|采购程序|'
                   r'递交(?:方式|要求)|提交方式|参与方式|报名方式|采购人$|采购项目$|项目名称$')
CATEGORY = {
    'qualification': re.compile(r'资质|资格|准入|入围条件|报名条件|供应商(?:条件|要求|征集标准)|应征条件'),
    'materials': re.compile(r'(?:报名|应征|响应|应答|投标|提交|提供|递交|申请|入围)(?:的|相关)?(?:资[料质]|材料|文件清单)|材料清单'),
    'service': re.compile(r'服务(?:需求|要求|范围|内容)|采购(?:需求|内容|范围)|业务(?:需求|要求)|项目(?:需求|要求|内容)|技术(?:要求|需求)|需求(?:明细|说明)'),
}
EVIDENCE = {'company': r'公司(?:简介|介绍)|企业(?:概况|介绍|简介)', 'cases': r'历史项目|历史案例|合作案例|同类案例|相关案例|以往业绩|类似业绩|项目经验|成功案例',
            'plan': r'服务方案|技术方案|执行方案|项目方案|策划方案|[、，,]方案[、，,等）)]',
            'price': r'报价[表单]|报价明细|分项报价|基础报价|[、，,]报价[、，,等）)]',
            'extra': r'认为(?:有必要|需要)补充的|其他(?:可供|补充|自愿)资料'}
DELIVERY = re.compile(r'(?:截止|规定时限|加密|公司后缀|抄送|不加密|密封|提交方式|邮寄|报名方式|'
                      r'(?:发至|发送至|提交至|寄至|递交至)|邮件.{0,14}(?:授权|承诺|声明|主题)|'
                      r'(?:上传|提交).{0,24}(?:平台|系统)|邮件.{0,90}(?:承诺|声明))')
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
EXTERNAL = re.compile(r'(?:详见|请见|参见|见|按)(?:本(?:函|文件))?(附件[一二三四五六七八九十\d]+(?:[《【][^》】]+[》】])?)|'
                      r'《([^》]+\.(?:docx?|xlsx?|pdf|pptx?))》', re.I)


def compact(s):
    return re.sub(r'\s+', '', s)


def unnumber(s):
    return PREFIX.sub('', s, count=1).strip()


def headings(src):
    """Recognize true numbered attachment bodies. A catalogue entry alone is not a form."""
    markers = src.markers(0)
    forms, spans = [], set()
    for pos, (start, number, title) in enumerate(markers):
        stop = markers[pos+1][0] if pos+1 < len(markers) else src.end
        first = src.next(start+1, stop)
        if first is None:
            continue
        if not title:
            title = first.text
            content = first.index + 1
        else:
            content = first.index + 1 if compact(first.text) == compact(title) else start+1
        title = compact(title).strip('《》：:').removesuffix('（格式）')
        # A title/filename with no actual body generally points to an external attachment.
        if len(title) > 45 or not FIXED.search(title) or not any(n.full or n.graphics or n.rows for n in src.nodes[content:stop]):
            continue
        forms.append((start, content, stop, title))
        spans.update(range(start, stop))
    return forms, spans


def group_type(label):
    label = compact(unnumber(label)).strip('：:。')
    if len(label) > 26 or ADMIN.search(label):
        return None
    if not label or re.search(r'[。；，（）,;()、]|如|应当|所递交', label):
        return None
    return next((kind for kind, word in CATEGORY.items() if re.fullmatch(
        r'(?:本次|本项目|响应单位|报名供应商|报价人)?(?:' + word.pattern + r')(?:及证明|说明书|说明|清单|如下|要求)?', label)), None)


def collect_requirements(nodes):
    """Maintain headings through numbered and multiline lists, stopping at administrative sections."""
    grouped = {key: [] for key in CATEGORY}
    active = None
    for n in nodes:
        text = n.full.strip()
        if not text:
            continue
        title, colon, detail = unnumber(text).partition('：')
        if not colon:
            title, colon, detail = unnumber(text).partition(':')
        cat = group_type(title)
        if re.match(r'^(?:附件|附表)[一二三四五六七八九十\d]+[：:]?$', compact(text)) or (
            len(title) <= 26 and ADMIN.search(compact(title))):
            active = None
            continue
        if cat:
            active = cat
            if colon and detail.strip():
                grouped[active].append((n, detail.strip()))
            continue
        if re.search(r'供应商(?:需|须|应)(?:满足|具备|具有|符合)',text):
            cat = 'service' if re.search(r'能力|经验|资源', text) else 'qualification'
            grouped[cat].append((n, text))
            continue
        if active:
            if DELIVERY.search(text) or re.search(r'采购程序|特别声明|采购文件的编写要求|按本邀请函',text):
                active = None
                continue
            grouped[active].append((n, text))
    return grouped


def label_project(src, nodes):
    for n in nodes:
        match = re.match(r'^(?:项目名称|采购项目)[：:]\s*(.+)$',unnumber(n.text))
        if match:
            return match[1], n.index
        if compact(unnumber(n.text)) in {'项目名称','采购项目'}:
            value = src.next(n.index+1)
            if value and len(value.text) <= 110 and not DELIVERY.search(value.text):
                return value.text, value.index
    first = next((n for n in nodes[:12] if n.text and len(n.text) < 100 and ('项目' in n.text or '邀约' in n.text)
                  and not (re.search(r'贵司|贵方|贵公司|我司|本次|我行|各位', n.text) or n.text.endswith(('。','！')))),None)
    if first:
        return first.text, first.index
    return src.project(), 0


def materials_in(nodes, explicit):
    # Read checklist sentences, not incidental statements inside a declaration, scoring rule or disclaimer.
    candidates = [n for n in nodes if re.search(r'应准备|需提供|须提供|需提交|须提交|出具|采购文件应包括|'
                        r'须包含|应包括|应提交|应提供|认为(?:有必要|需要)补充|请准备|请提供|请提交', n.full)]
    candidates.extend(n for n,_ in explicit if n not in candidates)
    candidates = [n for n in candidates if not re.search(r'(?:后续|届时|入围后|预审通过后).{0,16}(?:另行|再行|将)', n.full)]
    return {key: next((n for n in candidates if re.search(pattern, n.full)),None) for key,pattern in EVIDENCE.items()}


def add_requirements(plan, title, items, level=1):
    if not items:
        return
    plan.head(title, level, items[0][0].index)
    for n, text in items:
        if n.rows:
            plan.copy(n.index)
        else:
            plan.add('text',text,n.index)
        plan.add('slot','【待填写响应并附对应材料】',n.index)


def add_form(plan, form):
    ref, start, end, title = form
    plan.head(title,1,ref)
    plan.copy_nodes(start,end,skip_title=title)


def add_price(plan, n, nodes):
    plan.head('项目报价单',1,n.index)
    priced = [m for m in nodes if re.search(r'基础报价|按.{0,20}报价|报价.{0,12}(?:含税|不含税|盖章)|盖章的.{0,8}报价',m.full)]
    notes = []
    for m in priced:
        for s in re.split(r'[。；;（）()]',m.full):
            if re.search(r'基础报价|按.{0,15}报价|报价.{0,8}(?:含税|不含税|盖章)|盖章的.{0,8}报价',s):
                notes.append((m,s))
    for m,s in notes:
        plan.add('text',s+'。',m.index)
    plan.add('table',ref=n.index,rows=[['项目类别','报价内容','报价','计价说明'],['','','','']])
    if any('盖章' in m.full for m in priced):
        plan.add('text','报价单位（盖章）________　日期________',n.index)


def delivery_notes(plan, nodes):
    addresses = {}
    for n in nodes:
        if DELIVERY.search(n.full):
            plan.note(n.full,n.index)
        for email in EMAIL.findall(n.full):
            addresses.setdefault(email.split('@')[0].lower(),{}).setdefault(email.lower(),n.index)
    for choices in addresses.values():
        if len(choices)>1:
            plan.note('采购件中相同收件人邮箱前缀出现不同域名，递交前核实。'+'、'.join(choices), min(choices.values()))


def try_plan(src):
    """Return None for long formal tenders and documents lacking an actionable invitation."""
    from independent_lots import try_plan as split_plan
    from plan_framework import Plan
    split = split_plan(src)
    if split is not None:
        return split
    visible = [n for n in src.nodes if n.text.strip()]
    opening = visible[:12]
    invited = any(INVITATION.search(compact(n.text)) for n in opening)
    greeting = any(re.match(r'^(?:尊敬的|各位|各)?(?:意向)?(?:供应商|服务商|合作伙伴|供应单位)[：:！!，,]', n.text) for n in opening)
    requested = any(re.search(r'(?:有意|请|报名单位|意向单位).{0,24}(?:报送|递交|发送|提交|提供|参与|报名)', n.full) for n in visible)
    if not invited and not (greeting and requested):
        return None
    if any(re.fullmatch(r'招标文件|采购文件|竞争性磋商文件|公开招标文件|.{0,40}召集公告(?:（[^）]+）)?',compact(n.text)) for n in opening):
        return None
    forms, spans = headings(src)
    nodes = [n for n in visible if n.index not in spans]
    # The cover's attachment catalogue lists instructions and forms by name; it is not a service specification.
    attachment_names = set()
    for num, _, label in src.markers(0):
        next_node = src.next(num+1)
        attachment_names.add(compact(label or (next_node.text if next_node else '')))
    catalogue = set()
    for i,n in enumerate(nodes):
        if compact(n.text).strip('：:') == '附件':
            for following in nodes[i+1:]:
                if compact(following.text) not in attachment_names:
                    break
                catalogue.add(following.index)
    groups = collect_requirements([n for n in nodes if n.index not in catalogue])
    named = materials_in(nodes,groups['materials'])
    if not groups['materials']:
        # Short emails often state the application evidence in running prose instead of a labelled list.
        for n in nodes:
            if re.search(r'(?:请|应|须|需|务必).{0,8}(?:报送|递交|提交|提供).{0,65}(?:证明|复印件|扫描件|资质证书|营业执照|登记表|报名表)', n.full):
                if not any(n.index == item.index for item,_ in groups['qualification']):
                    groups['materials'].append((n, n.full))
    if not (forms or any(groups.values()) or any(named.values())):
        return None
    pending = [n for n in nodes if EXTERNAL.search(n.full)]
    plan = Plan(src)
    project, ref = label_project(src,nodes)
    plan.project = project
    from plan_framework import add_standard_cover, extract_project_id, extract_package_name
    add_standard_cover(
        plan, project=project, doc_title='响应文件', ref=ref,
        project_id=extract_project_id(src.alltext), package=extract_package_name(src.alltext),
        fields=(('响应单位名称：', '________'), ('响应单位地址：', '________'), ('日期：', '________')))
    plan.add('break')
    plan.add('toc')
    plan.add('break')

    company = named['company']
    cases = named['cases']
    price = named['price']
    for form in forms:
        add_form(plan,form)
    if company:
        plan.placeholder('公司介绍',1,company.index,'【待填】填入本次要求的机构介绍及可核实资料。')
    if cases:
        plan.placeholder('历史项目与业绩',1,cases.index,'【待填】按实际项目列明服务内容，并附可核实的项目材料。')
    add_requirements(plan,'资格条件及证明',groups['qualification'])
    screening = any(re.search(r'(?:本次|本轮|现阶段).{0,14}(?:仅|只|为).{0,10}(?:资格预审|预报名|资格征集|供应商征集)|(?:通过|完成|入围).{0,15}(?:资审|预审|初审).{0,16}(?:另行|再行|发放|发送)', n.full) for n in nodes)
    service = [] if screening and not named['plan'] else groups['service']
    if service or named['plan']:
        ref = (named['plan'] or service[0][0]).index
        plan.head('项目方案',1,ref)
        for n, text in service:
            if n.rows:
                plan.copy(n.index)
            else:
                plan.add('text',text,n.index)
            if re.search(r'供应商.{0,12}(?:满足|具备|具有)|(?:能力|资源).{0,24}[、，,].{0,35}(?:能力|经验)',text):
                # Enumerated capabilities deserve separate response positions, without determining attainment.
                labels = re.split(r'[:：]',text)[-1].strip('。；;')
                parts = re.split('[、，,；;]|及(?=.{2,10}(?:能力|经验|资源)$)',labels)
                for label in dict.fromkeys(p.strip('有 ') for p in parts if p.strip('有 ')):
                    if len(label) <= 28:
                        plan.placeholder(label,2,n.index,'【待填写并附对应证明】')
            else:
                plan.add('slot','【待填写方案及相应材料】',n.index)
        if not service:
            plan.add('slot','【待填写本次服务的执行方案】',ref)
    if price and not any('报价' in f[3] for f in forms):
        add_price(plan,price,nodes)
    if groups['materials']:
        add_requirements(plan,'其他应交材料',groups['materials'])
    if named['extra']:
        plan.placeholder('其他补充材料（如有）',1,named['extra'].index)
    for n in pending:
        for m in EXTERNAL.finditer(n.full):
            # Numbered attachments that are present in this source have already been parsed.
            name = m[1] or m[2]
            if (m[1] and re.search(r'^附件[一二三四五六七八九十\d]+$',name)
                    and any(compact(v.text).startswith(name) for v in src.nodes if v.index!=n.index)):
                continue
            plan.note('核对是否另供采购方指定文件：'+name,n.index)
            plan.add('slot','【待核附件】'+name+'，按采购方原件填报。',n.index)
    delivery_notes(plan,nodes)
    if not plan.titles:
        return None
    return {'source_sha256':src.digest,'project':plan.project,'strategy':'invitation-requirements',
            'review':plan.review,'blocks':plan.blocks}
