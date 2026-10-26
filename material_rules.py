"""Narrow evidence checks within shared library categories.

Use the material's declared name/version for its period, never the bank payment
date or an unrelated date in a description. Unknown metadata cannot prove a
specific month requirement.
"""
import re


def tax_kinds(text):
    kinds = set()
    if re.search(r'税收|税款|纳税|完税', text or ''):
        kinds.add('tax')
    if re.search(r'社保|社会保险|社会保障|职工缴费', text or ''):
        kinds.add('social')
    return kinds


_RANGE = r'(?:至|到|[-—–~～])'
_SPAN = re.compile(r'(20\d{2})年(\d{1,2})月?' + _RANGE + r'(?:(20\d{2})年)?(\d{1,2})月')
# A single submission date is not the contribution period. Ranges are parsed
# first, so "自2025年1月至2025年6月截止" keeps its end month.
_DEADLINE = re.compile(r'(?:应于|须于|需于|于)?20\d{2}年\d{1,2}月(?:\d{1,2}日)?'
                       r'(?:前|之前|以前)?(?:提交|递交|报送|送达)')
_RELATIVE = re.compile(r'(?:近|前|最近)[一二三四五六七八九十\d]+个?月|近半年')
_TAX_WORD, _SOCIAL_WORD = r'税收|税款|纳税|完税', r'社保|社会保险|社会保障|职工缴费'
_KIND_WORD = re.compile('(' + _TAX_WORD + ')|(' + _SOCIAL_WORD + ')')
# A period written just before its evidence (2025年1月的纳税证明, 2025年2月份的
# 社保缴费证明) names that evidence.
_BEFORE_KIND = re.compile(r'(?:份|期间|所属期)?[）)]?(?:的|之)?(?:单位|企业|公司|依法)?(?:缴纳)?'
                          r'(?=' + _TAX_WORD + '|' + _SOCIAL_WORD + ')')


def _spans(text, deadlines=False):
    """(start, end, months, valid) for each period in compact text, in order."""
    spans = []

    def mask(m):
        return '|' * (m.end() - m.start())
    for m in _SPAN.finditer(text):
        y, a, end_year, b = m.groups()
        first, last = int(y) * 12 + int(a) - 1, int(end_year or y) * 12 + int(b) - 1
        valid = 1 <= int(a) <= 12 and 1 <= int(b) <= 12 and 0 <= last - first <= 120
        spans.append((m.start(), m.end(), set(range(first, last + 1)) if valid else set(), valid))
    # Consume each range so its end is never read again as a single month.
    rest = _SPAN.sub(mask, text)
    if deadlines:
        rest = _DEADLINE.sub(mask, rest)
    for m in re.finditer(r'(20\d{2})年((?:\d{1,2}[、，,])*\d{1,2})月', rest):
        found = {int(m[1]) * 12 + int(n) - 1 for n in re.split('[、，,]', m[2]) if 1 <= int(n) <= 12}
        spans.append((m.start(), m.end(), found, True))
    return sorted(spans, key=lambda span: span[0])


def _period(text, deadlines=False):
    """Months named by text, and whether a range in it is not a valid period."""
    spans = _spans(re.sub(r'\s+', '', text or ''), deadlines)
    return set().union(*(span[2] for span in spans)), not all(span[3] for span in spans)


def months(text):
    return _period(text)[0]


def _sources(heading):
    """The slot's own wording; a synthetic end-of-document slot uses its evidence."""
    if heading.get('kind') == 'common_appendix':
        evidence = (heading.get('requirement') or {}).get('evidence', [])
        return '', '\n'.join(e.get('text', '') for e in evidence)
    return heading.get('title', ''), heading.get('preview', '')


def _period_clauses(heading):
    title, context = _sources(heading)
    if title and _period(title) != (set(), False):
        return [title]
    return [c for c in re.split('[。；;\n]', context) if tax_kinds(c)]


def relative_period(heading):
    return bool(_RELATIVE.search('\n'.join(_sources(heading))))


def period_issue(heading):
    """Why the contribution period cannot be checked automatically, if at all."""
    if relative_period(heading):
        return '原文要求相对月份，请按本项目投标截止日核对缴费所属期；系统未自动判定期限。'
    if any(_period(clause)[1] for clause in _period_clauses(heading)):
        return '原文缴费期限无法完整识别，请按原文核对缴费所属期；系统未自动判定期限。'
    return ''


def _clause_periods(clause):
    """Months per kind by position: a period just before a kind names it; any
    other period continues the nearest kind before it, or else the next one."""
    text = re.sub(r'\s+', '', clause)
    kinds = [(m.start(), m.end(), 'tax' if m.group(1) else 'social') for m in _KIND_WORD.finditer(text)]
    own, every = {}, set()
    for start, end, found, _ in _spans(text, deadlines=True):
        every |= found
        ahead = _BEFORE_KIND.match(text, end)
        before = [k for s, e, k in kinds if e <= start]
        after = [k for s, e, k in kinds if s >= (ahead.end() if ahead else end)]
        kind = after[0] if ahead and after else before[-1] if before else after[0] if after else None
        if kind:
            own.setdefault(kind, set()).update(found)
    return own, every


def tax_target(heading):
    """Wanted evidence kinds and the months each kind must cover (empty: none named)."""
    # The exact slot takes precedence over other tax/social sections in the file.
    title, context = _sources(heading)
    periods = {'tax': set(), 'social': set()}
    if not relative_period(heading):
        for clause in _period_clauses(heading):
            own, every = _clause_periods(clause)
            # Only the kinds a clause names take its months; one it names
            # without months of its own shares them (2025年1月的纳税和社保证明).
            for kind in tax_kinds(clause) or set(periods):
                periods[kind] |= own.get(kind) or every
    return tax_kinds(title) or tax_kinds(title + '\n' + context), periods


def material_tax_kinds(material):
    return tax_kinds(material.get('name', ''))


def _declared(material):
    return months(material.get('name', '') + '\n' + material.get('version', ''))


def tax_errors(material, heading):
    wanted, periods = tax_target(heading)
    kinds = material_tax_kinds(material)
    name = material.get('name', '材料')
    errors = []
    if wanted and not kinds:
        errors.append(f'{name}：无法核对是纳税还是社保凭证，请完善材料名称。')
    elif wanted and not wanted.intersection(kinds):
        errors.append(f'{name}：纳税与社保凭证类型不符。')
    relevant = (kinds & wanted) or wanted or kinds or set(periods)
    period = set().union(*(periods[kind] for kind in relevant))
    declared = _declared(material)
    if period and not declared:
        errors.append(f'{name}：缺少缴费所属月份，不能仅凭年度匹配。')
    elif period and not period.intersection(declared):
        errors.append(f'{name}：缴费所属月份与目标要求不符。')
    return errors


def tax_coverage_errors(materials, heading):
    wanted, periods = tax_target(heading)
    errors = []
    labels = {'tax': '纳税', 'social': '社保'}
    for kind in sorted(wanted):
        matching = [m for m in materials if kind in material_tax_kinds(m)]
        if not matching:
            errors.append('缺少要求凭证：' + labels[kind] + '。')
        elif periods[kind]:
            covered = set().union(*(_declared(m) for m in matching))
            missing = periods[kind] - covered
            if missing:
                values = '、'.join(f'{v // 12}年{v % 12 + 1}月' for v in sorted(missing))
                errors.append('缺少要求月份：' + labels[kind] + ' ' + values + '。')
    return errors


def text_kind(text):
    if re.search(r'授权(?:委托)?书', text or ''):
        return 'authorization'
    if re.search(r'(?:投标|报价|响应)函', text or ''):
        return 'bid_letter'
    if re.search(r'承诺[函书]', text or ''):
        return 'commitment'
    return ''


def partial_issue(issue):
    return issue.startswith(('缺少要求年度：', '缺少要求凭证：', '缺少要求月份：'))


def automatic_ids(row, candidates, headings, categories):
    """Preselect a unique set, even when it covers the requirement only in part.

    Overlapping versions remain manual; missing years, kinds or months stay
    visible as issues on the preselected row.
    """
    import engine
    if not candidates:
        return []
    if row['category_id'] == 'tax_social':
        heading = next((h for h in headings if h['id'] == row.get('locator_id')), {})
        wanted, periods = tax_target(heading)
        if period_issue(heading):
            # The bid cutoff date is not a reliable structured input yet, and a
            # submission date elsewhere in the text cannot stand in for it.
            return []
        if wanted:
            years = set(row.get('required_years', []))
            covered = set()
            for material in candidates:
                keys = set()
                for kind in material_tax_kinds(material) & wanted:
                    if periods[kind]:
                        keys |= {(kind, month) for month in periods[kind] & _declared(material)}
                    elif years:
                        # Complementary years are one set; two versions of a year overlap.
                        keys |= {(kind, year) for year in years & engine._material_years(material)}
                    else:
                        keys.add(kind)
                if covered.intersection(keys):
                    return []
                covered.update(keys)
        elif len(candidates) > 1:
            return []
    elif len(candidates) > 1:
        years = row.get('required_years', [])
        if not years or any(sum(y in engine._material_years(m) for m in candidates) > 1 for y in years):
            return []
    ids = [m['id'] for m in sorted(candidates, key=lambda m: (min(engine._material_years(m) or [9999]), m['id']))]
    probe = dict(row, action='fill', material_ids=ids)
    checked = engine.validate_mappings([probe], candidates, headings, categories)[0]
    return ids if all(partial_issue(issue) for issue in checked['issues']) else []
