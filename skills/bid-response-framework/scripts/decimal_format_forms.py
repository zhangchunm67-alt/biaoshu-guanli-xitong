"""Preserve explicit format chapters using decimal or unnumbered form titles.

Only complete short source paragraphs become headings. Ordinary requirements
and their comma-separated examples always remain intact source paragraphs.
"""
import re

CN = '零〇一二三四五六七八九十百两'
CHAPTER = re.compile(rf'^第[{CN}\d]+(?:章|部分|篇|卷)\s*(.+)$')
DECIMAL = re.compile(r'^([1-9]\d*(?:[.．][1-9]\d*)*)[.．、]?\s*(\D.+)$')
CHINESE = re.compile(rf'^[{CN}]+[、.．]\s*(.+)$')
TITLE_END = re.compile(r'(?:表|函|书|报价|材料|方案|说明|情况|介绍|概述|证明|记录|清单|文件|证件|安排|服务)$')


def compact(text):
    return re.sub(r'\s+', '', text)


def title_like(text):
    text = text.strip()
    # Parenthetical scope belongs to its caption, not to a new heading.
    bare = re.sub(r'[（(][^）)]*[）)]', '', text).strip()
    return (1 < len(text) < 75 and not re.search(r'[。；;：:，,！？!?]', bare)
            and not re.match(r'以上|请|本公司|我方|我司|供应商|投标人|应当|必须|备注|注[：:]', bare)
            and bool(TITLE_END.search(bare)))


def front_reply_ranges(src, before):
    """A labelled reply template is a separate prior response, not a requirement heading."""
    forms = []
    for n in src.nodes[:before]:
        if n.tag != 'p' or not re.fullmatch(r'[^。；;：:，,]{0,70}回复确认函', n.text):
            continue
        previous = next((p for p in reversed(src.nodes[:n.index]) if p.text), None)
        if previous is None or compact(previous.text) not in ('回复模板', '回复格式', '回复确认函模板'):
            continue
        end = next((p.index for p in src.nodes[n.index+1:before]
                    if p.tag == 'p' and CHAPTER.fullmatch(p.text)), before)
        body = '\n'.join(p.full for p in src.nodes[n.index+1:end])
        if not re.search(r'签字|盖章|公章', body):
            continue
        forms.append({'title': n.text, 'level': 1, 'source_index': n.index,
                      'source_start': n.index, 'content_start': n.index, 'source_end': end,
                      'format_start': before, 'submission_scope': 'prior_reply'})
    return forms


def format_ranges(src):
    starts = [n.index for n in src.nodes if n.tag == 'p' and len(n.text) < 80
              and CHAPTER.fullmatch(n.text)
              and re.search(r'(?:投标|响应|应答|参选).*(?:格式|范本)', CHAPTER.fullmatch(n.text)[1])
              and not re.search(r'[.．…]{2}|\d$', n.text)]
    if not starts:
        return []
    begin = starts[-1]
    end = next((n.index for n in src.nodes[begin+1:] if n.tag == 'p'
                and (CHAPTER.fullmatch(n.text) or compact(n.text) in ('评分办法', '评审办法', '评标办法'))), src.end)
    marked = []
    current_root = 0
    first_title = ''
    for n in src.nodes[begin+1:end]:
        if n.tag != 'p' or not n.text:
            continue
        decimal = DECIMAL.fullmatch(n.text)
        chinese = CHINESE.fullmatch(n.text)
        level = None
        if decimal and title_like(decimal[2]):
            code = decimal[1].replace('．', '.')
            parts = code.split('.')
            root = int(parts[0])
            if len(parts) == 1:
                # A repeated real form run supersedes its earlier catalogue.
                if root == 1 and current_root > 1 and compact(decimal[2]) == first_title:
                    marked = []
                    current_root = 0
                if root <= current_root or root > current_root+2:
                    continue
                current_root = root
                if not first_title:
                    first_title = compact(decimal[2])
                level = 1
            elif root == current_root:
                level = min(len(parts), 9)
        elif chinese and current_root and title_like(chinese[1]):
            level = 2
        elif current_root and title_like(n.text) and re.fullmatch(r'.{0,30}(?:响应方案|实施方案|服务方案|技术方案)', n.text):
            # Word automatic numbering is absent from paragraph text. A short
            # self-contained scheme caption still identifies a real form.
            level = 1
        if level:
            marked.append({'title': n.text, 'level': level, 'source_index': n.index,
                           'source_start': n.index, 'content_start': n.index+1})
    if len([m for m in marked if m['level'] == 1]) < 2:
        return []
    if not any(n.rows for n in src.nodes[begin+1:end]):
        return []
    for j, item in enumerate(marked):
        item['source_end'] = marked[j+1]['source_start'] if j+1 < len(marked) else end
        item['format_start'] = begin
    return front_reply_ranges(src, begin) + marked


def requirement_groups(src, before):
    """Use named numbered requirement items, never a comma-separated sentence."""
    chapters = [n for n in src.nodes[:before] if n.tag == 'p' and CHAPTER.fullmatch(n.text)]
    area = next((n for n in reversed(chapters) if re.fullmatch(r'(?:项目|采购|技术|服务)(?:需求|要求)', CHAPTER.fullmatch(n.text)[1])), None)
    if not area:
        return []
    end = next((n.index for n in chapters if n.index > area.index), before)
    end = next((n.index for n in src.nodes[area.index+1:end] if compact(n.text) in ('中选原则：', '中选原则:', '评审原则：', '评审原则:')), end)
    found = []
    for n in src.nodes[area.index+1:end]:
        match = re.fullmatch(r'([1-9]\d*)[、.．]\s*(.{2,35})', n.text)
        if n.tag == 'p' and match and not re.search(r'[，,。；;：:！？!?]', match[2]):
            found.append((n, match[2]))
    if len(found) < 2 or [int(re.match(r'\d+', n.text)[0]) for n, _ in found] != list(range(1, len(found)+1)):
        return []
    return [(n.index, found[j+1][0].index if j+1 < len(found) else end, title)
            for j, (n, title) in enumerate(found)]


def try_plan(src):
    from plan_framework import Plan
    forms = format_ranges(src)
    if not forms:
        return None
    plan = Plan(src)
    from plan_framework import add_standard_cover, extract_project_id, extract_package_name
    add_standard_cover(
        plan, project=plan.project, doc_title='响应文件', ref=forms[0]['format_start'],
        project_id=extract_project_id(src.alltext), package=extract_package_name(src.alltext),
        fields=(('供应商名称：', '________'), ('供应商地址：', '________'), ('日期：', '________')))
    plan.add('break'); plan.add('toc'); plan.add('break')
    demands = requirement_groups(src, forms[0]['format_start'])
    for item in forms:
        a, b = item['content_start'], item['source_end']
        is_scheme = bool(re.search('响应方案|实施方案|技术方案|服务方案', item['title']))
        prior_reply = item.get('submission_scope') == 'prior_reply'
        title = '前置回复确认函（采购原格式）' if prior_reply else item['title']
        plan.add('heading', title, item['source_index'], level=item['level'], numbered=False,
                 page_before=item['level'] == 1, locked_format=True,
                 source_start=item['source_start'], source_end=b,
                 **({'submission_scope': 'prior_reply'} if prior_reply else {}))
        if a < b:
            plan.add('copy', start=a, end=b-1, preserve_format=True)
        if prior_reply:
            plan.note('采购方前置回复确认函已保留原格式；请按采购邀请的回复要求办理。', item['source_index'])
        if is_scheme:
            plan.add('slot', '【待编】按上述采购要求编制项目响应方案；参与范围须以实际确认为准。', item['source_index'])
            for start, stop, title in demands:
                plan.add('heading', title, start, level=item['level']+1, numbered=False,
                         source_start=start, source_end=stop)
                plan.add('copy', start=start+1, end=stop-1, preserve_format=True)
                plan.add('slot', '【待编】针对原文要求填写方案；未参与的标段或不适用内容须明确说明。', start)
        contents = '\n'.join(n.full for n in src.nodes[a:b])
        if (re.search('报价.*表|报价清单|报价单', item['title'])
                and re.search(r'(?:详见|参见|见|按).{0,12}附件', contents)
                and not any(n.rows for n in src.nodes[a:b])):
            message = '【待补】本节引用外部报价附件，当前主文件未包含该原表；补充采购方附件后原样填写，不得自建替代表。'
            plan.add('slot', message, item['source_index'])
            plan.note(message, item['source_index'])
    for n in src.nodes:
        if n.tag == 'p' and re.search(r'单独提交|另行提交|单独密封|另行密封', n.text):
            plan.note(n.text, n.index)
    return {'source_sha256': src.digest, 'project': plan.project,
            'strategy': 'decimal-format-forms', 'forms': forms,
            'blocks': plan.blocks, 'review': plan.review}
