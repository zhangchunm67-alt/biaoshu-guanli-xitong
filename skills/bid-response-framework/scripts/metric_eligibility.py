"""Identify explicit rejection grounds and mandatory bidder eligibility, with source evidence."""
import re
import unicodedata


NUMBER = r'[零〇一二三四五六七八九十百\d]+'
BULLET = re.compile(r'^(?:[①-⑳]|[（(]\s*' + NUMBER + r'\s*[）)]|' + NUMBER +
                    r'\s*[、)）]|\d+(?:\.\d+)+(?:[.．、]\s*)?|' + NUMBER + r'\s*[.．](?!\d))\s*')
LIST_MARK = re.compile(r'[①-⑳]|[（(]\s*\d{1,2}\s*[）)]|(?<![\d.])\d{1,2}\s*[、）)]')
REJECT = re.compile(
    r'废标|无效(?:投标|标书|响应)|(?:投标|应答|响应|报价)(?:文件|书)?(?:均|将|被|视为|判为|按|为|直接|认定为){0,4}无效|'
    r'否决(?:其|该|所有|全部|相应|此|该项)?(?:投标|响应)|(?:投标|响应)(?:文件)?(?:均|将|应|可|可能|被|予以){1,5}否决|'
    r'(?:取消|撤销|撤消|丧失|失去|剥夺).{0,20}(?:本次|投标|应标|参与|入围|竞标|应征|比选|中标候选|中标).{0,8}资格|'
    r'取消该?投标人(?:的)?资格|(?:作|按)(?:无效标|无效报价)|无效标(?:书|处理|判定)|'
    r'(?:其|该)?(?:投标|应答|响应|报价)(?:文件|材料)?.{0,18}?[，,](?:招标人|采购人|代理机构)?不予(?:受理|接收)|'
    r'不(?:予|再)(?:接收|接受|受理|评审)(?:其|该|递交的)?(?:投标|响应|申请|报名|应答|报价)(?:文件|材料)?|'
    r'(?:投标|应答|响应|报价)(?:文件)?(?:均|将|可|可能|予以|不予|被|会|遭到|遭){1,5}(?:拒绝|拒收|受理)|'
    r'(?:不予|拒绝)(?:接收|接受|受理)其?(?:投标|响应|应标)(?:文件|书)?|'
    r'视[为作].{0,16}放弃(?:本次|其|对应|报名|参选|投标|应标|参与|报价|比选|响应|资格|[，。；;]|$)|'
    r'视为(?:撤回|撤销)(?:其|本次)?(?:投标|响应)|(?:否决|拒绝)全部投标|'
    r'(?:投标|响应|报价)(?:文件|书).{0,25}(?:作废件处理|不予接收)')
LIST_INTRO = re.compile(r'下[列述]|以下|如[有发现]|包括但不限于|情形之一|情况之一')
QUAL_HEAD = re.compile(r'^(?:第'+NUMBER+r'[章节部分]+\s*)?(?:(?:合格|潜在|报名|应征|本次|本项目)的?)?'
                       r'(?:(?:申请人|供应商|投标人|参选人|报价人|响应人|服务商|报名单位)(?:的|应具备的|须具备的)?)?'
                       r'(?:基本|一般|特定|专用|特殊|实质性|报名)?(?:资格(?:条件|要求|标准)?|资质(?:条件|要求)|准入(?:条件|要求)|报名条件)'
                       r'(?:如下)?[：:]?$')
SCORE = re.compile(r'(?:最高|最多|每.{0,8})(?:可)?得\s*\d+\s*分|(?:本项|该项)(?:满分|评分)|加\s*\d+\s*分|扣\s*\d+\s*分|评分细项|评分说明')
ACTOR = r'(?:投标单位|投标人|供应商|响应人|报价人|应征单位|参选人|报名单位|服务商|申请人)'
TRAIT = re.compile(r'法人|法定代表人|营业执照|注册|经营范围|依法设立|资格|资质|许可证|经营许可|证书|社保|社会保障|税收|纳税|'
                   r'信誉|信用|财务|审计|资信|联合体|近.{0,4}年|重大违法|控股|管理关系|分公司|分包|开具.{0,8}发票|签约主体|技术能力|独立承担')
ACTOR_GATE = re.compile(ACTOR + r'.{0,9}(?:必须|应当|需要|须|应具|应为|应是|应提|应有|不得|需具备|需满足|未被列入|不存在重大|不存在控股)')
REFER = re.compile(r'^(?:详见|见第|符合第.{0,20}章|按.{0,12}(?:公告|须知前附表)|详参)|由招标人在资格预审中确定')
STAR_POLICY = re.compile(r'标识|标注|标记|标有|带[有“「]?|加[注“]|(?:任一|任何|所有)[“「]?★|★[”」]?号?条款')
META = re.compile(r'具体条款是否实施|否决投标的情况说明|(?:废标|否决投标)(?:情况|情形)(?:报告|汇总)|招标人.{0,15}以显著的方式标明|[投应]标文件中?是否存在.{0,15}无效|评审标准和内容见|将可能被拒绝[：:]?[（(]详见|'
                  r'不得(?:以.{0,26})?(?:认定|视为|判定|作|作为).{0,4}(?:无效|废标)|不(?:能|会|应)导致.{0,8}(?:废标|被否决)')
NOT_APPLICABLE = re.compile(r'(?:投标|响应)保证金(?:是否收取|金额)?[：: ]*(?:本项目)?(?:无[，。 0-9１２]|无$|不(?:予)?收取|无需|免收)')
OTHER_HEADING = re.compile(r'^(?:第'+NUMBER+r'(?:部分|章|节)|['+'一二三四五六七八九十'+r']+[、．.])')
CONTRACT = re.compile(r'乙方应|合同签订后.{0,12}(?:提供|提交)|采购人(?:有权|可)(?:随时)?解除合同')
VAGUE = re.compile(r'(?:法律法规|法律、法规|法律或招标文件|法律法规及招标文件).{0,20}(?:其他|其它)(?:情形|情况)|'
                   r'^(?:(?:其他|其它).{0,20}规定的(?:情形|情况)|(?:须|应)?符合.{0,18}供应商管理相关要求)[。；;]?$')


def compact(s):
    return re.sub(r'\s+', '', s or '')


def stripped(s):
    return BULLET.sub('', s.strip(), count=1).strip(' ：:')


def is_qual_head(s):
    title = compact(stripped(s))
    title = re.sub(r'^对(?=投标|供应商|响应人)', '', title)
    title = re.sub(r'[（(](?:须|需|必须)?(?:同时|全部)?满足[）)](?=[：:]?$)', '', title)
    if len(title) > 60 or re.search(r'[。；;]', title):
        return False
    return bool(QUAL_HEAD.fullmatch(title) or re.fullmatch(
        r'(?:供应商|投标人|投标单位|响应人)(?:及(?:所报|拟投|其).{1,16})?的?'
        r'(?:基本)?(?:资质|资格)(?:要求|条件)(?:如下)?[：:]?', title))


def numbered(s):
    return bool(BULLET.match(s.strip()))


def split_numbered(s):
    """Split explicit list markers, without turning alternatives in ordinary prose into requirements."""
    marks = list(LIST_MARK.finditer(s))
    if not marks or (len(marks) == 1 and marks[0].start() > 3):
        return []
    return [s[m.start():marks[i+1].start() if i+1<len(marks) else len(s)].strip(' ；;')
            for i,m in enumerate(marks)]


def reject(s):
    if META.search(s) or re.search(r'不(?:会|应|得|因此)?(?:予以)?(?:废标|否决其投标)|不作为.{0,8}(?:废标|否决)依据',s):
        return False
    return bool(REJECT.search(compact(s)) or re.search(r'逾期(?:送达|递交|提交).{0,40}不(?:予|再)(?:受理|接受|接收)',s))


def normalized_key(s):
    s = stripped(s)
    selected_no = re.fullmatch(r'是否(?:允许|接受)(?:以)?联合体(?:形式)?(?:投标|响应)?[：:]\s*[■☑]否\s*[□☐]是', compact(s))
    if selected_no:
        return '不接受联合体'
    if re.fullmatch(r'(?:本(?:项目|次(?:招标|采购)?)|本项目采购)?(?:不接受|不允许)联合体'
                    r'(?:参加|参与|响应|应答|投标|报名)?[。；;]?(?:[（(]提供.{0,10}承诺函[）)])?', compact(s)):
        return '不接受联合体'
    s = s.replace('被邀请供应商', '供应商')
    return re.sub(r'[^\w\u3400-\u9fff]+','',unicodedata.normalize('NFKC',s)).lower()


def hit(category, s, unit, related=(), qualifier=''):
    raw = s.strip(' \t\n')
    item = {'category':category, 'text':raw, 'key':normalized_key(raw), 'unit':unit}
    if related:
        item['related'] = list(related)
    if qualifier:
        item['label'] = qualifier
    return item


def established_star(unit):
    for cell in unit.cells if unit.kind=='row' else [unit.text]:
        if '★' in cell and STAR_POLICY.search(cell) and reject(cell):
            return True
    return False


# Explicit project settings take precedence over conditional examples in the standard terms.
PROJECT_SETTINGS = (
    ('投标保证金', NOT_APPLICABLE),
    ('履约保证金', re.compile(r'履约保证金.{0,14}(?:不要求|免收|不收取|无需|不适用)')),
    ('最高限价', re.compile(r'不(?:设置?|设定)最高(?:投标|报价|响应)?限价|最高(?:投标|报价)?限价[：:]?(?:无|不设置)$')),
    ('资格预审', re.compile(r'资格审查方式[｜:：]*资格后审')),
    ('联合体', re.compile(r'(?:本项目|本次招标|本次采购)不(?:允许|接受)联合体|是否(?:允许|接受)(?:以)?联合体(?:形式)?(?:投标|响应)?[：:｜]*(?:否|不接受|不允许)')),
)


def table_flags(units):
    star, disabled = {}, {}
    for u in units:
        if established_star(u):
            star.setdefault((u.part, u.scope),u)
        if getattr(u,'selection_uncertain',False):
            continue
        for topic, pattern in PROJECT_SETTINGS:
            if (u.kind=='row' or topic=='联合体') and pattern.search(compact(u.text)):
                disabled.setdefault((u.part,u.scope),{})[topic] = u
    return star,disabled


def applicable(candidate, disabled):
    u=candidate['unit']
    conditions={**disabled.get((u.part,''),{}), **disabled.get((u.part,u.scope),{})}
    item=candidate['text']
    text=item+' '+candidate.get('label','')
    if '投标保证金' in conditions and re.search(r'投标保证金|响应保证金|(?<!履约)保证金瑕疵',text):
        return False
    if '履约保证金' in conditions and '履约保证金' in text:
        return False
    if '最高限价' in conditions and re.search(r'最高(?:投标|报价|响应)?限价',text):
        # A combined “below cost OR above the ceiling” still binds as to the cost floor.
        active=re.search(r'(?:投标|响应)?报价低于(?:其|企业|投标人)?成本(?=或)',item)
        if not active:
            return False
        candidate.update(text=active[0], key=normalized_key(active[0]), label='低于成本的报价')
        candidate.setdefault('related',[]).append(conditions['最高限价'])
    if '资格预审' in conditions and re.search(r'未通过资格预审',text):
        return False
    if '联合体' in conditions and re.search(r'(?:允许|接受)联合体.{0,12}的[，,].{0,14}联合体',text):
        return False
    return True


def list_style(s):
    s = re.sub(r'^(\d+)\s+([、.．)）])', r'\1\2', s.strip())
    dot = re.match(r'^(\d+(?:\.\d+)+)',s)
    if dot: return ('dotted', dot[1].rsplit('.',1)[0])
    if s.startswith(('（','(')):return ('parenthesis',)
    if re.match('[①-⑳]',s):return ('circled',)
    if re.match(r'^\d+([、.．)）])',s):return ('number',re.match(r'^\d+([、.．)）])',s)[1])
    return None


def grounds(units, stars):
    # Consequences can be in the previous paragraph, or in a different column of the same row.
    parent = None
    subheading = ''
    style = None
    last_idx = -1
    for u in units:
        vals = [v.strip() for v in u.cells if v.strip()] if u.kind=='row' else [u.text.strip()]
        if not vals:
            continue
        s = vals[-1]
        if not s:
            continue
        footprint = (u.part,u.scope)
        rule = stars.get(footprint) or stars.get((u.part,''))
        starred = next((v for v in vals if re.match(r'^\s*★\s*\S',v)), '')
        if rule and starred and not established_star(u):
            if not reject(s):
                yield hit('redline',u.text,u,[rule],'★条款不满足时可能触发原件规定的否决后果')
        if parent and (parent.part != u.part or parent.scope != u.scope or u.kind!='paragraph' or u.index-last_idx > 5):
            parent = None
        direct = reject(s)
        context = next((v for v in vals[:-1] if len(v)<90 and
                        (LIST_INTRO.search(v) and reject(v) or v.strip()=='否决响应')), '')
        if not direct and not context and len(vals)>1:
            # The last column may be points or a remark, not the actionable rule.
            s = next((v for v in reversed(vals[:-1]) if reject(v)),s)
            direct = reject(s)
        intro = bool(re.search(r'[:：][\s）)]*$',s) and direct and LIST_INTRO.search(s))
        if intro:
            before = re.split(r'(?<=[。；;])',s)
            for clause in before[:-1]:
                if reject(clause) and not VAGUE.search(clause):
                    yield hit('redline',clause,u)
            parent=u; last_idx=u.index; subheading=''; style=None
            continue
        if direct and len(compact(stripped(s))) < 35 and re.fullmatch(
                r'(?:取消.{0,24}资格|(?:废标|否决投标|无效投标)(?:情形|情况|条件|规定|条款)?)', compact(stripped(s))):
            # A consequence-only heading is not itself a rejection trigger.
            # Its following introduction supplies the consequence for the list.
            continue
        if parent:
            if (len(stripped(s)) <= 24 and re.search(r'瑕疵|(?:资格性|符合性|身份)检查|身份审查|^违规行为$',stripped(s))):
                last_idx=u.index;subheading=stripped(s);continue
            current_style = list_style(s)
            if ((numbered(s) and (style is None or current_style == style)) or
                    s.startswith(('包括但不限于','包括：','包括:'))):
                if current_style: style = current_style
                last_idx=u.index
                for item in split_numbered(s) or [s]:
                    mode = '原件列为可能否决情形' if re.search(r'可能|有权',parent.text) else '原件列为否决情形'
                    yield hit('redline',item,u,[parent],mode+(' · '+subheading if subheading else ''))
                continue
            parent=None
        if not direct and not context:
            continue
        inline=split_numbered(s)
        if inline and (context or (reject(s[:s.find(inline[0])]) and LIST_INTRO.search(s[:s.find(inline[0])]))):
            for item in inline:
                # A following decimal-numbered clause in the same cell is a peer, not part of its last bullet.
                clauses=re.split(r'\n(?=\d+(?:\.\d+)+\s*)',item)
                if not VAGUE.search(clauses[0]):
                    yield hit('redline',clauses[0],u,qualifier=context or '原文列明的否决情形')
                for clause in clauses[1:]:
                    if reject(clause) and not VAGUE.search(clause):
                        yield hit('redline',clause,u)
            continue
        if context and not direct and not REFER.search(s) and not VAGUE.search(s):
            yield hit('redline',s,u,qualifier=context)
            continue
        # End punctuation, never a decimal point, separates distinct operative sentences.
        sentences = re.split(r'(?<=[。！？])',s)
        for pos,sentence in enumerate(sentences):
            if not reject(sentence) or VAGUE.search(sentence):
                continue
            # A star legend is evidence for the concrete marked clauses, not a separate defect.
            if established_star(u) and '★' in sentence:
                continue
            if pos and re.match(r'^(?:采购人|招标人|评标委员会)(?:有权|可以)?将其',sentence):
                sentence=sentences[pos-1]+sentence
            yield hit('redline',sentence,u)


OUTSIDE_GATE = re.compile(ACTOR + r'(?:的)?(?:应当|应|必须|须|需|不得|不应|不能|可以|可|是)?(?:为|系|具有|具备|成立|拥有|持有|取得|注册|未被列入|不存在|具有依法缴纳|具有独立|被列入|应为|应具有|不得存在).{0,36}(?:法人|经营范围|独立承担|营业执照|资质|许可|财务会计|依法缴纳|违法|信用|失信|项目案例|实施案例|履约经验|财务状况|资信|资质等级|社保)')
GATE_TITLES = re.compile(r'资格预审方法|资格审查方法|文件的(?:获取|递交|编制)|技术要求|递交方式|技术规范|发布媒介|项目概述|报名方式|技术要求|招标文件的获取|获取方式|签订合同')
EXAMPLE_NOTE = re.compile(r'（(?:案例[：:]|公司介绍[-－]|合同[：:]).*$')


def qualifies(s, under_heading):
    if not s or len(stripped(s))<6 or SCORE.search(s) or REFER.search(stripped(s)) or VAGUE.search(stripped(s)):
        return False
    if re.search(r'^(?:我(?:方|司|公司|单位)|本公司|乙方|甲方|招标人|采购人|评标委员会|采购小组|入围候选|中标人)',stripped(s)):
        return False
    if re.search(r'示例|仅供参考|可(?:选择|选填)|非必(?:选|须)|若有|如有需要|不作为.{0,10}(?:资格|准入)',s):
        return False
    if re.search(r'(?:资格预审|资格后审)[：:]?是指|资格审查(?:标准和内容|方法|采用)|针对.{0,20}资格要求的证明材料|'
                 r'投标人(?:应|须)提交证明其有资格参加|在响应.{0,16}前提下自愿参加',s):
        return False
    if re.search(r'(?:若|如|仅当).{0,140}(?:签署人|签字人).{0,15}(?:可为|可以|可由)', s):
        return False
    # A mandatory heading can also contain performance obligations. Those belong
    # to commercial/technical responses, not supplier admission qualifications.
    if re.search(r'(?:小时|分钟|工作日|天)内.{0,10}(?:响应|到场|修复|交付)|'
                 r'(?:服务|报修)响应(?:时间|时限)|不得.{0,20}(?:分包|转包)', s):
        return False
    if re.search(r'不(?:接受|允许)联合体.{0,5}(?:投标|参加|响应|报名)|不得(?:同时|在同一次项目中)?参加同一',s):
        return True
    if re.search(ACTOR+r'不得.{0,85}(?:咨询公司|咨询机构|编制单位|设计单位|代理机构|采购人|招标人).{0,28}(?:有.{0,3}关联|存在.{0,5}关系)',s):
        return True
    if re.search(r'只有.{0,48}(?:独立|资质|许可|法人).{0,32}的'+ACTOR+r'才(?:能|可)参加(?:投标|响应|报名)',s):
        return True
    if under_heading:
        return bool(TRAIT.search(s) or ACTOR_GATE.search(s) or re.search(r'(?:报名|资质|资格|投标|供应商|应具备|须具备|须具有|必须)',s))
    # An explicit supplier instruction to supply an admission certificate is
    # still a qualification requirement outside a labelled qualification area.
    # Keep this narrow: generic plans, deliverables and implementation material
    # do not establish an admission gate.
    if re.search(ACTOR+r'(?:应当|必须|须|需|应)\s*(?:提供|提交|出具|上传)\s*'
                 r'(?:有效(?:的)?|合法(?:有效)?的?|本单位的?|其|加盖公章的)?\s*'
                 r'(?:营业执照|法人证书|事业单位法人证书|社会团体法人登记证书|'
                 r'经营许可证|资质证书|资格证书)', s):
        return True
    return bool(OUTSIDE_GATE.search(s) and not CONTRACT.search(s))


PROOF = re.compile(r'^(?:[^，,。；;]{1,20}(?:系指|是指|包括以下|包含以下)|(?:注|说明|释义|证明材料|证明方式)[：:]|'
                  r'(?:(?:投标人|供应商|申请人)(?:应当|必须|须|需|应)|(?:应当|必须|须|需)?)'
                  r'(?:提供|提交|附上|出具|上传)(?:相关|相应|上述)?(?:证明|材料|合同|截图|查询|复印件|财务|证书|营业执照)|'
                  r'(?:查询(?:截图|时间|期限|期间|地址|网址|网站)|证明材料(?:包括|应|须)))')
SOURCE_URL = re.compile(r'^.{0,30}[：:]\s*https?://\S+\s*$',re.I)


def supporting(units, start, base):
    """Definitions and proof instructions immediately below a criterion share its count."""
    refs=[]
    definition=False
    for pos in range(start,len(units)):
        u=units[pos]
        if u.kind!='paragraph' or u.part!=base.part or u.scope!=base.scope:
            break
        s=u.text.strip()
        starts_definition=bool(re.match(r'^.{1,20}(?:包括以下|包含以下|是指)[^。；;]*[：:]$',s))
        if numbered(s) or (getattr(u,'is_heading',False) and not starts_definition):
            break
        if not definition and not PROOF.match(s) and not SOURCE_URL.match(s):
            break
        if definition and (len(refs)>6 or re.search(r'^(?:投标人|供应商|采购人).{0,5}(?:应|须|不得)',s)):
            break
        refs.append(u)
        definition=definition or starts_definition
    return refs


def eligibility(units):
    header=None
    form_group=None
    form_style=None
    table_qual=None
    proof_units=set()
    for idx,u in enumerate(units):
        if id(u) in proof_units:
            continue
        if u.kind=='row':
            cells = [v.strip() for v in u.cells]
            if not cells:
                continue
            if '资格评审标准' in cells or '资格审查标准' in cells:
                table_qual=(u.part,u.index)
            if any(v in ('形式评审标准','响应性评审标准','符合性评审标准','否决响应') for v in cells):
                table_qual=None
            # A project-specific data sheet can encode "是否接受联合体：否" in a single cell.
            for cell in cells:
                for line in cell.splitlines():
                    if re.match(r'(?:是否(?:允许|接受)(?:以)?联合体(?:形式)?(?:投标|响应|参与)?|联合体(?:参与|投标|响应))[：:]',line) and re.search(r'否|不接受|不允许',line):
                        yield hit('qualification',line,u)
            if any(v in ('联合体响应','联合体投标','联合体报价','是否接受联合体') for v in cells[:-1]) and cells[-1] in ('否','不接受','不允许'):
                yield hit('qualification',u.text,u)
            # A buyer's schedule may put the entire entry gate in the value
            # cell. Only explicit qualification field names establish this
            # context; a bidder's yes/no investigation form does not.
            for j, cell in enumerate(cells[:-1]):
                field=re.sub(r'[（(][^）)]*[）)]', '', cell).strip(' ：:')
                if not re.fullmatch(ACTOR+r'(?:的)?(?:资格|资质)(?:条件|要求)?', field):
                    continue
                detail=' '.join(cells[j+1:]).strip()
                if detail and detail not in ('是','否','是/否','是否') and not REFER.search(detail):
                    for clause in split_numbered(detail) or detail.splitlines():
                        if len(stripped(clause)) > 5:
                            yield hit('qualification',clause,u,qualifier=field)
            for j,cell in enumerate(cells):
                if re.search(r'(?:供应商|投标人)(?:的|应具备的)?(?:资质要求|资格条件|资格要求)[：:]',cell):
                    detail=re.split(r'(?:资质要求|资格条件|资格要求)[：:]',cell,maxsplit=1)[-1].strip() or ' '.join(cells[j+1:])
                    if detail and not REFER.search(detail):
                        for item in split_numbered(detail) or [detail]:
                            if len(stripped(item))>4:yield hit('qualification',item,u)
            if table_qual==(u.part,u.index):
                detail=cells[-1]
                if len(detail)>5 and not REFER.search(detail):
                    yield hit('qualification',u.text,u)
            continue
        raw=u.text.strip()
        s=stripped(raw)
        if re.fullmatch(r'(?:资格|资质)(?:部分|证明(?:文件|材料))[：:]?(?:[（(].*[）)])?',s):
            form_group=u
            form_style=None
            continue
        if form_group:
            current_style=list_style(raw)
            if (not numbered(raw) or u.part!=form_group.part or u.scope!=form_group.scope
                    or current_style is not None and current_style[0] == 'dotted' and current_style == list_style(form_group.text)
                    or form_style is not None and current_style != form_style):
                form_group=None
            else:
                form_style=current_style
                if re.search(r'(?:本项目)?(?:无需|无须|不需|不须|不要求)提供|不适用',raw):
                    continue
                conditional=re.search(r'关联|非必选|其他项目可选|(?:如|若|仅当).{0,25}(?:为|是|属于|采用)|适用时',raw)
                yield hit('qualification',raw,u,[form_group],'条件适用时提交' if conditional else '资格证明材料')
                continue
        if is_qual_head(raw):
            header=u
            continue
        if header and re.fullmatch(r'(?:其他|其它)?(?:必须|应|须|需)(?:同时)?满足的?(?:要求|条件)[：:]?', s):
            continue
        if header and (u.part!=header.part or u.scope!=header.scope or OTHER_HEADING.match(raw)
                       or (getattr(u,'is_heading',False) and GATE_TITLES.search(s))):
            header=None
        if re.search(ACTOR+r'.{0,5}(?:需|必须|须|应)满足(?:以下|如下)(?:条件|要求)[：:]',s) and not SCORE.search(s):
            details=re.split(r'满足(?:以下|如下)(?:条件|要求)[：:]',s,1)[1].strip()
            if details:
                # Preserve grouped conditions where the source uses an alternative such as “或”.
                items=re.split(r'[、]|及(?=.{1,10}(?:能力|资源|经验)[。；;]?$)',details) if '、' in details and not re.search('或|至少.{0,5}项',details) else [details]
                for item in items:
                    yield hit('qualification',item.strip(' 。；;'),u,qualifier='原文明确的供应商条件')
            continue
        if '资质证书' in s and re.search('必备(?:材料|内容)|必须具备|须持有',s) and not SCORE.search(s):
            yield hit('qualification',raw,u)
            continue
        if s.startswith(('违法违规记录是指','注：','说明：')) or is_qual_head(s) or '://' in s and '网址' in s:
            continue
        if re.search(r'被列入|被.{1,15}网站列入',s) and re.search(r'不得参(?:与|加)本项目',s) and not SCORE.search(s):
            yield hit('qualification',raw,u)
            continue
        if qualifies(raw,header is not None):
            normalized=EXAMPLE_NOTE.sub('',raw).rstrip('；')
            proof=supporting(units,idx+1,u)
            proof_units.update(map(id,proof))
            candidate=hit('qualification',normalized,u,proof)
            if re.search('单位负责人为同一人|同一法定代表人',s) and re.search('控股|管理关系',s) and re.search('不得.{0,12}参加',s):
                candidate['key']='共用负责人及控股管理关系限制'
            yield candidate


def extract(units):
    """Yield evidence-backed candidates. The caller groups identical keys within their applicable scope."""
    stars, disabled=table_flags(units)
    positions = {id(unit): pos for pos, unit in enumerate(units)}
    for candidate in grounds(units,stars):
        # Word exports can split the last few characters of a sentence into
        # another paragraph (e.g. 废标处 / 理。). Rejoin only a short adjacent
        # fragment with the same source context, keeping both original locators.
        unit = candidate['unit']
        pos = positions[id(unit)]
        if unit.kind == 'paragraph' and pos + 1 < len(units):
            following = units[pos + 1]
            fragment = following.text.strip()
            if (following.kind == 'paragraph' and following.part == unit.part
                    and following.scope == unit.scope and following.context == unit.context
                    and 0 < following.index - unit.index <= 5 and 1 <= len(compact(fragment)) <= 6
                    and not following.is_heading and not numbered(fragment)
                    and not re.search(r'[。；;！？!?：:]$', candidate['text'].rstrip())
                    and re.fullmatch(r'[\u3400-\u9fff\s。；;！？!?]+', fragment)):
                candidate['text'] = candidate['text'].rstrip() + fragment
                candidate['key'] = normalized_key(candidate['text'])
                candidate.setdefault('related', []).append(following)
        if applicable(candidate,disabled):
            yield candidate
    yield from eligibility(units)
