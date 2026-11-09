"""Read buyer-supplied Word body text, with evidence addresses usable in Word."""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from lxml import etree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
MC = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
O = '{urn:schemas-microsoft-com:office:office}'
NUM = '一二三四五六七八九十百零〇两0-9０-９'
MAJOR = re.compile(rf'^第[{NUM}]+(?:章|篇|部分|卷)\s*')
SCOPE = re.compile(rf'^(?:(?:[（(][{NUM}]+[)）]|[{NUM}]+[、.])\s*)?'
                   rf'((?:子?项目|标包|包件|标段|采购包)\s*[{NUM}]+|第[{NUM}]+(?:标包|包件|包|标段))'
                   r'(?:名称)?\s*(?:[（(]([^（）()]{1,45})[)）])?\s*[：:、]\s*(.+)$')
SHARED = re.compile(r'各(?:子项目|包件|标段)(?:统一|共同)|(?:两|三|多个)(?:个)?(?:子项目|项目|平台|包件)'
                    r'(?:统一|共同|均应|均须|应分别)|(?:本项目统一|通用要求|各标段通用)|'
                    r'^(?:中选原则|评审原则|成交原则|定标原则|评标办法|评分办法|共同要求|通用条款)[：:]?$')
REQUIRE = re.compile(r'应当|须|必须|不得|应提供|不接受|不超过|不少于|未按|无效|视为|按时|星号')
HEADER = re.compile(r'序号|条款号|评[选分审]|指标|(?:满|得|分)值|得\s*分|标准|要求|响应情况|应答|'
                    r'说明|投标人|采购人|项目[名称分类]|内容|名称|单位|数量|附件|页码|考察项目|考核项目|分值分配')
FILE = re.compile(r'[^/\\<>《》【】\r\n]{1,110}\.(?:docx?|xlsx?|pptx?|pdf|zip|rar|7z)(?![A-Za-z])',re.I)
MAX_GRID_COLUMNS = 4096


@dataclass(frozen=True)
class Unit:
    text: str
    kind: str
    cells: tuple
    headers: tuple
    context: str
    scope: str
    locator: str
    part: str
    index: int
    row: int
    is_heading: bool = False
    original_text: str = ''
    selection_uncertain: bool = False


def xml(data):
    return ET.fromstring(data, parser=ET.XMLParser(resolve_entities=False, no_network=True))


def compact(value):
    return re.sub(r'\s+', '', value)


def applicable_choice(text):
    """Read a marked single-choice field. Keep the full evidence separately on Unit."""
    if not re.search(r'[☑☐□]',text):
        return text,False
    normalized=text
    # A filled square used as a list bullet cannot by itself assert a selection.
    if text.count('■')==1 and re.search(r'(?m)^\s*■',text) and re.search(r'(?m)^\s*[☐□]',text):
        normalized=text.replace('■','☑')
    if normalized.count('☑')!=1 or not re.search(r'[☐□]',normalized):
        return text,True
    result=[]
    selected_branch=True
    for line in normalized.splitlines():
        pieces=re.split(r'([☑☐□])',line)
        if len(pieces)==1:
            if line.startswith(('注：','注:', '说明：','备注：')):
                selected_branch=True
            if selected_branch:
                result.append(line)
            continue
        prefix=pieces[0].strip()
        selected=''
        for mark, label in zip(pieces[1::2],pieces[2::2]):
            selected_branch=(mark=='☑')
            if selected_branch:
                selected=label.strip()
        if selected:
            result.append(prefix+selected)
            # An inline yes/no decision is complete on this line. Numbered dependent
            # instructions which follow apply only when the buyer selected the positive option.
            if len(pieces)>3:
                selected_branch=not re.fullmatch(r'(?:无|否|不要求|不设置|无需|不允许)[。；;，,\s]*',selected)
        elif prefix:
            result.append(prefix)
    return '\n'.join(result),False


def normalize_tree(body):
    # Only one branch of a compatibility wrapper is displayed by Word.
    for alternate in list(body.iter(MC+'AlternateContent')):
        parent = alternate.getparent()
        branch = alternate.find(MC+'Choice')
        if branch is None:
            branch = alternate.find(MC+'Fallback')
        if branch is not None:
            for child in list(branch):
                parent.insert(parent.index(alternate), child)
        parent.remove(alternate)
    for tag in ('del', 'moveFrom'):
        for node in list(body.iter(W+tag)):
            node.getparent().remove(node)
    # Cached TOC entries can span paragraphs and contain their own nested PAGEREF fields.
    active = []
    for el in body.iter():
        if el.tag == W+'fldChar':
            typ = el.get(W+'fldCharType')
            if typ == 'begin':
                active.append(False)
            elif typ == 'end' and active:
                active.pop()
        elif el.tag == W+'instrText' and active and re.search(r'\bTOC\b', el.text or '', re.I):
            active[-1] = True
        elif el.tag == W+'t' and any(active):
            el.text = ''
    for el in list(body.iter(W+'fldSimple')):
        if re.match(r'\s*TOC\b',el.get(W+'instr',''),re.I):
            el.getparent().remove(el)


def text_of(el):
    lines = []
    for p in ([el] if el.tag == W+'p' else el.iter(W+'p')):
        # Do not let a nested table's rows consume the surrounding table's columns.
        if el.tag == W+'tc' and next(p.iterancestors(W+'tc'),None) is not el:
            continue
        tokens = []
        for t in p.iter():
            if t.tag == W+'t':
                tokens.append((t.text or '').replace('\uf0a3','☐'))
            elif t.tag == W+'sym' and compact(t.get(W+'font','')).lower()=='wingdings2':
                code=t.get(W+'char','').upper()
                tokens.append({'0052':'☑','F052':'☑','00A3':'☐','F0A3':'☐'}.get(code,''))
            elif t.tag in (W+'tab', W+'br', W+'cr'):
                tokens.append(' ')
        line = re.sub(r'[\t\r \u00a0\u3000]+',' ',''.join(tokens)).strip()
        if line and (el.tag == W+'p' or not any(a.tag == W+'p' for a in p.iterancestors())):
            lines.append(line)
    return '\n'.join(lines)


def named_styles(z):
    if 'word/styles.xml' not in z.namelist():
        return {}
    styles = {}
    for s in xml(z.read('word/styles.xml')).findall(W+'style'):
        name = s.find(W+'name')
        if name is not None:
            styles[s.get(W+'styleId')] = name.get(W+'val','')
    return styles


def heading_level(s, p, style):
    t = compact(s)
    # Mandatory-condition annotations belong to the heading, not its first clause.
    title = re.sub(r'[（(](?:须|需|必须)?(?:同时|全部)?满足[）)](?=[：:]?$)', '', t)
    if not t or len(t)>58 or REQUIRE.search(title.replace('须知', '')) or re.search(r'[。；;！？]',t):
        return None
    if MAJOR.match(s):
        return 1
    if re.fullmatch(rf'附件[{NUM}]+[：:]?',t):
        return 2
    if re.search(r'[:：].{3,}',t):  # a label followed by its actual value is evidence, not a heading
        return None
    if re.match(r'^[一二三四五六七八九十百]+[、．]',s):
        return 2
    if re.match(r'^[０-９0-9]+[、．]',s):
        return 4
    if re.match(rf'^[（(][{NUM}]+[）)]',s):
        return 3
    h = re.match(r'(?:heading|标题)\s*([1-9])',style,re.I)
    outline = p.find('./'+W+'pPr/'+W+'outlineLvl')
    if h:
        return min(5, int(h[1]))
    if outline is not None and outline.get(W+'val','9').isdigit() and int(outline.get(W+'val'))<6:
        return min(5,int(outline.get(W+'val'))+1)
    if len(t)<=30 and re.search(r'前附表|评分表|评分标准|评标办法|资格条件|资格要求|资质要求|招标公告|'
                                r'资格审查|技术规格|技术规范书|采购需求|用户需求书|服务内容|服务要求|'
                                r'合同格式|合同范本|合同模[板版]|文件格式|证明文件|项目需求说明书|评审标准|采购事项说明|'
                                r'须知|供应商条件|服务需求|技术要求|报名条件|技术评分项|报价要求|评标索引表|'
                                r'承诺与声明|投标函|授权委托书|投标有效期|投标文件的组成|报价\s*$|'
                                r'采购程序|应标时间|采购文件的编写要求|确认报价的文件|联系方式|特别声明|^其他$|'
                                r'下列情形|下列条件|以下(?:条件|要求|情形)|如下[：:]$',t.rstrip('：:')):
        return 4
    if len(t)<=20 and t.endswith(('：',':')) and not re.search(r'[（()）]',t):
        return 5
    return None


def scope_item(s):
    match = SCOPE.match(s)
    if not match:
        return None
    code, short, rest = match.groups()
    name = rest.strip('【】《》 。；;')
    declaration = len(name)<=65 and not re.search(r'报价|预算|上限|分值|应提交|必须|均需|最高限价|不得|评分|承诺',name)
    return compact(code), (short or name) if declaration else short, declaration


def scope_alias(s, names):
    """Recover a declared lot from its later numbered-name section heading.

    Only an exact, unique name already declared as 标段/包件 is eligible. An
    unchecked participation list or a vaguely related heading cannot select a lot.
    """
    if len(s) > 80 or re.search(r'[☐□☑■。；;：:]', s):
        return None
    match = re.fullmatch(rf'(?:(?:[{NUM}]+\s*[、.．)）])|(?:[（(][{NUM}]+[)）]))\s*(.+)', s)
    if not match:
        return None
    title = compact(match[1]).strip('《》【】')
    matches = [(code, name) for code, name in names.items() if compact(name).strip('《》【】') == title]
    return (*matches[0], True) if len(matches) == 1 else None


def table_rows(table):
    """Return physical cells with active vertical merges projected by grid column."""
    def grid_value(element, default, minimum):
        raw = element.get(W+'val', str(default)) if element is not None else str(default)
        try:
            value = int(raw) if re.fullmatch(r'[+-]?[0-9]+', raw) else None
        except ValueError:
            value = None
        if value is None or not minimum <= value <= MAX_GRID_COLUMNS:
            raise ValueError('Word 表格列数异常，请在 Word 中修复表格后重新上传')
        return value

    grid = table.find(W+'tblGrid')
    if grid is not None and len(grid.findall(W+'gridCol')) > MAX_GRID_COLUMNS:
        raise ValueError('Word 表格列数异常，请在 Word 中修复表格后重新上传')
    result, previous = [], {}
    for tr in table.findall(W+'tr'):
        before = tr.find('./'+W+'trPr/'+W+'gridBefore')
        col = grid_value(before, 0, 0)
        after = grid_value(tr.find('./'+W+'trPr/'+W+'gridAfter'), 0, 0)
        if col + after > MAX_GRID_COLUMNS:
            raise ValueError('Word 表格列数异常，请在 Word 中修复表格后重新上传')
        values, spans, active = [], [], {}
        for tc in tr.findall(W+'tc'):
            span = tc.find('./'+W+'tcPr/'+W+'gridSpan')
            width = grid_value(span, 1, 1)
            if col + width + after > MAX_GRID_COLUMNS:
                raise ValueError('Word 表格列数异常，请在 Word 中修复表格后重新上传')
            merge = tc.find('./'+W+'tcPr/'+W+'vMerge')
            value = text_of(tc)
            if merge is not None:
                if merge.get(W+'val') != 'restart' and not value:
                    value = previous.get(col,'')
                active.update((i,value) for i in range(col,col+width))
            values.append(value)
            spans.append((col,width))
            col += width
        previous=active
        result.append((values,spans,tr))
    return result


def table_heading(rows):
    for i,(values,spans,tr) in enumerate(rows[:4]):
        strs = [compact(t) for t in values if t]
        if len(strs)<2:
            if len(values)==1:
                continue
            break
        marked = tr.find('./'+W+'trPr/'+W+'tblHeader') is not None
        if marked or (all(len(t)<=58 for t in strs) and any(HEADER.search(t) for t in strs)
                      and not any(re.match(r'^[\d一二三]+(?:\.|、|$)',t) for t in strs)):
            expanded = {j:v for v,(c,n) in zip(values,spans) for j in range(c,c+n)}
            return i, expanded
        break
    return -1, {}


def story(block):
    if block.tag in (W+'p', W+'tbl'):
        yield block
    elif block.tag in (W+'sdt',W+'sdtContent',W+'customXml',W+'ins',W+'moveTo'):
        if block.tag==W+'sdt':
            gallery=block.find('./'+W+'sdtPr/'+W+'docPartObj/'+W+'docPartGallery')
            if gallery is not None and compact(gallery.get(W+'val','')).lower() in ('tableofcontents','目录'):
                return
        for child in block:
            yield from story(child)


def read_units(path):
    units, notes, seen = [], [], set()
    embedded_bytes = 0
    def note(msg):
        if msg not in notes:
            notes.append(msg)
    def read(z, part, inherited_scope, depth):
        nonlocal embedded_bytes
        body = xml(z.read('word/document.xml')).find(W+'body')
        original={node:i for i,block in enumerate(body) for node in block.iter()
                  if node.tag in (W+'p',W+'tbl')}
        normalize_tree(body)
        styles = named_styles(z)
        relpath='word/_rels/document.xml.rels'
        rels = {r.get('Id'):r for r in xml(z.read(relpath))} if relpath in z.namelist() else {}
        blocks = [(original[e],e) for el in body for e in story(el)]
        names, headings, scope, scope_level = {}, {}, inherited_scope, 2
        previous=''
        para_no=table_no=images=0
        unresolved_choices=[]
        def region(): return ' › '.join(headings[level] for level in sorted(headings))
        def tables(t, index, name):
            rr = table_rows(t)
            header, grid = table_heading(rr)
            banners = [values[0] for values,_,_ in rr[:max(header,0)] if len(values)==1 and values[0]]
            tcontext = ' › '.join(filter(None,(region(),*banners)))
            for j,(v,widths,row) in enumerate(rr):
                v=tuple(v)
                if any(v) and j>header:
                    # Align semantic header labels to the geometric columns occupied by this physical row.
                    heads = tuple(grid.get(c,'') for c,_ in widths) if grid else ()
                    original_text=' ｜ '.join(x for x in v if x)
                    decisions=[applicable_choice(cell) for cell in v]
                    applicable=tuple(value for value,_ in decisions)
                    uncertain=any(unsure for _,unsure in decisions)
                    where=f'{name}第{j+1}行'
                    if uncertain:
                        unresolved_choices.append(where)
                    u=Unit(' ｜ '.join(x for x in applicable if x), 'row',applicable,heads,tcontext,scope,
                           where,part,index,j+1,original_text=original_text,selection_uncertain=uncertain)
                    yield u
                for sub_no,inside in enumerate((sub for sub in row.iter(W+'tbl') if next(sub.iterancestors(W+'tbl'),None) is t),1):
                    yield from tables(inside,index,f'{name}第{j+1}行内表{sub_no}')
        for pos,(index,el) in enumerate(blocks):
            ispara=el.tag==W+'p'
            if ispara: para_no+=1
            else: table_no+=1
            txt=text_of(el) if ispara else ''
            loc=f'正文第{para_no}段' if ispara else f'表{table_no}'
            original_text=txt
            txt,uncertain=applicable_choice(txt)
            if uncertain:
                unresolved_choices.append(loc)
            pstyle=el.find('./'+W+'pPr/'+W+'pStyle') if ispara else None
            style=styles.get(pstyle.get(W+'val'),'') if pstyle is not None else ''
            if ispara and (re.match(r'^(?:toc|目录)\s*\d*$',style,re.I)
                           or re.search(r'[…．.·]{3,}\s*\d*\s*$',txt)
                           or compact(txt) in ('目录','总目录') or txt.startswith(('TAG_START_', 'TAG_END_'))):
                continue
            level=heading_level(txt,el,style) if ispara else None
            lot=scope_item(txt) if ispara else None
            alias=scope_alias(txt,names) if ispara and not lot else None
            if alias:
                lot=alias
            unit_scope=scope
            listed_scope=False
            if lot:
                code,label,declared=lot
                if declared and label:names[code]=label
                identified=names.get(code,label)
                unit_scope=code+(' · '+identified if identified else '')
                if declared:
                    # Consecutive names are a listing; common requirements immediately after it are unscoped.
                    lookahead=''
                    for _, following_block in blocks[pos+1:]:
                        # A table is substantive lot content, even when the next
                        # paragraph names another lot. Do not skip across it.
                        if following_block.tag != W+'p':
                            break
                        lookahead=text_of(following_block)
                        if lookahead:
                            break
                    prior=scope_item(previous)
                    following=scope_item(lookahead)
                    listed_scope=not alias and bool((following and following[2]) or (prior and prior[2]))
                    if not listed_scope:
                        scope=unit_scope
                        scope_level=level or 2
                    level=level or 3
            elif (level and level<=scope_level) or SHARED.search(txt):
                if scope != inherited_scope and SHARED.search(txt):
                    headings={k:v for k,v in headings.items() if k<scope_level}
                scope=inherited_scope
                unit_scope=scope
            if level and not listed_scope:
                headings={k:v for k,v in headings.items() if k<level}
                headings[level]=txt
            if ispara and txt:
                units.append(Unit(txt,'paragraph',(),(),region(),unit_scope,loc,part,index,0,bool(level),
                                  original_text,uncertain))
            elif not ispara:
                units.extend(tables(el,index,loc))
            if any(x.tag.split('}')[-1] in ('blip','imagedata') for x in el.iter() if isinstance(x.tag,str)):
                images+=1
            for link in el.iter(W+'hyperlink'):
                rel=rels.get(link.get(R+'id'))
                if rel is None or rel.get('TargetMode')!='External':continue
                target=unquote(urlsplit(rel.get('Target','')).path)
                label=''.join(t.text or '' for t in link.iter(W+'t')).strip()
                if FILE.search(label) or FILE.search(target.rsplit('/',1)[-1]):
                    note(f'{part} · {loc} 引用外部附件“{label or target.rsplit("/",1)[-1]}”，仅统计本文已显示文字。')
            if ispara and not any(True for _ in el.iter(W+'hyperlink')) and not any(True for _ in el.iter(O+'OLEObject')):
                filename=re.search(r'(附件\s*[一二三四五六七八九十\d]+[^\n。；;]{0,90}?\.(?:docx?|xlsx?|pptx?|pdf|zip|rar))(?![A-Za-z])',txt,re.I)
                if filename:
                    note(f'{part} · {loc} 提及“{filename[1]}”，此处仅有文件名称，未扫描外部文件。')
            for obj in el.iter(O+'OLEObject'):
                rel=rels.get(obj.get(R+'id'))
                label=previous[:55] or '嵌入附件'
                inner_part=f'{part} › {label}（{loc}）'
                if rel is None or rel.get('TargetMode')=='External':
                    note(f'{inner_part} 是未内附的对象，仅统计外层要求。');continue
                target=unquote(rel.get('Target',''))
                member=posixpath.normpath(posixpath.join('word',target)).lstrip('/')
                if member not in z.namelist() or depth>=3:
                    note(f'{inner_part} 未读取对象正文。');continue
                size=z.getinfo(member).file_size
                if size>32*1024*1024 or embedded_bytes+size>96*1024*1024:
                    note(f'{inner_part} 超过嵌入内容读取上限，未展开其条款。');continue
                embedded_bytes+=size
                payload=z.read(member)
                key=(sha256(payload).digest(),unit_scope)
                if key in seen:continue
                seen.add(key)
                try:
                    # Some Word OLE .bin packages enclose an intact OOXML ZIP. Probe content,
                    # never invoke Word, OLE or a macro handler to obtain the original.
                    with ZipFile(BytesIO(payload)) as child:
                        if 'word/document.xml' not in child.namelist():
                            raise BadZipFile('no Word body')
                        readable={'word/document.xml','word/styles.xml','word/_rels/document.xml.rels'}
                        if sum(child.getinfo(n).file_size for n in readable.intersection(child.namelist())) > 16*1024*1024:
                            note(f'{inner_part} 解压后的 Word 正文过大，未展开其条款。');continue
                        read(child,inner_part,unit_scope,depth+1)
                except (BadZipFile, KeyError, ET.XMLSyntaxError, RuntimeError, NotImplementedError):
                    note(f'{inner_part} 为 {obj.get("ProgID","二进制")} 对象，未能作为可读 Word 正文解析。')
            if txt:previous=txt
            elif not ispara:previous=''
        if images:note(f'{part} 有 {images} 处含图片或对象预览，图片内文字未作 OCR。')
        if unresolved_choices:
            note(f'{part} 有 {len(unresolved_choices)} 处未识别唯一勾选（{"、".join(unresolved_choices[:3])}），保留原选项，适用性待核实。')
    with ZipFile(Path(path)) as z:
        read(z,'主件','',0)
    return units,notes
