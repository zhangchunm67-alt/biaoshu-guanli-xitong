"""Store manual responses against the generated Word framework's stable chapter bookmarks."""
import re
from datetime import datetime, timezone

import writing_visuals
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph


CHAPTER_LIMIT = 50_000
PROJECT_LIMIT = 1_000_000


def remaining_capacity(project, chapter_ids):
    targets = set(chapter_ids)
    return PROJECT_LIMIT - sum(len(text) for ident, text in project.get('chapter_texts', {}).items()
                               if ident not in targets)


def check_capacity(project, changes):
    available = remaining_capacity(project, changes)
    size = sum(len(text.replace('\r\n', '\n').strip()) for text in changes.values())
    if size > available:
        raise ValueError(f'选中章节正文超出项目 100 万字上限 {size - available:,} 字，请缩写或编辑已保存章节。')


def _sections(document):
    for paragraph in document.paragraphs:
        properties = paragraph._p.find(qn('w:pPr'))
        if properties is None:
            continue
        style = properties.find(qn('w:pStyle'))
        style_id = style.get(qn('w:val'), '') if style is not None else ''
        if not re.fullmatch(r'BFHeading[1-9]', style_id):
            continue
        for mark in paragraph._p.findall(qn('w:bookmarkStart')):
            key = mark.get(qn('w:name'), '')
            if re.fullmatch(r'BidSection[1-9][0-9]*', key):
                yield key, int(style_id[-1]), paragraph
                break


def apply_decimal_numbering(document):
    """Normalize previously generated headings and TOC entries in a new export."""
    import framework_service
    framework_service.metric_module()
    from outline_check import decimal_headings

    def replace_text(element, label):
        runs = list(element.iter(qn('w:t')))
        if runs and ''.join(run.text or '' for run in runs) != label:
            runs[0].text = label
            for run in runs[1:]:
                run.text = ''

    sections = list(_sections(document))
    rows = [{'level': level, 'text': paragraph.text} for _, level, paragraph in sections]
    titles = {}
    for (key, _, paragraph), (number, title) in zip(sections, decimal_headings(rows)):
        titles[key] = f'{number} {title}'
        replace_text(paragraph._p, titles[key])
        properties = paragraph._p.find(qn('w:pPr'))
        numbering = properties.find(qn('w:numPr'))
        if numbering is not None:
            properties.remove(numbering)
    for paragraph in document.paragraphs:
        properties = paragraph._p.find(qn('w:pPr'))
        style = properties.find(qn('w:pStyle')) if properties is not None else None
        if style is None or style.get(qn('w:val')) != 'BFToc':
            continue
        for link in paragraph._p.findall(qn('w:hyperlink')):
            title = titles.get(link.get(qn('w:anchor')))
            if title:
                replace_text(link, title)


def read_chapters(store, key):
    project = store.require('project', key)
    document = Document(store.path(project['template_storage']))
    texts = project.get('chapter_texts', {})
    chapters = [dict(id=key, title=p.text, level=level, text=texts.get(key, ''),
                     revision=project.get('chapter_revisions', {}).get(key, 0))
                for key, level, p in _sections(document)]
    for chapter in chapters:
        visuals = project.get('chapter_visuals', {}).get(chapter['id'])
        if visuals:
            chapter['visuals'] = visuals
    return {'chapters': chapters, 'revision': project.get('chapter_revision', 0)}


def save_chapters(store, key, body, *, adopted_visuals=None, commit=True):
    with store.lock:
        return _save_chapters(store, key, body, adopted_visuals=adopted_visuals, commit=commit)


def _save_chapters(store, key, body, *, adopted_visuals, commit):
    project = store.require('project', key)
    framework = store.get('framework', project.get('framework_id', ''))
    if framework and framework.get('projects', {}).get('word') != key:
        raise ValueError('目录已更新，此工作稿已归档；请重新打开当前整本项目后修改')
    if framework and framework.get('review_version') and not framework.get('requirements_confirmed'):
        raise ValueError('请先重新确认招标要求，再保存正文')
    result = read_chapters(store, key)
    if type(body.get('revision')) != int or body['revision'] != result['revision']:
        from errors import ConflictError
        raise ConflictError('正文已在其他窗口更新，请重新打开项目后核对再保存。')
    changes = body.get('chapters')
    allowed = {c['id']: c for c in result['chapters']}
    if not isinstance(changes, list) or not changes or len(changes) > len(allowed):
        raise ValueError('请选择要保存的目录章节。')
    seen = set()
    for change in changes:
        section = change.get('id') if isinstance(change, dict) else None
        if not isinstance(section, str) or section not in allowed or section in seen:
            raise ValueError('选择的章节重复或不属于当前框架。')
        text = change.get('text')
        if (not isinstance(text, str) or len(text) > CHAPTER_LIMIT or
                re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', text)):
            raise ValueError('每章正文最多 50000 字，且不能包含无效字符。')
        allowed[section]['text'] = text.replace('\r\n', '\n').replace('\r', '\n').strip()
        seen.add(section)
    texts = {c['id']: c['text'] for c in result['chapters'] if c['text']}
    if sum(map(len, texts.values())) > PROJECT_LIMIT:
        raise ValueError('此项目在线编写的正文已超过 100 万字。')
    project = store.require('project', key)
    previous = project.get('chapter_texts', {})
    old_visuals = project.get('chapter_visuals', {})
    visuals = dict(old_visuals)
    for section in seen:
        blocks = (adopted_visuals[section] if adopted_visuals is not None
                  else writing_visuals.align(texts.get(section, ''), old_visuals.get(section, [])))
        if blocks:
            visuals[section] = blocks
        else:
            visuals.pop(section, None)
    if previous != texts or visuals != old_visuals:
        revisions = project.setdefault('chapter_revisions', {})
        for chapter in result['chapters']:
            section = chapter['id']
            if (previous.get(section, '') != texts.get(section, '')
                    or old_visuals.get(section) != visuals.get(section)):
                revisions[section] = revisions.get(section, 0) + 1
            chapter['revision'] = revisions.get(section, 0)
            chapter.pop('visuals', None)
            if section in visuals:
                chapter['visuals'] = visuals[section]
        result['revision'] += 1
        project.update(chapter_texts=texts, chapter_visuals=visuals, chapter_revision=result['revision'],
                       status='编写中', updated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        store.put('project', project)
        if commit:
            store.db.commit()
    result['mapping_revision'] = store.mapping_revision(store.require('project', key))
    return result


def insert_responses(document, texts, visuals=None, resolve_image=None):
    """Use only original section anchors. Fixed forms and the TOC stay intact."""
    sections = {key: p for key, _, p in _sections(document)}
    if not texts.keys() <= sections.keys():
        raise ValueError('部分章节已无法定位，请重新核对目录和正文。')
    for key, text in texts.items():
        anchor = sections[key]._p
        # Replace a single generated writing prompt in situ. Leave multiple
        # prompts, proof-material slots and buyer-supplied forms for review.
        prompts = []
        for block in anchor.itersiblings():
            if block.tag != qn('w:p'):
                continue
            style = block.find(qn('w:pPr') + '/' + qn('w:pStyle'))
            style_id = style.get(qn('w:val'), '') if style is not None else ''
            if re.fullmatch(r'BFHeading[1-9]', style_id):
                break
            if style_id == 'BFSlot' and Paragraph(block, document._body).text.startswith('【待编】'):
                prompts.append(block)
        if len(prompts) == 1:
            anchor = prompts[0]
        blocks = {b['start']: b for b in (visuals or {}).get(key, [])}
        skip_to = 0
        for position, line in enumerate(text.split('\n')):
            if position < skip_to:
                continue
            if position in blocks:
                block = blocks[position]
                anchor = writing_visuals.write_block(document, anchor, block, resolve_image)
                skip_to = position + len(block['lines'])
                continue
            element = OxmlElement('w:p')
            anchor.addnext(element)
            paragraph = Paragraph(element, document._body)
            # This style is supplied by the vendored framework in each generated DOCX.
            paragraph._p.get_or_add_pPr().get_or_add_pStyle().val = 'BFBody'
            paragraph.add_run(line)
            anchor = element
        if len(prompts) == 1:
            document.element.body.remove(prompts[0])
