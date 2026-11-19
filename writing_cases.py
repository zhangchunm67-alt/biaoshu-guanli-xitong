"""Read case evidence as inert, source-addressable text; never execute its contents."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import ntpath
import posixpath
import re
import stat
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import PurePosixPath

from PIL import Image, ImageOps
from pypdf import PdfReader


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_LOGICAL_FILES = 100
MAX_SOURCE_CHARS = 400_000
MAX_ZIP_ENTRIES = 10_000
MAX_ARCHIVE_DEPTH = 3
SUPPORTED = {'.zip', '.doc', '.docx', '.pdf', '.pptx', '.txt', '.md'}
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG_R = '{http://schemas.openxmlformats.org/package/2006/relationships}'
OLE_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'


class _UnsafeUpload(ValueError):
    """A validation failure must reject the entire batch, including partial results."""


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 1024:
        raise _UnsafeUpload('案例文件名为空或过长，请重新命名后上传')
    name = unicodedata.normalize('NFC', name.replace('\\', '/'))
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise _UnsafeUpload('案例文件名含控制字符，请重新命名后上传')
    if name.startswith('/') or ntpath.splitdrive(name)[0]:
        raise _UnsafeUpload('案例包含绝对路径，已拒绝上传')
    parts = name.rstrip('/').split('/')
    if any(p in {'', '.', '..'} for p in parts):
        raise _UnsafeUpload('案例包含不安全路径，已拒绝上传')
    return '/'.join(parts)


def _digest_id(prefix: str, *parts: str) -> str:
    value = '\x00'.join(parts).encode('utf-8')
    return prefix + hashlib.sha256(value).hexdigest()


class _Batch:
    def __init__(self, images=None, images_only=False):
        self.images = images
        self.images_only = images_only
        self.image_bytes = 0
        self.files = []
        self.sources = []
        self.warnings = []
        self.expanded_bytes = 0
        self.logical_files = 0
        self.source_chars = 0
        self.source_ids = set()
        self.file_ids = set()
        self.logical_names = set()

    def warn(self, text):
        if text not in self.warnings:
            self.warnings.append(text)

    def add_image(self, name, digest, location, load, hint='', original_bytes=None):
        if self.images is None:
            return
        if len(self.images) >= 180 or self.image_bytes >= MAX_EXPANDED_BYTES:
            self.warn(f'{name}：已到案例配图容量，其余图片请按需单独上传')
            return
        try:
            with load() as original:
                if original.width * original.height > 48_000_000:
                    raise ValueError()
                if (original_bytes is not None and original.format in {'JPEG', 'PNG'}
                        and original.getexif().get(274, 1) == 1):
                    # Preserve original resolution and colors of submitted artwork.
                    original.load()
                    data = original_bytes()
                else:
                    im = ImageOps.exif_transpose(original)
                    im.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
                    if 'A' in im.getbands() or 'transparency' in im.info:
                        rgba = im.convert('RGBA')
                        im = Image.new('RGB', rgba.size, 'white')
                        im.paste(rgba, mask=rgba.getchannel('A'))
                    output = io.BytesIO()
                    im.convert('RGB').save(output, 'JPEG', quality=90)
                    data = output.getvalue()
        except _UnsafeUpload:
            raise
        except (OSError, ValueError, RuntimeError, Image.DecompressionBombError):
            self.warn(f'{name} · {location}：图片无法用于排版，请核对原件')
            return
        if self.image_bytes + len(data) > MAX_EXPANDED_BYTES:
            self.warn(f'{name}：案例配图超过容量，请将所需图片单独上传')
            return
        source_id = _digest_id('case-source-', name, digest, location)
        if source_id in self.source_ids:
            raise _UnsafeUpload('案例包出现重复来源标识，请移除重复文件后上传')
        self.source_ids.add(source_id)
        source = dict(id=source_id, file_name=name, location=location, text='', image_id=source_id)
        if hint:
            source['caption_hint'] = hint[:200]
        self.sources.append(source)
        self.images[source_id] = data
        self.image_bytes += len(self.images[source['id']])

    def add_source(self, file_name, file_hash, location, text):
        text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
        if not text:
            return
        self.source_chars += len(text)
        if self.source_chars > MAX_SOURCE_CHARS:
            raise _UnsafeUpload(f'案例文本合计超过 {MAX_SOURCE_CHARS:,} 字符，请拆分案例包后上传；未截断内容')
        source_id = _digest_id('case-source-', file_name, file_hash, location)
        if source_id in self.source_ids:
            raise _UnsafeUpload('案例包出现重复来源标识，请移除重复文件后上传')
        self.source_ids.add(source_id)
        self.sources.append(dict(id=source_id, file_name=file_name, location=location, text=text))


def readable_source(source):
    text = source.get('text')
    return bool(isinstance(text, str) and text.strip() and not (
        source.get('image_id') and text.startswith('可引用的案例画面。')))


class _Archive:
    """Validate all entries before reading; stream reads stay inside a shared budget."""
    def __init__(self, raw, batch, file_name):
        if not raw.startswith(b'PK'):
            raise _UnsafeUpload(f'“{file_name}”的内容与扩展名不符，请使用原始格式上传')
        self.batch = batch
        self.file_name = file_name
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(raw))
        except (zipfile.BadZipFile, OSError, ValueError):
            raise ValueError(f'“{file_name}”压缩文件损坏，无法读取') from None
        self.entries = {}
        self.cache = {}
        try:
            infos = self.zip.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise _UnsafeUpload('案例压缩包条目过多，请拆分后上传')
            declared_size = 0
            for entry in infos:
                # orig_filename retains a NUL that ZipInfo.filename otherwise truncates.
                name = _safe_name(entry.orig_filename)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise _UnsafeUpload('案例包含软链，已拒绝上传')
                if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise _UnsafeUpload('案例包含特殊文件，已拒绝上传')
                if entry.flag_bits & 1:
                    raise _UnsafeUpload('不支持加密 ZIP，请解除密码后上传')
                if name in self.entries:
                    raise _UnsafeUpload('案例包含重复文件路径，已拒绝上传')
                if entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED,
                                               zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}:
                    raise _UnsafeUpload('案例包使用不支持的压缩方式，请重新压缩为 ZIP')
                self.entries[name] = entry
                declared_size += entry.file_size
            if batch.expanded_bytes + declared_size > MAX_EXPANDED_BYTES:
                raise _UnsafeUpload('案例包解压总量超过 100 MB，请拆分后上传')
            batch.expanded_bytes += declared_size
        except Exception:
            self.zip.close()
            raise

    def close(self):
        self.zip.close()

    def read(self, name):
        if name in self.cache:
            return self.cache[name]
        declared_size = self.entries[name].file_size
        try:
            with self.zip.open(self.entries[name]) as stream:
                raw = stream.read(declared_size + 1)
        except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError):
            raise ValueError(f'“{self.file_name}/{name}”损坏，无法解压') from None
        if len(raw) != declared_size:
            raise _UnsafeUpload('案例包的解压大小与声明不符，已拒绝上传')
        self.cache[name] = raw
        return raw


def _xml(raw, file_name):
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', raw.replace(b'\x00', b''), flags=re.I):
        raise _UnsafeUpload(f'“{file_name}”包含不安全的 XML 声明，已拒绝上传')
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError(f'“{file_name}”的文档结构损坏，无法读取') from None


def _office_archive(raw, batch, name, kind):
    archive = _Archive(raw, batch, name)
    try:
        main = 'word/document.xml' if kind == 'docx' else 'ppt/presentation.xml'
        if '[Content_Types].xml' not in archive.entries or main not in archive.entries:
            raise _UnsafeUpload(f'“{name}”不是有效的 {kind.upper()}，请核对文件扩展名')
        types = _xml(archive.read('[Content_Types].xml'), name)
        expected = ('application/vnd.openxmlformats-officedocument.'
                    + ('wordprocessingml.document.main+xml' if kind == 'docx'
                       else 'presentationml.presentation.main+xml'))
        actual = {item.get('ContentType', '') for item in types}
        if any('macroenabled' in value.lower() or 'vbaproject' in value.lower() for value in actual):
            raise _UnsafeUpload(f'“{name}”含宏，不支持作为案例上传')
        if expected not in actual:
            raise _UnsafeUpload(f'“{name}”的内容与扩展名不符，请使用原始格式上传')
        return archive
    except Exception:
        archive.close()
        raise


def _linked_images(archive, batch, name, digest, part, sections):
    if batch.images is None:
        return
    folder, filename = posixpath.split(part)
    rels = f'{folder}/_rels/{filename}.rels'
    if rels not in archive.entries:
        return
    targets = {}
    for rel in _xml(archive.read(rels), name):
        if rel.get('Type', '').endswith('/image') and rel.get('TargetMode') != 'External':
            target = rel.get('Target', '')
            path = (target.lstrip('/') if target.startswith('/')
                    else posixpath.normpath(posixpath.join(folder, target)))
            if path in archive.entries:
                targets[rel.get('Id')] = path
    for location, node, hint in sections:
        paths = dict.fromkeys(targets.get(blip.get(R + 'embed')) for blip in node.iter(A + 'blip'))
        for path in filter(None, paths):
            batch.add_image(name, digest, f'{location} · {path}',
                            lambda: Image.open(io.BytesIO(archive.read(path))), hint,
                            original_bytes=lambda: archive.read(path))


def _ocr_office_media(archive, batch, name, digest, prefix=''):
    """Pull text from embedded images so scan-heavy case books stay usable."""
    if batch.images_only:
        return
    import case_ocr
    media = sorted(path for path in archive.entries
                   if '/media/' in path and case_ocr.is_image_name(path))
    embeddings = [path for path in archive.entries if '/embeddings/' in path]
    if embeddings:
        batch.warn(f'{name}：含嵌入 OLE 附件，未转成文字，请另附可读取的案例说明')
    if not media:
        return
    if not case_ocr.available():
        batch.warn(f'{name}：含 {len(media)} 张图片文字未识别（{case_ocr.unavailable_reason()}）。证明材料册中的关键信息可能丢失。')
        return
    budget = case_ocr.media_budget()
    blank = 0
    for index, path in enumerate(media):
        if index >= budget:
            batch.warn(f'{name}：图片超过 {budget} 张，其余未做 OCR，请拆分后上传')
            break
        text = case_ocr.recognize_image(archive.read(path))
        label = f'{prefix}图片 OCR（{PurePosixPath(path).name}）'
        if text:
            batch.add_source(name, digest, label, text)
        else:
            blank += 1
    if blank:
        batch.warn(f'{name}：{blank} 张图片 OCR 未识别出文字，请人工核对原件')


def _word_text(node):
    pieces = []
    for item in node.iter():
        if item.tag == W + 't':
            pieces.append(item.text or '')
        elif item.tag == W + 'tab':
            pieces.append('\t')
        elif item.tag in {W + 'br', W + 'cr'}:
            pieces.append('\n')
    return ''.join(pieces)


def _word_blocks(root, batch, name, digest, prefix=''):
    paragraph_no, table_no = 0, 0

    def walk(node):
        nonlocal paragraph_no, table_no
        for child in node:
            if child.tag == W + 'p':
                paragraph_no += 1
                batch.add_source(name, digest, f'{prefix}第 {paragraph_no} 段', _word_text(child))
            elif child.tag == W + 'tbl':
                table_no += 1
                for row_no, row in enumerate(child.findall(W + 'tr'), 1):
                    cells = []
                    for cell in row.findall(W + 'tc'):
                        paragraphs = [_word_text(p) for p in cell.iter(W + 'p')]
                        cells.append('\n'.join(paragraphs).strip())
                    if any(cells):
                        batch.add_source(name, digest, f'{prefix}表 {table_no} 第 {row_no} 行', ' | '.join(cells))
            else:
                walk(child)
    walk(root)


def _parse_docx(raw, batch, name, digest):
    archive = _office_archive(raw, batch, name, 'docx')
    try:
        root = _xml(archive.read('word/document.xml'), name)
        body = root.find(W + 'body')
        if body is None:
            raise ValueError(f'“{name}”缺少 Word 正文，无法读取')
        _linked_images(archive, batch, name, digest, 'word/document.xml',
                       ((f'正文第 {i} 段', para, _word_text(para)) for i, para in enumerate(body.iter(W + 'p'), 1)))
        if batch.images_only:
            return
        _word_blocks(body, batch, name, digest)
        for part in sorted(archive.entries):
            if re.fullmatch(r'word/(?:header\d+|footer\d+|footnotes|endnotes|comments)\.xml', part):
                root = _xml(archive.read(part), name)
                label = {'footnotes': '脚注', 'endnotes': '尾注', 'comments': '批注'}.get(PurePosixPath(part).stem)
                label = label or ('页眉' if '/header' in part else '页脚')
                _word_blocks(root, batch, name, digest, f'{label}（{PurePosixPath(part).name}）')
        _ocr_office_media(archive, batch, name, digest)
    finally:
        archive.close()


def _parse_pptx(raw, batch, name, digest):
    archive = _office_archive(raw, batch, name, 'pptx')
    try:
        presentation = _xml(archive.read('ppt/presentation.xml'), name)
        relationship_path = 'ppt/_rels/presentation.xml.rels'
        relations = {}
        if relationship_path in archive.entries:
            for item in _xml(archive.read(relationship_path), name):
                if item.tag == PKG_R + 'Relationship' and item.get('TargetMode') != 'External':
                    target = item.get('Target', '')
                    path = target.lstrip('/') if target.startswith('/ppt/') else 'ppt/' + target
                    relations[item.get('Id')] = _safe_name(path)
        slides = []
        for item in presentation.findall(f'{P}sldIdLst/{P}sldId'):
            path = relations.get(item.get(R + 'id'))
            if not path or not re.fullmatch(r'ppt/slides/slide\d+\.xml', path) or path not in archive.entries:
                raise ValueError(f'“{name}”的幻灯片目录损坏，无法读取')
            if path in slides:
                raise _UnsafeUpload(f'“{name}”含重复幻灯片引用，已拒绝上传')
            slides.append(path)
        for number, path in enumerate(slides, 1):
            root = _xml(archive.read(path), name)
            paragraphs = [''.join(t.text or '' for t in para.iter(A + 't')) for para in root.iter(A + 'p')]
            location = f'第 {number} 张幻灯片'
            description = '\n'.join(paragraphs)
            _linked_images(archive, batch, name, digest, path, [(location, root, description)])
            if not batch.images_only:
                batch.add_source(name, digest, location, description)
        if batch.images_only:
            return
        # Speaker notes are kept separately so claims remain traceable to their actual location.
        for path in sorted(archive.entries):
            if re.fullmatch(r'ppt/notesSlides/notesSlide\d+\.xml', path):
                root = _xml(archive.read(path), name)
                paragraphs = [''.join(t.text or '' for t in para.iter(A + 't')) for para in root.iter(A + 'p')]
                batch.add_source(name, digest, f'演讲者备注（{PurePosixPath(path).name}）', '\n'.join(paragraphs))
        _ocr_office_media(archive, batch, name, digest)
    finally:
        archive.close()


def _parse_pdf(raw, batch, name, digest):
    if not raw.lstrip(b'\xef\xbb\xbf\r\n\t ').startswith(b'%PDF-'):
        raise _UnsafeUpload(f'“{name}”不是 PDF 文件，请核对扩展名')
    try:
        import case_ocr
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise _UnsafeUpload(f'“{name}”为加密 PDF，请解除密码后上传')
        empty_pages = []
        ocr_count = 0
        for number, page in enumerate(reader.pages, 1):
            value = page.extract_text() or ''
            if batch.images is not None and page.images.keys():
                batch.add_image(name, digest, f'第 {number} 页 · 案例展示页',
                                lambda: case_ocr.pdf_page_image(raw, number - 1), value.strip())
            if batch.images_only:
                continue
            if value.strip():
                batch.add_source(name, digest, f'第 {number} 页', value)
                continue
            if case_ocr.available() and ocr_count < case_ocr.MAX_IMAGES:
                ocr_count += 1
                ocr_text = case_ocr.recognize_pdf_page(raw, number - 1)
                if ocr_text:
                    batch.add_source(name, digest, f'第 {number} 页 OCR', ocr_text)
                    continue
            empty_pages.append(number)
        if empty_pages:
            numbers = '、'.join(map(str, empty_pages[:20]))
            extra = f'等 {len(empty_pages)} 页' if len(empty_pages) > 20 else '页'
            if not case_ocr.available():
                batch.warn(f'{name}：第 {numbers} {extra}无可读取文字（{case_ocr.unavailable_reason()}）')
            elif ocr_count:
                batch.warn(f'{name}：第 {numbers} {extra} OCR 后仍无文字，请另附可读取文字版')
            else:
                batch.warn(f'{name}：第 {numbers} {extra}无可读取文字；扫描图片请另附文字版')
    except _UnsafeUpload:
        raise
    except Exception:
        raise ValueError(f'“{name}”的 PDF 结构或页面无法读取，请重新导出后上传') from None


def _parse_text(raw, batch, name, digest):
    if raw.startswith((b'PK', OLE_MAGIC, b'%PDF-', b'\x89PNG', b'\xff\xd8\xff', b'GIF8', b'MZ')):
        raise _UnsafeUpload(f'“{name}”的内容与文本扩展名不符，请使用原始格式上传')
    encodings = ['utf-16'] if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
    value = None
    for encoding in encodings:
        try:
            value = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    if value is None or any(ord(c) < 32 and c not in '\n\r\t\f' for c in value):
        raise _UnsafeUpload(f'“{name}”不是可读取的文本，请另存为 UTF-8 文本后上传')
    # One line per source retains exact line positions and all nonempty material.
    for number, line in enumerate(value.splitlines(), 1):
        batch.add_source(name, digest, f'第 {number} 行', line)


def _parse_file(raw, batch, name, depth=0):
    extension = PurePosixPath(name).suffix.lower()
    if extension == '.zip':
        if depth >= MAX_ARCHIVE_DEPTH:
            raise _UnsafeUpload('案例包嵌套过深，请解压整理后上传')
        archive = _Archive(raw, batch, name)
        try:
            if 'word/document.xml' in archive.entries or 'ppt/presentation.xml' in archive.entries:
                raise _UnsafeUpload(f'“{name}”实际是 Office 文档，请恢复 .docx 或 .pptx 扩展名后上传')
            for member, entry in archive.entries.items():
                if entry.is_dir():
                    continue
                if member.startswith('__MACOSX/') or PurePosixPath(member).name == '.DS_Store':
                    batch.warn(f'{name}/{member}：已忽略系统元数据文件')
                    continue
                _parse_file(archive.read(member), batch, f'{name}/{member}', depth + 1)
        finally:
            archive.close()
        return
    batch.logical_files += 1
    if batch.logical_files > MAX_LOGICAL_FILES:
        raise _UnsafeUpload(f'每批最多 {MAX_LOGICAL_FILES} 个案例文件，请拆分后上传')
    if name in batch.logical_names:
        raise _UnsafeUpload('案例包出现重复来源路径，请移除重复文件后上传')
    batch.logical_names.add(name)
    import case_ocr
    if extension not in SUPPORTED and not case_ocr.is_image_name(name):
        batch.warn(f'{name}：不支持此格式，未作为写作资料使用')
        return
    digest = hashlib.sha256(raw).hexdigest()
    before = len(batch.sources)
    try:
        if case_ocr.is_image_name(name) and extension not in SUPPORTED:
            batch.add_image(name, digest, '原始图片', lambda: Image.open(io.BytesIO(raw)), original_bytes=lambda: raw)
            if batch.images_only:
                return
            if not case_ocr.available():
                batch.warn(f'{name}：图片文字未识别（{case_ocr.unavailable_reason()}）')
            else:
                text = case_ocr.recognize_image(raw)
                if text:
                    batch.add_source(name, digest, '图片 OCR', text)
                else:
                    batch.warn(f'{name}：图片 OCR 未识别出文字，请另附可读取说明')
        elif extension == '.doc':
            if not raw.startswith(OLE_MAGIC):
                raise _UnsafeUpload(f'“{name}”不是旧版 DOC 文件，请恢复正确扩展名后上传')
            from word_import import normalize_word
            converted, _ = normalize_word(name, raw, MAX_UPLOAD_BYTES)
            _parse_docx(converted, batch, name, digest)
        elif extension == '.docx':
            _parse_docx(raw, batch, name, digest)
        elif extension == '.pptx':
            _parse_pptx(raw, batch, name, digest)
        elif extension == '.pdf':
            _parse_pdf(raw, batch, name, digest)
        elif not batch.images_only:
            _parse_text(raw, batch, name, digest)
    except _UnsafeUpload:
        raise
    except ValueError as exc:
        # A partial document is never silently accepted as the complete source.
        removed = batch.sources[before:]
        del batch.sources[before:]
        for source in removed:
            if batch.images is not None and source['id'] in batch.images:
                batch.image_bytes -= len(batch.images.pop(source['id']))
            batch.source_ids.remove(source['id'])
            batch.source_chars -= len(source['text'])
        batch.warn(f'{name}：未采用此文件；{exc}')
        return
    if not batch.images_only and len(batch.sources) == before:
        batch.warn(f'{name}：未读取到文字，未作为写作资料使用')


def parse_uploads(files, *, images=None) -> dict:
    """Parse base64 uploads; all file contents remain untrusted evidence, not instructions.

    ``files`` in the result lists top-level uploads. Sources from a ZIP retain its
    name followed by the member path. Validation failures return no partial batch.
    """
    if not isinstance(files, list) or not files:
        raise ValueError('请先上传相关项目案例文件或 ZIP 案例包')
    if len(files) > MAX_LOGICAL_FILES:
        raise ValueError(f'每批最多上传 {MAX_LOGICAL_FILES} 个文件，请拆分后上传')
    batch = _Batch(images=images)
    decoded = []
    total = 0
    encoded_limit = ((MAX_UPLOAD_BYTES + 2) // 3) * 4
    for item in files:
        if not isinstance(item, dict):
            raise ValueError('案例上传数据格式不正确，请重新选择文件')
        name = _safe_name(item.get('name'))
        data = item.get('data')
        if not isinstance(data, str):
            raise ValueError('案例上传数据格式不正确，请重新选择文件')
        if len(data) > encoded_limit:
            raise ValueError('案例上传总量不能超过 50 MB，请拆分后上传')
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(f'“{name}”上传内容不完整，请重新选择文件') from None
        total += len(raw)
        if total > MAX_UPLOAD_BYTES:
            raise ValueError('案例上传总量不能超过 50 MB，请拆分后上传')
        digest = hashlib.sha256(raw).hexdigest()
        file_id = _digest_id('case-file-', name, digest)
        if file_id in batch.file_ids:
            raise ValueError('重复上传了同一案例文件，请移除重复文件')
        batch.file_ids.add(file_id)
        batch.files.append(dict(id=file_id, name=name, size=len(raw), sha256=digest))
        decoded.append((name, raw))
    for name, raw in decoded:
        _parse_file(raw, batch, name)
    if not any(readable_source(source) for source in batch.sources):
        reasons = '；'.join(batch.warnings[:5])
        raise ValueError('案例包未读取到可用文字，请上传文字版 DOCX、PDF、PPTX、TXT 或 MD。' + reasons)
    return dict(files=batch.files, sources=batch.sources, warnings=batch.warnings)
