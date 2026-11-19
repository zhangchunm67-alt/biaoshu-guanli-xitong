"""Case image references and ordered visual inserts shared by preview, review and Word."""
from collections import defaultdict
import re

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image

IMAGE_ID = re.compile(r'case-source-[0-9a-f]{64}')
BAD_TEXT = re.compile(r'[\x00-\x1f\ud800-\udfff\ufffe\uffff]')


def save_images(directory, images):
    for ident, data in images.items():
        suffix = '.png' if data.startswith(b'\x89PNG') else '.jpg'
        (directory / (ident + suffix)).write_bytes(data)


def image_path(store, key, image_id):
    if not isinstance(image_id, str) or not IMAGE_ID.fullmatch(image_id):
        raise ValueError('案例图片编号无效。')
    project = store.require('project', key)
    owner = project.get('chapter_image_origins', {}).get(image_id, project['id'])
    directory = store.path('writing-cases/' + owner)
    path = next((p for p in directory.glob('*/' + image_id + '.*') if p.suffix in {'.jpg', '.png'}), None)
    if path is None:
        raise ValueError('未找到本项目引用的案例原图，请核对案例包。')
    return path


def inherit_chapters(project, previous, chapters):
    """Retain only pictures belonging to explicitly migrated chapter bookmarks."""
    for old_id, new_id in chapters.items():
        blocks = previous.get('chapter_visuals', {}).get(old_id)
        if not blocks:
            continue
        project.setdefault('chapter_visuals', {})[new_id] = blocks
        for block in blocks:
            if block['kind'] == 'image':
                ident = block['image_id']
                project.setdefault('chapter_image_origins', {})[ident] = (
                    previous.get('chapter_image_origins', {}).get(ident, previous['id']))


def schema():
    string = {'type': 'string'}
    strings = {'type': 'array', 'items': string}
    variants = []
    for kind, extra in [('image', {'image_id': string}), ('flow', {'steps': strings}),
                        ('table', {'headers': strings, 'rows': {'type': 'array', 'items': strings}})]:
        fields = {'kind': {'type': 'string', 'enum': [kind]}, 'after': {'type': 'integer'},
                  'caption': string, **extra}
        variants.append({'type': 'object', 'properties': fields, 'required': list(fields),
                         'additionalProperties': False})
    return {'type': 'array', 'items': {'anyOf': variants}}


def _label(value, limit):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= limit
            and not BAD_TEXT.search(value))


def prepare(row, sources):
    """Validate model references and produce one searchable plain-text projection with offsets."""
    visuals = row.get('visuals', [])
    if not isinstance(visuals, list) or len(visuals) > 32:
        raise ValueError('本章图表清单无效。')
    if not visuals:
        return
    paragraphs = [p.strip() for p in row['text'].splitlines() if p.strip()]
    inserts = defaultdict(list)
    for item in visuals:
        if not isinstance(item, dict):
            raise ValueError('本章图表结构无效。')
        kind, after, caption = (item.get(k) for k in ('kind', 'after', 'caption'))
        if type(after) != int or not 1 <= after <= len(paragraphs) or not _label(caption, 240):
            raise ValueError('模型返回的图题或图表段落位置无效。')
        block = dict(kind=kind, caption=caption)
        lines = [caption]
        if kind == 'image':
            image_id = item.get('image_id')
            source = sources.get(image_id) if isinstance(image_id, str) else None
            if not source or source.get('image_id') != image_id or not IMAGE_ID.fullmatch(image_id):
                raise ValueError('候选正文引用了案例包以外的图片。')
            block.update(image_id=image_id, source=source['file_name'] + ' · ' + source['location'])
            lines.append('来源：' + block['source'])
            if image_id not in row['source_refs']:
                row['source_refs'].append(image_id)
        elif kind == 'flow':
            steps = item.get('steps')
            if not isinstance(steps, list) or not 2 <= len(steps) <= 24 or not all(_label(v, 160) for v in steps):
                raise ValueError('流程图步骤无效。')
            block['steps'] = steps
            lines.extend(steps)
        elif kind == 'table':
            headers, rows = item.get('headers'), item.get('rows')
            if (not isinstance(headers, list) or not 2 <= len(headers) <= 8
                    or not all(_label(h, 80) for h in headers)
                    or not isinstance(rows, list) or not 1 <= len(rows) <= 80
                    or any(not isinstance(r, list) or len(r) != len(headers)
                           or not all(_label(v, 500) for v in r) for r in rows)):
                raise ValueError('交付表列数或单元格内容无效。')
            block.update(headers=headers, rows=rows)
            lines.extend(' ｜ '.join(r) for r in [headers, *rows])
        else:
            raise ValueError('未知的正文图表类型。')
        block['lines'] = lines
        inserts[after].append(block)
    expanded, prepared = [], []
    for number, paragraph in enumerate(paragraphs, 1):
        expanded.append(paragraph)
        for block in inserts[number]:
            block['start'] = len(expanded)
            expanded.extend(block['lines'])
            prepared.append(block)
    row['text'] = '\n'.join(expanded)
    if len(row['text']) > 50000:
        raise ValueError('含图表的单章正文超过 50000 字。')
    row['visuals'] = prepared


def align(text, visuals):
    """Preserve intact visual blocks when a human edits surrounding paragraphs."""
    lines = text.split('\n')
    result, end = [], 0
    for block in visuals:
        match, start = block['lines'], block['start']
        size = len(match)
        if start < end or lines[start:start+size] != match:
            positions = [i for i in range(end, len(lines)) if lines[i:i+size] == match]
            if len(positions) != 1:
                continue
            start = positions[0]
        result.append(dict(block, start=start))
        end = start + size
    return result


def write_block(document, anchor, block, resolve_image):
    """Insert native tables, flow boxes or an inline picture at the current Word anchor."""
    def place(element):
        nonlocal anchor
        anchor.addnext(element)
        anchor = element

    def paragraph(value, *, keep=False):
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_with_next = keep
        p.paragraph_format.space_after = Pt(4)
        p.add_run(value)
        place(p._p)
        return p

    if block['kind'] == 'image':
        path = resolve_image(block['image_id'])
        with Image.open(path) as im:
            width, height = im.size
        section = document.sections[0]
        available = section.page_width - section.left_margin - section.right_margin
        max_height = section.page_height - section.top_margin - section.bottom_margin - Cm(2)
        p = paragraph('', keep=True)
        p.runs[0].add_picture(str(path), width=int(min(available, Cm(15), max_height * width / height)))
        paragraph(block['caption'], keep=True)
        paragraph('来源：' + block['source'])
        return anchor

    paragraph(block['caption'], keep=True)
    flow = block['kind'] == 'flow'
    data = ([[step] for step in block['steps']] if flow else [block['headers'], *block['rows']])
    table = document.add_table(rows=0, cols=len(data[0]))
    table.autofit = False
    for i, values in enumerate(data):
        if flow and i:
            arrow = table.add_row().cells[0].paragraphs[0]
            arrow.alignment = WD_ALIGN_PARAGRAPH.CENTER
            arrow.paragraph_format.keep_with_next = True
            arrow.add_run('↓')
        row = table.add_row()
        props = row._tr.get_or_add_trPr()
        props.append(OxmlElement('w:cantSplit'))
        if not flow and not i:
            props.append(OxmlElement('w:tblHeader'))
        for cell, value in zip(row.cells, values):
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(3)
            p.paragraph_format.space_before = Pt(3)
            if flow:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(value)
            run.font.size = Pt(10)
            run.bold = flow or i == 0
            shade = OxmlElement('w:shd')
            shade.set(qn('w:fill'), 'DFEBF1' if flow else '274D62' if i == 0 else 'EFF4F6' if i % 2 else 'FFFFFF')
            cell._tc.get_or_add_tcPr().append(shade)
            if not flow and i == 0:
                run.font.color.rgb = RGBColor.from_string('FFFFFF')
    place(table._tbl)
    return anchor
