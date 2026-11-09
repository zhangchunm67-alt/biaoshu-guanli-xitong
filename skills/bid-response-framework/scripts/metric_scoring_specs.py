"""Identify source-backed award factors and commercial/technical requirements."""
from collections import defaultdict
import re
import unicodedata
from metric_source import scope_item


def norm(s):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s))


def item(category, unit, label, text=None, *, key=None):
    record = {'category': category, 'unit': unit, 'text': text or unit.text, 'label': label}
    if key:
        record['key'] = key
    return record


def column(headers, *patterns):
    for pattern in patterns:
        for i, header in enumerate(headers):
            if re.search(pattern, norm(header)):
                return i
    return None


SUMMARY = re.compile(r'^(?:[（(]?\d+[)）.、]?\s*)?(?:合计|小计|总分|总计|总权重|满分)(?:数|值|合计|[：:\s\d.%％分（）()]|共)*$')
POINT = r'\d+(?:\.\d+)?(?:\s*[-~至—]\s*\d+(?:\.\d+)?)?'
SCORE_VALUE = re.compile(rf'{POINT}\s*(?:分|%|％)|(?:满分|得分|扣分|权重)[：:为]?\s*{POINT}')
GRADE = re.compile(r'^(?:优秀|良好|一般|较差|不合格|合格|不及格|优|良|中|差|A|B|C|D)[档级等]?(?:[：:(（、]|$)')
SCORE_LABELS = (r'^(?:评选细项|评分细项|评分因素|评分要点|评审因素|评审内容|考察项目|指标名称|考核项目|评分项目|评分项|评审项目|评审指标)',
                r'^(?:服务内容|指标列|评分因素|项目|类别)$')
DETAIL_HEADER = r'(?:评分标准|评分说明|评分细则|评审标准|评分办法|评分规则|具体要求|评价标准|打分标准|评分内容|评判标准)'


def table_factors(group):
    factors = []
    allocation = False
    previous = None
    for unit in group:
        hdr = unit.headers
        cells = [x.strip() for x in unit.cells]
        if len(cells) < 2 or not hdr:
            continue
        head = ''.join(map(norm, hdr))
        if re.search(r'初步评审|资格评审|形式评审|符合性审查', head):
            continue
        # Quote weights beside prices/taxes are not award factors.
        if re.search(r'单价|含税|税率|报价金额|不含税小计|计价单位', head):
            continue
        pcol = column(hdr, r'分值|满分|得分|权重|配分|标准分')
        dcol = column(hdr, DETAIL_HEADER)
        label_col = column(hdr, *SCORE_LABELS)
        if not re.search(r'评分|评审|评选|考评|打分|满分|得分|分值|配分|标准分', head):
            # A bare 项目/权重 summary is valid only in an explicit evaluation
            # section; ordinary service/quotation schedules remain excluded.
            if not re.search(r'评分|评标|评审|评选|考评', unit.context):
                continue
        if re.search(r'报价|计价|采购需求|服务清单', unit.context) and not re.search(
                r'评分|评审|满分|得分|分值|配分|标准分', head):
            continue
        if label_col is None or label_col >= len(cells) or (pcol is None and dcol is None):
            continue
        if pcol is None and (not re.search('评分|打分|考评', head) or not SCORE_VALUE.search(unit.text)):
            continue
        if re.search(r'投标人响应|应答情况|供应商应答', head) and pcol is None:
            continue
        allocation = len(set(map(norm, hdr))) == 2 and dcol is None
        # Points and mandatory participation can both be literal entries in a scoring matrix.
        pval = cells[pcol] if pcol is not None and pcol < len(cells) else ''
        details = cells[dcol] if dcol is not None and dcol < len(cells) else ''
        numeric = bool(re.search(r'\d', pval))
        marked = pval in ('必选', '必评', '必须', '必选项')
        if not (numeric or marked or SCORE_VALUE.search(details)):
            continue
        # Merged family columns can repeat the same header. Use the innermost
        # factor before the rubric, not the broad 商务部分 container.
        if dcol is not None:
            for i in range(label_col + 1, min(dcol, len(cells))):
                if cells[i] and any(re.search(pattern, norm(hdr[i])) for pattern in SCORE_LABELS):
                    label_col = i
        label = cells[label_col].strip(' ：:')
        if not label or GRADE.search(label):
            # A vertically merged item can have one printed name and a row per grade.
            if previous is not None and (GRADE.search(label) or GRADE.search(details) or
                                         any(GRADE.search(v) for v in cells if v)):
                previous.setdefault('related', []).append(unit)
                previous['text'] += '\n' + unit.text
            continue
        if any(SUMMARY.fullmatch(norm(v)) for v in cells[:label_col + 1] if v):
            continue
        if norm(label) == norm(hdr[label_col]) or re.search('页码范围及说明|详见N.?M页', unit.text, re.I):
            continue
        # A platform may repeat a broad family such as 团队硬件能力 in several rows.
        # Use the actual criterion, not the family, and retain the entire rubric as evidence.
        display = label
        category_col = column(hdr, r'^指标列$')
        if '评分细项' in norm(hdr[label_col]):
            short = re.match(r'^([^，,。；;：:]{2,24})[：:]', label)
            if category_col is not None and category_col < len(cells):
                display = cells[category_col] + (' · ' + short[1] if short else '')
            elif short:
                display = short[1]
        if not display:
            display = label
        # A full new criterion may have the same name and trailing grade bands.
        # Only grade-only rows continue the preceding factor.
        if previous and previous['_label'] == label and GRADE.match(details):
            previous.setdefault('related', []).append(unit)
            previous['text'] += '\n' + unit.text
            continue
        value = pval
        if numeric and re.fullmatch(POINT, pval):
            if '权重' in norm(hdr[pcol]):
                value = pval + '%' if '%' in norm(hdr[pcol]) else '权重' + pval
            else:
                value = pval + '分'
        if details and not GRADE.match(details) and label in ('综合实力', '服务方案', '项目实施与管理'):
            excerpt = re.split(r'[。；;\n]', details, 1)[0]
            display = label + ' · ' + excerpt[:42]
        title = display[:110] + ('（'+value+'）' if value else '')
        previous = item('score', unit, title)
        previous['_label'] = label
        factors.append(previous)
    for f in factors:
        f.pop('_label', None)
    return allocation, factors


WEIGHT = re.compile(rf'(?:^|[，,、；;：:。])\s*([^，,、；;：:。]{{2,28}}?)(?:评审占比|综合评审占比|评分权重(?:为)?|权重(?:为)?|(?:得分)?占比|占总分(?:的)?|占)\s*({POINT}\s*(?:分|%|％))')
NAMED_POINTS = re.compile(rf'^\s*(?:[（(]?[一二三四五六七八九十\d]+[)）、.．]\s*)?([^。；：:，,（(]{{2,35}})\s*[（(]\s*(?:(?:满分|分值)\s*[：:]?\s*)?({POINT}\s*分)\s*[)）]')


def scoring(units):
    by_table = defaultdict(list)
    for u in units:
        if u.kind == 'row':
            by_table[(u.part, u.index)].append(u)
    details, totals = [], []
    for group in by_table.values():
        allocation, factors = table_factors(group)
        (totals if allocation else details).extend(factors)
    detailed_scopes = {(r['unit'].part, r['unit'].scope) for r in details}
    yield from details
    for rec in totals:
        if (rec['unit'].part, rec['unit'].scope) not in detailed_scopes:
            yield rec
    for u in units:
        if u.kind != 'paragraph' or not SCORE_VALUE.search(u.text):
            continue
        if (u.part,u.scope) in detailed_scopes:
            continue  # the detailed matrix supersedes a nearby narrative summary
        if any(k in u.text for k in ('评分', '评审', '权重')) and not re.search(r'例如|举例|总得分为',u.text):
            for m in WEIGHT.finditer(u.text):
                label=m[1].strip()
                if re.search('总分|合计|评标委员会|评分法|得分排名',label): continue
                yield item('score', u, label+'（'+m[2]+'）', m[0].lstrip('，,、；;：:。 '))
        m = NAMED_POINTS.match(u.text)
        if m and re.search(r'评分|评标|评审|讲标', u.context + u.text) and not SUMMARY.fullmatch(norm(m[1])):
            yield item('score',u,m[1]+'（'+m[2]+'）')


# Label families in project-specific schedules. Administrative metadata (buyer, address
# of contact, enrolment windows), eligibility and evaluator-only decisions stay out.
BUSINESS = (
    ('evaluation_schedule',r'^比价$|比价时间|比价期间'),
    ('clarification',r'澄清答疑|答疑(?:要求|规则|方式|时间)|澄清(?:要求|方式|时间)'),
    ('language',r'(?:投标|响应|应答)(?:文件)?语言|投标语言|文件语言'),
    ('bond',r'投标保证金|履约保证金'),
    ('subcontract',r'分包|转包'),
    ('route',r'递交方式|投递方式|提交方式|投标方式|递交地点|递交地址|送达地点|递交时间|提交截止|投标截止|应标截止|响应截止|报价截止'),
    ('format',r'式样和签署|式样和签章|编制及签署|编制要求|文件正副本|文件份数|装订|密封|签字和盖章|签章要求|签署要求|电子文件|递交形式'),
    ('validity',r'投标有效期|报价有效期|响应有效期'),
    ('price',r'项目预算|预算上限|控制价|最高限价|最高.*限价|报价|价格要求|计价|折扣|含税|税率|结算|付款|支付方式|开票'),
    ('period',r'项目地点|项目开始日期|预计开始日期|服务期限|服务时间|服务周期|交付时间|服务地点|交付期限|履行地点|履行期限|交付地点|质保期|保修期|工期'),
    ('deliverable',r'交付物|验收标准|验收方法|交付要求|服务(?:标准|要求|范围|内容)|采购(?:内容|数量|范围)|采购清单'),
)
SERVICE_CONTEXT = re.compile(r'技术规范书|技术[及/和]?服务需求书|技术要求|用户需求书|采购(?:需求|要求)|工作(?:内容|要求)|项目概述|项目需求|核心服务需求|服务(?:范围|内容|需求|要求)|验收(?:[/／]考核)?(?:要求|标准)|项目采购清单|技术与服务标准')
DUTY = re.compile(r'应(?:当|予|按|在|向|在|提供|具备|保证|对|采用|根据|负责|满足|包括|明确|当)?|须|必须|需|不得|严禁|应当|不少于|不超过|至少|不低于|负责|要求|提供|完成|包括|执行|策划|确保|制作|开展|支持|安排|设计|组织|配备|协助|交付|配合|实现|严禁')
QUAL_LABEL = re.compile(r'资格(?:条件|要求|部分|证明)|资质(?:条件|要求|部分|证明)|合格投标人|资格审查|'
                        r'(?:投标人|供应商|报价人|响应人)资格|联合体(?:报价|响应|投标)')
FORMAT_AREA = re.compile(r'(?:投标|响应)文件格式|空白表式|合同(?:条款及格式|条款与格式|模板|模版|格式|范本)|中介机构评估规则')
EVALUATOR = re.compile(r'^(?:评标委员会|评标小组|评委|评审小组|采购小组|招标人有权|招标人可|采购人有权|采购人可|招标代理机构|我方|本公司|我公司|我单位)')


def topic(text):
    return next((name for name, pat in BUSINESS if re.search(pat,text)),None)


def labelled_row(unit):
    cells = [c.strip() for c in unit.cells]
    if not cells or not any(cells):return None
    hdr = unit.headers
    ci = column(hdr,r'^(?:条款名称|选项|项目[／/]活动|项目/活动|项目条款|内容|条款内容|事项|名称|项目)$')
    vi = column(hdr,r'编列内容|说明与要求|编列的内容|规定内容|项目内容|^说明$')
    if vi is not None and vi < len(cells):
        name = next((v for v in reversed(cells[:vi]) if v and not re.fullmatch(r'[\d.（）()、]+',v)), '')
        other = cells[vi]
    elif len(cells) >= 2 and ('资料表' in unit.context or '前附表' in unit.context or
                              ci is not None and ci<len(cells) and re.fullmatch(r'\d+\.\d+', cells[0])):
        rest=[c for c in cells if c and not re.fullmatch(r'[\d.（）()、]+',c)]
        if not rest:return None
        if len(rest)>1:
            name,other=rest[-2:]
        else:
            bits=re.split(r'[:：\n]', rest[0],maxsplit=1)
            if len(bits)<2:return None
            name,other=bits
    else:
        return None
    if not name or not other or len(name)>46 or QUAL_LABEL.search(name) or not topic(name):return None
    if re.search('评审标准|评标办法|资格证明|合同文本的提供',name):return None
    return name.strip('：: '),other


def matrix_specs(unit):
    hdr = unit.headers
    if column(hdr,r'评分|评审|分值|满分|得分|投标文件的响应|偏离情况|响应文件的') is not None:
        return
    req = column(hdr,r'主要规格[/／]服务内容|^服务要求$|^技术要求$|具体要求|交付标准|^验收标准$')
    if req is None or req>=len(unit.cells):return
    cells=unit.cells; criteria=cells[req].strip()
    if not criteria or len(criteria)<5 or '示例' in criteria[:10] or SUMMARY.fullmatch(norm(cells[0])):return
    subject=column(hdr,r'^服务项$|^技术项$|^服务内容$|^交付事项$|^货物/服务名称')
    label=cells[subject] if subject is not None and subject < len(cells) and cells[subject] else '采购要求'
    yield item('spec',unit,label[:70],criteria)


def response_matrices(units):
    """Read supplier-completed requirement tables, including physical continuations.

    A feedback column plus a buyer's description column supplies the schema.
    Headerless continuations may reuse that schema only in the same technical
    context with the same physical width. Original evidence is never rewritten.
    """
    rows, handled = [], set()
    active = None
    previous = None
    family = ''
    for unit in units:
        if unit.kind != 'row':
            continue
        hdr = unit.headers
        context = (unit.part, unit.scope, unit.context)
        if hdr:
            response = column(hdr, r'(?:供应商|投标人|响应人).*(?:反馈|响应|应答)|^(?:供应商反馈|响应情况|应答情况)$')
            description = column(hdr, r'^说明$|说明与要求|^技术要求$|^服务要求$|具体要求|要求说明')
            if (response is None or description is None
                    or column(hdr, r'评分|分值|得分|满分|单价|税率') is not None):
                active = None
                previous = None
                continue
            subject_cols = [i for i in range(description) if i != response and
                            not re.fullmatch(r'序号|编号', norm(hdr[i]))]
            if active is None or active['context'] != context:
                previous = None
                family = ''
            active = {'context': context, 'width': len(hdr), 'description': description,
                      'subjects': subject_cols, 'last_index': unit.index}
        elif (not active or active['context'] != context or len(unit.cells) != active['width']
              or unit.index-active['last_index'] > 20 or not SERVICE_CONTEXT.search(unit.context)):
            active = None
            previous = None
            continue
        description = active['description']
        if description >= len(unit.cells):
            continue
        criteria = unit.cells[description].strip()
        active['last_index'] = unit.index
        handled.add((unit.part, unit.index, unit.row))
        if not criteria:
            continue
        subjects = [unit.cells[i].strip() for i in active['subjects']]
        if not any(subjects) and previous is not None:
            previous['text'] += '\n' + criteria
            previous.setdefault('related', []).append(unit)
            continue
        if subjects and subjects[0]:
            family = subjects[0]
        labels = ([family] if family else []) + [s for s in subjects[1:] if s]
        label = ' · '.join(dict.fromkeys(norm(s) for s in labels)) or '技术要求（续表）'
        previous = item('spec', unit, label[:130], criteria,
                        key='response-row:' + str(unit.index) + ':' + str(unit.row))
        rows.append(previous)
    return rows, handled


def submission_paragraph(unit):
    """Literal submission/format obligations, including typography-split Chinese."""
    s = re.split(r'招标经办联系人[：:]|(?:联系电话|联系邮箱)[：:]', unit.text.strip(), 1)[0].strip()
    n = norm(s)
    if FORMAT_AREA.search(unit.context) or QUAL_LABEL.search(unit.context):
        return
    if (not re.search(r'投标|应答|响应|报价|回标', n)
            and not re.search(r'投标有效期|报价要求|商务报价', unit.context)) or len(n) < 10:
        return
    if re.search(r'^(?:[\d一二三四五六七八九十]+[、.)）])?(?:招标人|采购人|评标委员会|评委)', n):
        return
    label = None
    if re.search(r'\d+份(?:正本|副本)|(?:正本|副本).{0,6}\d+份|每.{0,3}(?:页|份).{0,12}(?:盖章|印章|公章)|骑缝章', n):
        label = '正副本份数及签章'
    elif re.search(r'(?:技术标|商务标).{0,35}(?:独立|单独|分别)(?:成册|封装)|分技术标与商务标.{0,15}密封', n):
        label = '技术商务分册与密封'
    elif re.search(r'(?:投标|应答|响应|报价)(?:文件|书)?(?:有效期|应以中文)|(?:以|用)中文(?:书写|编写)|'
                   r'(?:应|须|需).{0,8}(?:使用中文|中文译文)|提供.{0,8}中文译文', n):
        label = '文件语言与有效期'
    elif re.search(r'投标有效期|报价有效期|响应有效期', unit.context) and re.search(r'\d+个?(?:自然日|工作日|天)', n):
        label = '文件有效期（条件适用）'
    elif re.search(r'(?:回标|送达|递交).{0,30}(?:快递|密封|截止|地点)|快递方式.{0,15}(?:送达|提交)', n):
        label = '递交方式及截止要求'
    elif re.search(r'(?:根据|按|按照).{0,35}(?:商务标|商务要求).{0,12}(?:报价|提供报价)|报价.{0,30}(?:标明税率|人民币|结算)', n):
        label = '报价及结算要求'
    elif re.search(r'(?:费用|金额).{0,40}结算|(?:报价|计价).{0,45}(?:不同参数|多个报价)', n):
        label = '报价及结算要求'
    if label and not re.search(r'[:：]$', n):
        yield item('spec', unit, label, s)


def evaluation_method(unit):
    """Keep explicit evaluation sequence/prerequisites without inventing points."""
    s = unit.text.strip()
    n = norm(s)
    sequence = (re.search(r'评标流程|评审流程|评审顺序|评标顺序|评分顺序', n)
                and re.search(r'先.{1,40}(?:后|再)', n))
    prerequisite = (re.search(r'技术(?:标|评审)?(?:合格|通过)|资格(?:审查)?(?:合格|通过)', n)
                    and re.search(r'综合评分法|综合评估法|最低评标价法|经评审的最低', n))
    if sequence or prerequisite:
        yield item('spec', unit, '评审方法与顺序', s)
    elif re.search(r'(?:项目经理|项目负责人|拟派人员|述标人员|讲标人员).{0,65}(?:述标|讲标|答辩)', n):
        yield item('spec', unit, '述标人员与方式', s)


def envelope_fields(units):
    """Keep a sealing instruction and its following literal field checklist together."""
    for pos, unit in enumerate(units):
        if (unit.kind != 'paragraph' or not re.search(r'封套|封袋|封包', unit.text)
                or not re.search(r'(?:写明|注明|标明).{0,8}(?:下列|以下)', unit.text)):
            continue
        related=[]
        for following in units[pos+1:pos+9]:
            if following.kind != 'paragraph' or following.part != unit.part or following.scope != unit.scope:
                break
            field=re.sub(r'^(?:[（(][一二三四五六七八九十\d]+[)）]|\d+[、.)）])\s*', '', following.text.strip())
            if (len(field) > 55 or not re.search(r'名称|编号|地址|时间|日期|联系人|电话|标识', field)
                    or re.search(r'[。；;]|文件的递交|编制要求', field)):
                break
            related.append(following)
        if related:
            result=item('spec',unit,'封套标记与签章',unit.text+'\n'+'\n'.join(u.text for u in related))
            result['related']=related
            yield result


def choice_excerpt(name,value):
    # Some schedules place mutually exclusive options below the selected value in the same cell.
    m=re.match(r'\s*(方式[一二三四五六七八九十\d]+)',value)
    if m and ('递交' in name or '方式' in name):
        opts=list(re.finditer(r'方式[一二三四五六七八九十\d]+\s*[：:]', value))
        for i,opt in enumerate(opts):
            if opt[0].rstrip('：:').strip()==m[1]:
                return value[opt.start():opts[i+1].start() if i+1<len(opts) else len(value)].strip()
    if '保证金' in name:
        # 金额/收款账户文案可以是未选选项的空白说明。
        no=re.match(r'\s*(?:无|不收取|无需|免收|不要求|否)(?=\s|[，,；;。.\d（(]|$)',value)
        if no:return no[0].strip()
    return value


def substantive_paragraph(unit, authoritative):
    # A locally annotated buyer document sometimes appends a bidder-side example after the printed clause.
    # The locator still exposes the raw unit; the counted excerpt ends before the example.
    s = re.split(r'(?<=[。；;])[（(](?:案例[：:]|公司介绍[-－]|内部备注[：:]|业务批注[：:])', unit.text.strip(), 1)[0]
    declared = scope_item(s)
    if unit.is_heading and declared and declared[2]:
        return
    n = re.sub(r'^(?:[★＊*]\s*)?(?:\d+(?:\.\d+)*[、.)）]?|[（(][一二三四五六七八九十\d]+[)）]|[一二三四五六七八九十]+[、.])\s*', '',s)
    if len(n)<7 or EVALUATOR.match(n) or re.search(r'^(?:TAG_|第[一二三四五六七八九十百零\d]+[章节目]|目录|中标通知书|联系方式|注：?上述为例)',n):
        return
    if not re.search(r'[，,:：;；。\d]|(?:供应商|投标人|服务商|应标人员|报价人|应答人).*(?:须|应|需|不得)', n):
        return
    if re.search(r'\.docx?$|\.xlsx?$|\.{3,}\s*\d+$|[：:]\s*[_＿ ]*$|^见附件[一二三四五六七八九十\d]+$',n,re.I):return
    if re.search(r'评标委员会|评审委员会|评分法|综合得分|评分权重|推荐.{0,8}候选|加减分|是导致.*常见问题',n):return
    if re.search(r'(?:视为|按|为)(?:无效(?:投标|响应)|废标)|(?:投标|响应)(?:将|应)?(?:被)?否决',n) and not re.search('限价|预算|有效期|递交|密封|签章|保证金',n):return
    if re.search(r'^(?:乙方|甲方|买方|卖方|受托方|委托方)',n) and not SERVICE_CONTEXT.search(unit.context):
        return
    if QUAL_LABEL.search(unit.context) and not SERVICE_CONTEXT.search(unit.context):return
    if FORMAT_AREA.search(unit.context):
        return
    prefix=re.split(r'(?<!\d)[：:]|[，,。；;]',n,maxsplit=1)[0]
    # Preserve times such as 17:00 and keep a concise human-readable item name.
    def label():
        if re.search(r'截止|\d{4}年.+(?:前|收)|发送至.+邮箱', n) and re.search('递交|文件|发送|提交|截止',n):
            return '递交时间与方式'
        candidate=prefix if len(prefix)<=35 else re.split('[。；;，,]', n,maxsplit=1)[0]
        return candidate if len(candidate)<=55 else candidate[:52]+'…'
    t=topic(prefix) if len(prefix)<=28 else None
    if t and t in authoritative and re.search(r'(?:详见|见|具体以).{0,20}(?:须知前附表|投标资料表)|以《?投标资料表》?为准', n):
        return
    if 'route' in authoritative and re.match(r'^方式[一二三四五六七八九十\d]+[:：]',n):return
    if re.search(r'(?:如|可视|视项目需要).{0,18}(?:投标资料表|须知前附表)|^(?:本协议|本合同)自.{0,32}(?:生效|签订)', n):
        return
    if re.match(r'第[一二三四五六七八九十百\d]+条',n) and not re.search('应|须|需|不得|按照|根据|[。；]',n):
        return
    service_context=SERVICE_CONTEXT.search(unit.context)
    # Include numbered technical specifications even when declarative, but do not count a heading.
    if service_context and (DUTY.search(n) or re.search(r'是否|^(?:服务需求|需求明细)[：:]',n) or
           (unit.scope and not unit.is_heading and re.match(r'[^，,。；;：:]{2,28}[：:]\S', n)) or
           re.search(r'^[★＊*]?\s*\d+\.\d+\s*',s) or bool(re.search(r'^验收|服务周期|项目[一二三四五六七八九十\d]+[（(:：].*预算', n))):
        if not re.search(r'^项目(?:背景|目标|名称|构成)|^(?:拟|本次拟)采购|^招标人拟|^(?:我公司|我行拟)|^(?:现将|以下简称)|^项目[一二三四五六七八九十\d]+[:：][^。；]+项目$',n):
            yield item('spec',unit,label(),s)
        return
    explicit = t and re.search(r'[：:]\s*\S|\d|须|不得|不少于|不超过|加盖|扫描|上传|各项|以.+为准',n)
    direct = re.search(r'(?:供应商|投标人|报价人|应标人员|应答人|项目组|中标人)(?:应|必须|须|不得|需|应当|必须按照)',n) and re.search(r'报价|开票|签章|盖章|扫描|装订|密封|递交|交付|验收|服务|工作日|翻译|版权',n)
    actionable = re.search(r'(?:加盖.{0,6}(?:公章|单位章)|盖章的.{0,14}报价[单表]|签字盖章|扫描|单独密封|分开.{0,6}提交|分别.{0,4}报价|每份材料|另封|闭口全包|超预算|各服务单项|上传.{0,5}(?:平台|系统))',n)
    if explicit or direct or actionable:
        if re.search(r'投标人须知[》”]|^[（(]?如发生|^除非|^如果|^凡|^其他可能|示例|样例|模拟|模板范例',n) and not t:return
        yield item('spec',unit,label(),s)


def contract_blocks(units):
    """Contract specimens contain reused service descriptions and buyer-side boilerplate."""
    by_part = defaultdict(list)
    for u in units:
        if u.kind == 'paragraph':
            by_part[u.part].append(u)
    inside = set()
    chapter = re.compile(r'^第[一二三四五六七八九十百零〇\d]+章\s*(.{1,30})$')
    for part, paragraphs in by_part.items():
        active = False
        for i, u in enumerate(paragraphs):
            m = chapter.fullmatch(u.text.strip())
            if m and not re.search(r'[.．…]|\d$', m[1]):
                # A run of bare chapter names in an early contents list is not a real boundary.
                prev_chapter = i and chapter.fullmatch(paragraphs[i-1].text.strip())
                next_chapter = i+1 < len(paragraphs) and chapter.fullmatch(paragraphs[i+1].text.strip())
                if not prev_chapter and not next_chapter:
                    active = bool(re.search('合同(?:条款|模版|模板|格式|范本)|^合同$', m[1]))
            if active:
                inside.add((part, u.index))
    return inside


def extract(units):
    """One award factor per criterion or explicit narrative weight; one substantive source clause per spec."""
    yield from scoring(units)
    envelope_rows=list(envelope_fields(units))
    yield from envelope_rows
    envelope_handled={id(u) for row in envelope_rows for u in [row['unit'],*row['related']]}
    matrix_rows, matrix_handled = response_matrices(units)
    yield from matrix_rows
    contracts = contract_blocks(units)
    schedules = {}
    for u in units:
        if u.kind=='row':
            pair=labelled_row(u)
            if pair:
                schedules[(u.part,u.index,u.row)]=pair
    authoritative=defaultdict(set)
    waived_bonds=defaultdict(set)
    for u in units:
        pair=schedules.get((u.part,u.index,u.row))
        if pair:
            authority = (u.part,u.scope)
            authoritative[authority].add(topic(pair[0]))
            name,value = pair
            bond = re.search(r'投标保证金|履约保证金',name)
            if bond and re.search(r'^(?:无|免收|不要求|不收取|不适用|无需)(?:[，。；;\s]|$)|'
                                  r'(?:是否要求.{0,8}保证金|本项目)[：:]?(?:不要求|免收|不收取)',value.strip()):
                waived_bonds[authority].add(bond[0])
    for u in units:
        if u.kind=='paragraph':
            if id(u) in envelope_handled:
                continue
            waived = waived_bonds[(u.part,u.scope)] | waived_bonds[(u.part,'')]
            if any(bond in u.text for bond in waived):
                continue
            if (u.part,u.index) in contracts and not (re.search(
                r'付款|结算|验收期限|免费保修|售后服务时限|质保期限',u.text) and
                re.search(r'\d+\s*(?:%|％|天|个工作日|个月|年|小时)',u.text) and not re.search(r'_{2,}|【待',u.text)):
                continue
            yield from evaluation_method(u)
            yield from submission_paragraph(u)
            yield from substantive_paragraph(u,authoritative[(u.part,u.scope)]|authoritative[(u.part,'')])
        elif u.kind=='row':
            if (u.part,u.index,u.row) in matrix_handled:
                continue
            pair=schedules.get((u.part,u.index,u.row))
            if pair:
                name,value=pair
                evidence=choice_excerpt(name,value)
                if evidence:
                    # Use a literal excerpt; label carries the policy field for selected options like “无”.
                    clause = name + '：' + evidence
                    yield item('spec',u,name,clause if norm(clause) in norm(u.text) else evidence,
                               key=norm(name)+'|'+norm(evidence))
            else:
                yield from matrix_specs(u)
