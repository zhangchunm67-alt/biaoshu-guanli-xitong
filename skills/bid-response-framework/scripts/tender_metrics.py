"""Build the internal, source-linked requirement summary delivered alongside the bid outline."""
import argparse
from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import unicodedata

from metric_source import read_units
from metric_eligibility import extract as eligibility_items
from metric_scoring_specs import extract as scoring_items


# The cards, item identifiers, group definitions and empty states share one ordering.
CATEGORIES = {
    'redline': ('废标红线条款', '条', '原文明确会或可能导致否决、不受理、响应无效或取消资格的条件。保留采购方的条件和措辞。'),
    'qualification': ('强制资格要求', '项', '参与或通过资格审查必须满足的主体、人员、资质、业绩等门槛。按要求保留证明方式及适用条件。'),
    'score': ('评分细则要点', '条', '按正式评分因素或可区分的子项计数。同一因素的优良等级、区间或扣分档合计一条；已有细项时不重复计权重总表。'),
    'spec': ('商务技术规范', '项', '需要供应商响应的商务及技术要求，包括报价、交付、服务和履约条件。保留采购原件给出的核对依据。'),
    'format': ('指定格式', '项', '采购原件实际提供的响应附件与固定表式。按原表保留字段、表注和签章位；名称引用不等于附件原件已经提供。'),
}
PREFIX = re.compile(r'^\s*(?:(?:第[零一二三四五六七八九十百\d]+条)|(?:[（(][零一二三四五六七八九十百\d]+[)）])|(?:\d+(?:\.\d+)+[、.．]?\s*)|(?:[一二三四五六七八九十百\d]+[、.．](?!\d)))\s*')


def norm(text):
    text = unicodedata.normalize('NFKC', text)
    return re.sub(r'\s+', '', text).rstrip('。；; ')


def identity(category, scope, key):
    return sha256('\n'.join([category, norm(scope), norm(key)]).encode()).hexdigest()[:16]


@dataclass
class Item:
    identifier: str
    label: str
    text: str
    scope: str
    references: dict = field(default_factory=dict)
    note: str = ''


def source_id(unit):
    """An evidence identity binds a location to its literal source content."""
    return 'src_' + sha256('\n'.join([unit.part, unit.locator, str(unit.index),
                                     str(unit.row), unit.original_text or unit.text]).encode()).hexdigest()[:24]


def source_record(unit):
    return {'id': source_id(unit), 'part': unit.part, 'locator': unit.locator,
            'context': unit.context, 'text': unit.text,
            'original_text': unit.original_text or unit.text,
            'index': unit.index, 'row': unit.row, 'kind': unit.kind,
            'scope': unit.scope, 'headers': list(unit.headers), 'cells': list(unit.cells),
            'is_heading': unit.is_heading, 'selection_uncertain': unit.selection_uncertain}


def format_items(source, units, plan=None, source_document=None):
    # Reuse the planner's actual-form boundaries so the fifth group and copied
    # original forms point to the same source blocks, not an attachment catalog.
    from plan_framework import Source
    from formal_attachment_forms import attachment_ranges
    by_index = {}
    for unit in units:
        if unit.part == '主件':
            by_index.setdefault(unit.index, []).append(unit)
    planned = plan or {}
    forms = list(planned.get('forms', []))
    if not forms and not any(b.get('locked_format') for b in planned.get('blocks', [])):
        src = source_document or Source(source)
        forms = attachment_ranges(src)
        if not forms:
            from prescribed_response_books import named_form_ranges, try_plan
            planned = try_plan(src) or planned
            forms = list(planned.get('forms', [])) or named_form_ranges(src)
            if not forms:
                from decimal_format_forms import format_ranges
                forms = format_ranges(src)
    # A fixed supplier-feedback matrix is a form even when no 附件 number
    # exists. The planner supplies the same exact block range it copies.
    forms.extend({'title': b['text'], 'source_start': b.get('source_start', b.get('source_index')),
                  'source_end': b.get('source_end'), 'code': ''}
                 for b in planned.get('blocks', []) if b.get('locked_format'))
    seen = set()
    for form in forms:
        if form['source_start'] in seen:
            continue
        seen.add(form['source_start'])
        matches = by_index.get(form['source_start'], [])
        if not matches:
            continue
        unit = matches[0]
        # The title is the review unit; the full source block is copied by the
        # planner. Relevant table evidence can be reached via the source range.
        yield {'category': 'format', 'unit': unit, 'text': unit.text,
               'label': form['title'], 'key': str(form['source_start'])}


def reviewed_groups(rows, units):
    """Read reviewed content while binding every evidence reference to this source."""
    if not isinstance(rows, list):
        raise ValueError('已核对要求必须为列表。')
    groups = {key: OrderedDict() for key in CATEGORIES}
    by_id = {source_id(u): u for u in units}
    by_location = {(u.part, u.locator): u for u in units}
    seen = set()
    for row in rows:
        cat, uid = row.get('category'), row.get('id')
        if cat not in groups or not isinstance(uid, str) or not uid or uid in seen:
            raise ValueError('已核对要求的分类或标识无效。')
        seen.add(uid)
        text = row.get('text', '').strip()
        if not text or not row.get('references'):
            raise ValueError('已核对要求须保留内容及采购原文出处。')
        issue = Item(uid, row.get('label', '').strip(), text, row.get('scope', '').strip(),
                     note=row.get('note', '').strip())
        for ref in row['references']:
            unit = by_id.get(ref.get('id')) if ref.get('id') else by_location.get((ref.get('part'), ref.get('locator')))
            if unit is None or (ref.get('part', unit.part), ref.get('locator', unit.locator)) != (unit.part, unit.locator):
                raise ValueError('已核对要求引用了不属于本采购原件的出处。')
            issue.references[(unit.part, unit.locator, unit.text)] = unit
        groups[cat][uid] = issue
    return groups


def collect(source, plan=None, *, parsed=None, source_document=None):
    units, notes = parsed if parsed is not None else read_units(source)
    if plan is not None and 'reviewed_requirements' in plan:
        return reviewed_groups(plan['reviewed_requirements'], units), list(dict.fromkeys(notes))
    groups = {key: OrderedDict() for key in CATEGORIES}

    def append(candidate):
        cat = candidate['category']
        unit = candidate['unit']
        text = candidate['text'].strip()
        if not text:
            raise ValueError('指标明细缺少条款。')
        scope = candidate.get('scope', unit.scope) or ''
        key = candidate.get('key') or PREFIX.sub('', text, count=1)
        uid = identity(cat, scope, str(key))
        group = groups[cat]
        if uid not in group:
            group[uid] = Item(uid, candidate.get('label', '').strip(), text, scope)
        issue = group[uid]
        for ref in [unit, *candidate.get('related', ())]:
            issue.references[(ref.part, ref.locator, ref.text)] = ref

    for extract in (eligibility_items, scoring_items):
        for candidate in extract(units):
            append(candidate)
    for candidate in format_items(source, units, plan, source_document):
        append(candidate)

    # Any case-specific correction is bound to this source by the calling plan's digest.
    edits = (plan or {}).get('metric_edits', [])
    if edits:
        by_location = {(u.part, u.locator): u for u in units}
        for edit in edits:
            cat = edit['category']
            if edit['action'] == 'drop':
                del groups[cat][edit['id']]
            elif edit['action'] == 'add':
                unit = by_location[edit['part'], edit['locator']]
                if norm(edit['text']) not in norm(unit.text):
                    raise ValueError('指标修订须引用所选原文位置中的文字。')
                append({'category':cat, 'unit':unit, 'text':edit['text'], 'label':edit.get('label','')})
            else:
                raise ValueError('未知指标修订动作。')
    return groups, list(dict.fromkeys(notes))


def export_review(source, plan=None, *, parsed=None, source_document=None):
    """JSON-safe requirements and full source registry for review and model input."""
    stamp = sha256(Path(source).read_bytes()).hexdigest()
    if plan and plan.get('source_sha256', stamp) != stamp:
        raise ValueError('编排计划与指标所用的采购版本不同。')
    parsed = parsed if parsed is not None else read_units(source)
    groups, notes = collect(source, plan, parsed=parsed, source_document=source_document)
    units, _ = parsed
    requirements = []
    for category, items in groups.items():
        for item in items.values():
            requirements.append({'id': item.identifier, 'category': category,
                'label': item.label, 'text': item.text, 'scope': item.scope, 'note': item.note,
                'references': [source_record(u) for u in item.references.values()]})
    return {'requirements': requirements, 'sources': [source_record(u) for u in units],
            'notes': notes, 'source_sha256': stamp}


STYLE = """
*{box-sizing:border-box}body{margin:0;background:#f6f8fa;color:#182a38;font:16px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
main{max-width:1110px;margin:auto;padding:40px 24px 76px}.eyebrow{font-size:12px;letter-spacing:.12em;color:#486779;margin:0}
h1{font-size:clamp(25px,4vw,38px);line-height:1.25;margin:8px 0}h2{font-size:22px;margin:0}p{margin:.4em 0}.muted,small{color:#526674}small{font-size:13px}
.cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin:24px 0}.card{display:block;border-radius:17px;color:#14202b;text-decoration:none;padding:18px 20px;border:1px solid #dce4e9}.card span{display:block;font-weight:650}.card b{display:block;font-size:34px;line-height:1.3;margin-top:6px}.card em{font-size:17px;font-weight:550;font-style:normal;margin-left:4px}
.redline{background:#eaf3ff}.qualification{background:#e8faf2}.score{background:#fff5e8}.spec{background:#fbeefa}.format{background:#eef0ff}.card:hover,.card:focus{outline:2px solid #40667c}
section,.panel{background:white;border:1px solid #dae1e5;border-radius:12px;margin:17px 0;padding:19px 23px}section{scroll-margin-top:15px}section header{display:flex;align-items:baseline;gap:12px;justify-content:space-between}a{color:#245c75}
summary{cursor:pointer}.item{padding:9px 0;border-top:1px solid #e5eaed}.item summary{font-weight:510}.ident{font-size:12px;color:#61798a;margin-right:8px}.chip{display:inline-block;border-radius:4px;background:#f1f4f6;color:#395967;padding:0 5px;margin-right:6px;font-size:12px}
blockquote{margin:6px 0 12px;padding:7px 14px;border-left:3px solid #c4d2db;white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;color:#334650}.loc{font-size:13px;color:#495f70;overflow-wrap:anywhere}.outline{list-style:none;margin:8px 0;padding-left:0}.outline li{border-left:1px solid #d2dce1;padding:1px 0 1px 10px}.note-list{margin:.5em 0;padding-left:22px}
footer{font-size:12px;color:#526674;overflow-wrap:anywhere}.desc{margin-bottom:10px;font-size:14px;color:#526674}#source-details{overflow-wrap:anywhere}
@media(max-width:720px){.cards{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}main{padding:24px 12px}.card{padding:11px 13px}section,.panel{padding:14px}}
@media print{body{background:white}main{padding:0}.cards{grid-template-columns:repeat(5,1fr)}.item{break-inside:avoid}}
"""


def short(text, limit=108):
    return text if len(text) <= limit else text[:limit].rstrip()+'…'


def evidence(item):
    pieces = []
    for ref in item.references.values():
        location = ' · '.join(v for v in (ref.part, ref.context, ref.locator) if v)
        original = getattr(ref, 'original_text', '') or ref.text
        pieces.append(f'<p class="loc">{escape(location)}</p><blockquote>{escape(original)}</blockquote>')
    return ''.join(pieces)


def outline(plan):
    if not plan:
        return ''
    def tree(blocks):
        return ''.join(f'<li style="margin-left:{(max(1,min(9,int(b.get("level",1))))-1)*15}px">{escape(b["text"])}</li>' for b in blocks if b['kind']=='heading')
    if plan.get('book_layout_version'):
        from response_books import trees, walk
        _, books = trees(plan['blocks'])
        panels = []
        for book in books:
            heading = book['heading']
            role = heading.get('book_role', '')
            body = [dict(n['heading'], level=n['heading'].get('level', 1) - 1)
                    for n in walk(book['children'])]
            basis = '' if heading.get('book_origin') == 'source' else '<p class="desc">按内容编排，递交方式以采购原文为准</p>'
            panels.append(f'<section data-book-role="{escape(role)}"><h3>{escape(heading["text"])}目录</h3>'
                          f'{basis}<ul class="outline">{tree(body)}</ul></section>')
        return '<details class="panel" open><summary><strong>本次生成的目录</strong></summary>' + ''.join(panels) + '</details>'
    entries = tree(plan.get('blocks',[]))
    for part in plan.get('parts', []):
        entries += f'<li><strong>{escape(part["name"])}</strong><ul class="outline">{tree(part["blocks"])}</ul></li>'
    return '<details class="panel"><summary><strong>本次生成的目录</strong></summary><ul class="outline">'+entries+'</ul></details>' if entries else ''


def save_report(source, output, plan=None, *, parsed=None, extra_notes=(), reminder=''):
    source = Path(source)
    stamp = sha256(source.read_bytes()).hexdigest()
    if plan and plan.get('source_sha256') != stamp:
        raise ValueError('编排计划与指标所用的采购版本不同。')
    groups, notes = collect(source, plan, parsed=parsed)
    cards, sections = [], []
    counts = {cat:len(items) for cat,items in groups.items()}
    for cat,(name,measure,meaning) in CATEGORIES.items():
        qty = counts[cat]
        cards.append(f'<a class="card {cat}" href="#{cat}"><span>{name}</span><b data-count="{qty}">{qty}<em>{measure}</em></b></a>')
        detail = []
        for number,item in enumerate(groups[cat].values(),1):
            label = item.label or short(item.text)
            if cat in ('redline', 'qualification') and item.label and norm(item.label) not in norm(item.text):
                label = f'{item.label} · {short(item.text, 88)}'
            scope = f'<span class="chip">{escape(item.scope)}</span>' if item.scope else ''
            more = f'<p>{escape(item.text)}</p>' if label != item.text else ''
            note = f'<p class="desc">核对备注：{escape(item.note)}</p>' if item.note else ''
            detail.append(f'<details class="item" data-id="{escape(item.identifier, quote=True)}"><summary><span class="ident">{number:02}</span>{scope}{escape(label)}</summary>{more}{note}{evidence(item)}</details>')
        if not detail:
            detail.append('<p class="muted">本次未识别到。</p>')
        sections.append(f'<section id="{cat}"><header><h2>{name}　{qty}{measure}</h2><a href="#top">返回汇总 ↑</a></header>'+''.join(detail)+'</section>')
    coverage = ('<details class="panel"><summary><strong>识别范围与待核附件</strong></summary><ul class="note-list">'+
                ''.join(f'<li>{escape(s)}</li>' for s in notes)+'</ul></details>') if notes else ''
    reminder_html = f'<p class="panel">{escape(reminder)}</p>' if reminder else ''
    extra_notes = list(dict.fromkeys(extra_notes))
    delivery = ('<section class="panel"><h2>编制提示与待核对事项</h2><ul>' +
                ''.join(f'<li>{escape(s)}</li>' for s in extra_notes) + '</ul></section>') if extra_notes else ''
    definitions = ''.join(f'<dt>{escape(name)}</dt><dd>{escape(meaning)}</dd>'
                          for name, _, meaning in CATEGORIES.values())
    counting = ('<details class="panel" id="counting-method"><summary>统计口径与分类说明</summary>'
                '<p>数字为当前可读采购材料中识别或核对后的条目数。同一类别与适用范围内，按相同条文或已识别的相同要求归并，并保留出处。'
                '一项要求可同时涉及多类，五个数字不宜直接相加。共同适用的要求只计一次。'
                '已明确勾选的互斥选项按选中内容识别；未取得的外部附件不计入。</p>'
                f'<dl>{definitions}</dl></details>')
    source_notes = [f'<li>{escape(str(note.get("text", "")))}（源位置 {escape(str(note["source_index"]))}）</li>'
                    for note in (plan or {}).get('review', [])
                    if isinstance(note, dict) and 'source_index' in note]
    technical = ('<details class="panel" id="source-details"><summary>来源技术详情</summary>'
                 f'<p>采购文件 SHA-256　<code>{stamp}</code></p>' +
                 ('<ul>' + ''.join(source_notes) + '</ul>' if source_notes else '') + '</details>')
    title = (plan or {}).get('project') or source.stem
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · 招标五类要求</title><style>{STYLE}</style></head><body><main id="top">
<h1>{escape(title)}</h1><p class="muted">{escape(source.name)}</p>
{reminder_html}<nav class="cards" aria-label="五类要求汇总">{''.join(cards)}</nav>{delivery}
<p class="desc">按已读取内容统计，跨类条目不可相加。</p>{counting}
{outline(plan)}{coverage}{''.join(sections)}{technical}</main></body></html>'''
    with Path(output).open('x', encoding='utf-8') as f:
        f.write(html)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path, help='内部编制汇总 .html')
    parser.add_argument('--plan', type=Path, help='可选的本项目编排计划，提供目录及有原文依据的复核修订')
    parser.add_argument('--json', action='store_true', help='输出包含五类要求和出处注册表的 JSON')
    args = parser.parse_args()
    if args.output.suffix.lower() != ('.json' if args.json else '.html'):
        parser.error('输出请使用 .json。' if args.json else '输出请使用 .html。')
    if args.source.suffix.lower() != '.docx':
        parser.error('输入请使用采购方发布的 .docx 文件。')
    plan = json.loads(args.plan.read_text(encoding='utf-8')) if args.plan else None
    if args.json:
        args.output.write_text(json.dumps(export_review(args.source, plan), ensure_ascii=False), encoding='utf-8')
        return
    counts = save_report(args.source,args.output,plan)
    print('，'.join(f'{CATEGORIES[k][0]} {v}{CATEGORIES[k][1]}' for k,v in counts.items()))


if __name__ == '__main__':
    main()
