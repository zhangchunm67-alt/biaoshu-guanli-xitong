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
    blocked = {'法定代表人姓名', '被授权人的姓名', '全权代表姓名', '姓名'}
    return (legal[1] if legal and legal[1] not in blocked else '',
            agent[1] if agent and agent[1] not in blocked else '')
