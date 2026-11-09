"""User-required response sections, independent of buyer requirement extraction."""
import re


def ensure_business_license(plan):
    """Add one supplier license section without inventing procurement evidence."""
    blocks = plan['blocks']
    headings = [b for b in blocks if b.get('kind') == 'heading']
    all_headings = headings + [b for p in plan.get('parts', []) for b in p['blocks']
                               if b.get('kind') == 'heading']
    if any('营业执照' in b.get('text', '') and not re.search(
            r'会计师|事务所|审计机构|制造商|生产厂家', b['text']) for b in all_headings):
        return plan
    if not headings:
        return plan  # Do not turn an invalid/unrecognized plan into a valid one.

    # Place the extra section immediately before the company introduction, at
    # the same level. Without one, use the qualification book or append it.
    company = next((b for b in headings if re.fullmatch(
        r'(?:公司|企业|单位)(?:介绍|简介|概况)', b.get('text', '').strip())), None)
    parent = next((b for b in headings if b.get('book_role') == 'qualification'), None)
    if parent is None:
        parent = next((b for b in headings if re.fullmatch(
            r'资格证明文件|资格条件及证明|资格证明与准入响应',
            b.get('text', '').strip())), None)
    index, level = len(blocks), 1
    if company is not None:
        index, level = blocks.index(company), company.get('level', 1)
    elif parent is not None:
        level = parent.get('level', 1) + 1
        if level <= 9:
            index = next((i for i in range(blocks.index(parent) + 1, len(blocks))
                          if blocks[i]['kind'] == 'heading'
                          and blocks[i].get('level', 1) < level), len(blocks))
        else:
            level = 1
    heading = {'kind': 'heading', 'text': '营业执照', 'level': level,
               'origin': 'user_policy', 'requirement_ids': []}
    used = [int(b['bookmark'][10:]) for b in headings
            if re.fullmatch(r'BidSection[1-9][0-9]*', b.get('bookmark', ''))]
    if used:
        heading['bookmark'] = 'BidSection' + str(max(used + [plan.get('chapter_counter', 0)]) + 1)
    blocks[index:index] = [
        heading,
        {'kind': 'slot', 'text': '【待附】营业执照复印件或扫描件。'},
    ]
    return plan
