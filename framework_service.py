"""Bind the bundled, source-only bid framework skill to the local project store."""
from __future__ import annotations

import hashlib
import copy
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile, is_zipfile

from docx import Document
from lxml import etree
import olefile

import chapter_drafts
import framework_review
from unified_plan import build_unified_plan
from word_import import normalize_word

SCRIPTS = Path(__file__).resolve().parent / 'skills/bid-response-framework/scripts'
MAX_TENDER = 50 * 1024 * 1024
WORD_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
GROUPS = ('redline', 'qualification', 'score', 'spec', 'format')
REVIEW_VERSION = 3


def metric_module():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import tender_metrics
    return tender_metrics


def outline_check(record):
    """目录校验（序号乱序、重复、跳号、混用自动编号、层级错位、附件序号、同级重名）。"""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from outline_check import check_outline
    return check_outline(record.get('outline') or [])


def require_outline_check_ack(record, body):
    result = outline_check(record)
    if result['issues'] and body.get('outline_check_acknowledged') is not True:
        raise ValueError(f'目录校验发现 {len(result["issues"])} 项序号或结构问题。请按提示修正后保存目录，'
                         '或逐项核对后勾选“已核对目录校验提示”再确认。')


def ensure_required_sections(plan):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from required_sections import ensure_business_license
    ensure_business_license(plan)
    framework_review.assign_bookmarks(plan)
    return plan


def organize_books(plan, source, requirements, source_document=None):
    ensure_required_sections(plan)
    from plan_framework import Source
    from response_books import organize
    plan = organize(plan, source_document or Source(source), requirements)
    framework_review.assign_bookmarks(plan)
    # Keep generated requirement groups tied to their new book across later edits.
    book = None
    for block in plan['blocks']:
        if block['kind'] != 'heading':
            continue
        if block.get('book_role'):
            book = block['bookmark']
        elif block.get('review_group') and book:
            block['review_group'] = block['review_group'].split('@book:')[0] + '@book:' + book
    return plan


def check_procurement_xml(name, raw):
    """Do not allow active/external objects into downloadable or editable drafts."""
    root = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError('采购文件包含不受支持的 XML 声明')
    # Consumers expand merged cells into logical columns. Bound geometry before
    # python-docx or the requirement extractor can allocate those columns.
    for namespace in ('http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                      'http://purl.oclc.org/ooxml/wordprocessingml/main'):
        tag = '{' + namespace + '}'
        counts = {}
        for kind, minimum in (('gridSpan', 1), ('gridBefore', 0), ('gridAfter', 0)):
            for element in root.iter(tag + kind):
                number = element.get(tag + 'val', '').strip()
                if not re.fullmatch(r'[+-]?[0-9]+', number):
                    raise ValueError('Word 表格列数异常')
                try:
                    value = int(number)
                except (ValueError, TypeError):
                    raise ValueError('Word 表格列数异常') from None
                if not minimum <= value <= 4096:
                    raise ValueError('Word 表格列数异常')
                counts[element] = value
        for grid in root.iter(tag + 'tblGrid'):
            if len(grid.findall(tag + 'gridCol')) > 4096:
                raise ValueError('Word 表格列数异常')
        for row in root.iter(tag + 'tr'):
            columns = sum(counts[e] for kind in ('gridBefore', 'gridAfter')
                          for e in row.findall(f'{tag}trPr/{tag}{kind}'))
            for cell in row.findall(tag + 'tc'):
                spans = cell.findall(f'{tag}tcPr/{tag}gridSpan')
                columns += sum(counts[e] for e in spans) if spans else 1
            if columns > 4096:
                raise ValueError('Word 表格列数异常')
    if name.lower().endswith('.rels'):
        if any(rel.get('TargetMode', '').lower() == 'external'
               and not rel.get('Type', '').endswith('/hyperlink') for rel in root):
            raise ValueError('采购文件包含外部链接对象，请将正式表单作为原件内附后重试')
    if name.lower() == '[content_types].xml':
        if any(any(token in part.get('ContentType', '').lower()
                   for token in ('macroenabled', 'vbaproject', 'activex', 'macrosheet')) for part in root):
            raise ValueError('采购文件含有宏或活动控件，请提供无宏的采购原件')


def check_attachment(raw):
    """Allow only inert Word/Excel attachments, including common OLE wrappers."""
    if not is_zipfile(io.BytesIO(raw)):
        with olefile.OleFileIO(io.BytesIO(raw)) as ole:
            streams = {tuple(s.lower() for s in path): path for path in ole.listdir()}
            if ('package',) in streams:
                allowed = {'package', '\x01ole', '\x01compobj', '\x03objinfo',
                           '\x05summaryinformation', '\x05documentsummaryinformation'}
                if any(len(path) != 1 or path[0] not in allowed for path in streams):
                    raise ValueError('内嵌 Office 附件含有不支持的对象，请单独核对原件')
                raw = ole.openstream(streams[('package',)]).read()
            else:
                allowed = {'worddocument', '0table', '1table', 'data', 'wpscustomdata',
                           '\x01ole', '\x01compobj', '\x03objinfo', '\x05summaryinformation',
                           '\x05documentsummaryinformation'}
                if ('worddocument',) not in streams or any(
                        len(path) != 1 or path[0] not in allowed for path in streams):
                    raise ValueError('内嵌附件仅支持无宏 Word 或 Excel 原表')
                header = ole.openstream(streams[('worddocument',)]).read(12)
                if len(header) != 12 or header[:2] != b'\xec\xa5' or int.from_bytes(header[10:12], 'little') & 0x8100:
                    raise ValueError('不支持加密或无法识别的内嵌 Word 附件')
                return
    with ZipFile(io.BytesIO(raw)) as archive:
        names = set(archive.namelist())
        if '[Content_Types].xml' not in names or not {'word/document.xml', 'xl/workbook.xml'} & names:
            raise ValueError('内嵌附件仅支持无宏 Word 或 Excel 原表')
        infos = archive.infolist()
        if len(infos) > 5000 or len(infos) != len(names) or sum(f.file_size for f in infos) > 32 * 1024 * 1024:
            raise ValueError('内嵌附件过大或格式异常')
        for entry in infos:
            lower = entry.filename.lower()
            if (entry.flag_bits & 1 or lower.endswith(('vbaproject.bin', '.exe', '.dll'))
                    or '/activex/' in lower or ('/embeddings/' in lower and not entry.is_dir())
                    or (entry.file_size > 1024 * 1024 and entry.file_size > 250 * max(1, entry.compress_size))):
                raise ValueError('内嵌附件含加密、嵌套或不支持的内容')
            payload = archive.read(entry)
            if lower.endswith(('.xml', '.rels')):
                check_procurement_xml(entry.filename, payload)


def run_skill(script, *args, payload=None, timeout=180):
    try:
        stream = {'stdin': subprocess.DEVNULL} if payload is None else {'input': json.dumps(payload, ensure_ascii=False)}
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / script), *map(str, args)],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=timeout, **stream)
    except subprocess.TimeoutExpired:
        raise ValueError('本次采购文件处理超时，请按采购分册拆分后重试') from None
    if result.returncode:
        if '无法自动生成计划：' in result.stderr:
            raise ValueError('未能从这份采购原件识别完整的应答目录或正式表单。请核对主件与格式章后重新上传。'
                             '若采购方格式特殊，可在 Codex 中使用投标框架 Skill 复核原文并生成 Word，再从“已有模板 · 材料填充”导入。')
        raise ValueError('未能生成完整框架，请核对采购方格式章节、附件和 Word 文件后重试')
    return result.stdout


def build_edition(work, out, source, name, key, *, saved_plan=None, requirements=None,
                  sources=None, source_notes=None):
    """One bounded worker owns all planning, extraction and rendering for this edition."""
    payload = dict(work=str(work), out=str(out), source=str(source), name=name, key=key,
                   saved_plan=saved_plan, requirements=requirements, sources=sources,
                   source_notes=source_notes)
    return tuple(json.loads(run_skill('framework_worker.py', payload=payload, timeout=360)))


def build_edition_in_process(work, out, source, name, key, *, saved_plan=None, requirements=None,
                             sources=None, source_notes=None):
    """Render the same merged plan into Word, the HTML outline and API metadata."""
    work, out, source = Path(work), Path(out), Path(source)
    metrics_module = metric_module()
    from plan_framework import Source, generate
    from generate import generate_framework
    source_document = Source(source)
    parsed = metrics_module.read_units(source)
    if saved_plan is None:
        try:
            original = generate(source_document)
        except ValueError as error:
            raise ValueError('无法自动生成计划：' + str(error)) from error
        plan, bookmark_map = build_unified_plan(original)
    else:
        original = saved_plan
        plan, bookmark_map = copy.deepcopy(saved_plan), {}
    ensure_required_sections(plan)
    if requirements is None or sources is None:
        payload = framework_review.normalize_sources(metrics_module.export_review(
            source, plan=plan, parsed=parsed, source_document=source_document))
        requirements, sources = payload['requirements'], payload['sources']
        source_notes = payload.get('notes', [])
    if saved_plan is None:
        # Stable original sequence keeps historical chapter migration valid.
        framework_review.assign_bookmarks(plan)
        plan = framework_review.bind_requirements(plan, requirements)
    else:
        plan['reviewed_requirements'] = requirements
    plan = organize_books(plan, source, requirements, source_document)
    out.mkdir()
    stem = re.sub(r'[^\w\-·]+', '_', name).strip('_')[:45] or '项目'
    word = out / (stem + '_投标文件框架.docx')
    report = word.with_name(word.stem + '_目录与五类要求.html')
    notes = list(dict.fromkeys(parsed[1]))
    plan_notes = []
    merged_notice = '正式递交时，请按采购原件的标段、方案、报价、签章及分件要求办理。'
    former_notice = '各子项目在本框架中统一编制。正式递交时，请按采购原件对所投子项目的方案、报价、签章及分别递交要求办理。'
    for message in plan.get('review', []):
        if isinstance(message, dict):
            reference = next((s for s in sources if 'source_index' in message and s.get('part') == '主件'
                              and s.get('index') == message.get('source_index')), None)
            location = ' · '.join(dict.fromkeys(v for v in
                ((reference or {}).get('context'), (reference or {}).get('locator')) if v))
            message = message.get('text', '') + (f'（{location}）' if location else '')
        if message == former_notice:
            message = merged_notice
        plan_notes.append(str(message))
    if bookmark_map and merged_notice not in plan_notes:
        plan_notes.append(merged_notice)
    notes.extend(plan_notes)
    rendered = generate_framework(source, word, plan, parsed=parsed,
                                  extra_notes=list(dict.fromkeys(plan_notes)))
    metrics = [{'key': group, 'label': metrics_module.CATEGORIES[group][0],
                'unit': metrics_module.CATEGORIES[group][1], 'count': rendered['counts'][group]}
               for group in GROUPS]
    entries = framework_review.outline(plan)
    from response_books import book_directories
    artifacts = [{'id': aid, 'kind': aid, 'name': path.name, 'url': f'/api/frameworks/{key}/artifacts/{aid}'}
                 for aid, path in [('word', word), ('html', report)]]
    metadata = {'layout': 'whole', 'normalized_sha256': plan['source_sha256'], 'metrics': metrics,
                'outline': entries, 'books': book_directories(plan),
                'book_basis': plan.get('book_basis'), 'notes': list(dict.fromkeys(notes)), 'artifacts': artifacts,
                '_plan': plan, 'requirements': requirements, 'sources': sources, 'source_notes': source_notes or [],
                'revision': 1, 'review_version': REVIEW_VERSION, 'review_editable': True,
                'requirements_confirmed': False}
    old_titles = {p['name']: [b['text'].strip() for b in p['blocks'] if b['kind'] == 'heading']
                  for p in original.get('parts', [])}
    return metadata, bookmark_map, old_titles


def canonical_record(record):
    """Persist one requirements/source registry; outline/books/metrics are derived caches."""
    record = copy.deepcopy(record)
    if record.get('review_version'):
        record['requirements'] = framework_review.compact_requirements(record.get('requirements', []))
        record['sources'] = [{k: v for k, v in source.items() if k != 'source_id'}
                             for source in record.get('sources', [])]
        record.get('_plan', {}).pop('reviewed_requirements', None)
    return record


def save_record(store, record):
    return store.put('framework', canonical_record(record))


def public(record):
    value = {k: copy.deepcopy(v) for k, v in record.items() if k not in {'storage', 'source_storage', 'normalized_storage', 'legacy_artifacts', 'model_review'}
             and not k.startswith('_')}
    if record.get('review_version'):
        value['sources'] = [dict(s, source_id=s['id']) for s in record.get('sources', [])]
        value['requirements'] = framework_review.expand_requirements(record.get('requirements', []), value['sources'])
    if not record.get('review_version'):
        value.update(review_editable=False, revision=0, requirements_confirmed=True,
                     requirements=[], sources=[])
    value['outline_check'] = outline_check(record)
    return value


def list_frameworks(store):
    result = []
    for record in reversed(store.all('framework')):
        summary = {k: record[k] for k in ('id', 'name', 'filename', 'created_at', 'project_id', 'projects')}
        history = record.get('legacy_projects', [])
        if any(a['kind'] == 'part' for a in record['artifacts']):
            history = [p for pid in record['projects'].values() if (p := store.get('project', pid))]
            summary.update(projects={}, project_id=None)
        summary['legacy_projects'] = [{'id': p['id'], 'name': p['name']} for p in history]
        result.append(summary)
    return result


def create(store, body):
    from server import check_docx, decode_upload, now, text_value
    name = text_value(body.get('name', ''), '项目名称', 150, True)
    filename, original = decode_upload({'name': body.get('filename', ''), 'data': body.get('data')}, MAX_TENDER)
    if Path(filename).suffix.lower() not in {'.docx', '.doc'}:
        raise ValueError('请上传可编辑的采购 Word（DOCX 或 DOC）。PDF 或扫描件请先转换并核对原表。')
    if len(filename.encode('utf-8')) > 245:
        raise ValueError('采购文件名过长，请缩短文件名后重试')
    raw, import_kind = normalize_word(filename, original, MAX_TENDER)
    check_docx('采购文件.docx', raw, procurement=True)
    # The same upload can be retried after a response is lost. Bind it to the exact
    # inputs and bundled rules, while allowing new versions of a tender or skill.
    source_hash = hashlib.sha256(original).hexdigest()
    skill_hash = hashlib.sha256()
    for script in sorted(SCRIPTS.glob('*.py')):
        skill_hash.update(script.name.encode() + b'\0' + hashlib.sha256(script.read_bytes()).digest())
    identity = json.dumps([name, filename, source_hash, skill_hash.hexdigest(), REVIEW_VERSION],
                          ensure_ascii=False, sort_keys=True)
    key = 'framework_' + hashlib.sha256(identity.encode()).hexdigest()
    with store.lock:
        existing = store.get('framework', key)
        if existing:
            return public(current(store, key))
    base = store.path('frameworks')
    base.mkdir(exist_ok=True)
    destination = store.path('frameworks/' + key)
    with tempfile.TemporaryDirectory(prefix='.building-', dir=base) as working:
        work = Path(working)
        sources = work / 'source'
        sources.mkdir()
        original_path = sources / filename
        original_path.write_bytes(original)
        source = original_path
        if import_kind != 'docx':
            source = sources / 'normalized-source.docx'
            source.write_bytes(raw)
        out = work / 'deliverables'
        metadata, _, _ = build_edition(work, out, source, name, key)
        record = {'id': key, 'name': name, 'filename': filename, 'created_at': now(),
                  'source_sha256': source_hash, 'skill_sha256': skill_hash.hexdigest(),
                  'source_storage': str(original_path.relative_to(work)), 'projects': {}, 'project_id': None,
                  'normalized_storage': str(source.relative_to(work)),
                  'storage': {a['id']: str(Path(out.name) / a['name']) for a in metadata['artifacts']},
                  'legacy_projects': [], **metadata}
        with store.lock:
            store.db.execute('BEGIN IMMEDIATE')
            published = False
            try:
                existing = store.get('framework', key)
                if existing:
                    store.db.commit()
                    return public(current(store, key))
                work.rename(destination)
                published = True
                save_record(store, record)
                store.db.commit()
            except Exception:
                store.db.rollback()
                if published:
                    shutil.rmtree(destination)
                raise
    return public(record)


def asset(store, key, asset_id):
    record = current(store, key) if asset_id in {'word', 'html'} else store.require('framework', key)
    if asset_id == 'source':
        filename = record['filename']
        return (store.path(Path('frameworks') / key / record['source_storage']),
                WORD_MIME if filename.lower().endswith('.docx') else 'application/msword', filename)
    artifact = next((a for a in [*record['artifacts'], *record.get('legacy_artifacts', [])]
                     if a['id'] == asset_id), None)
    if not artifact:
        raise LookupError('框架文件不存在')
    path = store.path(Path('frameworks') / key / record['storage'][asset_id])
    return path, 'text/html; charset=utf-8' if artifact['kind'] == 'html' else WORD_MIME, artifact['name']


def new_project(store, record):
    from server import now, uid
    template = store.path(Path('frameworks') / record['id'] / record['storage']['word'])
    stamp = now()
    project = {'id': uid('project'), 'name': record['name'], 'framework_id': record['id'],
               'framework_artifact_id': 'word', 'template_name': template.name,
               'template_storage': str(template.relative_to(store.root)),
               'template_hash': hashlib.sha256(template.read_bytes()).hexdigest(),
               'template_import_kind': 'generated_framework', 'template_source_hash': record['source_sha256'],
               'created_at': stamp, 'updated_at': stamp, 'status': '待确认', 'mappings': [],
               'headings': [], 'warnings': []}
    store.scan(project)
    return project


def copy_previous_work(store, record, project, old_projects, bookmark_map, old_titles):
    """Old projects remain independent snapshots; match by part, old title and bookmark."""
    texts = {}
    canonical_template = store.path(project['template_storage'])
    new_chapters = {key: paragraph.text for key, _, paragraph in chapter_drafts._sections(Document(canonical_template))}
    history = []
    for aid, pid in old_projects.items():
        previous = store.require('project', pid)
        template = store.path(previous['template_storage'])
        part = Path(previous['template_name']).stem
        mapping = bookmark_map.get(part, {})
        draft = previous.get('chapter_texts', {})
        matched = {}
        if draft and mapping and template.is_file() and hashlib.sha256(template.read_bytes()).hexdigest() == previous['template_hash']:
            old_sections = {key: p.text for key, _, p in chapter_drafts._sections(Document(template))}
            expected = {f'BidSection{i}': title for i, title in enumerate(old_titles[part], 1)}
            for key, text in draft.items():
                target = mapping.get(key)
                if (target in new_chapters and old_sections.get(key) is not None
                        and old_sections[key] == expected.get(key) == new_chapters[target] and target not in texts):
                    texts[target] = text
                    matched[key] = target
        history.append({'id': pid, 'name': previous['name'], 'artifact_id': aid,
                        'migrated_chapters': len(matched), 'pending_chapters': len(draft) - len(matched),
                        'chapter_map': matched, 'pending_chapter_ids': sorted(draft.keys() - matched.keys()),
                        'material_mapping_count': len(previous['mappings']),
                        'source_revision': previous.get('chapter_revision', 0)})
    project['chapter_texts'] = texts
    project['chapter_revision'] = 1 if texts else 0
    record['legacy_projects'] = history


def verified_original(store, record):
    root = store.path(Path('frameworks') / record['id'])
    original = store.path(root / record['source_storage'])
    raw = original.read_bytes()
    if hashlib.sha256(raw).hexdigest() != record['source_sha256']:
        raise ValueError('采购原件指纹发生变化，未修改已存框架，请核对原件')
    return original, raw


def edition_source(store, record, work):
    """Reuse the verified normalized DOCX; old records convert once on a real revision."""
    original, original_raw = verified_original(store, record)
    root = store.path(Path('frameworks') / record['id'])
    if record.get('normalized_storage'):
        source = store.path(root / record['normalized_storage'])
    elif original.suffix.lower() == '.docx':
        source = original
    else:
        raw, _ = normalize_word(record['filename'], original_raw, MAX_TENDER)
        source = Path(work) / 'normalized-source.docx'
        source.write_bytes(raw)
    if hashlib.sha256(source.read_bytes()).hexdigest() != record['normalized_sha256']:
        raise ValueError('采购原件转换结果已变化，请重新上传并核对')
    return source


def cache_edition_source(source, root, work, out, target, metadata):
    if source.is_relative_to(work):
        shutil.copyfile(source, out / 'normalized-source.docx')
        metadata['normalized_storage'] = str((target / 'normalized-source.docx').relative_to(root))
    else:
        metadata['normalized_storage'] = str(source.relative_to(root))


def current(store, key):
    """Upgrade a historical split framework once; its existing templates are never overwritten."""
    with store.lock:
        record = store.require('framework', key)
    if not any(a['kind'] == 'part' for a in record['artifacts']):
        record.setdefault('layout', 'whole')
        record.setdefault('legacy_projects', [])
        return record
    root = store.path(Path('frameworks') / key)
    with tempfile.TemporaryDirectory(prefix='.upgrade-', dir=root) as workspace:
        work = Path(workspace)
        source = edition_source(store, record, work)
        out = work / 'deliverables'
        metadata, bookmark_map, old_titles = build_edition(work, out, source, record['name'], key)
        if not bookmark_map:
            raise ValueError('采购原件未能重建原有分件，请核对原件和编排要求；历史工作稿仍可打开。')
        # A unique directory makes publication independent of a previous interrupted upgrade.
        target = root / ('whole-' + work.name.removeprefix('.upgrade-'))
        cache_edition_source(source, root, work, out, target, metadata)
        with store.lock:
            store.db.execute('BEGIN IMMEDIATE')
            try:
                record = store.require('framework', key)
                if record.get('layout') == 'whole':
                    store.db.commit()
                    return record
                out.rename(target)
                old_projects = record['projects']
                record['legacy_artifacts'] = [a for a in record['artifacts'] if a['kind'] == 'part']
                record.update(metadata, projects={}, project_id=None, legacy_projects=[])
                record['storage'].update({a['id']: str(Path(target.name) / a['name']) for a in metadata['artifacts']})
                if old_projects:
                    record['requirements_confirmed'] = True
                    project = new_project(store, record)
                    copy_previous_work(store, record, project, old_projects, bookmark_map, old_titles)
                    store.put('project', project)
                    record.update(projects={'word': project['id']}, project_id=project['id'])
                save_record(store, record)
                store.db.commit()
            except Exception:
                store.db.rollback()
                if target.is_dir():
                    shutil.rmtree(target)
                raise
    return record


def describe(store, key):
    record = store.require('framework', key)
    try:
        return public(current(store, key))
    except (OSError, ValueError, LookupError) as error:
        if not any(a['kind'] == 'part' for a in record['artifacts']):
            raise
        # Keep separate, previously edited project records reachable if reconstruction fails.
        message = '整本工作稿未能重建，请核对已存采购原件。历史分件仍可查阅。'
        if isinstance(error, ValueError):
            message += ' ' + str(error)
        history = []
        for aid, pid in record['projects'].items():
            previous = store.get('project', pid)
            if previous:
                history.append({'id': pid, 'name': previous['name'], 'artifact_id': aid, 'migrated_chapters': 0,
                                'pending_chapters': len(previous.get('chapter_texts', {}))})
        return dict(public(record), artifacts=[], projects={}, project_id=None, outline=[],
                    legacy_projects=history, migration_error=message, notes=[message, *record.get('notes', [])])


def archived_project_ids(store):
    """The main project list shows the canonical draft. Its outline links to historical drafts."""
    hidden = set()
    with store.lock:
        records = store.all('framework')
    for record in records:
        # Listing must not migrate or regenerate business files. Existing split
        # drafts remain visible until the user opens their framework to upgrade.
        if any(a['kind'] == 'part' for a in record['artifacts']):
            continue
        if record.get('project_id'):
            hidden.update(p['id'] for p in record.get('legacy_projects', []))
    return hidden


def confirm(store, key, body):
    record = current(store, key)
    if record.get('review_version') and not record.get('requirements_confirmed'):
        raise ValueError('请先保存并确认招标要求，再确认目录进入系统填充')
    if body.get('artifact_id', 'word') != 'word':
        raise ValueError('请刷新目录并确认本项目的整本 Word 框架')
    # Serialize the first confirmation even when two local server instances share a store.
    with store.lock:
        store.db.execute('BEGIN IMMEDIATE')
        try:
            record = store.require('framework', key)
            if record.get('review_version'):
                if not record.get('requirements_confirmed'):
                    raise ValueError('请先保存并确认招标要求，再确认目录进入系统填充')
                if type(body.get('revision')) is not int or body['revision'] != record['revision']:
                    raise ValueError('目录已更新，请重新预览当前版本后再确认')
            pid = record['projects'].get('word')
            if pid:
                project = store.require('project', pid)
            else:
                if record.get('review_version'):
                    require_outline_check_ack(record, body)
                project = new_project(store, record)
                # Edits publish a new template; old projects/exports stay immutable.
                # Surviving stable chapter IDs retain the user's authored text.
                if record.get('_previous_project'):
                    previous = store.get('project', record['_previous_project'])
                    allowed = {r['id'] for r in record['outline']}
                    texts = {k: v for k, v in (previous or {}).get('chapter_texts', {}).items() if k in allowed}
                    project.update(chapter_texts=texts, chapter_revision=1 if texts else 0)
                    for history in record.get('legacy_projects', []):
                        if history['id'] == record['_previous_project']:
                            history.update(migrated_chapters=len(texts),
                                pending_chapters=len((previous or {}).get('chapter_texts', {})) - len(texts),
                                chapter_map={k: k for k in texts})
                store.put('project', project)
                record.update(projects={'word': project['id']}, project_id=project['id'])
                save_record(store, record)
            store.db.commit()
        except Exception:
            store.db.rollback()
            raise
        return store.public_project(project)


def revise(store, key, body, kind):
    """Publish a complete revision atomically, retaining every prior working template."""
    from server import now
    with store.lock:
        record = canonical_record(store.require('framework', key))
    if not record.get('review_version') or not record.get('_plan'):
        raise ValueError('旧版本框架仅供查看；请重新上传采购原件使用核对与目录编辑')
    if type(body.get('revision')) is not int or body['revision'] != record['revision']:
        raise ValueError('此项目已在其他窗口更新，请重新读取后核对再保存')
    requirements = framework_review.expand_requirements(record['requirements'], record['sources'])
    plan = record['_plan']
    confirmed = record.get('requirements_confirmed', False)
    if kind == 'requirements':
        previous_requirements = requirements
        requirements = framework_review.validate_requirements(body.get('items'), record['sources'], requirements)
        if type(body.get('confirmed')) is not bool:
            raise ValueError('请明确本次是保存草稿还是确认要求')
        confirmed = body['confirmed']
        framework_review.require_confirmation_acks(
            requirements, previous_requirements, record.get('notes', []), body)
        if framework_review.compact_requirements(requirements) == record['requirements']:
            # Confirming the same content is a state transition, not a new edition.
            verified_original(store, record)
            if record.get('normalized_storage'):
                cached = store.path(Path('frameworks') / key / record['normalized_storage'])
                if hashlib.sha256(cached.read_bytes()).hexdigest() != record['normalized_sha256']:
                    raise ValueError('采购原件转换结果已变化，请重新上传并核对')
            with store.lock:
                store.db.execute('BEGIN IMMEDIATE')
                try:
                    latest = store.require('framework', key)
                    if latest.get('revision') != record['revision']:
                        raise ValueError('此项目已在其他窗口更新，本次未覆盖；请重新读取后核对')
                    latest.update(revision=record['revision'] + 1, requirements_confirmed=confirmed,
                                  updated_at=now())
                    save_record(store, latest)
                    store.db.commit()
                except Exception:
                    store.db.rollback()
                    raise
            return public(latest)
        plan = framework_review.bind_requirements(plan, requirements)
    elif kind == 'outline':
        if not confirmed:
            raise ValueError('请先确认招标要求，再修改响应目录')
        plan = framework_review.edit_outline(plan, body.get('items'), requirements)
    else:
        raise ValueError('未知核对操作')
    plan = ensure_required_sections(plan)
    root = store.path(Path('frameworks') / key)
    with tempfile.TemporaryDirectory(prefix='.revision-', dir=root) as temporary:
        work = Path(temporary)
        source = edition_source(store, record, work)
        out = work / 'deliverables'
        metadata, _, _ = build_edition(work, out, source, record['name'], key,
            saved_plan=plan, requirements=requirements, sources=record['sources'],
            source_notes=record.get('source_notes', []))
        next_revision = record['revision'] + 1
        target = root / ('revision-' + str(next_revision) + '-' + work.name.removeprefix('.revision-'))
        cache_edition_source(source, root, work, out, target, metadata)
        published = False
        with store.lock:
            store.db.execute('BEGIN IMMEDIATE')
            try:
                current_record = store.require('framework', key)
                if current_record.get('revision') != record['revision']:
                    raise ValueError('此项目已在其他窗口更新，本次未覆盖；请重新读取后核对')
                out.rename(target)
                published = True
                revised_content = canonical_record(metadata)
                changed = (record['_plan'] != revised_content['_plan']
                           or record['requirements'] != revised_content['requirements'])
                # Confirmation can create a project while rendering runs without
                # the store lock. Keep its latest pointers/history before publishing.
                for field in ('projects', 'project_id', 'legacy_projects', '_previous_project'):
                    if field in current_record:
                        record[field] = copy.deepcopy(current_record[field])
                pid = record['projects'].get('word')
                record.update(metadata, revision=next_revision, requirements_confirmed=confirmed,
                              updated_at=now())
                record['storage'].update({a['id']: str(Path(target.name) / a['name']) for a in metadata['artifacts']})
                if pid and changed:
                    previous = store.require('project', pid)
                    if not any(p['id'] == pid for p in record['legacy_projects']):
                        record['legacy_projects'].append({'id': pid, 'name': previous['name'] + '（目录修改前）',
                            'migrated_chapters': 0, 'pending_chapters': len(previous.get('chapter_texts', {})),
                            'material_mapping_count': len(previous.get('mappings', [])),
                            'source_revision': previous.get('chapter_revision', 0)})
                    record.update(projects={}, project_id=None, _previous_project=pid)
                save_record(store, record)
                store.db.commit()
            except Exception:
                store.db.rollback()
                if published:
                    shutil.rmtree(target)
                raise
    return public(record)


def remove(store, key):
    record = store.require('framework', key)
    if (record['projects'] or record.get('legacy_projects') or record.get('_previous_project')
            or any(p.get('framework_id') == key for p in store.all('project'))):
        raise ValueError('该框架已用于填充项目，请保留原件及编制依据')
    directory = store.path(Path('frameworks') / key)
    with tempfile.TemporaryDirectory(prefix='.remove-', dir=directory.parent) as pending:
        removed = Path(pending) / 'record'
        directory.rename(removed)
        try:
            store.db.execute('DELETE FROM objects WHERE kind=? AND id=?', ('framework', key))
            store.db.commit()
        except Exception:
            store.db.rollback()
            removed.rename(directory)
            raise
    return {'removed': True}
