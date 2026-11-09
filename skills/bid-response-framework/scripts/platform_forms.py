"""Plan responses where a bidder data sheet and portal matrices govern two distinct volumes."""
from collections import Counter
from io import BytesIO
import posixpath
import re
from urllib.parse import unquote
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
VOLUME = re.compile(r'^((?:商务|价格|技术|服务)[标卷册])(?:主要)?包括(?:下列|以下)(?:部分|内容)[：:]?$')
PRICE = re.compile(r'报价|价格|单价|折扣|费用')
FILENAME = re.compile(r'\.(?:docx?|xlsx?|pdf|zip|rar|pptx?)$', re.I)
FORMAT_LOCK = re.compile(
    r'(?:不得|不可|不能|严禁|禁止|不允许|不支持).{0,8}(?:更改|改变|修改|调整|变更).{0,12}(?:表式|表格|格式|列宽)'
    r'|(?:表式|表格|格式|列宽).{0,12}(?:不得|不可|不能|严禁|禁止|不允许|不支持).{0,8}(?:更改|改变|修改|调整|变更)'
    r'|(?:锁定|保留|不得改变|统一|限定|固定).{0,4}列宽|列宽.{0,10}(?:不变|锁定|固定|厘米|毫米|磅|cm|mm|pt|%)',re.I)


def joined(e):
    return ''.join(n.text or '' for n in e.iter(W+'t'))


def volume_items(nodes):
    groups = []
    for i, n in enumerate(nodes):
        match = VOLUME.fullmatch(n.text)
        if not match:
            continue
        items = []
        for item in nodes[i+1:]:
            if not item.full:
                continue
            label = re.split(r'[（(]', item.text, 1)[0].strip(' ：:')
            if (item.tag != 'p' or VOLUME.fullmatch(item.text) or len(label) > 42
                    or re.search(r'[。；，：:]', label)):
                break
            items.append((label, item.index))
        if items:
            groups.append((match[1], n.index, items))
    return groups


def packaged_forms(zipfile, body):
    """Map embedded DOCX forms to their actual containing block, never an attachment number."""
    rels = {r.get('Id'): r for r in ET.fromstring(zipfile.read('word/_rels/document.xml.rels'))}
    forms = {}
    for index, block in enumerate(body):
        for obj in block.findall('.//{urn:schemas-microsoft-com:office:office}OLEObject'):
            rel = rels.get(obj.get(REL+'id'))
            if rel is None or rel.get('TargetMode') == 'External':
                continue
            target = unquote(rel.get('Target',''))
            part = posixpath.normpath(posixpath.join('word', target)).lstrip('/')
            if not part.lower().endswith('.docx') or part not in zipfile.namelist():
                forms[index] = []
                continue
            try:
                with ZipFile(BytesIO(zipfile.read(part))) as inner:
                    nested = ET.fromstring(inner.read('word/document.xml')).find(W+'body')
                    # Complex forms remain available as original objects; do not rebuild a table as prose.
                    simple = all(e.tag in (W+'p', W+'sectPr') for e in nested)
                    forms[index] = [joined(p) for p in nested if joined(p).strip()] if simple else []
            except (BadZipFile, KeyError, ET.ParseError):
                forms[index] = []
    return forms


def sheet_lines(element):
    result = []
    for row in element.findall(W+'tr'):
        cells = row.findall(W+'tc')
        if len(cells) < 2:
            continue
        lines = [joined(p).strip() for p in cells[-1].findall(W+'p')]
        result.append([s for s in lines if s])
    return [lines for lines in result if lines]


def selected_delivery(lines):
    item = next((i for i in lines if re.search(r'(?:递交|提交)方式',i[0])), [])
    if not item:
        return ''
    decision = re.search(r'(方式[一二三四五六七八九十\d]+)', item[0])
    if not decision:
        return ' '.join(item)
    choice = next((s for s in item[1:] if re.match(re.escape(decision[1])+r'[：:]', s)), '')
    return choice or (item[0]+'（具体操作按采购平台核实）')


def sheet_value(lines, key):
    return next((r for r in lines if re.match(key, r[0])), [])


def copy_matrix(plan, n, *, pricing=False):
    edits = []
    for c, label in enumerate(n.rows[0]):
        response = re.fullmatch(r'(?:供应商|投标人|应标方)(?:应答|说明|响应)', label)
        amount = pricing and re.search(r'(?:单价|小计|总价|合计|折扣)', label) and '限价' not in label
        for r, row in enumerate(n.rows[1:], 1):
            if c >= len(row) or not row[c]:
                continue
            if response or (amount and re.fullmatch(r'0+(?:\.0+)?', row[c])):
                edits.append({'row':r,'col':c,'text':''})
    plan.copy(n.index,cells=edits,fit_platform_matrix=not FORMAT_LOCK.search(plan.source.alltext))


def original_form(plan, form, index, title):
    # Supply the exact attached source as well as fillable text for paragraph-only forms.
    for line in form:
        if re.sub(r'\s','',line) != re.sub(r'\s','',title):
            plan.add('text', line, index)
    plan.add('text','采购文件所附原始表式',index)
    plan.copy(index)


def score_sections(plan, table):
    copy_matrix(plan,table)
    header = table.rows[0]
    col = next((j for j,s in enumerate(header) if re.fullmatch(
        r'指标列|评审项目|评分项目|评审因素|评分因素|指标名称|评审内容',s)),None)
    if col is None:
        col=next((j for j,s in enumerate(header) if re.search('评分细项|评审项|评分项',s)),None)
    if col is None:
        return
    detail_col=next((j for j,label in enumerate(header) if re.fullmatch(
        r'评分细项|评审细项|评选细项|评分要点|评审要点',label)),None)
    grouped=Counter(row[col].strip(' ：:') for row in table.rows[1:] if col<len(row))
    last_group=None
    for number,row in enumerate(table.rows[1:],1):
        if col>=len(row):
            continue
        name=row[col].strip(' ：:')
        if not name or name in ('合计','总计'):
            continue
        detail=row[detail_col].strip() if detail_col is not None and detail_col<len(row) else ''
        repeated=grouped[name]>1
        if not repeated or name!=last_group:
            plan.head(name if len(name)<=40 else name[:36]+'…',3,table.index)
        last_group=name
        if repeated:
            short=re.split(r'[：:；;。]',detail,1)[0].strip()
            title=short if short and len(short)<=42 else f'评分明细第{number}项'
            plan.head(title,4,table.index)
        wording=' '.join(row)
        if re.search(r'提供.{0,6}完整.{0,10}方案|须.{0,8}完整.{0,10}方案',detail):
            plan.add('slot','完整方案正文：________',table.index)
        elif repeated and re.search(r'(?:请|须|需)提供',detail):
            plan.add('text',detail,table.index)
        plan.add('slot','应答内容：________    证明材料及页码：________',table.index)
        declaration=re.search(r'(?:需(?:要)?|须|应).{0,6}(?:出具|提供|提交).{0,4}承诺[函书]',wording)
        if declaration:
            # Preserve the condition as the buyer's requirement; the bidder decides what to promise.
            context=re.split(r'[；;。]',wording[:declaration.start()])[-1]
            condition=re.sub(r'^.*?要求[：:]','',context).rstrip(' （(此项')
            if condition:
                plan.add('text','采购方要求：'+condition,table.index)
            plan.head('承诺函（'+(title if repeated else name)+'）',5 if repeated else 4,table.index)
            plan.add('slot','承诺内容（由投标人核实后填写）：________',table.index)
            plan.add('text','投标人（签章）：________    日期：____年__月__日',table.index)


def try_plan(src):
    volumes=volume_items(src.nodes)
    if len(volumes)!=2 or not (any(v[0].startswith('技术') for v in volumes) and
                              any(v[0].startswith(('商务','价格')) for v in volumes)):
        return None
    sheet=next((n for n in src.nodes if n.rows and '投标文件递交方式' in n.full and
                ('投标保证金' in n.full or '供应商资质要求' in n.full)),None)
    if sheet is None:
        return None
    matrices=[n for n in src.nodes[sheet.index+1:] if n.rows]
    price=next((n for n in matrices if '数量' in n.rows[0] and any(PRICE.search(c) for c in n.rows[0])),None)
    response=[n for n in matrices if any(re.fullmatch(r'(?:供应商|投标人)(?:响应|应答)',v) for v in n.rows[0])]
    scores=[n for n in matrices if any(re.search('分值|满分',v) for v in n.rows[0]) and
            any(re.search('评分|评审',v) for v in n.rows[0])]
    if price is None or not response:
        return None
    from plan_framework import Plan
    plan=Plan(src)
    with ZipFile(src.path) as archive:
        body=ET.fromstring(archive.read('word/document.xml')).find(W+'body')
        lines=sheet_lines(body[sheet.index])
        forms=packaged_forms(archive,body)
    cover=next((n for n in src.nodes if n.tag=='p' and re.sub(r'\s','',n.text) in ('招标文件','采购文件')),None)
    if cover:
        names=[n.text for n in src.nodes[:cover.index] if n.text and not n.text.startswith('TAG_')]
        if names:
            plan.project=names[-1]
    buyer=sheet_value(lines,r'(?:招标人|采购人)名称')
    from plan_framework import add_standard_cover, extract_project_id, extract_package_name
    add_standard_cover(
        plan, project=plan.project, doc_title='投标文件', ref=sheet.index,
        project_id=extract_project_id(src.alltext), package=extract_package_name(src.alltext),
        fields=(('投标人名称：', '________'), ('投标人地址：', '________'), ('日期：', '________')))
    if len(buyer)>1 and re.fullmatch(r'(?:招标人|采购人)名称[：:]?',buyer[0]):
        plan.note('采购人：'+buyer[1], sheet.index)
    plan.add('break')
    plan.add('toc')
    plan.add('break')
    mode=selected_delivery(lines)
    due=sheet_value(lines,r'投标截止')
    format_rule=sheet_value(lines,r'投标文件的式样和签署')
    language=sheet_value(lines,r'(?:投标|响应)(?:文件)?语言')
    instructions='；'.join(v.strip('；。') for v in [mode,' '.join(format_rule),' '.join(language),' '.join(due)] if v)
    if instructions:
        plan.note(instructions,sheet.index)
    links=[n for n in src.nodes[price.index+1:] if n.tag=='p' and FILENAME.search(n.text)]
    bid_bond=sheet_value(lines,r'投标保证金')
    no_bond=bid_bond and re.search(r'[:：]\s*(?:无|不收取|无需|免收|不要求|否)',bid_bond[0])
    for vol,ref,items in volumes:
        commercial=vol.startswith(('商务','价格'))
        plan.head(vol,1,ref)
        if instructions:
            plan.add('text',instructions,sheet.index)
        if commercial and no_bond:
            plan.add('text',bid_bond[0],sheet.index)
        performance=sheet_value(lines,r'履约保证金') if commercial else []
        if performance and re.search(r'[:：]\s*(?:无|不收取|无需|免收|不要求|否)',performance[0]):
            plan.add('text',performance[0],sheet.index)
        if commercial:
            for rule in [sheet_value(lines,r'投标报价'),sheet_value(lines,r'最高限价')]:
                if rule:
                    wording=' '.join(rule)
                    plan.add('text',wording,sheet.index)
                    if re.search(r'限价为[：:]?\s*人民币\s*[圆元]整',wording):
                        plan.add('text','本栏未载明金额，请以采购方报价附件或补遗核定限价。',sheet.index)
        for label,i in items:
            if '投标保证金' in label and no_bond:
                continue
            plan.head(label,2,i)
            following=next((k for k in forms if k==i or
                            (k>i and not any(x.full.strip() for x in src.nodes[i+1:k]))),None)
            if following is not None:
                original_form(plan,forms[following],following,label)
                continue
            if commercial and PRICE.search(label):
                copy_matrix(plan,price,pricing=True)
                # Copy actual submission and cap text, signature fields and external quotation links.
                stop=min((n.index for n in response+scores if n.index>price.index),default=src.end)
                for n in src.nodes[price.index+1:stop]:
                    if n.tag!='p' or not n.text:continue
                    if FILENAME.search(n.text):
                        if PRICE.search(n.text):
                            plan.copy(n.index)
                            plan.add('text','按平台链接取得本项原附件后填写、提交。',n.index)
                    elif re.search(r'系统内填写|作为附件上传|最高限价|报价超出|预计采购数量|最终执行报价|签字[（(]|投标人盖章',n.text):
                        plan.copy(n.index)
                continue
            if not commercial:
                topic='服务' if '服务' in label else '技术'
                match=[n for n in response if any(re.search(topic+r'(?:项|要求|标准|指标|条款)',c)
                                                   for c in n.rows[0])] if re.search('应答表|响应表',label) else []
                if match:
                    for n in match:copy_matrix(plan,n)
                    continue
                if re.search('证明材料|资格|资质',label):
                    for rule in lines:
                        if re.match('供应商资质要求|是否允许分包|是否接受联合体',rule[0]):
                            plan.add('text',' '.join(rule),sheet.index)
                    for node in src.nodes[:sheet.index]:
                        if node.tag=='p' and re.search(r'如有需要.{0,60}营业执照|^证明投标人已具备履行合同|^证明投标人满足招标文件业绩',node.text):
                            plan.copy(node.index)
                    plan.add('slot','【待附】按本项目资质条件提交证明文件。',i)
                    continue
            plan.add('slot','【待补】按采购文件要求填写并签署。',i)
        if not commercial:
            for n in links:
                if not PRICE.search(n.text):
                    plan.head('项目需求附件',2,n.index)
                    plan.copy(n.index)
                    plan.add('slot','按采购平台链接取得原件，逐项编写服务响应。',n.index)
            for n in scores:
                plan.head('技术评分与应答',2,n.index)
                score_sections(plan,n)
    return {'source_sha256':src.digest,'project':plan.project,
            'strategy':'platform-volumes-and-matrices','blocks':plan.blocks,'review':plan.review}
