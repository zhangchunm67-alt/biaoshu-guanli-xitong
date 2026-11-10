#!/usr/bin/env python3
"""目录生成后的校验：章节序号与结构。

从招标文件直接拆出的标题常带原文序号（“一、”“1.”“第三章”“（二）”“附件3”），
拆分、补充或人工调整后容易出现：同级序号乱序、重复、跳号、不从 1 开始；
同级混用原文序号与 Word 自动编号；同级编号样式不一致；下级与上级用同一种样式（层级错位）；
附件序号乱序或重复；同级重名。

输入为目录条目 [{id, text, level, locked_format?}]（与标书管理系统 outline 相同），
输出问题清单；可安全自动修正的问题附带建议标题（fixes），由人工确认后保存。

命令行：python3 outline_check.py 框架.docx   （读取生成的 Word 标题；有问题时退出码为 1）
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
             '六': 6, '七': 7, '八': 8, '九': 9}
CN_UNITS = {'十': 10, '百': 100}
CN = '一二三四五六七八九十百零〇两'
CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'

STYLE_NAMES = {
    'chapter': '“第X章/部分”', 'cn': '“一、”', 'paren_cn': '“（一）”', 'paren_num': '“（1）”',
    'decimal': '“1.”', 'circled': '“①”', 'attachment': '“附件N”',
}
# 与 build_framework.py 一致：这些前缀的标题不再加 Word 自动编号。
MANUAL_STYLES = {'chapter', 'cn', 'decimal'}

PATTERNS = (
    ('chapter', re.compile(r'^第\s*([' + CN + r'\d]{1,5})\s*([章节部分篇卷册])\s*')),
    ('attachment', re.compile(r'^(附件|附表)\s*([' + CN + r'\d]{1,4})(?![\d.．])\s*[：:、.．\-—]?\s*')),
    ('paren_cn', re.compile(r'^[（(]\s*([' + CN + r']{1,4})\s*[）)]\s*')),
    ('paren_num', re.compile(r'^[（(]\s*(\d{1,3})\s*[）)]\s*')),
    ('cn', re.compile(r'^([' + CN + r']{1,4})\s*[、.．]\s*')),
    ('circled', re.compile(r'^([' + CIRCLED + r'])\s*')),
    # “2023年…”“3.5万元”不是序号：数字后须是分隔符再接非数字。
    ('decimal', re.compile(r'^(\d{1,3}(?:[.．]\d{1,3})*)(?:[.．、]\s*|\s+)(?![\d年月日%万元])')),
)


def cn_number(value):
    if value.isdigit():
        return int(value)
    total, current = 0, 0
    for char in value:
        if char in CN_DIGITS:
            current = CN_DIGITS[char]
        elif char in CN_UNITS:
            total += (current or 1) * CN_UNITS[char]
            current = 0
        else:
            return None
    return total + current


def to_cn(number):
    digits = '零一二三四五六七八九'
    if number < 10:
        return digits[number]
    if number < 20:
        return '十' + (digits[number % 10] if number % 10 else '')
    if number < 100:
        return digits[number // 10] + '十' + (digits[number % 10] if number % 10 else '')
    return str(number)


def parse_number(title):
    """返回 (样式, 数值, 层级路径, 前缀, 正文) 或 None。"""
    text = (title or '').strip()
    for style, pattern in PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        rest = text[match.end():].strip()
        if style == 'chapter':
            value, path = cn_number(match.group(1)), None
        elif style == 'attachment':
            value, path = cn_number(match.group(2)), None
        elif style == 'circled':
            value, path = CIRCLED.index(match.group(1)) + 1, None
        elif style == 'decimal':
            path = [int(p) for p in re.split(r'[.．]', match.group(1))]
            value = path[-1]
        else:
            value, path = cn_number(match.group(1)), None
        if value is None or (style != 'chapter' and not rest):
            return None
        return {'style': style, 'value': value, 'path': path, 'prefix': text[:match.end()],
                'rest': rest, 'match': match}
    return None


def format_number(info, number):
    """按原样式生成新序号，保留原有分隔符与章/部分等后缀。"""
    match, style = info['match'], info['style']
    prefix = info['prefix']
    if style == 'chapter':
        raw = match.group(1)
        new = str(number) if raw.isdigit() else to_cn(number)
        return prefix[:match.start(1)] + new + prefix[match.end(1):]
    if style in {'cn', 'paren_cn'}:
        return prefix[:match.start(1)] + to_cn(number) + prefix[match.end(1):]
    if style == 'paren_num':
        return prefix[:match.start(1)] + str(number) + prefix[match.end(1):]
    if style == 'circled':
        return (CIRCLED[number - 1] if number <= len(CIRCLED) else f'({number})') + prefix[match.end(1):]
    if style == 'decimal':
        raw = match.group(1)
        parts = re.split(r'([.．])', raw)
        parts[-1] = str(number)
        return prefix[:match.start(1)] + ''.join(parts) + prefix[match.end(1):]
    return prefix


def build_tree(entries):
    """返回 [(父 id 或 None, 子条目列表)]，按出现顺序。"""
    groups, order, stack = {}, [], []
    for entry in entries:
        level = max(1, int(entry.get('level') or 1))
        while stack and stack[-1][1] >= level:
            stack.pop()
        parent = stack[-1][0] if stack else None
        if parent not in groups:
            groups[parent] = []
            order.append(parent)
        groups[parent].append(entry)
        stack.append((entry['id'], level))
    return [(parent, groups[parent]) for parent in order]


def _label(entry):
    return f'「{(entry.get("text") or "").strip()[:40]}」'


def _parent_label(parent_id, by_id):
    return f'「{by_id[parent_id]["text"].strip()[:30]}」下' if parent_id in by_id else '第一级'


def _normal_title(text):
    info = parse_number(text)
    value = info['rest'] if info else (text or '').strip()
    return re.sub(r'\s+', '', value)


def check_outline(entries):
    """校验目录；返回 {'issues': [...], 'checked': n}。issue: code/severity/message/chapter_ids/fixes。"""
    entries = [e for e in entries if isinstance(e, dict) and e.get('id')]
    by_id = {e['id']: e for e in entries}
    parsed = {e['id']: parse_number(e.get('text', '')) for e in entries}
    issues = []

    def add(code, message, rows, fixes=None, severity='warning'):
        issues.append({'code': code, 'severity': severity, 'message': message,
                       'chapter_ids': [r['id'] for r in rows], 'fixes': fixes or []})

    for parent, rows in build_tree(entries):
        where = _parent_label(parent, by_id)
        numbered = [(r, parsed[r['id']]) for r in rows if parsed[r['id']]]
        attachments = [(r, p) for r, p in numbered if p['style'] == 'attachment']
        ordinal = [(r, p) for r, p in numbered if p['style'] != 'attachment']

        # 1. 同级混用原文序号与 Word 自动编号。
        manual = [(r, p) for r, p in ordinal if p['style'] in MANUAL_STYLES]
        automatic = [r for r in rows if not parsed[r['id']] or parsed[r['id']]['style'] not in MANUAL_STYLES]
        if manual and automatic:
            fixes = [{'id': r['id'], 'text': p['rest']} for r, p in manual if p['rest']]
            add('mixed_auto',
                f'{where}有 {len(manual)} 个章节沿用原文序号、{len(automatic)} 个章节使用 Word 自动编号，'
                f'导出后会出现两套编号（如“一、”与“1.”交替）。建议去掉原文序号，统一由 Word 自动编号。',
                [r for r, _ in manual] + automatic, fixes)
            ordinal = [(r, p) for r, p in ordinal if p['style'] not in MANUAL_STYLES]

        # 2. 同级编号样式不一致。
        styles = list(dict.fromkeys(p['style'] for _, p in ordinal))
        if len(styles) > 1:
            add('mixed_style',
                f'{where}同时使用了 ' + '、'.join(STYLE_NAMES[s] for s in styles) + ' 等编号样式，可能是不同层级的标题被拆到了同一级，请核对层级。',
                [r for r, _ in ordinal])
        else:
            # 3. 单一样式下的序号连续性。
            values = [p['value'] for _, p in ordinal]
            if values:
                problems = []
                if values[0] != 1:
                    problems.append(f'从 {values[0]} 开始')
                duplicates = sorted({v for v in values if values.count(v) > 1})
                if duplicates:
                    problems.append('重复 ' + '、'.join(map(str, duplicates)))
                if any(b < a for a, b in zip(values, values[1:])):
                    problems.append('顺序颠倒')
                elif not duplicates and any(b - a > 1 for a, b in zip(values, values[1:])):
                    problems.append('中间跳号')
                if problems:
                    fixes = [{'id': r['id'], 'text': format_number(p, n) + p['rest']}
                             for n, (r, p) in enumerate(ordinal, 1) if p['value'] != n]
                    add('sequence',
                        f'{where}的 {STYLE_NAMES[styles[0]]} 序号' + '、'.join(problems) + '：'
                        + ' → '.join(str(v) for v in values) + '。可按当前顺序重新编号。',
                        [r for r, _ in ordinal], fixes)
            # 4. 下级与上级同一样式（非小数编号），多为层级错位。
            if styles and parent in parsed and parsed[parent] and parsed[parent]['style'] == styles[0] \
                    and styles[0] not in {'decimal', 'attachment'}:
                add('level_style',
                    f'{where}的子章节与上级都使用 {STYLE_NAMES[styles[0]]} 编号，可能是同级标题被缩进成了下级，请核对层级。',
                    [by_id[parent]] + [r for r, _ in ordinal])

        # 5. 附件序号：对应采购原件，不自动改号，只提示顺序与重复。
        if attachments:
            values = [p['value'] for _, p in attachments]
            duplicates = sorted({v for v in values if values.count(v) > 1})
            disordered = any(b < a for a, b in zip(values, values[1:]))
            if duplicates or disordered:
                detail = []
                if disordered:
                    detail.append('顺序颠倒（' + ' → '.join(map(str, values)) + '）')
                if duplicates:
                    detail.append('重复 ' + '、'.join(map(str, duplicates)))
                add('attachment',
                    f'{where}附件序号' + '，'.join(detail) + '。附件号须与采购原件一致，请按原件调整章节顺序或核对是否重复拆分。',
                    [r for r, _ in attachments])

        # 6. 同级重名（去掉序号后相同）。
        seen = {}
        for row in rows:
            key = _normal_title(row.get('text', ''))
            if key:
                seen.setdefault(key, []).append(row)
        for key, same in seen.items():
            if len(same) > 1:
                add('duplicate_title', f'{where}有 {len(same)} 个同名章节 {_label(same[0])}，请核对是否重复拆分。', same)

    return {'issues': issues, 'checked': len(entries)}


def read_docx_outline(path):
    """从生成的框架 Word 读取标题（BFHeadingN 样式）与书签。"""
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    from xml.etree import ElementTree as ET
    with zipfile.ZipFile(path) as package:
        root = ET.fromstring(package.read('word/document.xml'))
    entries = []
    for p in root.iter(ns + 'p'):
        style = p.find(f'{ns}pPr/{ns}pStyle')
        name = style.get(ns + 'val', '') if style is not None else ''
        match = re.fullmatch(r'BFHeading([1-9])', name)
        if not match:
            continue
        mark = p.find(f'{ns}bookmarkStart')
        text = ''.join(t.text or '' for t in p.iter(ns + 't')).strip()
        entries.append({'id': mark.get(ns + 'name') if mark is not None else f'H{len(entries) + 1}',
                        'text': text, 'level': int(match.group(1))})
    return entries


def format_report(result, entries=None):
    by_id = {e['id']: e for e in entries or []}
    if not result['issues']:
        return f'目录校验通过：{result["checked"]} 个章节，未发现序号或结构问题。'
    lines = [f'目录校验：{result["checked"]} 个章节，发现 {len(result["issues"])} 项需核对：']
    for i, issue in enumerate(result['issues'], 1):
        lines.append(f'{i}. {issue["message"]}')
        for fix in issue['fixes'][:12]:
            old = by_id.get(fix['id'], {}).get('text', fix['id'])
            lines.append(f'   建议：{old} → {fix["text"]}')
    return '\n'.join(lines)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='校验生成框架的目录序号与结构')
    parser.add_argument('docx', type=Path, help='生成的投标文件框架 DOCX')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args(argv)
    entries = read_docx_outline(args.docx)
    result = check_outline(entries)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else format_report(result, entries))
    return 1 if result['issues'] else 0


if __name__ == '__main__':
    sys.exit(main())
