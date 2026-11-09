#!/usr/bin/env python3
"""Create an editable, source-indexed Word response-framework plan from one solicitation."""
import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from evidence_slots import enrich

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
NUM = '零〇一二三四五六七八九十百两0-9０-９'
MARKER = re.compile(rf'^(?:附\s*[件表录]|格\s*式|表格)\s*[（(]?([{NUM}]+)[）)]?\s*[、.．：:\-]?\s*(.*)$')
CHAPTER = re.compile(rf'^第[{NUM}]+(?:章|部分|篇|卷)\s*')
FORM_AREA = re.compile(r'^(?:(?:投标|响应|参选|应答|报价|申请|竞价|比选|磋商|谈判)(?:申请)?文件(?:的)?(?:组成及)?(?:编制)?(?:格式|模板|范本)|附[件录])$')
SINGLE = re.compile(r'^\s*\d+[-－]\d+\s*(.+)$')


def clean(text):
    return re.sub(r'\s+', ' ', text).strip()


def compact(text):
    return re.sub(r'\s+', '', text)


def extract(node):
    return clean(''.join(node.itertext())) if node.tag == W + 't' else clean(''.join(t.text or '' for t in node.iter(W + 't')))


def form_text(el):
    # Text-box fallback copies can duplicate their labels. Prefer the visible main story for titles.
    return clean(''.join(t.text or '' for run in el.findall(W + 'r') for t in run.findall(W + 't'))) or extract(el)


@dataclass
class Node:
    index: int
    tag: str
    text: str
    rows: list
    graphics: bool
    full: str


class Source:
    def __init__(self, path):
        self.path = Path(path)
        self.digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with ZipFile(path) as z:
            body = ET.fromstring(z.read('word/document.xml')).find(W + 'body')
        self.nodes = []
        for i, el in enumerate(body):
            rows = [[extract(c) for c in r.findall(W + 'tc')] for r in el.findall(W + 'tr')]
            graphics = any(el.find('.//' + W + name) is not None for name in ('drawing', 'pict', 'object'))
            self.nodes.append(Node(i, el.tag.split('}')[-1], form_text(el), rows, graphics, extract(el)))
        self.end = len(self.nodes)
        self.alltext = '\n'.join(x.full for x in self.nodes)

    def next(self, idx, end=None):
        return next((x for x in self.nodes[idx:end] if x.text), None)

    def chapter_start(self):
        matches = [x.index for x in self.nodes if x.tag == 'p' and FORM_AREA.fullmatch(
            CHAPTER.sub('', compact(x.text)).strip('：:'))]
        # A last-page bare "附件" label may belong to an individual form. Prefer headings followed by a real run.
        for i in reversed(matches):
            if len(self.markers(i + 1)) > 1:
                return i
        for i in reversed(matches):
            if '格式' in compact(self.nodes[i].text) and fallback_groups(self, i):
                return i
        for x in self.nodes:
            if x.tag == 'p' and re.search(r'(?:响应|投标|参选|应答)文件封面格式$', compact(x.text)):
                return x.index
        return None

    def markers(self, lower):
        result = []
        for x in self.nodes[lower:]:
            if x.tag != 'p' or len(x.text) > 145:
                continue
            m = MARKER.match(x.text)
            if not m:
                continue
            title = m[2].strip()
            if re.search(r'\.(?:docx?|xlsx?|pdf|pptx?)(?![a-z0-9])', title, re.I):
                continue
            result.append((x.index, m[1], title))
        return result

    def project(self):
        # Prefer an explicit project-name field, including a label on its own line.
        # A table header such as 项目名称 | 甲方单位名称 is not a project value.
        for n in self.nodes:
            if n.tag != 'p':
                continue
            label = re.match(r'^(?:[一二三四五六七八九十\d]+[、.．]\s*)?(?:项目名称|招标项目名称|采购项目名称|采购项目)\s*[：:]\s*(.*)$', n.text)
            if label:
                following = self.next(n.index+1, n.index+4)
                value = label[1].strip() or (following.text if following and following.tag == 'p' else '')
                if 4 <= len(value) <= 120 and not re.search(r'^[一二三四五六七八九十\d]+[、.．]|[：:]', value):
                    return value
        # Stamped project names/IDs come from source text or real table cells, never filenames.
        for n in self.nodes:
            for row in n.rows:
                for j, s in enumerate(row[:-1]):
                    if compact(s).strip('：:') in ('项目名称', '招标项目名称', '项目/活动名称'):
                        val = re.sub(r'^(?:项目名称|项目)[：:]\s*', '', row[j+1]).strip('。；; ')
                        if 4 <= len(val) <= 90 and not re.fullmatch(r'.{0,8}(?:单位名称|项目名称|签订时间|联系人|联系电话|编号)', val):
                            return val
        top = [n.text for n in self.nodes[:20] if n.tag == 'p' and n.text]
        for j, s in enumerate(top):
            if re.fullmatch('招标文件|采购文件|比选文件|谈判文件', compact(s)) and j:
                return ''.join(top[max(0, j-2):j])
        return next((n.text for n in self.nodes if n.tag == 'p' and re.fullmatch(r'.{4,70}(?:项目|招标|采购|召集)(?:[（(].{1,12}[）)])?', n.text)), '【待核项目名称】')


def normal_title(s):
    s = s.strip(' ：:。')
    return re.split(r'[【（(](?:模板|备注|格式|\*|非必选|如适用|若有|供应商承诺)', s, 1)[0].strip(' ：:')


def sample_deviation_cells(rows):
    if not rows:
        return []
    response_cols = {i for i, label in enumerate(rows[0])
                     if re.search(r'响应(?:情况|规格|指标|条款)|偏离(?:情况)?$|偏差(?:情况)?$|响应文件的|投标文件的', label)}
    example = re.compile(r'(?:全部|所有|完全)?(?:(?:商务|技术|服务)(?:条款|指标|要求))?'
                         r'(?:均)?(?:无偏离|无偏差|无异议|完全响应)(?:[，,、；;]其[他它](?:无偏离|无异议))?[。.]?')
    return [{'row': r, 'col': c, 'text': ''} for r, row in enumerate(rows[1:], 1)
            for c, value in enumerate(row) if c in response_cols and example.fullmatch(compact(value))]


class Plan:
    def __init__(self, src):
        self.source = src
        self.blocks = []
        self.review = []
        self.project = src.project()
        self.titles = []
        self.score = []
        self.bound_extras = []

    def add(self, kind, text=None, ref=None, **kw):
        data = {'kind': kind, **kw}
        if text is not None:
            data['text'] = text
        if ref is not None:
            data['source_index'] = ref
        self.blocks.append(data)

    def head(self, text, level, ref):
        self.add('heading', text, ref, level=level)
        self.titles.append(compact(text))

    def placeholder(self, title, level, ref, note=''):
        self.head(title, level, ref)
        self.add('slot', note or '【待补】', ref)

    def copy(self, i, **kw):
        self.add('copy', start=i, end=i, **kw)

    def note(self, text, ref):
        self.review.append({'source_index': ref, 'text': text})

    def copy_nodes(self, start, end, skip_title='', finance_examples=False):
        for n in self.source.nodes[start:end]:
            if n.tag not in ('p', 'sdt', 'tbl') or (not n.full and not n.graphics and n.tag != 'tbl'):
                continue
            if skip_title and compact(n.text) == compact(skip_title):
                skip_title = ''
                continue
            # Blank example rows in an organization chart, never rewrite requirements/price rows.
            preset_answers = sample_deviation_cells(n.rows)
            if preset_answers:
                self.copy(n.index, cells=preset_answers)
                continue
            if n.rows and not any(c for row in n.rows for c in row):
                # Some blank pricing grids refer to a populated line-item schedule in another chapter.
                previous = [x for x in self.source.nodes[max(0, n.index-12):n.index] if x.text]
                if any(re.fullmatch('分项报价表|报价明细表', x.text) for x in previous):
                    candidates = [x for x in self.source.nodes[:start] if x.rows and len(x.rows)>2
                                  and '单位' in x.rows[0] and any('单价' in c for c in x.rows[0])
                                  and any(c in ('板块','品目','项目','项目名称') for c in x.rows[0])]
                    if len(candidates)==1:
                        self.copy(candidates[0].index)
                        self.note('分项报价格式为空，采用采购文件另附的项目化报价明细。', n.index)
                        continue
            if finance_examples and n.rows:
                repl = [{'row': i, 'col': j, 'text': ''} for i, row in enumerate(n.rows[1:], 1)
                        if any(re.fullmatch(r'(?:张|李|王)[Xx×某]{1,3}', c) for c in row)
                        for j in range(len(row))]
                if repl:
                    self.copy(n.index, cells=repl)
                    continue
            self.copy(n.index)

    def scoring(self, till):
        for n in self.source.nodes[:till]:
            for h, row in enumerate(n.rows[:3]):
                if not any(re.search(r'评选细项|评审内容|评分因素|评分项目|评审因素|评审项目|评分项|评审指标', s) for s in row):
                    continue
                if not (any(re.search(r'评分标准|评审标准|计分规则|评分细则|评分内容', s) for s in row) and
                        any(re.search(r'分值|满分|权重|得分', s) for s in row)):
                    continue
                self.score.append((n.index, h))
                break

    def score_block(self):
        for i,h in self.score:
            self.add('score', index=i, header_row=h)

    def score_topics(self):
        topics = []
        for i,h in self.score:
            rows = self.source.nodes[i].rows
            label = next((j for j,c in enumerate(rows[h]) if re.search('评选细项|评审内容|评分因素|评分项目|评审因素|评审项目|评分项|评审指标', c)), None)
            group = ''
            for row in rows[h+1:]:
                if label is None or len(row) <= label:
                    continue
                if label and row[0]:
                    group = row[0]
                item = row[label]
                if item and len(item) < 55 and not re.search('总计|合计|总分', item):
                    topics.append((group, item, i))
        return topics

    def chapter_contract(self):
        chapters = [n for n in self.source.nodes if CHAPTER.match(compact(n.text)) and len(n.text) < 50]
        for j, n in reversed(list(enumerate(chapters))):
            if re.fullmatch(r'合同(?:格式|条款及格式|条款|模[版板]|文本)', CHAPTER.sub('',compact(n.text))):
                end = chapters[j+1].index if j+1 < len(chapters) else self.source.end
                if end - n.index > 5:
                    return n.index + 1, end
        return None


def required_years(text):
    interval = re.search(r'(20\d{2})\s*[年]?\s*[-—至到]\s*(20\d{2})\s*年', text)
    if interval:
        a,b = map(int,interval.groups())
        if a<=b<=a+7:
            return list(range(b,a-1,-1))
    return list(dict.fromkeys(int(y) for y in re.findall(r'(20\d{2})\s*年', text)))


def table_cover(src, start, end):
    return next((n.index for n in src.nodes[start:end] if n.tag=='tbl' and '投标' in n.full and '单位' in n.full and '项目' in n.full), None)


def extract_project_id(text):
    match = re.search(r'(?:招标|采购|项目|比选|磋商)编号\s*[：:]\s*([A-Za-z\d][A-Za-z\d.\-_/]{3,})', text or '')
    return match.group(1) if match else None


def extract_package_name(text):
    match = re.search(r'标包名称[（(]?如有[）)]?\s*[：:]\s*([^\n\r]{0,40})', text or '')
    if not match:
        return '/'
    value = match.group(1).strip().strip('／/')
    return value or '/'


def add_standard_cover(plan, *, project=None, doc_title='投标文件', ref=None,
                       project_id=None, package='/', badge='正本/副本', fields=None):
    """Three-zone cover: top-right badge, centered project meta, large doc title, bottom fields."""
    project = (project or plan.project or '').strip() or '【待核项目名称】'
    fields = fields or (
        ('投标人名称：', '________'),
        ('投标人地址：', '________'),
        ('日期：', '________'),
    )
    plan.add('cover_badge', badge, ref)
    plan.add('cover_space', count=2)
    plan.add('cover_meta', project, ref)
    plan.add('cover_space', count=1)
    plan.add('cover_meta', f'标包名称（如有）：{package or "/"}', ref)
    plan.add('cover_space', count=1)
    plan.add('cover_meta', f'项目编号：{project_id or "________"}', ref)
    plan.add('cover_space', count=4)
    plan.add('title', doc_title, ref)
    plan.add('cover_space', count=8)
    for index, (label, value) in enumerate(fields):
        if index:
            plan.add('cover_space', count=1)
        plan.add('cover_field', ref=ref, label=label, value=value)


def cover(plan, groups, lower):
    src = plan.source
    cover_group = next(((a,b,t) for a,b,t in groups if '封面' in t), None)
    if cover_group:
        a,b,_ = cover_group
        table = table_cover(src,a+1,b)
        if table is not None:
            plan.copy(table)
            return
    else:
        a,b = lower,groups[0][0]
    fields = src.nodes[a+1:b]
    response = next((n for n in fields if compact(n.text) in ('响应文件','投标文件','应答文件','参选文件')),None)
    project_id = extract_project_id(src.alltext)
    package = extract_package_name(src.alltext)
    if response:
        name = next((n.text for n in fields if n.index < response.index and 7 < len(n.text) < 85
                     and '编号' not in n.text and '正本' not in n.text and '副本' not in n.text), plan.project)
        plan.project = name
        doc_title = response.text
        ref = response.index
        role = '投标人' if '投标' in doc_title or '投标人' in src.alltext else '响应单位'
    else:
        doc_title = '投标文件' if '投标人' in src.alltext else '响应文件'
        ref = lower
        role = '投标人' if doc_title == '投标文件' else '响应单位'
    add_standard_cover(
        plan, project=plan.project, doc_title=doc_title, ref=ref,
        project_id=project_id, package=package,
        fields=((f'{role}名称：', '________'), (f'{role}地址：', '________'), ('日期：', '________')))


def named_groups(src, lower):
    marks=src.markers(lower+1)
    if not marks:
        return []
    groups=[]
    for j,(a,num,t) in enumerate(marks):
        b = marks[j+1][0] if j+1<len(marks) else src.end
        # A different procurement chapter after the forms is not response content.
        b = next((n.index for n in src.nodes[a+1:b] if CHAPTER.match(compact(n.text)) and
                  re.search('招标公告|合同条款|合同格式|采购需求|技术要求|评标办法|评审办法',n.text)), b)
        if not t:
            next_n=src.next(a+1,b)
            t=next_n.text if next_n else '附件'+num
        groups.append((a,b,normal_title(t)))
    return groups


def qualification_notes(plan, title, start, end, level):
    src=plan.source
    context='\n'.join(n.text for n in src.nodes[start:end])
    if re.search('基本情况表|企业概况',title):
        for n in src.nodes[start:end]:
            if not re.search('后附|附[：:]|请附', n.text):
                continue
            listing=re.split(r'后附|附[：:]|请附',n.text,1)[1]
            for seg in re.split(r'[、，,；;](?![^（]*）)',listing):
                label=re.split(r'[（(]|等证明|\s',seg)[0].strip('。； ')
                if label and len(label)<28 and re.search('营业执照|资质|资格证书|公司简介|案例展示|服务方案|荣誉证书',label):
                    plan.placeholder(label,level,n.index,'【待补】'+seg.strip('。'))
    elif re.search('业绩|类似项目', title) and re.search('合同', context):
        plan.placeholder('案例资料',level,start,'【待补】按汇总表顺序附对应项目证明，可按真实项目数量复制本组。')
    elif re.search('人员|负责人',title):
        if '社保' in context:
            plan.placeholder('社保缴纳记录',level,start,'【待补】按表内规定的参保人员、时间和证明类型提供。')
        if '组织结构图' in context:
            plan.placeholder('组织结构图',level,start)
    elif re.search('财务报表|财务报告', title):
        for y in required_years(context):
            plan.placeholder(f'{y}年审计报告',level,start)
    return context


def cond_qual(plan, level, ref):
    s=plan.source
    options=[('营业执照','营业执照'), ('信用中国','信用中国查询记录'),
             ('不接受联合体|联合体(?:投标|响应)不接受','非联合体投标承诺函'), ('没有重大违法|无重大违法|不存在重大违法','近三年内没有重大违法违规行为承诺函')]
    for pattern,title in options:
        if pattern == '信用中国':
            clause = next((n for n in s.nodes if n.tag == 'p' and '信用中国' in n.text
                           and '中国政府采购网' in n.text and re.search('失信|违法',n.text)), None)
            if clause and not any('信用中国' in x for x in plan.titles):
                plan.head('信用中国和中国政府采购网查询记录',level,clause.index)
                plan.add('text',clause.text,clause.index)
                for site in ('信用中国','中国政府采购网'):
                    plan.add('slot','【待附】'+site+'的对应查询结果截图',clause.index)
                continue
        node=next((x for x in s.nodes if re.search(pattern,x.full)),None)
        if node and not any(compact(title) in n for n in plan.titles):
            plan.placeholder(title,level,node.index,'【待补】核实适用条件并附相应材料。')
    if re.search('审计报告|经审计',s.alltext) and not any('审计报告' in x for x in plan.titles):
        for n in s.nodes:
            m=re.search(r'(20\d{2})\s*年(?:度)?(?:经.{0,17}审计的财务|的?审计报告|年度审计报告)',n.full)
            if m:
                plan.placeholder(m[1]+'年审计报告',level,n.index)
                break


def copy_with_cases(plan, a, b, title, level):
    src=plan.source
    block=''.join(n.full for n in src.nodes[a:b])
    m=re.search(r'(\d{1,2})个单项合同.{0,50}?(?:案例|业绩)',block)
    per_case=any(n.rows and n.rows[0][0:1]==['项目名称'] and any('项目概况' in r for r in n.rows) for n in src.nodes[a:b])
    number=int(m[1]) if per_case and m and 1<=int(m[1])<=20 else 1
    for j in range(number):
        if j:
            plan.add('text',f'{title}（{j+1}）',a)
        plan.copy_nodes(a,b,skip_title=title,finance_examples=True)
        if number>1: plan.add('slot','【待附】对应案例合同及金额证明',a)
    if number == 1:
        qualification_notes(plan, title, a, b, level + 1)


def identity_form(plan,a,b,title,level):
    src=plan.source
    other=next((n for n in src.nodes[a:b] if n.text in ('授权委托书','法定代表人授权书') and n.text!=title),None)
    if other:
        plan.copy_nodes(a,other.index,skip_title=title)
        plan.head(other.text,level,other.index)
        plan.copy_nodes(other.index+1,b)
    else:
        plan.copy_nodes(a,b,skip_title=title)


def list_items(plan, start, stop):
    items=[]
    for n in plan.source.nodes[start:stop]:
        m = re.match(r'^\s*\d+[.．]\d+\s*(.+)', n.text)
        if m:
            # A year range embedded in a document name is part of its identity.
            full = re.sub(r'[（(](20\d{2}\s*[-–—至]\s*20\d{2}年?)[）)]', r'〈\1〉', m[1])
            name = re.split(r'[（【(，；]', full, 1)[0].replace('〈','（').replace('〉','）')
            items.append((n.index, normal_title(name)))
    return items


def form_by_name(name, group):
    return (name==group or normal_title(name)==normal_title(group) or
            (len(group)>3 and group in name) or (len(name)>3 and name in group))


def material_section(plan, title, ref, details, level, groups, used):
    plan.placeholder(title,level,ref, '【待补】'+details)
    if '承诺函' in details and re.search('关联方|合作方',details) and '授权承诺函' not in plan.titles:
        condition = '【如适用】使用关联方或合作方代理资质时，须按上述要求由双方用印。'
        refs = set(re.findall(r'模板(?:如|见)附件\s*(['+NUM+r']+)',details))
        official = next((g for g in groups if g[0] not in used and '承诺函' in g[2]
                         and (match := MARKER.match(plan.source.nodes[g[0]].text))
                         and match[1] in refs), None)
        if official:
            make_section(plan,official,level)
            plan.add('text',condition,ref)
            used.add(official[0])
        else:
            plan.placeholder('授权承诺函',level,ref,condition)


def other_materials(plan,start,end,level):
    for n in plan.source.nodes[start:end]:
        m=re.match(r'^\s*\d+[、.．]\s*(.+)',n.text)
        if not m:
            continue
        req=m[1]
        if '设备' in req: label='设备器材服务能力'
        elif '案例' in req and '合同' in req: label='项目案例合同'
        elif '项目负责人' in req: label='项目负责人行业经验承诺书'
        else: label=re.split(r'[，。；]',req,1)[0][:50]
        if label:
            plan.placeholder(label,level,n.index,'【待补】'+req)


def tech_section(plan, title, ref, level=1):
    plan.head(title,level,ref)
    topics=plan.score_topics()
    tech=[(item,i) for group,item,i in topics if '技术' in group or not group]
    if tech:
        # The service-design measurements are children of the detailed service plan; value-added
        # services and response guarantees may be separate evaluated deliverables.
        service_group = title=='技术方案' and len(tech)>1 and any('增值服务' in item for item,_ in tech)
        if service_group:
            plan.head('详细服务方案',level+1,ref)
        for item,i in tech:
            if item in plan.titles:
                continue
            if service_group and '增值服务' in item:
                plan.placeholder('增值服务',level+1,i,'【待编制】'+item+'，按评分标准列示可提供的项目、适用条件和证明。')
            else:
                plan.placeholder(item,level+(2 if service_group else 1),i,'【待编制】按评分标准说明响应内容并关联证明。')
    else:
        plan.add('slot','【待编制】按采购需求逐项编制方案，并提供相应交付或证明要求。',ref)


def make_section(plan, group, level, title=None):
    src=plan.source
    a,b,raw=group
    title=title or raw
    first=src.next(a+1,b)
    if len(title)>30 and '审计' in title and '财务' in title:
        years=required_years(title)
        title=(str(years[0])+'年度' if years else '')+'审计报告'
    if title=='响应函' and first and first.text in ('报价函','投标函','响应函'):
        title=first.text
    plan.head(title,level,a)
    caption = MARKER.match(src.nodes[a].text)
    if caption:
        condition = re.search(r'[（(【](.*(?:必填|无效标|非必选|报价模[板版]|报价单|对应一类|单独递交).*)[）)】]$',caption[2])
        if condition:
            plan.add('text','附件说明：'+condition[1],a)
    start=a+1
    if '身份证' in title and src.nodes[a].graphics and '粘贴处' in src.nodes[a].full:
        plan.copy(a)
    if re.search(r'法(?:定代表)?人',title) and ('身份证明' in title or '授权书' in title):
        identity_form(plan,start,b,title,level)
        return
    if '信用中国' in title and '截图' in title:
        for n in src.nodes[start:b]:
            if n.text.startswith('注：') or re.match(r'^\d+[、.]',n.text):
                plan.add('text',n.text,n.index)
                if re.match(r'^\d+[、.]',n.text): plan.add('slot','【待附】查询截图',n.index)
        return
    if title=='其他':
        other_materials(plan,start,b,level+1)
        return
    subforms=[(n.index,SINGLE.match(n.text)[1].strip()) for n in src.nodes[start:b]
              if n.tag=='p' and SINGLE.fullmatch(n.text) and len(n.text)<55]
    if subforms:
        for j,(i,sub) in enumerate(subforms):
            stop=subforms[j+1][0] if j+1<len(subforms) else b
            plan.head(sub,level+1,i)
            if re.search('其他资格|其它资格',sub):
                cond_qual(plan,level+2,i)
                # Explicit contract inclusion pulls the actual contract; attach after nested commitment forms.
                if re.search('完整的合同文本', ''.join(n.text for n in src.nodes[i:stop])):
                    plan.bound_extras.append(('contract',i,level+2))
            else:
                copy_with_cases(plan,i+1,stop,sub,level+1)
        return
    cut=next((n.index for n in src.nodes[start:b] if re.match(r'^最终.{0,10}报价表',n.text) and
             re.search('无需装订|手持|单独递交',''.join(t.text for t in src.nodes[n.index:b]))), b)
    if cut<b:
        plan.note('最终报价文件单独持有或递交，另按采购文件制作。',cut)
    for n in src.nodes[start:cut]:
        if n.tag=='p' and re.search(r'附件\s*['+NUM+r'].*\.(?:docx?|xlsx?)',n.text,re.I):
            plan.copy_nodes(start,n.index,skip_title=title)
            plan.add('slot','【待附原件】'+n.text+'。请取得招标方对应的原始附件，按指定方式填写和提交。',n.index)
            plan.note('核对是否收到原始可编辑附件及递交方式。',n.index)
            start=n.index+1
    copy_with_cases(plan,start,cut,title,level)
    if re.fullmatch('(?:投标|开标|报价)一览表', title):
        breakdown=next((n for n in src.nodes[:a] if re.search(r'分项报价表[（(]格式见附件',n.text)),None)
        if breakdown and '分项报价表' not in plan.titles:
            plan.placeholder('分项报价表',level+1,breakdown.index,'【待编制】按本节原报价表所列档位和费用逐项核价，与总报价核对。')
        for n in src.nodes[:a]:
            if n.tag=='p' and title in n.text and re.search('单独密封|另行密封',n.text):
                plan.add('text','递交要求：'+re.split(r'[。]',n.text,1)[0]+'。',n.index)
                plan.note(n.text,n.index)
                break
    if title=='承诺函' and '信用中国' in ''.join(n.text for n in src.nodes[start:b]) and not any('信用中国' in x for x in plan.titles):
        plan.placeholder('信用中国截图',level+1,a)


def flush_contract(plan):
    for _,i,level in plan.bound_extras:
        plan.head('合同文本',level,i)
        segment=plan.chapter_contract()
        if segment: plan.copy_nodes(*segment)
        else: plan.add('slot','【待附】招标文件规定的完整合同文本',i)
    plan.bound_extras.clear()


def from_table_of_contents(plan,groups,catalog):
    src=plan.source
    a,b,_=catalog
    forms=[g for g in groups if g[0]>a]
    section=None
    used=set()
    for n in src.nodes[a+1:b]:
        m=re.match(r'^(?:\d+\.\d+\s*)?((?:技术|商务|资格|报价|价格)部分)',n.text)
        if m:
            section=m[1]
            if section=='技术部分':
                tech_section(plan,section,n.index)
            else:
                plan.head(section,1,n.index)
            continue
        if section is None or len(n.text) > 40 or not n.text:
            continue
        label=n.text.strip('：:。')
        match=next((g for g in forms if form_by_name(label,g[2]) or
                    (label in ('报价函','投标函') and g[2]=='响应函')),None)
        if match and match[0] not in used:
            make_section(plan,match,2,title=label)
            used.add(match[0])
        elif re.fullmatch('资格证明(?:文件|材料)',label):
            plan.head(label,2,n.index)
            cond_qual(plan,3,n.index)
    for g in forms:
        if g[0] not in used and g[2]!='其他':
            make_section(plan,g,2)
        elif g[2]=='其他':
            plan.note('逐条核对格式中“其他”所列的资格条件、适用主体及技术文件。',g[0])
    if any('响应时间' in i for _,i,_ in plan.score_topics()):
        ref=next(i for _,t,i in plan.score_topics() if '响应时间' in t)
        plan.placeholder('服务响应和服务承诺',1,ref,'【待补】响应时限、联系人、保障措施及可履行的承诺。')
    if plan.score:
        plan.head('评标索引表',1,plan.score[0][0])
        plan.score_block()


def scoped_response_forms(plan, lower):
    src = plan.source
    starts = [(n.index,m[1]) for n in src.nodes[:lower] if n.tag=='p'
              and (m:=re.match(r'^(?:[（(]['+NUM+r']+[)）])?项目['+NUM+r']+(?:名称)?[：:]\s*[【「]?(.+?)[】」]?$',
                               compact(n.text)))]
    for j,(a,project) in enumerate(starts):
        segment=src.nodes[a+1:starts[j+1][0] if j+1<len(starts) else lower]
        start=next((n.index for n in segment if n.tag=='p' and
                    re.match(r'^(?:\d+(?:\.\d+)*\s*)?(?:代理商)?服务内容[：:]?',n.text)),None)
        if start is None:
            continue
        stop=next((n.index for n in segment if n.index>start and n.tag=='p' and
                   re.match(r'^(?:\d+\s*)?报价须知[：:]?',n.text)),segment[-1].index+1)
        content=src.nodes[start:stop]
        if not any(n.rows for n in content) or not any('讲标' in n.text or '应答文档' in n.text for n in content):
            continue
        plan.head(project,2,a)
        plan.add('slot','所投标段：________　投标媒体：________（按实际选择）',a)
        modes = [n for n in segment if n.index<start and n.tag=='p' and len(n.text)<220
                 and re.search(r'拟招标的媒体|拟招标标段|媒体[（(].{0,16}[）)][：:]|需区分媒体|分开讲标|不允许转换标段',n.text)]
        for n in modes:
            plan.add('text',n.text,n.index)
        # Keep mode-to-deliverable rules before the forms; no selection is inferred.
        for n in content:
            if n.tag=='p' and re.search(r'拆分提交|需同时竞标|选择代运营模式的|实际优化人员讲标|实际优化人员分开讲标|多个媒体',n.text):
                plan.add('text',n.text,n.index)
        commercial=[n for n in content if n.rows and any('应答情况' in h or '响应情况' in h for h in n.rows[0])]
        if commercial:
            plan.head('商务标应答文档',3,commercial[0].index)
            rule = next((n for n in content if n.tag=='p' and '格式不支持修改' in n.text),None)
            if rule: plan.add('text',rule.text,rule.index)
            for n in commercial:
                plan.copy(n.index)
        scores=[n for n in content if n.rows and any(re.search(r'得分|分值|满分',compact(h)) for h in n.rows[0])]
        prep=[n for n in content if n.rows and n not in commercial and n not in scores]
        if scores or prep:
            plan.head('讲标PPT',3, (scores or prep)[0].index)
            for n in content:
                if n.tag=='p' and ('PPT' in n.text.upper() or '产品参考' in n.text) and ('参考资料' in n.text or '产品参考' in n.text):
                    plan.copy(n.index)
            for n in prep:
                plan.copy(n.index)
            for n in scores:
                plan.head('讲标评分与应答位置',4,n.index)
                plan.copy(n.index)
                labels=[row[0] for row in n.rows[1:] if row and row[0]]
                plan.add('table',ref=n.index,rows=[['评分项目','拟讲内容或证明','讲标PPT页码']]
                         +[[item,'',''] for item in labels])
    # Delivery requirements can be a long row in the bidder-instructions schedule.
    for n in src.nodes[:lower]:
        if n.tag=='p' and '宋体' in n.text and '五号' in n.text:
            plan.note(n.text,n.index)
        if n.rows:
            for row in n.rows:
                for value in row:
                    if 'PDF' in value.upper() and '公章' in value and re.search('递交|接收邮箱|发送',value):
                        plan.note(value,n.index)


def from_material_list(plan, groups, items, lower):
    src=plan.source
    used=set()
    for g in groups:
        if '封面' in g[2]:
            used.add(g[0])
            continue
        if g[2] in ('投标函','报价函','响应函','参选函'):
            make_section(plan,g,1)
            used.add(g[0])
    qual = next((n for n in src.nodes[lower:items[0][0]] if
                 re.match(r'^(?:资质|资格)证明(?:文件|材料)[：:（(]?', n.text)),None)
    level = 1
    if qual:
        plan.head(re.split('[：:（(]',qual.text,1)[0],1,qual.index)
        level = 2
    for i,name in items:
        if any(form_by_name(name,g[2]) for g in groups if g[0] in used):
            continue
        matching=next((g for g in groups if form_by_name(name,g[2]) and g[0] not in used),None)
        if matching:
            make_section(plan,matching,level,title=name)
            if re.search('非必选|非必需|如适用',src.nodes[i].text):
                plan.add('text','【如适用】'+src.nodes[i].text,i)
            used.add(matching[0])
        else:
            material_section(plan,name,i,src.nodes[i].text,level,groups,used)
    for g in groups:
        if g[0] not in used:
            make_section(plan,g,1)
    for n in src.nodes[lower:groups[0][0]]:
        if re.match(r'^(?:技术|商务|技术及商务)?应答文件[（(]',n.text):
            plan.placeholder('应答文件',1,n.index,'【按所投标段单独准备】'+n.text)
            scoped_response_forms(plan, lower)
            plan.note('核对标段、应答表与另交演示文件。',n.index)


def additional_evidence(plan, level):
    s=plan.source
    for category,name,i in plan.score_topics():
        if '商务' not in category:
            continue
        if re.search('机制|体系|服务',name):
            plan.placeholder(name,level,i,'【待补】对照评分标准填写并附证明。')
    if re.search(r'ISO|质量管理体系认证',s.alltext,re.I):
        node=next(n for n in s.nodes if re.search(r'ISO|质量管理体系认证',n.full,re.I))
        plan.placeholder('管理体系认证证书',level,node.index,'【如适用】附有效认证文件。')


def from_attachments(plan,groups):
    src=plan.source
    nested=None
    for g in groups:
        i,_,t=g
        if '封面' in t or t in ('目录', '目 录'):
            continue
        if nested is not None and re.search('承诺[书函]',t):
            make_section(plan,g,nested)
            continue
        flush_contract(plan)
        nested=None
        make_section(plan,g,1)
        if re.fullmatch('资格证明(?:文件|材料)',t):
            nested=3 if any('其他资格' in x or '其它资格' in x for x in plan.titles) else 2
        if re.fullmatch('其[他它]证明材料',t):
            additional_evidence(plan,2)
    flush_contract(plan)
    if plan.score and any('技术' in group for group,_,_ in plan.score_topics()) and not any(
            t in plan.titles for t in ('技术部分','技术方案','技术文件')):
        tech_section(plan,'技术方案',plan.score[0][0])
    if any(re.search('分公司.{0,20}(?:参与|参加).{0,30}总公司授权',n.full) for n in src.nodes):
        n=next(n for n in src.nodes if re.search('分公司.{0,20}(?:参与|参加).{0,30}总公司授权',n.full))
        plan.head('分公司授权文件',2,n.index)
        plan.add('text',re.sub(r'^\d+[、.．]\s*','',n.text),n.index)
        plan.add('slot','【如适用】附按上述要求签章的分公司授权文件。',n.index)


def response_work_items(plan, lower):
    # Workstreams must come from the procurement requirements chapter, not the sample SI contract.
    src = plan.source
    requirements = []
    in_scope = False
    active = ''
    for n in src.nodes[:lower]:
        if n.tag != 'p' or not n.full:
            continue
        text = compact(n.full)
        if CHAPTER.match(text) and len(text)<45:
            in_scope = bool(re.search(r'(?:采购需求|技术[/／及]?服务需求|技术需求|技术要求|项目需求)(?:书)?$',text))
            active = ''
            continue
        if not in_scope:
            continue
        section = re.match(r'^['+NUM+r']+[、.．](.{2,22})[：:]?$',text)
        if section:
            title = section[1].rstrip('：:')
            active = ('work' if re.fullmatch('(?:工作|服务)内容(?:描述|及要求|要求)?',title)
                      else 'staff' if re.fullmatch('(?:服务|团队)?人员(?:配置)?要求',title) else '')
            continue
        if active == 'work':
            m = re.match(r'^(?:[（(]['+NUM+r']+[)）]|['+NUM+r']+[、.．])\s*([^：:，。；]{2,22})[：:]\s*(.+)',n.full)
            if m:
                requirements.append(('work',m[1].strip(),m[2],n.index))
        elif active == 'staff' and re.search(r'至少|不少于|专职|清单|须提供',n.full):
            requirements.append(('staff','',n.full,n.index))
    if not requirements:
        return

    for topic, target in [('work',r'(?:详细)?服务方案'),('staff',r'.{0,15}(?:人员情况表|团队人员|团队配置表)')]:
        selected = [entry for entry in requirements if entry[0]==topic]
        index = next((i for i,b in enumerate(plan.blocks) if b['kind']=='heading'
                      and re.fullmatch(target,b['text'])),None)
        if index is None or not selected:
            continue
        end = next((i for i in range(index+1,len(plan.blocks))
                    if plan.blocks[i]['kind']=='heading'),len(plan.blocks))
        extra = []
        for _, title, description, ref in selected:
            if topic=='work':
                extra.append({'kind':'heading','level':plan.blocks[index]['level']+1,
                              'text':title,'source_index':ref})
            extra.append({'kind':'text','text':'采购要求：'+description,'source_index':ref})
            if topic=='work':
                extra.append({'kind':'slot','text':'【待编制】实施方法、执行安排和交付内容。','source_index':ref})
        if topic=='staff':
            extra.append({'kind':'slot','text':'【待补】按上述要求提交实际拟派人员及其职责和证明。',
                          'source_index':selected[0][3]})
        plan.blocks[end:end] = extra


def add_missing_undertakings(plan, lower):
    request = re.compile(r'(?:另行)?(?:提供|提交|出具)\s*(?:一[份个]|个|该项)?\s*(?:书面)?承诺[函书]')
    candidates = [n for n in plan.source.nodes[:lower]
                  if n.tag == 'p' and request.search(n.text)]
    if not candidates:
        return

    # Compare only to actual declaration sections. Another mention in eligibility instructions
    # or the table of contents does not create an editable declaration.
    declarations = []
    in_declaration = False
    for block in plan.blocks:
        if block['kind'] == 'heading':
            in_declaration = bool(re.search('承诺[函书]|声明', block['text']))
            if in_declaration:
                declarations.append(block['text'])
        elif in_declaration and block['kind'] == 'copy':
            declarations.extend(n.text for n in plan.source.nodes[block['start']:block['end'] + 1]
                                if n.tag == 'p')

    def normalize(text):
        return re.sub(r'[^\u4e00-\u9fff0-9]', '', re.sub(
            r'报名的供应商|参与报名的|供应商|本公司|我公司|我方|本项目|该项目|[（(].*?[）)]', '', text))

    def grams(text):
        return {text[i:i+3] for i in range(max(0, len(text)-2))}

    present = [grams(normalize(t)) for t in declarations]
    missing = []
    for n in candidates:
        claim = re.split(r'[（(]?'+request.pattern, n.text, maxsplit=1)[0]
        claim = re.sub(r'^\s*(?:\d+[.、．]|[一二三四五六七八九十]+、)\s*', '', claim).strip('。；，：;,:（(')
        if len(claim)<4:
            plan.note('请核实本条单独承诺要求及承诺主题。', n.index)
            continue
        if re.search(r'不(?:接受|得|允许).{0,4}联合体|非联合体', claim):
            title = '非联合体投标承诺函'
            if any(re.search('非联合体|不(?:接受|以|组成|得).{0,5}联合体', t) for t in declarations):
                continue
        else:
            words = grams(normalize(claim))
            if words and any(len(words & stated)/len(words) >= .70 for stated in present):
                continue
            item = re.match(r'(\d+)\s*[、.．]',n.text)
            title = '资格条件第'+item[1]+'项承诺函' if item else claim[:22]+'承诺函'
        if title not in plan.titles and not any(b.get('text') == title for b in missing):
            missing.extend([
                {'kind':'heading','level':2,'text':title,'source_index':n.index},
                {'kind':'text','text':'采购要求：'+claim+'。','source_index':n.index},
                {'kind':'slot','text':'【待核实并填写】承诺内容：________________','source_index':n.index},
                {'kind':'table','repeat_header':False,'rows':[
                    ['供应商（盖章）',''],['法定代表人或授权代表（签字）',''],['日期','']],
                 'source_index':n.index},
            ])
    if not missing:
        return
    positions = [i for i,b in enumerate(plan.blocks) if b['kind']=='heading'
                 and re.fullmatch('其[他它](?:资格)?(?:证明材料|材料|证明文件)?', b['text'])]
    if positions:
        parent = positions[-1]
        level = plan.blocks[parent]['level']
        end = next((i for i in range(parent+1,len(plan.blocks))
                    if plan.blocks[i]['kind']=='heading' and plan.blocks[i]['level']<=level),len(plan.blocks))
        for block in missing:
            if block['kind']=='heading':
                block['level'] = level+1
        plan.blocks[end:end] = missing
    else:
        plan.head('其他资格证明材料',1,missing[0]['source_index'])
        plan.blocks.extend(missing)


def fallback_groups(src, lower):
    # Only title-like paragraphs in the response-format chapter qualify as unnumbered form starts.
    # If ambiguous or no official spans, the plan must be established by reading the original.
    result=[]
    for n in src.nodes[lower+1:]:
        if n.tag != 'p' or not n.text or len(n.text) > 48:
            continue
        t=normal_title(re.sub(rf'^(?:[（(]?[{NUM}]+[）)、.．]\s*|第[{NUM}]+[章节]\s*)','',n.text))
        if re.search(r'(?:投标函|报价函|响应函|授权委托书|授权书|偏离表|资格证明文件|资格审查材料|承诺书|一览表|情况表|简历表|报价表|投标文件封面|响应文件封面)$',t):
            if not result or result[-1][1]!=t:
                result.append((n.index,t))
    if len(result) < 2:
        return []
    return [(i, result[j+1][0] if j+1<len(result) else src.end,t) for j,(i,t) in enumerate(result)]


def generate(src):
    from required_sections import ensure_business_license
    return ensure_business_license(_generate(src))


def _generate(src):
    from sectioned_forms import try_plan as sectioned_plan
    from platform_forms import try_plan as platform_plan
    from invitation_forms import try_plan as invitation_plan
    from formal_attachment_forms import try_plan as formal_plan
    from prescribed_response_books import try_plan as books_plan
    from decimal_format_forms import try_plan as decimal_plan
    from composition_checklist_forms import try_plan as composition_plan

    for strategy in (sectioned_plan, platform_plan, formal_plan, books_plan, decimal_plan, composition_plan, invitation_plan):
        plan = strategy(src)
        if plan is not None:
            return plan

    lower=src.chapter_start()
    if lower is None:
        # Without a chapter label, a consecutive set of numbered actual forms can suffice.
        marks=src.markers(0)
        first=next((i for i,num,title in marks if num in ('一','1','１') and re.search('封面|投标函|报价函|响应函|基本情况|授权委托',title)),None)
        if first is None:
            raise ValueError('未可靠识别响应格式起点，请用 inspect 核查招标文件并编制有来源的计划。')
        lower=first-1
    groups=named_groups(src,lower) or fallback_groups(src,lower)
    if len(groups)<2:
        raise ValueError('未识别出足够的正式表单；由技能执行方用 inspect 定位采购要求、编制计划，再用 --plan 生成。')
    plan=Plan(src)
    plan.scoring(lower)
    catalog=next((g for g in groups if compact(g[2]) in ('目录','文件目录') and
                  re.search('商务部分|资格部分',''.join(n.text for n in src.nodes[g[0]:g[1]]))),None)
    items=list_items(plan,lower+1,groups[0][0])
    cover(plan,groups,lower)
    plan.add('break')
    if plan.score and not catalog:
        plan.add('title','评分索引表',plan.score[0][0])
        plan.score_block()
        plan.add('break')
    plan.add('toc')
    plan.add('break')
    if catalog:
        strategy='prescribed-contents'
        from_table_of_contents(plan,groups,catalog)
    elif len(items)>=2:
        strategy='material-list-and-forms'
        from_material_list(plan,groups,items,lower)
    else:
        strategy='ordered-forms'
        from_attachments(plan,groups)
    response_work_items(plan, lower)
    add_missing_undertakings(plan, lower)
    if len(plan.titles)<2:
        raise ValueError('未能规划足够的正文标题，请用 inspect 复核编排。')
    if any(re.search('单独密封|另行密封|单独提交',n.full) for n in src.nodes):
        pos=next(n.index for n in src.nodes if re.search('单独密封|另行密封|单独提交',n.full))
        plan.note('复核单独密封、额外副本及同时需装订的材料。',pos)
    result = {
        'source_sha256': src.digest,
        'project': plan.project,
        'strategy': strategy,
        'review': plan.review,
        'blocks': plan.blocks,
    }
    enrich(result, src)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    if args.source.resolve()==args.output.resolve():
        parser.error('计划文件不能覆盖招标原件。')
    try:
        plan=generate(Source(args.source))
    except (ValueError, OSError, ET.ParseError, KeyError) as exc:
        parser.exit(2,f'无法自动生成计划：{exc}\n')
    args.output.write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


if __name__=='__main__':
    main()
