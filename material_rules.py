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


def months(text):
    text = re.sub(r'\s+', '', text or '')
    found = set()

    def span(y, a, end_year, b):
        start, end = int(y) * 12 + int(a) - 1, int(end_year or y) * 12 + int(b) - 1
        if 1 <= int(a) <= 12 and 1 <= int(b) <= 12 and 0 <= end - start <= 120:
            found.update(range(start, end + 1))
    for m in re.finditer(r'(20\d{2})年(\d{1,2})月' + _RANGE + r'(?:(20\d{2})年)?(\d{1,2})月', text):
        span(*m.groups())
    # 2025年1-6月 shares one year and one trailing 月.
    for m in re.finditer(r'(20\d{2})年(\d{1,2})' + _RANGE + r'(\d{1,2})月', text):
        span(m[1], m[2], None, m[3])
    for m in re.finditer(r'(20\d{2})年((?:\d{1,2}[、，,])*\d{1,2})月', text):
        found.update(int(m[1]) * 12 + int(n) - 1 for n in re.split('[、，,]', m[2]) if 1 <= int(n) <= 12)
    return found


def tax_target(heading):
    # The exact slot takes precedence over other tax/social sections in the file.
    title = heading.get('title', '')
    context = title + '\n' + heading.get('preview', '')
    period = months(title)
    if not period:
        for clause in re.split('[。；;\n]', heading.get('preview', '')):
            if tax_kinds(clause):
                period.update(months(clause))
    return tax_kinds(title) or tax_kinds(context), period


def material_tax_kinds(material):
    return tax_kinds(material.get('name', ''))


def relative_period(heading):
    return bool(re.search(r'(?:近|前|最近)[一二三四五六七八九十\d]+个?月|近半年',
                         heading.get('title', '') + heading.get('preview', '')))


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
        if relative_period(heading) and not period:
            # The bid cutoff date is not a reliable structured input yet.
            # Do not treat today's date or a payment date as that cutoff.
            return []
        if wanted:
            covered = set()
            for material in candidates:
                kinds = material_tax_kinds(material) & wanted
                declared = months(material.get('name', '') + '\n' + material.get('version', ''))
                keys = {(kind, month) for kind in kinds for month in period & declared} if period else kinds
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
