"""Compose one editable master framework from the procurement planner's document blocks."""


def build_unified_plan(plan):
    """Return the build plan and the old-to-new chapter bookmarks, keyed by part name."""
    if not plan.get('parts'):
        return plan, {}

    blocks = []
    main = plan['blocks']
    for block in main:
        # These are planner-generated labels. Keep buyer text and submission tables.
        if block['kind'] == 'title' and block['text'] == '分件编制说明':
            block = dict(block, text='投标文件编制框架')
        elif block['kind'] == 'heading' and block['text'] == '分件清单':
            block = dict(block, text='子项目及递交文件清单')
        blocks.append(block)
    if not any(b['kind'] == 'toc' for b in blocks):
        start = next((i for i, b in enumerate(blocks) if b['kind'] == 'heading'), len(blocks))
        blocks[start:start] = [{'kind': 'break'}, {'kind': 'toc'}, {'kind': 'break'}]

    # The procurement planner creates two cover titles for each lot response.
    # Use them as headings. Reviewed plans without that convention keep their own blocks.
    groups = {}
    seen = set()
    for part in plan['parts']:
        name, content = part['name'], part['blocks']
        if not name.strip() or name in seen or not any(b['kind'] == 'heading' for b in content):
            raise ValueError('独立响应内容缺少可识别的名称或章节，请核对编排计划。')
        seen.add(name)
        cover = content[:2]
        grouped = (len(cover) == 2 and all(b['kind'] == 'title' for b in cover)
                   and name == cover[0]['text'] + '_' + cover[1]['text'])
        parent = cover[0]['text'] if grouped else None
        groups.setdefault((parent, None if grouped else name), []).append(part)

    sequence = sum(b['kind'] == 'heading' for b in blocks)
    bookmarks = {}
    for (parent, _), parts in groups.items():
        if parent is not None:
            blocks.append(dict(parts[0]['blocks'][0], kind='heading', level=1))
            sequence += 1
        for part in parts:
            name, content = part['name'], part['blocks']
            if parent is not None:
                container_level = 2
                blocks.append(dict(content[1], kind='heading', level=container_level))
                content = content[2:]
            else:
                container_level = 1
                blocks.append({'kind': 'heading', 'text': name, 'level': container_level})
            sequence += 1
            old_ids = {}
            for block in content:
                if block['kind'] == 'toc':
                    continue
                if block['kind'] == 'heading':
                    block = dict(block, level=min(9, block.get('level', 1) + container_level))
                    sequence += 1
                    old_ids[f'BidSection{len(old_ids) + 1}'] = f'BidSection{sequence}'
                blocks.append(block)
            bookmarks[name] = old_ids

    note = '各子项目在本框架中统一编制。正式递交时，请按采购原件对所投子项目的方案、报价、签章及分别递交要求办理。'
    unified = {key: value for key, value in plan.items() if key != 'parts'}
    unified.update(blocks=blocks, review=[*plan.get('review', []), note])
    return unified, bookmarks
