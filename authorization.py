"""Read-only authorization section boundaries and identity matching helpers."""
import re
from docx.oxml.ns import qn


def person(material):
    name = str(material.get('name', ''))
    match = re.fullmatch(r'([\u4e00-\u9fff·]{2,12})[（(](?:法定代表人|委托代理人)[）)]身份证', name)
    return match[1] if match else ''


def describe(record):
    import engine
    # Procurement extracts can flatten the next attachment heading to an
    # unstyled paragraph. It must still end this form's ownership interval.
    own = []
    for element in record.get('_own', []):
        if re.match(r'^(?:附件|附表|格式)[一二三四五六七八九十\d]+[：:、.．]', engine._text(element).strip()):
            break
        own.append(element)
    record['_own'] = own
    substantive = [e for e in own if engine._text(e).strip() or engine._drawing(e) or e.tag == qn('w:tbl')]
    if substantive: record['_anchor'] = substantive[-1]
    body = record.get('_authorization_context') or '\n'.join(engine._text(e) for e in own)
    record['authorization_text'] = body if 'authorization' in record['category_ids'] else ''
    record['authorization_has_images'] = any(engine._drawing(e) for e in own)


def _names(text):
    legal = re.search(r'本人[【（(]?([\u4e00-\u9fff·]{2,8})[】）)]?系', text)
    if not legal:
        legal = re.search(r'在下面签字的[（(]([\u4e00-\u9fff·]{2,8})[、，,]', text)
    agent = re.search(r'现委托[【（(]?([\u4e00-\u9fff·]{2,8})[、，,】）)]', text)
    if not agent:
        agent = re.search(r'授权[（(][^）)]+[）)]的[（(]([\u4e00-\u9fff·]{2,8})[、，,]', text)
    blocked = {'法定代表人姓名', '被授权人的姓名', '全权代表姓名', '姓名',
               '委托代理人姓名', '法人名字', '被授权人名字', '签字或盖章'}
    return (legal[1] if legal and legal[1] not in blocked else '',
            agent[1] if agent and agent[1] not in blocked else '')


def add_body_targets(records):
    """Stable attachment targets tied to each authorization text-template section."""
    from material_rules import text_kind
    targets = []
    for record in records:
        if ('text_template' not in record.get('category_ids', [])
                or text_kind(record['title']) != 'authorization'):
            continue
        # Some buyer forms style every field as a heading. Keep their signature
        # and date fields with this form, but stop at the next actual section.
        import engine
        own = list(record.get('_own', []))
        by_element = {r['_element']: r for r in records if r['kind'] != 'placeholder'}
        fields = re.compile(r'^(?:(?:委托期限|授权期限|投标人|供应商|法定代表人|委托代理人|授权代表|'
                            r'被授权人|身份证号(?:码)?|日期|签字|签章)[：:]|代理人无转委托权)')
        clause = re.compile(r'^(?:本人.{0,40}系|本授权(?:委托)?书|现委托|现授权委托|兹授权)')
        if not record.get('_inside_table'):
            own = []
            for element in record['_element'].itersiblings():
                if element.tag not in {qn('w:p'), qn('w:tbl')}:
                    break
                other = by_element.get(element)
                compact = re.sub(r'\s+', '', engine._text(element))
                if (element not in record.get('_own', []) and other and
                        not fields.match(compact) and not clause.match(compact) and
                        (other.get('_structural') or other.get('_public') or other.get('category_ids'))):
                    break
                own.append(element)
            record['_own'] = own
            substantive = [e for e in own if engine._text(e).strip() or engine._drawing(e) or e.tag == qn('w:tbl')]
            if substantive:
                record['_anchor'] = substantive[-1]
        record['authorization_text'] = '\n'.join(engine._text(e) for e in own)
        record['authorization_original_body_present'] = bool(re.search(
            r'本人.{0,40}系|本授权(?:委托)?书|现委托|现授权委托|兹授权',
            re.sub(r'\s+', '', record['authorization_text'])))
        record['authorization_has_images'] = any(engine._drawing(e) for e in own)
        targets.append(dict(record, id='auth-body:' + record['id'],
            title='授权书底部 · ' + record['title'], kind='authorization_bundle',
            category_ids=['authorization'], authorization_source=record['id'],
            authorization_body_present=True, identity_attachment_target=True,
            _public=True))
    records.extend(targets)


def preserves_form(heading):
    """A recognized body form may be retained even when its fields are still blank."""
    from material_rules import text_kind
    return bool(heading and heading.get('kind') in {'heading', 'paragraph'}
                and 'text_template' in heading.get('category_ids', [])
                and text_kind(heading.get('title', '')) in {'authorization', 'bid_letter', 'commitment'})


def link_body_targets(mappings, headings, materials, *, preserve_forms=False):
    """Only link a unique selected authorization body; never guess across lots."""
    import copy
    from material_rules import text_kind
    rows = copy.deepcopy(mappings)
    by_material = {m['id']: m for m in materials}
    by_heading = {h['id']: h for h in headings}
    if preserve_forms:
        for row in rows:
            if row['category_id'] == 'text_template' and preserves_form(by_heading.get(row.get('locator_id'))):
                row.update(action='skip', material_ids=[])
    targets = {h['authorization_source']: h for h in headings if h.get('authorization_source')}
    bodies = {}
    for row in rows:
        selected = [by_material.get(i) for i in row.get('material_ids', [])]
        if (row['category_id'] == 'text_template' and row.get('action') == 'fill'
                and row.get('locator_id') in targets and len(selected) == 1
                and selected[0] and not selected[0].get('archived')
                and selected[0].get('mode') == 'text'
                and text_kind(selected[0].get('name', '')) == 'authorization'):
            bodies[targets[row['locator_id']]['id']] = selected[0]
    for source, target in targets.items():
        text_rows = [r for r in rows if r['category_id'] == 'text_template' and r.get('locator_id') == source]
        if ((target.get('authorization_original_body_present') or
             (preserve_forms and preserves_form(by_heading.get(source)))) and
                (not text_rows or all(r.get('action') == 'skip' for r in text_rows))):
            bodies[target['id']] = {'content': target.get('authorization_text', ''), 'from_original': True}
    for row in rows:
        if row['category_id'] != 'authorization':
            continue
        # Selecting the original form heading means its own attachment slot,
        # never a same-named form elsewhere in the document.
        target = targets.get(row.get('locator_id'))
        if target and target['id'] in bodies:
            row['locator_id'] = target['id']
            row['location_match'] = {'status': 'manual', 'candidates': [],
                'reason': '已定位到所选授权书底部，双方身份证附在该文书之后。'}
        if not row.get('locator_id') or row.get('location_match', {}).get('status') == 'after_authorization':
            if len(bodies) == 1:
                row['locator_id'] = next(iter(bodies))
                row['location_match'] = {'status': 'after_authorization', 'candidates': [],
                    'reason': ('保留采购原授权书；双方身份证附在原文书底部。' if bodies[row['locator_id']].get('from_original')
                               else '已关联核对后的授权书正文；双方身份证附在正文底部。')}
            elif row.get('location_match', {}).get('status') == 'after_authorization':
                row['locator_id'] = ''
    return rows, bodies
