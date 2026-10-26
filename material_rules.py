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
# A submission, deadline or transaction date is not the contribution period.
_DEADLINE = re.compile(r'(?:应于|须于|需于|于|截至|截止(?:到|至)?)?20\d{2}年\d{1,2}月(?:\d{1,2}日)?'
                       r'(?:前|之前|以前)?(?:提交|递交|报送|送达|截止)')
_RELATIVE = re.compile(r'(?:近|前|最近)[一二三四五六七八九十\d]+个?月|近半年')


def _period(text):
    """Months named by text, and whether a range in it is not a valid period."""
    text = re.sub(r'\s+', '', text or '')
    found, invalid = set(), False

    def span(m):
        nonlocal invalid
        y, a, end_year, b = m.groups()
        start, end = int(y) * 12 + int(a) - 1, int(end_year or y) * 12 + int(b) - 1
        if 1 <= int(a) <= 12 and 1 <= int(b) <= 12 and 0 <= end - start <= 120:
            found.update(range(start, end + 1))
        else:
            invalid = True
        # Consume the whole range so its end is never read as a single month.
        return '|'
    rest = _SPAN.sub(span, text)
    for m in re.finditer(r'(20\d{2})年((?:\d{1,2}[、，,])*\d{1,2})月', rest):
        found.update(int(m[1]) * 12 + int(n) - 1 for n in re.split('[、，,]', m[2]) if 1 <= int(n) <= 12)
    return found, invalid


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
    return [_DEADLINE.sub('', c) for c in re.split('[。；;\n]', context) if tax_kinds(c)]


def relative_period(heading):
    return bool(_RELATIVE.search('\n'.join(_sources(heading))))


def period_issue(heading):
    """Why the contribution period cannot be checked automatically, if at all."""
    if relative_period(heading):
        return '原文要求相对月份，请按本项目投标截止日核对缴费所属期；系统未自动判定期限。'
    if any(_period(clause)[1] for clause in _period_clauses(heading)):
        return '原文缴费期限无法完整识别，请按原文核对缴费所属期；系统未自动判定期限。'
    return ''


def tax_target(heading):
    # The exact slot takes precedence over other tax/social sections in the file.
    title, context = _sources(heading)
    period = set()
    if not relative_period(heading):
        for clause in _period_clauses(heading):
            period.update(months(clause))
    return tax_kinds(title) or tax_kinds(title + '\n' + context), period


def material_tax_kinds(material):
    return tax_kinds(material.get('name', ''))


def tax_errors(material, heading):
    wanted, period = tax_target(heading)
    kinds = material_tax_kinds(material)
    name = material.get('name', '材料')
    errors = []
    if wanted and not kinds:
        errors.append(f'{name}：无法核对是纳税还是社保凭证，请完善材料名称。')
    elif wanted and not wanted.intersection(kinds):
        errors.append(f'{name}：纳税与社保凭证类型不符。')
    declared = months(material.get('name', '') + '\n' + material.get('version', ''))
    if period and not declared:
        errors.append(f'{name}：缺少缴费所属月份，不能仅凭年度匹配。')
    elif period and not period.intersection(declared):
        errors.append(f'{name}：缴费所属月份与目标要求不符。')
    return errors


def tax_coverage_errors(materials, heading):
    wanted, period = tax_target(heading)
    errors = []
    labels = {'tax': '纳税', 'social': '社保'}
    for kind in sorted(wanted):
        matching = [m for m in materials if kind in material_tax_kinds(m)]
        if not matching:
            errors.append('缺少要求凭证：' + labels[kind] + '。')
        elif period:
            covered = set().union(*(months(m.get('name', '') + '\n' + m.get('version', '')) for m in matching))
            missing = period - covered
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
        wanted, period = tax_target(heading)
        if period_issue(heading):
            # The bid cutoff date is not a reliable structured input yet, and a
            # submission date elsewhere in the text cannot stand in for it.
            return []
        if wanted:
            years = set(row.get('required_years', []))
            covered = set()
            for material in candidates:
                kinds = material_tax_kinds(material) & wanted
                if period:
                    declared = months(material.get('name', '') + '\n' + material.get('version', ''))
                    keys = {(kind, month) for kind in kinds for month in period & declared}
                elif years:
                    # Complementary years are one set; two versions of a year overlap.
                    keys = {(kind, year) for kind in kinds for year in years & engine._material_years(material)}
                else:
                    keys = kinds
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
