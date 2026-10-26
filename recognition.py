"""Find material requirements and fill corresponding entries in the original document."""
from __future__ import annotations

import copy
import hashlib
import re

from docx.oxml.ns import qn

FAMILIES = {'01': 'case', '02': 'people', '03': 'qualification', '04': 'resource'}
_NUMBER = r'[一二三四五六七八九十百零〇\d]+'
_ATTACHMENT = re.compile(r'^[（(]?(?:附件|附表|格式)\s*' + _NUMBER + r'(?:[）)\s:：、.．-]|$)')
_REQUEST = re.compile(r'提供|提交|出具|附[上后：:]?|后附|须附|应附|需附|复印|扫描|电子件|影印|粘贴|填写|填报|列举|列明|列出|说明|介绍|展示|证明|证书|简历|一览表|汇总表')
_IMAGE = re.compile(r'复印件|扫描件|影印件|电子件|正反面|双面|照片|粘贴|证书|合同.{0,16}(?:证明|关键页)|证明材料')
_COPY = re.compile(r'后附|附[：:]|附上|须附|应附|需附|粘贴|复印件|扫描件|影印件|电子件')
_SIGNATURE = re.compile(r'^(?:投标人|供应商|法定代表人|委托代理人|日期|年月日|年\s*月\s*日)')
_AGENT = r'(?:委托代理人|被授权人|授权代表|代理人|受托人)'
_AGENT_ID = re.compile(_AGENT + r'.{0,18}(?:身份证|护照)|(?:身份证|护照).{0,12}' + _AGENT)


def agent_identity_target(title, attachment):
    """Authorization images are agent ID copies; the letter itself takes a text template."""
    value = compact(title)
    return bool(_AGENT_ID.search(value) and (attachment or not re.search(r'授权(?:委托)?书', value)))


def family(category):
    code = category.get('code', category['id'])
    return FAMILIES.get(code[1:], code) if re.fullmatch(r'[IFBW]0[1-4]', code) else code


def compact(text):
    return re.sub(r'\s+', '', text or '')


def region_title(title):
    import engine
    value = engine._normal(title)
    if len(value) > 65 or re.search(r'[。；;]', value):
        return ''
    if value=='附件' and re.match(r'^第' + _NUMBER + r'章', compact(title)):
        return 'form'
    if re.fullmatch(r'(?:采购|谈判|竞争性谈判|磋商)?(?:投标|响应|应答|比选|报价)(?:文件)?(?:的|组成及|编制)?(?:格式|格式及附件|格式与附件|格式文件)', value):
        return 'form'
    if re.fullmatch(r'.{0,12}(?:评标办法|评审办法|评分标准|评审标准|评分细则|评审细则|评审内容及标准|评审内容及评分标准)', value):
        return 'scoring'
    if re.fullmatch(r'(?:合同|协议)(?:草案)?(?:条款(?:及格式|与格式)?|格式|范本|文本|书)?', value):
        return 'contract'
    if re.fullmatch(r'(?:投标|响应|应答|报价)文件(?:的)?(?:编制|组成|递交|编写要求)',value):
        return 'instructions'
    if re.fullmatch(r'.{0,8}(?:招标公告|采购公告|采购邀请|招标邀请|投标邀请|投标人须知(?:及?前附表)?|供应商须知(?:及?前附表)?|采购需求(?:书)?|技术规范书|技术要求|项目需求|资质要求|资格条件)', value):
        return 'instructions'
    return ''


def structural(title, styled=False):
    """Attachment and subform boundaries survive lost Word heading styles."""
    value = compact(title)
    if not value or len(value) > 100:
        return False
    if region_title(title) or _ATTACHMENT.match(value):
        return True
    if re.match(r'^第' + _NUMBER + r'(?:章|节|部分|篇)', value):
        return True
    if len(value) <= 65 and re.match(r'^[（(][一二三四五六七八九十]+[）)]', value) and not re.search(r'[。；;]', value):
        return True
    if re.search(r'[。；;，,]', value) or _SIGNATURE.match(value):
        return False
    # Numbered explanatory sentences stay attached to their own form.
    if re.search(r'提供|提交|须|应当|不得|按照|根据|供应商需|投标人需|加盖|填写|说明如下', value):
        return False
    return bool(styled or (len(value) <= 60 and re.match(
        r'^(?:[（(]' + _NUMBER + r'[）)]|' + _NUMBER + r'[、.．]|\d+(?:[-－.．]\d+)+[、.．:：]?)', value)))


def _matches(text, key):
    """Nouns plus submission intent; exclude non-bidder and non-document uses."""
    v = compact(text)
    if not v:
        return False
    # A negated submission is evidence of exemption, not a positive request.
    if re.search(r'(?:无需|不需|不要求|不必|免交|无须)(?:再|另行)?(?:提供|提交|出具|附)?', v):
        return False
    if key == 'business_license':
        return ('营业执照' in v and not re.search(r'会计师事务所|审计机构|营业执照号|具有营业执照的分支机构|分支机构[（(]具有营业执照', v)
                and (bool(_REQUEST.search(v)) or bool(re.fullmatch(r'.{0,8}营业执照(?:正本|副本)?[；;。]?', v))))
    if key == 'legal_identity':
        return bool(re.search(r'(?:法定代表人|法人代表|法人|单位负责人)(?:[（(].{0,12}[）)])?.{0,18}?(?:身份证(?:明)?|证明书|护照)', v))
    if key == 'authorization':
        if re.search(r'(?:平台|媒体|厂商|制造商|品牌|渠道).{0,12}授权', v) and not re.search(r'法定代表|委托代理人|被授权人', v):
            return False
        return bool(re.search(r'(?:法定代表人|法人代表|法人|负责人)授权(?:委托)?书|授权委托书|委托代理人.{0,18}身份证|(?:被)?授权人.{0,18}身份证', v)
                    or re.fullmatch(r'(?:\d+[.、])?授权书[：:；;]?', v))
    if key == 'audit_report':
        return bool(re.search(r'审计报告|财务(?:会计)?(?:报告|报表)|资产负债表|现金流量表|利润表', v) and
                    (_REQUEST.search(v) or re.fullmatch(r'(?:[（(]?\d+[）).、])?(?:近?[一二三\d-]+年(?:度)?)?(?:财务|财务会计)(?:报告|报表)',v)))
    if key == 'tax_social':
        personal = re.search(r'(?:人员|个人|成员|团队|项目负责人|项目经理|员工).{0,65}(?:社保|社会保险)', v)
        corporate = re.search(r'缴纳税收|税收缴纳|依法缴纳税|完税凭证|纳税证明', v)
        return bool(re.search(r'(?:缴纳|缴费|完税|社会保险权益记录).{0,40}(?:税|社保|社会保障|社会保险|证明|凭证)|(?:税收|税款|纳税).{0,12}(?:缴纳|缴费|完税|凭证)|(?:社保|社会保险|社会保障资金).{0,20}(?:缴纳|缴费|证明|凭证)|纳税证明', v)
                    and (not personal or corporate) and not re.search(r'纳税信用|税务登记证|一般纳税人', v) and _REQUEST.search(v))
    if key == 'company_profile':
        return bool(re.search(r'(?:公司|企业|供应商|投标人)(?:基本)?(?:情况表|情况介绍|信息表|介绍|简介|概况)|贵公司.{0,12}基本情况|(?:公司|企业|供应商|投标人)基本(?:情况|信息)', v))
    if key == 'iso_cert':
        return bool(re.search(r'ISO\s*[-/\w]*|质量管理体系|环境管理体系|信息安全管理体系|职业健康安全管理体系', v, re.I)
                    and re.search(r'认证|证书|证明', v) and not re.search(r'优先采购|采购产品|节能产品', v))
    if key == 'case':
        return bool((re.search(r'(?:同类|类似|历史|过往|以往|成功|典型|合作|服务|项目|执行|企业|公司|客户).{0,10}(?:业绩|案例|经验)|业绩(?:情况|汇总|一览|证明)|案例(?:介绍|展示|证明|汇总)', v)
                     or (re.search(r'提供|后附|提交|附[：:]', v) and re.search(r'合同.{0,25}(?:复印|扫描|证明|关键页|收尾页|首尾页)|服务合同|框架合同', v)))
                    and not re.search(r'合同履约担保|违约处罚|不良业绩', v))
    if key == 'people':
        v = re.sub(r'法定代表人[（(]单位负责人[）)]|法定代表人|委托代理人|被授权人', '', v)
        v = re.sub(r'[，,。；;]', '\n', v)
        if re.search(r'参加采购人员|中选资格|取消.{0,10}资格',v):
            return False
        return bool(re.search(r'(?:拟派|拟投入|服务|项目|核心|主要|业务|全职)?(?:团队|人员|负责人|项目经理|成员).{0,35}(?:简历|履历|经验|经历|学历|职称|资质|资格|证书|证明|社保|社会保险|配置|配备|组成|构成|介绍|名单|情况|人数)|简历表|人员配置表', v)
                    and not re.search(r'^法定代表人|^委托代理人|^被授权人', v))
    if key == 'qualification':
        return bool(re.search(r'(?:广播电视节目制作经营|增值电信业务经营|互联网新闻信息服务|广告经营)许可证|(?:行业专项|专项业务|专业经营)(?:资质|资格)(?:证书|证明)', v))
    if key == 'resource':
        if re.search(r'领购|下载招标文件|CA证书|投标专用章',v):
            return False
        return bool(re.search(r'(?:平台|媒体|媒介|渠道|厂商|制造商|品牌|合作伙伴).{0,20}(?:代理资质|代理资格|授权|合作协议|合作证明)|(?:媒体|媒介|平台|渠道|合作)资源.{0,20}(?:情况|介绍|优势|清单|证明|授权|储备)', v)
                    and not re.search(r'投标平台|电子采购平台|交易平台', v))
    return False


def title_topics(title):
    """Only nominal material/form headings, not a nearby paragraph mentioning it."""
    import engine
    v = engine._normal(title)
    v = re.sub(r'^[（(]?(?:附件|附表|格式)' + _NUMBER + r'[）):：、.．-]*', '', v)
    v = re.sub(r'[（(](?:格式自拟|自行编制|可自拟)[）)]', '', v).rstrip('；;')
    if len(v)>65 or re.search(r'[{}。；;，,]|提供|提交|应当|不得|按照|根据|后附|详见|填写|签字|盖章',v):
        return set()
    keys = {k for k in [c['id'] for c in engine.DEFAULT_CATEGORIES] + list(FAMILIES.values()) if _matches(v,k)}
    if re.fullmatch(r'(?:公司|企业|供应商|投标人)?财务(?:状况|情况|信息|证明)(?:及.*)?',v):keys.add('audit_report')
    if re.fullmatch(r'(?:拟派|拟投入|服务|项目)?(?:人员|团队)(?:信息|情况|配置|组成|简历)?',v):keys.add('people')
    if re.fullmatch(r'(?:行业|同类|类似|项目|服务|合作)(?:经验|案例|业绩)(?:情况|介绍|展示)?',v):keys.add('case')
    if re.fullmatch(r'(?:企业|公司|供应商|投标人)(?:基本)?(?:情况|信息)(?:表|介绍)?',v):keys.add('company_profile')
    return keys


def entry_score(title, category):
    """Match a material entry by meaning, without requiring a formal response area.

    Normalize presentation words, not arbitrary edit distance: an auditor's
    license, a credit rating and a person's social insurance are different items.
    """
    import engine
    raw = compact(title)
    if not raw or len(raw)>100 or re.search(r'[{}]|无需|不需|无须|免交|不要求|会计师事务所|审计机构|营业执照号|信用|税务登记|一般纳税人|采购产品|节能产品|指具有|是指',raw):
        return 0
    value = re.sub(r'^[•●◆◇★☆*·\-—]+', '', raw)
    value = engine._normal(value, remove_years=True)
    value = re.sub(r'^(?:附件|附表|格式)' + _NUMBER + r'[）):：、.．-]*', '', value)
    value = re.sub(r'[（(【\[].*?[）)】\]]', '', value)
    value = value.rstrip('：:；;。')
    value = re.sub(r'^(?:(?:投标人|投标方|供应商)(?:应|须|需)?|请|须|需|应)?(?:提供|提交|附上|后附)', '', value)
    value = re.sub(r'(?:复印件|扫描件|影印件|电子件|电子版|复印本|副本|正本|附件|正反面|双面|彩色|原件|加盖公章|盖章版)', '', value)
    if not value or re.search(r'[，,。；;：:]|提供|提交|包括|应当|不得|按照|根据|详见|是否|具有|填写|须|需',value):
        return 0
    key = family(category)
    if category.get('layer')=='special':
        industry = {'insurance':'保险','fund':'基金','bank':'银行','wealth':'理财'}.get(category.get('industry_id'))
        for label in ('保险','基金','银行','理财'):
            if value.startswith(label):
                if industry and industry!=label:
                    return 0
                value=value[len(label):]
                break
    variants = {
        'business_license': r'(?:(?:投标人|投标方|供应商|企业法人|企业|公司|单位)的?)?(?:营业执照|工商营业执照|企业执照)',
        'legal_identity': r'(?:法定代表人|法人代表|法人|单位负责人)(?:身份证(?:明书?)?|证明书|护照)',
        'authorization': r'(?:(?:法定代表人|法人代表|法人|单位负责人)的?)?授权(?:委托)?书|(?:委托代理人|被授权人|授权代表)(?:身份证(?:明)?|护照)',
        'audit_report': r'(?:近?[一二三\d-]+年)?(?:公司|企业|年度)?(?:财务(?:会计)?审计报告|年度审计报告|审计报告|财务(?:会计)?(?:报告|报表))',
        'tax_social': r'(?:依法)?(?:(?:税收|纳税|税款)(?:和|及|与)?(?:社会保障资金|社会保险|社保)?|社会保障资金|社会保险|社保)(?:缴纳|缴费)?(?:证明(?:材料)?|凭证|记录)|完税(?:证明|凭证)',
        'company_profile': r'(?:公司|企业|单位|供应商|投标人)(?:基本)?(?:简介|介绍|概况|情况介绍|情况表|信息表|情况)',
        'iso_cert': r'(?:iso[\w/-]*|质量管理体系|环境管理体系|信息安全管理体系|职业健康安全管理体系)(?:认证)?(?:资质)?(?:证书|证明)',
        'case': r'(?:以往|既往|过往|历史|同类|类似|项目|服务|成功|典型|执行|企业|公司|客户)*(?:项目)?(?:业绩|案例|经验)(?:情况|实施经验|说明|介绍|展示|罗列|清单|汇总表|一览表|证明材料|证明|表)?',
        'people': r'(?:拟派|拟投入)?(?:项目|服务|核心|主要)?(?:人员|团队|团队成员|项目负责人|项目经理)(?:简历表?|履历|介绍|配置|名单|情况表|资质证书|资格证书|经历与专业证明|经历证明)',
        'qualification': r'(?:广播电视节目制作经营|增值电信业务经营|互联网新闻信息服务|广告经营)许可证|(?:行业)?(?:专项|专业|业务)(?:资质|资格)(?:证书|证明|证明材料)',
        'resource': r'(?:平台|媒体|媒介|渠道|厂商|制造商|品牌|业务|合作)(?:资源)?(?:代理|合作)?(?:授权(?:书|证明|证明材料|文件)?|合作协议|合作证明)|(?:媒体|媒介|平台|渠道)(?:资源)(?:介绍|清单|证明|情况|优势)',
    }
    if key in variants and re.fullmatch(variants[key],value,re.I):
        return 100
    # User-defined categories still recognize configured aliases with decoration.
    aliases=[engine._normal(n,True) for n in [category['name'],*category.get('aliases',[])]]
    return 110 if value in aliases else 0


def _requirement(record, category, alias=False):
    import engine
    if record.get('_region') == 'contract':
        return None
    key = family(category)
    text = record['title']
    if not alias and not _matches(text, key):
        return None
    v = compact(text)
    if re.search(r'(?:无需|不需|不要求|不必|免交|无须)(?:再|另行)?(?:提供|提交|出具|附)?',v):
        return None
    if re.search(r'甲方|乙方',v) and re.search(r'支付|税款|违约|交货|质保',v):
        return None
    if category.get('layer')=='special' and not alias:
        if not _REQUEST.search(v) and not record.get('_title_topics'):
            return None
        if key=='people' and not re.search(r'简历|履历|学历|职称|证书|社保|社会保险|名单|一览表|汇总表|(?:人员|团队|成员).{0,15}(?:介绍|证明)', re.sub(r'[，,。；;]', '\n',v)):
            return None
    if key == 'tax_social' and re.search(r'人员|员工|团队|项目负责人|项目经理|简历', record.get('_section_title','')) and not re.search(r'税收|纳税|完税|税款', v):
        return None
    # Definitions, penalty clauses and signature labels are not submission tasks.
    if re.search(r'指具有|是指|定义为|原件备查|签字或盖章|授权代表签字', v) and not _COPY.search(v):
        return None
    status = 'required'
    if record.get('_region') == 'scoring' or re.search(r'得\d+(?:\.\d+)?分|不得分|评分|加分|分值', v):
        status = 'optional'
    if re.search(r'如有|若有|如需要|如有需要|如需|另行要求|如法定代表人|委托代理人.{0,8}参加', v):
        status = 'conditional'
    return {'status': status, 'image_requirement': 'explicit' if _IMAGE.search(v) else 'unspecified',
            'source_locator': record['id'], 'source_path': list(record['_path']),
            'text': text[:1800], 'region': record.get('_region', '')}


def requirements(records, categories):
    import engine
    result = {}
    for category in categories:
        evidence = []
        for r in records:
            if r['kind'] == 'placeholder':
                alias = category['id'] in r['category_ids']
            else:
                alias = category['id'] in engine._category_ids(r['title'], [category]) or bool(entry_score(r['title'],category))
            item = _requirement(r, category, alias)
            if item:
                evidence.append(item)
        if not evidence:
            result[category['id']] = {'status': 'not_detected', 'evidence': [], 'required_years': [], 'year_options': []}
            continue
        statuses = {e['status'] for e in evidence}
        status = next(s for s in ('required', 'conditional', 'optional') if s in statuses)
        years, options = set(), []
        if family(category) == 'audit_report':
            for e in evidence:
                # Extract only the financial clause, not unrelated deadlines or case years.
                for clause in re.split(r'[。；;\n]', e['text']):
                    if not re.search(r'审计|财务|资产负债|现金流量|利润表', clause):
                        continue
                    y = engine._years(clause)
                    alternatives = re.search(r'20\d{2}年?度?\s*或\s*20\d{2}', clause)
                    if alternatives:
                        options.extend([[v] for v in engine._years(alternatives[0])])
                    elif y and not re.search(r'或.{0,25}财务报表', clause):
                        years.update(y)
        if years:
            options = []  # An explicit all-year requirement takes precedence over alternatives.
        result[category['id']] = {'status': status, 'evidence': evidence[:20],
            'image_requirement': 'explicit' if any(e['image_requirement']=='explicit' for e in evidence) else 'unspecified',
            'required_years': sorted(years), 'year_options': [list(x) for x in dict.fromkeys(tuple(x) for x in options)]}
    return result


def _form_table(element):
    """A response table has fillable cells or form-field labels, not scoring rows."""
    import engine
    if element.tag != qn('w:tbl'):
        return False
    text = engine._text(element)
    if re.search(r'评分|得分|分值|评审|满分', text):
        return False
    cells = list(element.iter(qn('w:tc')))
    return bool(any(not engine._text(c).strip() and not engine._drawing(c) for c in cells)
                or re.search(r'供应商名称|投标人名称|姓名年龄|合同名称|项目名称合同|营业执照号|注册资本|注册地址', compact(text)))


def _slot_record(base, category, own, title=None):
    import engine
    if family(category) in {'legal_identity','authorization'}:
        area = []
        for e in own:
            text = compact(engine._text(e))
            if e.tag == qn('w:p') and (_SIGNATURE.match(text) or re.match(r'^(?:说明|注)[：:]',text)):
                break
            area.append(e)
        own = area
    r = copy.copy(base)
    r.update(id='at:' + category['id'] + ':' + base['id'],
             title=title or base['title'], kind='attachment', category_ids=[category['id']],
             _own=list(own), _public=True, _special_scores={}, _attachment=True)
    meaningful = [e for e in own if engine._text(e).strip() or engine._drawing(e) or e.tag == qn('w:tbl')]
    r['_anchor'] = meaningful[-1] if meaningful else base['_element']
    r['existing_content'] = any(engine._drawing(e) for e in own)
    r['identity_attachment_target'] = family(category) == 'legal_identity'
    return r


def resolve(document, records, categories, toc_elements=()):
    """Prefer precise form slots, then corresponding material entries, then appendices."""
    import engine
    top = [r for r in records if len(r['_path']) == 1 and r['kind'] != 'placeholder']
    explicit_forms = any(region_title(r['title']) == 'form' for r in top)
    mode = ''
    regions = {}
    for r in top:
        region = region_title(r['title'])
        if region:
            mode = region
        elif re.match(r'^第' + _NUMBER + r'(?:章|部分|篇)',compact(r['title'])):
            mode = ''
        elif not explicit_forms and _ATTACHMENT.match(compact(r['title'])) and mode != 'contract' and not re.search(r'评分|评审|须知', r['title']):
            mode = 'form'
        regions[r['_path'][0]] = mode
    mode = ''
    section_title = ''
    for r in records:
        mode = regions.get(r['_path'][0], mode)
        r['_region'] = mode
        r['_topics'] = {family(c) for c in categories if _matches(r['title'],family(c))}
        r['_title_topics'] = title_topics(r['title'])
        r['_entry_topics'] = {family(c) for c in categories if entry_score(r['title'],c)}
        if not r['_inside_table'] and (r['_structural'] or r['_title_topics']):
            section_title = r['title']
        r['_section_title'] = section_title
    # Rebuild ownership with all structural boundaries, even non-public attachments.
    children = list(document.element.body)
    by_top = {r['_path'][0]: r for r in top}
    for r in top:
        own = []
        for i in range(r['_path'][0]+1, len(children)):
            if children[i] in toc_elements or any(p in toc_elements for p in children[i].iter(qn('w:p'))):
                break
            other = by_top.get(i)
            numbering = r'^(?:附[：:]\s*)?(\d+(?:[.．]\d+)*)(?:[.．、]|(?=[^\d.．]))'
            current_number = re.match(numbering, compact(r['title']))
            next_number = re.match(numbering, compact(other['title'])) if other else None
            next_peer = bool(current_number and next_number and
                tuple(map(int, re.split('[.．]', next_number[1]))) >
                tuple(map(int, re.split('[.．]', current_number[1]))))
            next_requirement = bool(other and r['_region']=='form' and
                re.match(r'^(?:附[：:]\s*)?\d+[.．、]', compact(other['title'])) and
                (next_peer or (other['_topics'] and not other['_topics'].intersection(r['_title_topics'] or r['_topics']))))
            if other and (other['_structural'] or other.get('_raw_categories') or
                          other['_title_topics'] or other['_entry_topics'] or next_requirement or
                          (other['_public'] and not _REQUEST.search(other['title']))):
                break
            if children[i].tag in {qn('w:p'), qn('w:tbl')}:
                own.append(children[i])
        substantive = [e for e in own if engine._text(e).strip() or engine._drawing(e) or e.tag == qn('w:tbl')]
        txt = '\n'.join(engine._text(e) for e in own)
        r.update(_own=own, _anchor=substantive[-1] if substantive else r['_element'],
                 preview=txt[:240], existing_content=bool(substantive),
                 legal_body_present=bool(re.search(r'兹证明|(?:系|为).{0,35}法定代表人', compact(txt))),
                 authorization_body_present=bool(re.search(r'本授权(?:委托)?书|本人.{0,40}系|现委托|现授权委托|兹授权', compact(txt))))
    reqs = requirements(records, categories)
    # An agent ID label after an authorization letter is that letter's attachment
    # slot, also in bid frameworks without an explicit 投标文件格式 region.
    letter = False
    for r in top:
        if r['authorization_body_present']:
            letter = True
        elif r['_structural'] or region_title(r['title']) or _ATTACHMENT.match(compact(r['title'])):
            letter = False
        r['_after_letter'] = letter and not r['authorization_body_present']
    extra = []
    for r in records:
        if r['kind'] != 'placeholder':
            r['category_ids'] = []
            r['_special_scores'] = {}
    for category in categories:
        cid, key = category['id'], family(category)
        candidates = []
        for r in records:
            if r['kind'] == 'placeholder':
                score, reason = engine._special_score(r['title'], category) if category.get('layer')=='special' else (130 if cid in r['category_ids'] else 0, '显式占位符')
                if score:
                    r['category_ids'] = list(dict.fromkeys([*r['category_ids'], cid]))
                    r['_special_scores'][cid] = (score, reason)
                continue
            if r['_region'] in {'contract','scoring','instructions'}:
                continue
            title = compact(r['title'])
            if region_title(title) or re.fullmatch(r'[（(]?(?:附件|附表|格式)\s*' + _NUMBER + r'[）)\s:：、.．-]*', title):
                continue
            aliases = cid in engine._category_ids(r['title'], [category])
            score, reason = engine._special_score(r['title'], category) if category.get('layer')=='special' else (130 if aliases else 0, '栏目名称或别名')
            form = (r['_region'] == 'form' or any(_form_table(e) for e in r['_own']) or r['legal_body_present']
                    or r['authorization_body_present'] or (key == 'authorization' and r.get('_after_letter')))
            # Table clauses identify requirements. An empty adjacent answer/score
            # cell alone does not authorize inserting images into that cell.
            if r['_inside_table']:
                continue
            if re.search(r'格式见|见附件|详见|应包括|包括以下|提交下述', title):
                continue
            request = _requirement(r, category, aliases)
            attachment = bool(request and form and _COPY.search(title))
            # Financial/general form titles can be identified from their owned request.
            contextual = (form and key in r['_title_topics'] and len(title)<=65 and (
                key=='company_profile' or any(_matches(engine._text(e), key) for e in r['_own'])))
            if request and form and key in {'audit_report','tax_social'} and re.search(r'提供|须附|应附',title):
                attachment = True
            if not score and not attachment and not contextual:
                continue
            if not form and not score:
                continue
            if not form and (re.search(r'[；;。]', title) or (not aliases and category.get('layer')!='special')):
                continue
            # Requirements can share one paragraph, but each material gets a stable
            # logical attachment target. It does not reuse the wrong role's table.
            if attachment:
                # Supplemental numbered instructions refer back to the preceding
                # picture label; they are not a second copy of the same slot.
                if key in {'legal_identity','authorization'} and re.match(r'^\d+[.、]', title) and re.search(r'应随|随本|说明|注[：:]',title):
                    continue
                owned = []
                for e in r['_own']:
                    t = engine._text(e)
                    if any(_matches(t, family(other)) for other in categories if other['id']!=cid) and _COPY.search(t):
                        break
                    owned.append(e)
                slot = _slot_record(r, category, owned)
                dedicated = bool(re.match(r'^附[：:]',title) and len(r['_topics'])==1)
                candidates.append((slot,175 if dedicated else 155,'原文明确标注的材料附件位置'))
            elif score:
                if key == 'authorization' and not r['authorization_body_present'] and form:
                    continue
                if key == 'legal_identity' and not r['legal_body_present'] and form and not re.search(r'身份证(?!明)', title):
                    continue
                if key == 'legal_identity' and re.search(r'身份证(?!明)', title):
                    r['identity_attachment_target'] = True
                candidates.append((r,score,reason))
            elif contextual:
                candidates.append((r,105,'应答栏目内明确的材料提交要求'))
        # A precise attachment label supersedes its generic parent. Keep genuinely
        # separate response sections; ambiguous special sections remain reviewable.
        precise = [(r,s,why) for r,s,why in candidates if s>=145]
        # A same-category table heading with a following attachment note already
        # owns the correct write-back point. Retain that meaningful heading.
        if key not in {'legal_identity','authorization'}:
            retained = []
            for slot,s,why in candidates:
                parents = [(p,ps,pw) for p,ps,pw in candidates if p is not slot and not p.get('_attachment')
                           and ps>=80 and slot['_element'] in p.get('_own', [])]
                if slot.get('_attachment') and parents:
                    parent = max(parents, key=lambda x:x[0]['_path'])[0]
                    parent['_attachment_note_score'] = max(s, parent.get('_attachment_note_score',0))
                    continue
                retained.append((slot,s,why))
            candidates = [(r,max(s,r.get('_attachment_note_score',0)) +
                           (15 if category.get('layer')=='special' and not r.get('_attachment') and
                            any(_form_table(e) for e in r['_own']) else 0),why) for r,s,why in retained]
            precise = [(r,s,why) for r,s,why in candidates if r.get('_attachment')]
        filtered = []
        for r,s,why in candidates:
            if key == 'authorization' and not agent_identity_target(r['title'], r.get('_attachment')):
                continue
            if key == 'tax_social':
                from material_rules import tax_kinds
                number = re.match(r'^(\d+(?:\.\d+)*)[.、]', compact(r['title']))
                subtypes = set().union(*(tax_kinds(p['title']) for p,_,_ in candidates
                    if p is not r and number and compact(p['title']).startswith(number[1] + '.')
                    and re.match(r'^' + re.escape(number[1]) + r'\.\d', compact(p['title']))))
                if number and len(tax_kinds(r['title'])) > 1 and tax_kinds(r['title']) <= subtypes:
                    continue
            if key in {'legal_identity','authorization'} and not r.get('_attachment'):
                nearby = [p for p,_,_ in precise if 0 < p['_path'][0]-r['_path'][0] < 60]
                if any(not any(
                    r['_path'][0] < middle['_path'][0] < p['_path'][0] and
                    (region_title(middle['title']) or _ATTACHMENT.match(compact(middle['title'])) or
                     (middle['_title_topics'] - {'legal_identity','authorization'}) or
                     (middle.get('legal_body_present') and 'legal_identity' in middle['_title_topics'] and
                      'authorization' not in middle['_title_topics'] and key=='authorization'))
                    for middle in top) for p in nearby):
                    continue
            if any(r is not p and r['_path'][0] <= p['_path'][0] and
                   p['_element'] in r.get('_own', []) for p,_,_ in precise):
                continue
            if r.get('_region') == 'form' and not r.get('_own') and not r.get('_attachment'):
                # Collapse a section label followed by the same form title,
                # not a distinct blank section elsewhere in the framework.
                siblings = [p for p,_,_ in candidates if p is not r and p.get('_own')
                            and p['_path'][0] == r['_path'][0] + 1
                            and engine._normal(p['title']) == engine._normal(r['title'])]
                if siblings:
                    continue
            if r.get('_attachment'):
                extra.append(r)
            if cid not in r['category_ids']:
                r['category_ids'].append(cid)
            r['_special_scores'][cid] = (s,why)
            r['_public'] = True
            r.setdefault('location_match', {'status':'matched','reason':why,'candidates':[]})
            r['requirement'] = copy.deepcopy(reqs[cid])
            filtered.append(r)
        has_token = any(cid in r['category_ids'] for r in records if r['kind']=='placeholder')
        if not filtered and not has_token:
            entries = []
            for r in records:
                score = entry_score(r['title'],category)
                if not score or r['kind']=='placeholder' or r['_region'] in {'contract','scoring'}:
                    continue
                if key == 'authorization' and not agent_identity_target(r['title'], True):
                    continue
                table = next((a for a in r['_element'].iterancestors() if a.tag==qn('w:tbl')),None)
                if table is not None and re.search(r'评分|得分|分值|评审|满分',engine._text(table)):
                    continue
                entries.append((r,score))
            if entries:
                # Prefer response context and stronger names, then the first
                # occurrence. Repeated submission lists must not duplicate images.
                entries.sort(key=lambda pair:(pair[0]['_region']!='form',-pair[1],pair[0]['_path']))
                base,score = entries[0]
                slot = _slot_record(base,category,[])
                reason = '已按材料含义匹配原文条目，将在该条目后填入材料'
                slot.update(location_match={'status':'matched','reason':reason,'candidates':[],
                                             'method':'entry','alternative_count':len(entries)-1},
                            requirement=copy.deepcopy(reqs[cid]))
                slot['_special_scores'][cid]=(score,reason)
                extra.append(slot)
                filtered.append(slot)
        # The appendix is used only when no matching entry or form slot exists.
        # Agent ID copies without a labelled slot stay missing, not appended.
        if (not filtered and not has_token and reqs[cid]['status']!='not_detected'
                and category.get('layer','common')=='common' and key != 'authorization'):
            digest = hashlib.sha256(document.element.body.xml.encode()).hexdigest()[:16]
            name = '法定代表人身份证附件（证明正文另行核对）' if cid=='legal_identity' else category['name']
            extra.append({'id':f'ca:{cid}:{digest}','title':'文末新增材料区 · '+name,
                'level':2,'kind':'common_appendix','preview':'原文已识别材料要求，未找到可靠的独立图片位置。',
                'category_ids':[cid],'existing_content':False,'legal_body_present':False,'authorization_body_present':False,
                'identity_attachment_target':cid=='legal_identity','requirement':copy.deepcopy(reqs[cid]),
                'location_match':{'status':'appendix','reason':'已识别材料要求，未找到可靠独立位置；可预览核对文末追加。','candidates':[]},
                'requirement_locations':[e['source_locator'] for e in reqs[cid]['evidence']],
                '_element':None,'_anchor':None,'_own':[],'_path':(len(document.element.body), categories.index(category)),
                '_inside_table':False,'_special_scores':{},'_structural':True,'_public':True})
    records.extend(extra)
    # All headings retain the separate requirements map for scan/mapping consumers.
    for r in records:
        r['_requirements'] = reqs
    return reqs
