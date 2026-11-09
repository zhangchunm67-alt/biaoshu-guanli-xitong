#!/usr/bin/env python3
"""Inspect a tender DOCX, or assemble selected response forms from a JSON plan."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import tempfile
from urllib.parse import unquote
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
DOC = 'word/document.xml'
DOC_RELS = 'word/_rels/document.xml.rels'
NS = {'w': W, 'r': R, 'mc': MC, 'wp': WP}
OFFICE_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.'


def w(tag):
    return '{%s}%s' % (W, tag)


def node(tag, parent=None, **attrs):
    e = ET.Element(w(tag)) if parent is None else ET.SubElement(parent, w(tag))
    for key, value in attrs.items():
        e.set(w(key), str(value))
    return e


def xml(e):
    return ET.tostring(e, encoding='UTF-8', xml_declaration=True, standalone=True)


def words(e):
    return ''.join(e.xpath('.//w:t/text()', namespaces=NS))


def detach(e):
    e.getparent().remove(e)


def add_run(p, text):
    r = node('r', p)
    for n, line in enumerate(text.split('\n')):
        if n:
            node('br', r)
        t = node('t', r)
        t.set(XML_SPACE, 'preserve')
        t.text = line
    return r


def paragraph(text='', style=None):
    p = node('p')
    if style:
        node('pStyle', node('pPr', p), val=style)
    if text:
        add_run(p, text)
    return p


def field_part(p, value):
    node('fldChar', node('r', p), fldCharType=value, **({'dirty': 'true'} if value == 'begin' else {}))


def make_toc(headings,width):
    if not headings:
        raise ValueError('目录需要至少一个 heading 块')
    yield paragraph('目录', 'BFCover')
    ps = []
    for seq, heading in enumerate(headings, 1):
        level, title, prefix = heading[:3]
        bookmark = heading[3] if len(heading) > 3 else f'BidSection{seq}'
        p = paragraph(style='BFToc')
        pp = p.find(w('pPr'))
        node('tab',node('tabs',pp),val='right',leader='dot',pos=width-90)
        node('ind', pp, left=260 * (level - 1))
        if not ps:
            field_part(p, 'begin')
            instr = node('instrText', node('r', p))
            instr.set(XML_SPACE, 'preserve')
            style_map = ','.join(f'Bid Outline {i},{i}' for i in range(1, 10))
            instr.text = f' TOC \\h \\z \\t "{style_map}" '
            field_part(p, 'separate')
        link = node('hyperlink', p, anchor=bookmark)
        add_run(link, prefix + title)
        node('tab',node('r',p))
        page=node('fldSimple',p,instr=f' PAGEREF {bookmark} \\h ',dirty='true')
        add_run(page,'待更新')
        ps.append(p)
    field_part(ps[-1], 'end')
    yield from ps


def style_def(styles, ident, name, size, *, level=None, center=False, bold=False,
              before=None, after=None, line=None, left=None):
    found = styles.xpath('w:style[@w:styleId=$id]', namespaces=NS, id=ident)
    if found:
        raise ValueError('输入文档与生成样式重名：' + ident)
    st = node('style', styles, type='paragraph', styleId=ident)
    node('name', st, val=name)
    pp = node('pPr', st)
    if level or center:
        node('keepNext', pp)
        node('keepLines', pp)
    node('spacing', pp,
         before=160 if before is None and level else (0 if before is None else before),
         after=110 if after is None and (level or center) else (60 if after is None else after),
         line=280 if line is None and (level or center) else (260 if line is None else line),
         lineRule='auto')
    if center:
        node('jc', pp, val='center')
    if left:
        node('ind', pp, left=left)
    if level:
        node('outlineLvl', pp, val=level-1)
    rp = node('rPr', st)
    node('rFonts', rp, ascii='宋体', hAnsi='宋体', eastAsia='宋体', cs='宋体')
    if bold:
        node('b', rp)
    node('color', rp, val='000000')
    node('sz', rp, val=size)
    node('szCs', rp, val=size)


def cover_field_paragraph(label, value='________'):
    """Bottom cover line: plain label + underlined writable value."""
    p = paragraph(style='BFCoverField')
    if label:
        add_run(p, label)
    run = add_run(p, value or '________')
    props = ET.Element(w('rPr'))
    run.insert(0, props)
    node('u', props, val='single')
    return p


def requires_song_five(body):
    """Recognize 宋体五号 (10.5pt) only when it is an instruction for response body text."""
    font_pair = r'(?:宋体.{0,12}?(?<!小)五号|(?<!小)五号.{0,12}?宋体)'
    scope = r'正文|(?:投标|响应|应答|参选)文件|标书'
    for p in body.iter(w('p')):
        for sentence in re.split(r'[。；;！？]', re.sub(r'\s+', '', words(p))):
            for match in re.finditer(font_pair, sentence):
                before = sentence[:match.start()]
                contexts = list(re.finditer(scope, before))
                if not contexts:
                    continue
                excerpt = sentence[contexts[-1].start():match.end()]
                if len(excerpt) <= 65 and not re.search(
                    r'封面|标题|目录|页眉|页脚|表头|禁止|不得使用|不(?:宜|得|可)?(?:采用|用)|仿宋体', excerpt):
                    return True
    return False


def make_table(rows, width, *, height=None, repeat_header=True):
    if not rows or not rows[0] or any(len(r) != len(rows[0]) for r in rows):
        raise ValueError('table 需要非空矩形行列')
    count = len(rows[0])
    tbl = node('tbl')
    pr = node('tblPr', tbl)
    node('tblW', pr, w=width, type='dxa')
    node('jc', pr, val='center')
    borders = node('tblBorders', pr)
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        node(edge, borders, val='single', sz=5, color='000000')
    node('tblLayout', pr, type='fixed')
    grid = node('tblGrid', tbl)
    widths = [width*(i+1)//count-width*i//count for i in range(count)]
    form_labels = ' '.join(str(s) for row in rows for s in row)
    signature = (1 < len(rows) <= 4 and len(form_labels) < 180
                 and re.search(r'签[字章]|盖(?:公)?章',form_labels)
                 and re.search(r'日\s*期|年\s*月\s*日',form_labels))
    for cw in widths:
        node('gridCol', grid, w=cw)
    for i, vals in enumerate(rows):
        tr = node('tr', tbl)
        trpr = node('trPr', tr)
        if height or signature:
            node('cantSplit', trpr)
        if height:
            node('trHeight', trpr, val=height, hRule='atLeast')
        if i == 0 and repeat_header:
            node('tblHeader', trpr, val=1)
        for val, cw in zip(vals, widths):
            tc = node('tc', tr)
            tcp = node('tcPr', tc)
            node('tcW', tcp, w=cw, type='dxa')
            node('vAlign', tcp, val='center')
            p = paragraph(val, 'BFCell')
            if signature and i+1<len(rows):
                node('keepNext',p.find(w('pPr')))
            tc.append(p)
    return tbl


def source_copy(el, latest_heading, width, previous_text='', preserve_format=False):
    """Replace duplicate Word/WPS renderings of ID-card boxes with inline boxes."""
    el = deepcopy(el)
    if el.tag not in (w('p'), w('tbl'), w('sdt')):
        return []
    # The unchecked box is commonly encoded as a private symbol with no
    # platform fallback. A normal Unicode square keeps the same form choice.
    for sym in el.xpath('.//w:sym[@w:font="Wingdings 2"]',namespaces=NS):
        if sym.get(w('char'),'').upper() in ('00A3','F0A3'):
            t=node('t')
            t.text='□'
            sym.getparent().replace(sym,t)
    for item in el.xpath('.//w:sectPr|.//w:commentRangeStart|'
                         './/w:commentRangeEnd|.//w:commentReference|.//w:del|.//w:moveFrom|'
                         './/w:dataBinding|.//w:lastRenderedPageBreak', namespaces=NS):
        detach(item)
    if preserve_format:
        # Preserve every cell and dimension while preventing a short original
        # table row (especially its header) from being split between pages.
        for row in el.iter(w('tr')):
            props = row.find(w('trPr'))
            if props is None:
                props = node('trPr')
                row.insert(0, props)
            if props.find(w('cantSplit')) is None:
                node('cantSplit', props)
        return [el]
    boxes = []
    if el.tag == w('p'):
        # Some templates use an empty, floating VML rectangle after a printed
        # attachment caption. Convert only that identifiable paste area.
        if not words(el).strip() and re.search('底单|粘贴|复印件', previous_text):
            shapes = el.xpath('.//*[local-name()="roundrect" or local-name()="rect"]')
            if len(shapes)==1 and not relation_ids(shapes[0]):
                style=shapes[0].get('style','')
                size=dict(re.findall(r'(height|width):\s*([\d.]+)pt',style))
                if 'width' in size and 'height' in size:
                    return [make_table([['']],min(width,round(float(size['width'])*20)),
                                       height=round(float(size['height'])*20),repeat_header=False)]
        for ac in el.iter('{%s}AlternateContent' % MC):
            branches = ac.xpath('mc:Choice[.//w:txbxContent][1]', namespaces=NS)
            if not branches:
                branches = ac.xpath('mc:Fallback[.//w:txbxContent]', namespaces=NS)
            frames = branches[0].xpath('.//w:txbxContent', namespaces=NS) if branches else []
            if not frames or not all('身份证' in words(fr) and '粘贴' in words(fr) for fr in frames):
                continue
            sizes = branches[0].xpath('.//wp:extent', namespaces=NS)
            for i, frame in enumerate(frames):
                fw, fh = width, 3150
                if i < len(sizes):
                    fw = min(width, round(int(sizes[i].get('cx')) / 635))
                    fh = min(5800, round(int(sizes[i].get('cy')) / 635))
                boxes.append(make_table([[words(frame)]], fw, height=fh, repeat_header=False))
            detach(ac)
    text = words(el).strip()
    if boxes and (not text or re.sub(r'^附件\s*[一二三四五六七八九十\d]+[：:、.\s]*', '', text).strip() == latest_heading):
        return boxes
    return [el, *boxes]


def fit_platform_matrix(tbl):
    """Rebalance an equal-width, unmerged portal grid when long prose dominates a column."""
    grid = tbl.findall('w:tblGrid/w:gridCol',NS)
    rows = [r.findall(w('tc')) for r in tbl.findall(w('tr'))]
    if len(grid) < 5 or len(rows) < 2 or any(len(row) != len(grid) for row in rows):
        return
    if tbl.xpath('w:tr/w:tc/w:tcPr/w:gridSpan|w:tr/w:tc/w:tcPr/w:hMerge|w:tr/w:tc/w:tcPr/w:vMerge',namespaces=NS):
        return
    original = [int(c.get(w('w'), '0')) for c in grid]
    if min(original) <= 0 or max(original) > min(original)*1.35:
        return
    lengths = [max(len(words(cell).strip()) for cell in column) for column in zip(*rows[1:])]
    if max(lengths) < 60 or max(lengths) < 5*max(1,sorted(lengths)[len(grid)//2]):
        return
    # Retain space for numeric and presently empty response fields. Allocate the
    # remaining width by prose length, with diminishing returns on long passages.
    weights = [max(1,min(len(words(header))/2,5),min(40,length**0.7))
               for header,length in zip(rows[0],lengths)]
    total = sum(original)
    minimum = min(680,int(total/len(grid)*0.55))
    spare = total - minimum*len(grid)
    weight_sum = sum(weights)
    widths = [minimum+round(spare*weight/weight_sum) for weight in weights]
    widths[weights.index(max(weights))] += total - sum(widths)
    percentages = [round(5000*width/total) for width in widths]
    percentages[weights.index(max(weights))] += 5000 - sum(percentages)
    for j,col in enumerate(grid):
        col.set(w('w'), str(widths[j]))
        for row in rows:
            cell_width = row[j].find('w:tcPr/w:tcW',NS)
            if cell_width is not None:
                percent = cell_width.get(w('type')) == 'pct'
                cell_width.set(w('type'), 'pct' if percent else 'dxa')
                cell_width.set(w('w'), str(percentages[j] if percent else widths[j]))


def edit_cells(tbl, edits):
    rows = tbl.findall(w('tr'))
    for edit in edits:
        row, col = edit['row'], edit['col']
        if not (0 <= row < len(rows) and 0 <= col < len(rows[row].findall(w('tc')))):
            raise ValueError('cells 的行列序号超出表格范围')
        cell = rows[row].findall(w('tc'))[col]
        ps = cell.findall(w('p'))
        if not ps or cell.find(w('tbl')) is not None:
            raise ValueError('cells 不支持整格替换嵌套表格')
        if 'paragraph_match' in edit:
            matches = [p for p in ps if words(p).strip() == edit['paragraph_match']]
            if len(matches) != 1:
                raise ValueError('cells 的 paragraph_match 必须唯一命中一个原始段落')
            target = matches[0]
            if any(child.tag not in (w('pPr'), w('r')) for child in target) or any(
                    child.tag not in (w('rPr'), w('t')) for run in target.findall(w('r')) for child in run):
                raise ValueError('paragraph_match 只能替换不含域或图形的纯文字段落')
            texts = list(target.iter(w('t')))
            if not texts:
                raise ValueError('paragraph_match 匹配的段落没有文字')
            texts[0].text = edit['text']
            for text in texts[1:]:
                text.text = ''
            continue
        for p in ps:
            for child in list(p):
                if child.tag != w('pPr'):
                    p.remove(child)
        add_run(ps[0], edit['text'])
        for p in ps[1:]:
            detach(p)


def score_table(tbl, header_row, width):
    grid = tbl.find(w('tblGrid'))
    rows = tbl.findall(w('tr'))
    if grid is None or not 0 <= header_row < len(rows):
        raise ValueError('score 需要原表格和正确的表头行')
    header = rows[header_row]
    if any(re.search(r'页码', words(c)) for c in header.findall(w('tc'))):
        return
    cols = grid.findall(w('gridCol'))
    total = sum(int(c.get(w('w'), '0')) for c in cols)
    if not total:
        raise ValueError('评分表没有可分配的列宽')
    available = min(total, width)
    added = 610
    ratio = (available-added)/total
    for col in cols:
        col.set(w('w'), str(max(1, round(int(col.get(w('w'))) * ratio))))
    node('gridCol', grid, w=added)
    for i, row in enumerate(rows):
        for cw in row.xpath('w:tc/w:tcPr/w:tcW[@w:type="dxa"]',namespaces=NS):
            cw.set(w('w'), str(max(1, round(int(cw.get(w('w'))) * ratio))))
        if i < header_row and len(row.findall(w('tc'))) == 1:
            tcpr = row.find('w:tc/w:tcPr', NS)
            gs = tcpr.find(w('gridSpan'))
            if gs is not None:
                gs.set(w('val'),str(int(gs.get(w('val'))) + 1))
                continue
        last_cell = row.findall(w('tc'))[-1]
        source_borders = last_cell.find('w:tcPr/w:tcBorders',NS)
        cell = node('tc', row)
        cell_props = node('tcPr',cell)
        node('tcW',cell_props,w=added,type='dxa')
        if source_borders is not None:
            cell_props.append(deepcopy(source_borders))
        cell.append(paragraph('页码' if i==header_row else '', 'BFCell'))


def relation_path(part):
    directory, name = posixpath.split(part)
    return posixpath.join(directory, '_rels', name+'.rels')


def relation_ids(element):
    return {v for e in element.iter() for k,v in e.attrib.items()
            if k.startswith('{'+R+'}') or k == '{urn:schemas-microsoft-com:office:office}relid'}


def relationship(root, ident, kind, target):
    ET.SubElement(root, '{%s}Relationship' % PKG, Id=ident, Type=f'{R}/{kind}', Target=target)


def clean_package(z, doc, styles, settings, footer, section_footer_id):
    # Start from the new body and copy its relationship closure, not all of the invitation.
    types = ET.fromstring(z.read('[Content_Types].xml'))
    rels = ET.fromstring(z.read(DOC_RELS))
    rootrels = ET.Element('{%s}Relationships'%PKG, nsmap={None:PKG})
    relationship(rootrels,'rId1','officeDocument',DOC)
    files = {'_rels/.rels':xml(rootrels), DOC:xml(doc)}
    input_names = set(z.namelist())
    footer_path = 'word/bidResponseFooter.xml'
    while footer_path in input_names:
        footer_path = footer_path[:-4]+'X.xml'
    files[footer_path] = xml(footer)
    ET.SubElement(types,'{%s}Override'%CT,PartName='/'+footer_path,ContentType=OFFICE_MIME+'footer+xml')
    implicit = {'styles','stylesWithEffects','numbering','fontTable','theme','settings'}
    main_ids = relation_ids(doc) - {section_footer_id}
    note_ids = {name:{e.get(w('id')) for e in doc.iter(w(name+'Reference'))} for name in ('footnote','endnote')}
    for rel in list(rels):
        kind = rel.get('Type').split('/')[-1]
        used_notes = kind in ('footnotes','endnotes') and note_ids[kind[:-1]]
        if kind not in implicit and rel.get('Id') not in main_ids and not used_notes:
            rels.remove(rel)
    present = {rel.get('Id') for rel in rels}
    if main_ids-present:
        raise ValueError(f'选定表单含缺失的关系：{main_ids-present}')
    relationship(rels,section_footer_id,'footer',posixpath.basename(footer_path))
    files[DOC_RELS] = xml(rels)
    queue = [(DOC,rel) for rel in rels]
    while queue:
        owner, rel = queue.pop()
        if rel.get('TargetMode')=='External':
            continue
        target = unquote(rel.get('Target').split('#')[0])
        part = posixpath.normpath(posixpath.join(posixpath.dirname(owner),target)).lstrip('/')
        if part in files:
            continue
        if part not in input_names:
            raise ValueError(f'源文件缺少关系部件：{part}')
        kind = rel.get('Type').split('/')[-1]
        data = z.read(part)
        if owner == DOC and kind == 'styles':
            data = xml(styles)
        if owner == DOC and kind == 'settings':
            data = xml(settings)
        if owner == DOC and kind in ('footnotes','endnotes'):
            notes = ET.fromstring(data)
            for n in list(notes):
                if n.get(w('id')) not in note_ids[kind[:-1]] and n.get(w('type')) not in ('separator','continuationSeparator'):
                    notes.remove(n)
            data = xml(notes)
        files[part] = data
        rp = relation_path(part)
        if rp not in input_names:
            continue
        sub = ET.fromstring(z.read(rp))
        if part.endswith('.xml'):
            ids = relation_ids(ET.fromstring(data))
            for r in list(sub):
                if r.get('Id') not in ids and r.get('Type').split('/')[-1] not in ('chartStyle','chartColorStyle'):
                    sub.remove(r)
        if len(sub):
            files[rp] = xml(sub)
            queue.extend((part,r) for r in sub)
    for e in list(types):
        if e.get('PartName') and unquote(e.get('PartName')).lstrip('/') not in files:
            types.remove(e)
    files['[Content_Types].xml'] = xml(types)
    return files


def create_numbering(numbering):
    abstract_id = max([int(x.get(w('abstractNumId'))) for x in numbering.findall(w('abstractNum'))]+[-1])+1
    ident = max([int(x.get(w('numId'))) for x in numbering.findall(w('num'))]+[0])+1
    levels = node('abstractNum', abstractNumId=abstract_id)
    node('multiLevelType', levels, val='multilevel')
    for idx in range(9):
        lvl=node('lvl',levels,ilvl=idx)
        node('start',lvl,val=1)
        node('numFmt',lvl,val='decimal')
        node('suff',lvl,val='space')
        node('lvlText',lvl,val='.'.join('%'+str(i+1) for i in range(idx+1))+'.')
        node('lvlJc',lvl,val='left')
    existing=numbering.find(w('num'))
    if existing is None:
        numbering.append(levels)
    else:
        existing.addprevious(levels)
    node('abstractNumId',node('num',numbering,numId=ident),val=abstract_id)
    return ident


def portrait_section(body):
    source = next((e for e in reversed(list(body.iter(w('sectPr'))))
                   if e.find(w('pgSz')) is not None
                   and e.find(w('pgSz')).get(w('orient')) != 'landscape'), None)
    s = node('sectPr')
    for name in ('pgSz','pgMar'):
        child = source.find(w(name)) if source is not None else None
        if child is not None:
            s.append(deepcopy(child))
    if s.find(w('pgSz')) is None:
        node('pgSz', s, w=11906,h=16838)
    if s.find(w('pgMar')) is None:
        node('pgMar', s, top=1440,right=1440,bottom=1440,left=1440,header=720,footer=720,gutter=0)
    return s


def layout(elements, body, base, footer_id, landscape_tables, join_volume_headings=False):
    pg, mar = base.find(w('pgSz')), base.find(w('pgMar'))
    portrait = int(pg.get(w('w')))
    long_side = int(pg.get(w('h')))
    margins = int(mar.get(w('left')))+int(mar.get(w('right')))
    if portrait-margins <= 0:
        raise ValueError('源文档页边距无效')
    def section(wide):
        s = deepcopy(base)
        ref = node('footerReference',type='default')
        ref.set('{%s}id'%R, footer_id)
        s.insert(0,ref)
        if wide:
            pg = s.find(w('pgSz'))
            pg.set(w('orient'),'landscape')
            pg.set(w('h'), str(portrait))
            pg.set(w('w'), str(long_side))
        return s

    # A wide original form and its label/signatures should share the same section.
    batches, batch = [], []
    page_before = False
    for typ, e in elements:
        grouped = False
        if join_volume_headings and typ == 'heading' and batch:
            levels = [p.find('w:pPr/w:outlineLvl', NS) for p in batch]
            child_level = e.find('w:pPr/w:outlineLvl', NS)
            grouped = (all(level is not None for level in levels) and child_level is not None and
                       int(levels[-1].get(w('val'))) < int(child_level.get(w('val'))))
        if typ in ('heading','toc','break') and batch and not grouped:
            batches.append((page_before,batch))
            batch = []
            page_before = False
        if typ == 'break':
            page_before = True
            continue
        batch.append(e)
    if batch:
        batches.append((page_before,batch))
    prev_wide = None
    for page_before,batch in batches:
        # A negatively indented portrait cover can exceed the text width.
        # Only the source section, not a grid-width guess, selects landscape.
        wide = any(e in landscape_tables for e in batch)
        if prev_wide is not None and prev_wide != wide:
            s = section(prev_wide)
            p = paragraph()
            node('pPr',p).append(s)
            body.append(p)
        elif page_before and len(body):
            if batch[0].tag == w('p'):
                pp = batch[0].find(w('pPr'))
                if pp is None:
                    pp = node('pPr')
                    batch[0].insert(0,pp)
                if pp.find(w('pageBreakBefore')) is None:
                    pp.insert(sum(e.tag in (w('pStyle'),w('keepNext'),w('keepLines')) for e in pp),
                              node('pageBreakBefore'))
            else:
                p = paragraph()
                node('br',node('r',p),type='page')
                body.append(p)
        for item in batch:
            if item.tag==w('tbl'):
                pr=item.find(w('tblPr'))
                anchor=pr.find(w('tblpPr')) if pr is not None else None
                if not len(body) and anchor is not None and anchor.get(w('horzAnchor'))=='page':
                    indent=pr.find(w('tblInd'))
                    if indent is not None and anchor.get(w('tblpX')):
                        indent.set(w('w'),str(int(anchor.get(w('tblpX')))-int(mar.get(w('left')))))
                for anchor in item.xpath('w:tblPr/w:tblpPr|w:tblPr/w:tblOverlap',namespaces=NS):
                    detach(anchor)
                # Keep the short form label and its project number on the page
                # with the first row. Many sources use ordinary body styles.
                for before in reversed(list(body)[-3:]):
                    label=words(before).strip()
                    if before.tag!=w('p') or not re.fullmatch(
                        r'(?:(?:项目|招标|采购|标段)(?:名称|编号|号)[：:].{0,60}|.{0,36}表(?:[（(].{0,12}[）)])?)',label):
                        break
                    props=before.find(w('pPr'))
                    if props is None:
                        props=node('pPr')
                        before.insert(0,props)
                    if props.find(w('keepNext')) is None:
                        props.insert(int(props.find(w('pStyle')) is not None),node('keepNext'))
                # Keep a short generated declaration and its signature table
                # together. Do not bind ordinary source paragraphs or long forms.
                if item.xpath('w:tr/w:tc/w:p/w:pPr[w:pStyle[@w:val="BFCell"]]/w:keepNext',namespaces=NS):
                    for before in reversed(list(body)[-2:]):
                        if before.tag!=w('p') or not re.match(r'(?:【待核实并填写】)?承诺内容[：:]|采购要求[：:]',words(before)):
                            break
                        props=before.find(w('pPr'))
                        if props is None or len(words(before))>110:
                            break
                        if props.find(w('keepNext')) is None:
                            props.insert(int(props.find(w('pStyle')) is not None),node('keepNext'))
            if len(body) and body[-1].tag == item.tag == w('tbl'):
                spacer = paragraph()
                node('spacing',node('pPr',spacer),before=0,after=0,line=80,lineRule='exact')
                body.append(spacer)
            body.append(item)
        prev_wide=wide
    body.append(section(bool(prev_wide)))


def locate(z, kind):
    rels = ET.fromstring(z.read(DOC_RELS))
    rel = next((r for r in rels if r.get('Type').endswith('/'+kind)),None)
    if rel is None:
        return None
    return posixpath.normpath(posixpath.join('word',unquote(rel.get('Target')))).lstrip('/')


def build(source, plan, output):
    if output.resolve()==source.resolve():
        raise ValueError('输出路径不得覆盖招标原件')
    if not isinstance(plan.get('blocks'),list) or not plan['blocks']:
        raise ValueError('计划必须含非空 blocks')
    if plan.get('source_sha256') and plan['source_sha256'].lower()!=hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError('计划所用招标文件与当前文件指纹不同')
    with ZipFile(source) as z:
        doc=ET.fromstring(z.read(DOC))
        body=doc.find(w('body'))
        song_five_required=requires_song_five(body)
        original=list(body)
        base=portrait_section(body)
        landscape_indices=set()
        is_landscape=False
        for i in range(len(original)-1,-1,-1):
            sect=next(original[i].iter(w('sectPr')),None)
            if sect is not None and sect.find(w('pgSz')) is not None:
                is_landscape=sect.find(w('pgSz')).get(w('orient'))=='landscape'
            if is_landscape:
                landscape_indices.add(i)
        landscape_tables=set()
        pg, mar = base.find(w('pgSz')), base.find(w('pgMar'))
        width=int(pg.get(w('w')))-int(mar.get(w('left')))-int(mar.get(w('right')))
        for old in original:
            body.remove(old)
        style_path=locate(z,'styles')
        if not style_path:
            raise ValueError('源文件缺少样式部件；请先在 Word 另存为 DOCX')
        styles=ET.fromstring(z.read(style_path))
        for i in range(1,10):
            style_def(styles,'BFHeading'+str(i),'Bid Outline '+str(i),32 if i==1 else 28 if i==2 else 24,level=i,bold=True)
        style_def(styles,'BFCover','Bid Cover',48,center=True,bold=True,before=200,after=200)
        style_def(styles,'BFCoverMeta','Bid Cover Meta',28,center=True,bold=True,before=80,after=80,line=360)
        style_def(styles,'BFCoverBadge','Bid Cover Badge',22,before=0,after=80,line=320)
        style_def(styles,'BFCoverField','Bid Cover Field',24,before=60,after=60,line=360,left=1400)
        style_def(styles,'BFCoverSpace','Bid Cover Space',22,before=0,after=0,line=280)
        style_def(styles,'BFBody','Bid Body',21 if song_five_required else 24)
        style_def(styles,'BFSlot','Bid Attachment',21 if song_five_required else 22)
        style_def(styles,'BFCell','Bid Table Cell',21)
        style_def(styles,'BFToc','Bid Contents',22)
        numbering_path=locate(z,'numbering')
        numbering=ET.fromstring(z.read(numbering_path)) if numbering_path else ET.Element(w('numbering'),nsmap={'w':W})
        numid=create_numbering(numbering)
        settings=ET.Element(w('settings'),nsmap={'w':W})
        node('updateFields',settings,val='true')
        source_settings=locate(z,'settings')
        if source_settings:
            raw=ET.fromstring(z.read(source_settings))
            compat=raw.find(w('compat'))
            if compat is not None:
                settings.append(deepcopy(compat))
        relroot=ET.fromstring(z.read(DOC_RELS))
        footer_id='rIdBidFooter'
        while any(r.get('Id')==footer_id for r in relroot):
            footer_id+='X'
        footer=ET.Element(w('ftr'),nsmap={'w':W})
        fp=node('p',footer)
        node('jc',node('pPr',fp),val='center')
        add_run(node('fldSimple',fp,instr=' PAGE '),'1')

        counts=[0]*9
        headings=[]
        bookmarks=set()
        element_list=[]
        source_elements=[]
        copy_generation=0
        previous_source_index=-1
        last_heading=''
        toc_count=0
        for ix, block in enumerate(plan['blocks']):
            kind=block['kind']
            produced=[]
            if kind=='copy' or kind=='score':
                if block.get('preserve_format') and (kind=='score' or any(k in block for k in ('cells','row_order','fit_platform_matrix'))):
                    raise ValueError('固定原表不得同时设置改单元格、删行或调整列宽')
                start=block['index'] if kind=='score' else block['start']
                end=start if kind=='score' else block.get('end',start)
                if not (isinstance(start,int) and isinstance(end,int) and 0<=start<=end<len(original)):
                    raise ValueError(f'第 {ix+1} 块没有有效来源区间')
                for n in range(start,end+1):
                    if n <= previous_source_index:
                        copy_generation+=1
                    previous_source_index=n
                    previous = words(original[n-1]).strip() if n else ''
                    selected=source_copy(original[n],last_heading,width,previous,block.get('preserve_format',False))
                    if n in landscape_indices:
                        landscape_tables.update(e for e in selected if e.tag==w('tbl'))
                    produced.extend(selected)
                    source_elements.extend((element,copy_generation) for element in selected)
                if kind=='score' or block.get('cells') or 'row_order' in block:
                    if len(produced)!=1 or produced[0].tag!=w('tbl'):
                        raise ValueError('score、cells 或 row_order 只能指向一张表')
                    if 'row_order' in block:
                        table = produced[0]
                        rows = table.findall(w('tr'))
                        order = block['row_order']
                        if (not isinstance(order, list) or not order or order[0] != 0 or
                                any(type(i) is not int or i < 0 or i >= len(rows) for i in order) or
                                0 in order[1:] or order != sorted(order)):
                            raise ValueError('row_order 必须从表头 0 开始，按原行序选择或重复明细行')
                        expanded = [deepcopy(rows[i]) for i in order]
                        position = table.index(rows[0])
                        for row in rows:
                            table.remove(row)
                        for offset, row in enumerate(expanded):
                            table.insert(position + offset, row)
                    if block.get('cells'):
                        edit_cells(produced[0],block['cells'])
                    if kind=='score':
                        score_table(produced[0],block.get('header_row',0),width)
                if block.get('fit_platform_matrix'):
                    if len(produced)!=1 or produced[0].tag!=w('tbl'):
                        raise ValueError('平台列宽调整只适用于单张采购表')
                    fit_platform_matrix(produced[0])
            elif kind=='heading':
                level=block.get('level',1)
                if level not in range(1,10):
                    raise ValueError('heading 的 level 应为 1–9')
                title=block['text'].strip()
                if not title:
                    raise ValueError('标题不能为空')
                prefix=''
                p=paragraph(style='BFHeading'+str(level))
                pp=p.find(w('pPr'))
                if block.get('numbered',True) and not re.match(r'^(\d+[.、]\s*|[一二三四五六七八九十]+、|第.{1,5}[篇章节部分])',title):
                    counts[level-1]+=1
                    counts[level:]=[0]*(9-level)
                    prefix='.'.join(str(max(1,n)) for n in counts[:level])+'. '
                    npr=node('numPr',pp)
                    node('ilvl',npr,val=level-1)
                    node('numId',npr,val=numid)
                if block.get('page_before'):
                    pp.insert(1,node('pageBreakBefore'))
                node('outlineLvl',pp,val=level-1)
                seq=len(headings)+1
                bookmark=block.get('bookmark',f'BidSection{seq}')
                if not isinstance(bookmark,str) or not re.fullmatch(r'BidSection[1-9][0-9]{0,27}',bookmark):
                    raise ValueError('章节书签须使用唯一的 BidSection 正整数编号')
                if bookmark in bookmarks:
                    raise ValueError('章节书签重复，请保留每章唯一标识')
                bookmarks.add(bookmark)
                node('bookmarkStart',p,id=seq,name=bookmark)
                add_run(p,title)
                node('bookmarkEnd',p,id=seq)
                headings.append((level,title,prefix,bookmark))
                last_heading=title
                produced=[p]
            elif kind=='badge':
                table=make_table([[block['text']]],min(1800,width),height=660,repeat_header=False)
                table.find('w:tblPr/w:jc',NS).set(w('val'),'right')
                pp=table.find('.//w:pPr',NS)
                node('jc',pp,val='center')
                produced=[table]
            elif kind=='cover_badge':
                p=paragraph(block['text'],'BFCoverBadge')
                node('jc',p.find(w('pPr')),val='right')
                produced=[p]
            elif kind=='cover_meta':
                produced=[paragraph(block['text'],'BFCoverMeta')]
            elif kind=='cover_space':
                count=block.get('count',1)
                if type(count) is not int or not 1<=count<=20:
                    raise ValueError('cover_space 的 count 应为 1–20')
                produced=[paragraph(style='BFCoverSpace') for _ in range(count)]
            elif kind=='cover_field':
                label=block.get('label')
                value=block.get('value','________')
                if label is None and block.get('text'):
                    label, value = block['text'], ''
                if not isinstance(label,str) or not isinstance(value,str):
                    raise ValueError('cover_field 需要字符串 label 与 value')
                produced=[cover_field_paragraph(label,value)]
            elif kind in ('title','text','slot'):
                sty={'title':'BFCover','text':'BFBody','slot':'BFSlot'}[kind]
                produced=[paragraph(block['text'],sty)]
            elif kind=='toc':
                toc_count+=1
                if toc_count>1:
                    raise ValueError('单个框架仅支持一个目录')
                produced=[None]
            elif kind=='break':
                p=paragraph()
                node('br',node('r',p),type='page')
                produced=[p]
            elif kind=='table':
                height=block.get('min_height')
                if height is not None and (type(height) is not int or not 1<=height<=9000):
                    raise ValueError('min_height 应为 1 至 9000 twips 的正整数')
                produced=[make_table(block['rows'],width,height=height,
                                     repeat_header=block.get('repeat_header',True))]
            else:
                raise ValueError('未知块类型：'+kind)
            element_list.extend((kind,el) for el in produced)
        from source_references import repair_references
        repair_references(source_elements,len(headings))
        expanded=[]
        for kind,el in element_list:
            if kind=='toc':
                expanded.extend(('toc' if j==0 else 'toc_row',p) for j,p in enumerate(make_toc(headings,width)))
            else:
                expanded.append((kind,el))
        layout(expanded,body,base,footer_id,landscape_tables,
               join_volume_headings=plan.get('strategy') == 'sectioned-volume-forms')
        for idx,draw in enumerate(body.iter('{%s}docPr'%WP),1):
            draw.set('id',str(idx))
        files=clean_package(z,doc,styles,settings,footer,footer_id)
        # Add absent implicit relationships for minimal but valid source packages.
        outrels=ET.fromstring(files[DOC_RELS])
        types=ET.fromstring(files['[Content_Types].xml'])
        for name,path,part in [('numbering',numbering_path,numbering),('settings',source_settings,settings)]:
            if path is None:
                path='word/bidResponse'+name+'.xml'
                relationship(outrels,'rIdBid'+name,name,posixpath.basename(path))
                ET.SubElement(types,'{%s}Override'%CT,PartName='/'+path,ContentType=OFFICE_MIME+name+'+xml')
            files[path]=xml(part)
        files[DOC_RELS]=xml(outrels)
        files['[Content_Types].xml']=xml(types)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent,suffix='.docx',delete=False) as temp:
        tmp=Path(temp.name)
    try:
        with ZipFile(tmp,'w',ZIP_DEFLATED) as dest:
            for path,data in files.items():
                dest.writestr(path,data)
        os.replace(tmp,output)
    finally:
        tmp.unlink(missing_ok=True)


def inventory(source,start,end):
    with ZipFile(source) as z:
        body=ET.fromstring(z.read(DOC)).find(w('body'))
        print('SHA256',hashlib.sha256(source.read_bytes()).hexdigest())
        for i,el in enumerate(body):
            if i<start or end is not None and i>end:
                continue
            e=deepcopy(el)
            for ac in e.iter('{%s}AlternateContent'%MC):
                branches=list(ac)
                for branch in branches[1:]:
                    ac.remove(branch)
            if e.tag==w('tbl'):
                lines=[' | '.join(words(c) for c in row.findall(w('tc'))) for row in e.findall(w('tr'))]
                print(f'{i}\ttbl\t'+'\n'.join(f'[{r}] {line}' for r,line in enumerate(lines)))
            else:
                print(f'{i}\t{ET.QName(e).localname}\t{words(e)}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    ins=sub.add_parser('inspect')
    ins.add_argument('source',type=Path)
    ins.add_argument('--start',type=int,default=0)
    ins.add_argument('--end',type=int)
    gen=sub.add_parser('build')
    gen.add_argument('source',type=Path)
    gen.add_argument('plan',type=Path)
    gen.add_argument('output',type=Path)
    args=parser.parse_args()
    if args.command=='inspect':
        inventory(args.source,args.start,args.end)
    else:
        build(args.source,json.loads(args.plan.read_text(encoding='utf-8')),args.output)
        print(args.output)


if __name__=='__main__':
    main()
