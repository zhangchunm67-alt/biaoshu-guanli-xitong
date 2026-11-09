"""Compile response-format chapters arranged as explicitly numbered volumes."""
import re

CN = '一二三四五六七八九十百零〇两'
CHAPTER = re.compile(rf'^第[{CN}\d]+章\s*(.+)$')
VOLUME = re.compile(rf'^[{CN}]+[、．.]\s*(?:商务|技术|价格|报价|资格|经济|资信)(?:册文件|部分|文件|册|标书)$')
ITEM = re.compile(rf'^[（(][{CN}]+[)）]\s*(.+)')
NESTED = re.compile(r'^\d+[、.]\s*([^。；：]+(?:信息表及证明材料|汇总表|简历表|证明|声明|经验|查询截图|申报表|评估规则))\s*$')


def chapter_sections(source):
    """Use actual chapters; TOC lines with trailing page numbers do not qualify."""
    chapters = []
    for n in source.nodes:
        if n.tag == 'p' and len(n.text) < 38:
            title = CHAPTER.fullmatch(n.text)
            if title and not re.search(r'[.．…]|\d$', title[1]):
                chapters.append((n.index, title[1].strip()))
    return [(a, chapters[k+1][0] if k+1 < len(chapters) else source.end, title)
            for k, (a, title) in enumerate(chapters)]


def grouped_rows(rows, column):
    groups = []
    for i, row in enumerate(rows[1:], 1):
        if column >= len(row):
            return []
        if row[column]:
            groups.append((row[column], []))
        if not groups:
            return []
        groups[-1][1].append(i)
    return groups


def index_table(template, criteria, detailed):
    """Use original merged group starts; repeat only a continuation row, within its own group."""
    group_column = 0 if detailed else 1
    physical = grouped_rows(template.rows, group_column)
    actual = grouped_rows(criteria.rows, group_column)
    if not actual or len(actual) != len(physical):
        return None
    order, edits = [0], []
    for (label, old_rows), (subject, required_rows) in zip(physical, actual):
        if label[:2] != subject[:2] or (len(required_rows) > 1 and len(old_rows) < 2):
            return None
        for j, source_row in enumerate(required_rows):
            orig = old_rows[min(j, len(old_rows)-1)]
            fields = criteria.rows[source_row]
            if len(fields) != 4 or len(template.rows[orig]) != 4:
                return None
            order.append(orig)
            if detailed:
                values = {1:fields[1], 2:f'{fields[2]}（{fields[3]}分）' if fields[3].isdecimal() else fields[2], 3:''}
            else:
                values = {2:fields[2], 3:''}
            edits.extend({'row':len(order)-1,'col':col,'text':value} for col,value in values.items())
    return {'kind':'copy','start':template.index,'end':template.index,'row_order':order,'cells':edits}


def score_tables(source, before):
    tables = {}
    for n in source.nodes[:before]:
        if not n.rows:
            continue
        header = n.rows[0]
        if header == ['条款号','评审因素','评审标准']:
            tables['prelim'] = n
        if len(header) == 4 and '评审因素' in header and header[-1] in ('分值','满分') and '评' in header[2]:
            tables['detail'] = n
    return tables


def chapter_requirements(source, spans):
    tech = next(((a,b) for a,b,t in reversed(spans) if re.fullmatch('技术规范书|技术[和及/]?服务需求书|技术要求|采购需求',t)), None)
    specs = []
    if tech:
        for n in source.nodes[tech[0]+1:tech[1]]:
            if n.tag=='p' and re.match(r'^[★＊*]?\s*\d+\.\d+\s*\S',n.text):
                specs.append(n)
    return specs


def try_plan(src):
    from plan_framework import Plan

    chapters = chapter_sections(src)
    bounds = next(((a,b) for a,b,t in reversed(chapters) if t in ('投标文件格式','响应文件格式','参选文件格式')), None)
    if not bounds:
        return None
    begin,end = bounds
    volumes = [n for n in src.nodes[begin+1:end] if VOLUME.fullmatch(n.text)]
    if len(volumes)<2 or not all(any(ITEM.match(n.text) for n in src.nodes[v.index+1:
            volumes[j+1].index if j+1 < len(volumes) else end]) for j,v in enumerate(volumes)):
        return None

    plan = Plan(src)
    first = volumes[0].index
    forms = score_tables(src,begin)
    specs = chapter_requirements(src,chapters)
    clauses = [n for n in src.nodes[:begin] if n.tag=='p' and re.match(r'^\d+\.\d+\s*',n.text)]
    notice_rows = [(n.index,row[1],row[-1]) for n in src.nodes[:begin] for row in n.rows
                   if len(row)==3 and row[1] and n.rows[0][-1] in ('编列内容','说明与要求','编列的内容','内容')]
    def head(n,text,level,*,newpage=False):
        plan.add('heading',text,n.index,level=level,numbered=False,page_before=newpage)
        plan.titles.append(text)

    cover = next((n for n in src.nodes[begin+1:first] if n.rows and '项目名称' in n.full and '投标人' in n.full),None)
    if cover:
        # The declared field may be part of a longer paragraph; only replace plain exact fields.
        edits = []
        project_id = next((n.text for n in src.nodes[:begin] if re.fullmatch(r'(?:项目|招标)编号[：:]\s*[A-Za-z\d][A-Za-z\d\-./_()]+',n.text)),None)
        if len(cover.rows)==1 and len(cover.rows[0])==1:
            marker = '（项目名称）项目'
            if marker in cover.full and plan.project != '【待核项目名称】':
                edits.append({'row':0,'col':0,'paragraph_match':marker,'text':plan.project})
            if project_id and '项目编号：投标文件' in cover.full:
                edits.append({'row':0,'col':0,'paragraph_match':'项目编号：','text':project_id})
        plan.copy(cover.index, cells=edits)
    else:
        from plan_framework import add_standard_cover, extract_project_id, extract_package_name
        doc_title='响应文件' if '响应文件格式' in src.nodes[begin].text else '投标文件'
        role='响应单位' if doc_title=='响应文件' else '投标人'
        add_standard_cover(
            plan, project=plan.project, doc_title=doc_title, ref=begin,
            project_id=extract_project_id(src.alltext), package=extract_package_name(src.alltext),
            fields=((f'{role}名称：', '________'), (f'{role}地址：', '________'), ('日期：', '________')))
    plan.add('break');plan.add('toc')
    score_items = []
    if 'detail' in forms:
        for category, group in grouped_rows(forms['detail'].rows,0):
            for ri in group:
                row=forms['detail'].rows[ri]
                if len(row)==4:
                    score_items.append((category,row[1],row[2],forms['detail'].index))
    current = nested = ''
    skip_examples = False
    previous=''
    for n in src.nodes[first:end]:
        t = n.text.strip()
        if n.tag=='tbl':
            key = None
            if n.rows and n.rows[0] in (['条款号','评审因素','页码范围及说明'],['评审因素','评审标准说明','页码范围及说明']):
                key='prelim' if n.rows[0][0]=='条款号' else 'detail'
            packed=index_table(n,forms[key],key=='detail') if key in forms else None
            if packed:
                plan.blocks.append(packed)
            else:
                plan.copy_nodes(n.index,n.index+1)
                if key in forms:
                    plan.add('score',index=forms[key].index,header_row=0)
            continue
        if n.tag != 'p':
            if n.tag=='sdt': plan.copy(n.index)
            continue
        if n.graphics and skip_examples and not t:
            skip_examples=False
            continue
        if not t:
            if n.graphics: plan.copy(n.index)
            continue
        if VOLUME.fullmatch(t):
            head(n,t,1,newpage=True);current=nested=''
        elif ITEM.match(t) or re.fullmatch('评标索引表|评审索引表',t):
            current = ITEM.match(t)[1] if ITEM.match(t) else t
            nested=''
            head(n,t,2)
            if re.fullmatch('技术方案|服务方案|实施方案',current):
                for group,title,rule,ref in score_items:
                    if re.search('技术|服务',group):
                        plan.add('heading',title,ref,level=3,numbered=False)
                        plan.add('slot','【待编】'+rule,ref)
                        if re.search('述标|演示|讲标',title):
                            for i,label,value in notice_rows:
                                if label.strip()==title:
                                    plan.add('text',value,i)
            if '保证金' in current and '凭证' in current:
                for i,label,value in notice_rows:
                    if re.search('保证金金额',label):plan.add('text',value,i)
                plan.add('slot','【待附】按实际缴纳或担保方式提供凭证。',n.index)
        elif NESTED.match(t) or (re.match(r'^\d+、',t) and '税收和社会保障资金' in t and len(t)<85):
            nested=t
            head(n,t,3)
            if '同类项目' in t or '实施经验' in t:
                for c in clauses:
                    if '合同' in c.text and '案例' in c.text and re.search(r'近[一二三四五\d]+年',c.text):
                        plan.add('text','资格要求　'+c.text,c.index);break
                for group,title,rule,ref in score_items:
                    if re.search('业绩|案例',title):
                        plan.add('text','评分证明要求　'+rule,ref)
                plan.add('slot','【待附】按汇总表顺序放置每个项目的合同及相应证明。',n.index)
            elif '基本情况' in t:
                for c in clauses:
                    if '法人' in c.text and '营业执照' in c.text and '其他' in c.text:
                        plan.add('text',c.text,c.index);break
                for group,title,rule,ref in score_items:
                    if '综合实力' in title:
                        plan.add('slot','【待附】'+rule,ref)
            elif '查询截图' in t:
                for c in src.nodes[:first]:
                    if c.tag=='p' and '查询截图' in c.text and '截止' in c.text and len(c.text)<380:
                        plan.add('text',c.text,c.index);break
            elif any(key in t for key in ('缴纳凭证','收入证明','审计报告')):
                plan.add('slot','【待附】按本项规定的期间和资料类型附证明。',n.index)
            elif '声明' in t:
                plan.add('slot','【待填】声明内容 ____________________',n.index)
                plan.add('text','投标人（盖章）________　日期 ________',n.index)
        elif re.match(r'^[（(]\d+[）)]',t) and (('索引表' in t and '索引表' in current) or
                ('查询截图' in t and '查询' in nested)):
            head(n,t,3 if '索引表' in current else 4)
            if '查询截图' in t: plan.add('slot','【待附】本单位在规定日期内的真实查询截图。',n.index)
        elif re.fullmatch('(?:查询)?截图[样示]式如下[：:]?|(?:查询)?截图示例如下[：:]?',t):
            skip_examples = True
        elif t!=current and not (previous and previous.endswith(t) and re.match(r'^(?:\d+[、.]|[（(].+[)）])',previous)):
            plan.copy(n.index)
        if '偏离表' in current and '技术' in current and n.tag=='p' and t.startswith('注：') and '逐条' in t and specs:
            plan.add('text','技术条款逐条应答工作表（定稿时按上表和填表说明归并）',n.index)
            plan.add('table',ref=n.index,rows=[['招标条款（含实质性要求）','投标应答','说明及证明位置']]+[[v.text,'',''] for v in specs])
        previous=t
    for ref,label,value in notice_rows:
        if re.search('投标文件份数|密封和标记|盖章或签字|述标|开标一览',label):
            plan.note(value,ref)
        if '投标文件的组成' in label and '电子版投标文件' in value:
            plan.note(value[value.index('电子版投标文件'):],ref)
    return {'source_sha256':src.digest,'project':plan.project,'strategy':'sectioned-volume-forms',
            'review':plan.review,'blocks':plan.blocks}
