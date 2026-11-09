"""Separate proposal and pricing files for an invitation with independently evaluated lots."""
import re

NUMBER = r'[一二三四五六七八九十百0-9０-９]+'
LOT = re.compile(rf'^((?:子?项目|包件?|标段)\s*{NUMBER})\s*[：:]\s*(.+)$')
BUDGET = re.compile(r'预算|最高限价|最高限额|报价上限')
STAMP = re.compile(r'加盖.{0,5}(?:公章|单位章)|盖章')
INDEPENDENT = re.compile(r'独立|互不绑定|互不影响')
SEPARATE = re.compile(r'分别|单独|不得合并|不得混编|不可合并')


def after_colon(text):
    return re.split('[：:]', text, maxsplit=1)[-1].strip()


def series(text, start, stop):
    """Extract a procurement-defined list, removing its lead-in and trailing sentence."""
    found = re.search(start, text)
    if not found:
        return []
    listed = re.split(stop,text[found.end():],maxsplit=1)[0].strip('：:。；; ')
    return [item.strip() for item in re.split(r'[、；;]',listed) if item.strip()]


def source_project(nodes, fallback):
    for n in nodes:
        m = re.match(r'^(?:项目名称|采购项目名称)\s*[：:]\s*(.+)',n.text)
        if m:
            return m[1].strip(),n.index
    return fallback,0


def try_plan(src):
    from plan_framework import Plan
    nodes = [n for n in src.nodes if n.tag=='p' and n.text]
    lot_rows, codes = [], set()
    for n in nodes:
        match = LOT.match(n.text)
        if not match or match[1] in codes:
            continue
        name = match[2].strip()
        if len(name) > 80 or BUDGET.search(name) or re.search(r'评分|评审|占比|权重|满分|\d+\s*(?:分|元|%)|[。；;]|须提交|需提供',name):
            continue
        lot_rows.append((n,match[1],name))
        codes.add(match[1])
    if len(lot_rows)<2:
        return None
    split_instructions = [n for n in nodes if SEPARATE.search(n.text)]
    scheme = next((n for n in split_instructions if re.search(r'(?:服务|技术|实施)方案',n.text)
                   and not re.search('报价单|分别报价',n.text)),None)
    scheme = scheme or next((n for n in split_instructions if re.search(r'(?:服务|技术|实施)方案',n.text)),None)
    price = next((n for n in split_instructions if re.search(r'报价(?:单|表|文件|书)|分别(?:独立)?报价|单独报价',n.text)),None)
    if not scheme or not price:
        return None
    independent = next((n for n in nodes if re.search(r'子项目|标段|包件',n.text) and INDEPENDENT.search(n.text)),scheme)
    service_nodes = [n for n in nodes if re.search(r'^(?:核心|主要|采购|项目)?服务(?:需求|内容|范围)[（(：:]|^(?:项目|采购)需求[：:]',n.text)]
    service = next((n for n in service_nodes if re.search(r'涵盖|包括|包含|分别为|工作内容为',n.text)),None)
    services = (series(service.text,r'(?:具体|主要)?(?:涵盖|包括(?:但不限于)?|包含|分别为|工作内容为)',r'等(?:全部|相关|工作|内容|服务)|[,，。]')
                if service else [])
    topics = series(scheme.text,r'(?:方案.{0,10})?(?:均需|须|应|需)(?:明确|包括(?:但不限于)?|涵盖|包含|说明)',r'等(?:核心|主要|内容|事项|要素)|[,，。]')
    terms = next((n for n in nodes if re.match(r'^(?:服务|合作|项目)(?:期限|期|周期)\s*[：:]',n.text)),None)
    ceiling_rows = [n for n in nodes if BUDGET.search(n.text)]
    no_transfer = next((n for n in ceiling_rows if re.search(r'调剂|互不通用|不得挪用|不可挪用',n.text)),None)
    score = next((n for n in nodes if not LOT.match(n.text) and not any(n.text.startswith(name) for _,_,name in lot_rows)
                  and re.search('评分|评审',n.text) and re.search(r'\d+\s*(?:分|%|％)',n.text)
                  and re.search('报价|价格',n.text) and '方案' in n.text),None)
    optional = next((n for n in nodes if re.search(r'自愿|非强制|非必选|如有',n.text) and re.search('佐证|证明|资质|案例',n.text)),None)
    delivery = [n for n in nodes if re.search(r'截止|\d\s*[:：]\s*\d\d.{0,4}前|发送至|递交至|逾期',n.text)]
    rules = next((n for n in nodes if n!=independent and re.search(r'独立(?:打分|排名)|分别评审',n.text)),None)
    integr = next((n for n in nodes if re.search(r'框架.{0,5}合同',n.text) and re.search(r'采购|约定',n.text)),None)

    plan = Plan(src)
    project, ref = source_project(nodes,plan.project)
    plan.project = project
    plan.add('title',project,ref)
    plan.add('title','分件编制说明',ref)
    plan.add('text','各子项目独立响应。仅提交实际参与的子项目对应文件。',independent.index)
    plan.head('分件清单',1,independent.index)
    plan.add('text',independent.text,independent.index)
    plan.add('table',ref=independent.index,rows=[['子项目','专属文件','是否参与（待选）']]+
             [[title,'服务方案、报价单',''] for _,_,title in lot_rows])
    plan.head('采购要求与递交安排',1,scheme.index)
    for n in [integr,terms,*service_nodes,scheme,price,*ceiling_rows,score,rules,optional,*delivery]:
        if n and not any(b.get('text')==n.text for b in plan.blocks):
            plan.add('text',n.text,n.index)
    for n in delivery:
        plan.note(n.text,n.index)

    parts = []
    for location,code,name in lot_rows:
        # Match a lot's own budget only. Do not divide a project-level ceiling or reassign another lot's amount.
        marker = re.compile(rf'^{re.escape(code)}(?:\s*[（(][^）)]+[）)])?\s*[：:]')
        budget = next((n for n in ceiling_rows if marker.match(n.text) or n.text.startswith(name)),None)
        if ceiling_rows and not budget:
            plan.note(f'{code}未能定位独立预算数额，须核实对应上限。',location.index)
        specific_score = [n for n in nodes if marker.match(n.text) and n!=location and n!=budget and re.search('评分|复评|评审权重',n.text)]
        specific = [n for n in nodes if marker.match(n.text) and n!=location and not BUDGET.search(n.text)
                    and n not in specific_score and re.search(r'服务|工作范围|须|需提供|提交|执行|实施',after_colon(n.text))]
        others = [identifier for _,identifier,title in lot_rows if identifier != code]
        others.extend(title for _,identifier,title in lot_rows if identifier != code)
        def suitable(n):
            return not any(other in n.text for other in others)
        for mode in ('scheme','price'):
            sub=Plan(src)
            role='服务方案' if mode=='scheme' else ('含税报价单' if '含税' in price.text else '报价单')
            sub.add('title',name,location.index)
            sub.add('title',role,scheme.index if mode=='scheme' else price.index)
            sub.add('text','响应单位：________　编制日期：________',location.index)
            if mode=='scheme':
                sub.head('服务范围及实施响应',1,service.index if service else location.index)
                sub.add('text',f'本文件适用于{code}：{name}。',location.index)
                if terms:
                    sub.add('text',terms.text,terms.index)
                for item in services:
                    sub.placeholder(item,2,service.index,'【待编】填写本子项目的做法、交付物及保障措施。')
                for n in [*service_nodes,*specific]:
                    if (n!=service or not services) and suitable(n):
                        sub.add('text',n.text,n.index)
                if not services:
                    sub.add('slot','【待编】根据本子项目实际要求填写服务范围、工作内容及交付物。',location.index)
                scope_tail=re.search(r'(?:统筹完成|遵循|严格符合)(.+)$',service.text) if service else None
                if scope_tail:
                    sub.add('text',scope_tail[0],service.index)
                sub.head('专项方案',1,scheme.index)
                for item in topics:
                    sub.placeholder(item,2,scheme.index,'【待编】按本子项目实际情况编制。')
                if not topics:
                    if suitable(scheme):
                        sub.add('text',scheme.text,scheme.index)
                    sub.add('slot','【待编】填入本子项目的执行方案及相关证明。',scheme.index)
                if optional:
                    sub.placeholder('补充佐证材料（如有）',1,optional.index,'【选填】按本子项目提供与采购要求相符的佐证。')
            else:
                sub.head('分项报价',1,price.index)
                if budget:
                    sub.add('text','采购预算上限（采购方规定）：'+after_colon(budget.text),budget.index)
                if no_transfer:
                    sub.add('text',after_colon(no_transfer.text),no_transfer.index)
                # Quote conditions are transcribed, without copying the competing lot into this file.
                for clause in re.split(r'(?<=[。；;])',after_colon(price.text)):
                    if re.search(r'闭口|全包|税费|成本|费用|隐性|含税|超出',clause) and not any(k!=code and k in clause for _,k,_ in lot_rows):
                        sub.add('text',clause.strip(),price.index)
                rows=[['序号','服务项目','分项报价（含税）' if '含税' in price.text else '分项报价','说明']]
                rows.extend([[str(i),item,'',''] for i,item in enumerate(services or [''],1)])
                rows.append(['','合计','',''])
                sub.add('table',ref=price.index,rows=rows)
            if specific_score or score:
                for n in specific_score or [score]:
                    sub.add('text',n.text,n.index)
            if STAMP.search((scheme if mode=='scheme' else price).text) or any(STAMP.search(n.text) for n in delivery):
                sub.add('text','供应商（盖章）________　日期________',location.index)
            parts.append({'name':name+'_'+role,'blocks':sub.blocks})
    return {'source_sha256':src.digest,'project':project,'strategy':'separately-submitted-lots','review':plan.review,'blocks':plan.blocks,'parts':parts}
