"""Read-only authorization section boundaries and identity matching helpers."""
import re
from docx.oxml.ns import qn


_ROLE_LEGAL = r'(?:法定代表人|法人代表|法人|单位负责人|负责人)'
_ROLE_AGENT = r'(?:委托代理人|代理人|被授权人|授权代表|全权代表)'
# Personal names: two to four Han characters, or a transliterated name with "·".
_NAME = r'([\u4e00-\u9fff]{2,4}|[\u4e00-\u9fff]{1,6}·[\u4e00-\u9fff]{1,8})'
# Words that fill a name slot in blank or descriptive templates.
_NOT_NAMES = {'姓名', '本人', '我方', '我公司', '本公司', '我单位', '本单位', '单位', '公司', '签字', '签章',
              '盖章', '某某', '代理人', '委托代理人', '被授权人', '授权代表', '全权代表', '法人', '法人代表',
              '法定代表人', '负责人', '单位负责人', '职务', '正反面', '复印件', '人像面', '国徽面'}


def _clean(name):
    name = re.sub(r'(?:同志|先生|女士|的)$', '', name or '')
    if len(name) < 2 or name in _NOT_NAMES or any(word in name for word in ('姓名', '签字', '公司', '单位', '代表')):
        return ''
    return name


def person(material):
    """The ID holder named in a material title, e.g. 张三（法定代表人）身份证."""
    name = re.sub(r'\s+', '', str(material.get('name', '')))
    for pattern in (_NAME + r'[（(]' + '(?:' + _ROLE_LEGAL + '|' + _ROLE_AGENT + r')[）)]的?身份证',
                    '(?:' + _ROLE_LEGAL + '|' + _ROLE_AGENT + r')[:：]?' + _NAME + r'的?身份证'):
        match = re.search(pattern, name)
        if match and _clean(match[1]):
            return _clean(match[1])
    return ''


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


def _first(patterns, text):
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            name = _clean(match[1])
            if name:
                return name
    return ''


_OPEN, _CLOSE = r'[【（(]?', r'[】）)]?(?:[（(]姓名[）)])?'
_FIELD_END = r'(?=性别|年龄|职务|身份证|电话|[，,；;。、]|$)'


def legal_name(text):
    """Legal representative named in a filled certificate or authorization body."""
    return _first([
        r'本人' + _OPEN + _NAME + _CLOSE + r'(?=系|为|是|，|,)',
        r'兹证明' + _OPEN + _NAME + _CLOSE + r'(?=同志|先生|女士|系|为|是|，|,)',
        r'在下面签字的[（(]' + _NAME + r'[、，,）)]',
        _ROLE_LEGAL + r'姓名[:：]' + _NAME + _FIELD_END,
        r'法定代表人[:：]' + _NAME + _FIELD_END,
    ], text)


def agent_name(text):
    """Authorized agent named in an authorization body."""
    return _first([
        r'委托' + _OPEN + _NAME + _CLOSE + r'(?=为|作为|担任|同志|先生|女士|、|，|,|[（(])',
        r'授权' + _OPEN + _NAME + _CLOSE + r'(?=为|作为|担任|同志|先生|女士)',
        r'授权[（(][^）)]+[）)]的[（(]' + _NAME + r'[、，,]',
        _ROLE_AGENT + r'(?:姓名)?[:：]' + _NAME + _FIELD_END,
    ], text)


def _names(text):
    return legal_name(text), agent_name(text)
