#!/usr/bin/env python3
"""仅监听本机的标书材料管理服务。原始交付文件始终只读。"""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, parse_qs
import uuid
import warnings
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image, ImageOps

from credit_service import CreditService, MANAGED_IDS
from special import SpecialLibrary
from export_jobs import ExportJobs
from word_import import normalize_word
from location_preview import LocationPreview

APP_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = APP_DIR / 'data'
SOURCE_ROOT = APP_DIR.parent
SUBJECT = '北京腾宝科技有限公司'
MAX_REQUEST = 80 * 1024 * 1024
MAX_IMAGE = 15 * 1024 * 1024
MAX_DOCX = 30 * 1024 * 1024
MAX_PIXELS = 40_000_000
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
IMAGE_FORMATS = {'PNG', 'JPEG', 'WEBP', 'BMP', 'TIFF'}
# Bump when stored mappings need a one-time re-validation after a rule change.
MAPPING_SYNC_VERSION = 2  # 2: headings carry legal_text for ID name checks


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def uid(prefix):
    return prefix + '_' + uuid.uuid4().hex


def natural_key(value):
    return [int(v) if v.isdigit() else v.lower() for v in re.split(r'(\d+)', str(value))]


def text_value(value, label, limit=200, required=False):
    if not isinstance(value, str):
        raise ValueError(f'{label}必须为文字')
    value = value.strip()
    if required and not value:
        raise ValueError(f'请填写{label}')
    if len(value) > limit:
        raise ValueError(f'{label}过长（最多 {limit} 字）')
    return value


def file_name(value):
    value = text_value(value, '文件名', 240, True)
    if value in {'.', '..'} or '/' in value or '\\' in value or '\x00' in value or any(ord(c) < 32 for c in value):
        raise ValueError('文件名不能包含路径或控制字符')
    return value


def decode_upload(item, limit):
    if not isinstance(item, dict):
        raise ValueError('文件格式不正确')
    name = file_name(item.get('name', ''))
    data = item.get('data')
    if not isinstance(data, str) or len(data) > ((limit + 2) // 3) * 4:
        raise ValueError(f'文件过大，单个文件上限为 {limit // 1024 // 1024} MB')
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('文件编码不正确，请重新选择文件') from None
    if not raw or len(raw) > limit:
        raise ValueError('文件为空或超过大小限制')
    return name, raw


def check_image(name, raw, enforce_size=True):
    if Path(name).suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError('材料附件仅支持 PNG、JPEG、WebP、BMP、TIFF 图片')
    if enforce_size and len(raw) > MAX_IMAGE:
        raise ValueError('单张材料图片不能超过 15 MB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as picture:
                if picture.format not in IMAGE_FORMATS or picture.width * picture.height > MAX_PIXELS:
                    raise ValueError('图片格式不支持或像素过大')
                width, height = picture.size
                fmt = picture.format
                picture.verify()
            with Image.open(io.BytesIO(raw)) as picture:
                picture.load()
        return width, height, Image.MIME.get(fmt, 'application/octet-stream')
    except ValueError:
        raise
    except Exception:
        raise ValueError('图片损坏或无法解码，请重新选择图片') from None


def check_docx(name, raw):
    if Path(name).suffix.lower() != '.docx':
        raise ValueError('仅支持 .docx 格式的 Word 模板，请先转换旧版 .doc 文件')
    if not raw or len(raw) > MAX_DOCX:
        raise ValueError('Word 模板不能为空，且不能超过 30 MB')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
            if len(infos) > 5000:
                raise ValueError('Word 文件内部项目过多')
            names = [i.filename for i in infos]
            if len(names) != len(set(names)):
                raise ValueError('Word 文件存在重复内部路径')
            if not {'[Content_Types].xml', 'word/document.xml'}.issubset(names):
                raise ValueError('文件不是有效的 Word 文档')
            total = 0
            for info in infos:
                path = PurePosixPath(info.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in info.filename or ':' in info.filename:
                    raise ValueError('Word 文件内部路径不安全')
                if info.flag_bits & 1:
                    raise ValueError('不支持加密的 Word 文件')
                total += info.file_size
                if total > 220 * 1024 * 1024 or info.file_size > 80 * 1024 * 1024:
                    raise ValueError('Word 文件解压后过大')
                if info.file_size > 1024 * 1024 and info.file_size / max(info.compress_size, 1) > 250:
                    raise ValueError('Word 文件压缩比异常')
                lower = info.filename.lower()
                if lower.endswith('vbaproject.bin') or lower.startswith('word/embeddings/'):
                    raise ValueError('模板包含宏或嵌入文件，请移除后上传')
            doc = z.read('word/document.xml')
            if b'<!DOCTYPE' in doc.upper() or b'<!ENTITY' in doc.upper():
                raise ValueError('Word 文件 XML 不安全')
            ET.fromstring(doc)
            # Read every member to validate CRC before the document reaches the engine.
            for info in infos:
                with z.open(info) as stream:
                    while stream.read(1024 * 1024):
                        pass
    except ValueError:
        raise
    except Exception:
        raise ValueError('Word 文件损坏或格式不正确') from None


DEFAULT_CATEGORIES = [
    ('legal_identity', '法定代表人（负责人）身份证明', ['法定代表人身份证明', '法人代表身份证明', '法人身份证明']),
    ('authorization', '法定代表人（负责人）授权委托书', ['法定代表人授权委托书', '授权委托书', '法人授权书']),
    ('business_license', '营业执照', ['企业法人营业执照', '营业执照副本']),
    ('audit_report', '2023-2024年财务审计报告', ['财务审计报告', '审计报告', '年度审计报告']),
    ('tax_social', '税收和社会保障资金缴纳凭证', ['税收和社会保障资金缴纳凭证', '税收及社保缴纳凭证', '纳税和社保缴纳证明']),
    ('company_profile', '公司介绍', ['企业介绍', '公司简介', '企业简介']),
    ('iso_cert', 'ISO相关资质证书', ['ISO认证', 'ISO资质证书', 'ISO证书']),
]


class Store(SpecialLibrary):
    def __init__(self, data_dir, seed=False, source_root=SOURCE_ROOT):
        self.root = Path(data_dir).resolve()
        self.source_root = Path(source_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in ['materials', 'templates', 'exports']:
            (self.root / directory).mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.root / 'store.sqlite3'), check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS objects (kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,id))')
        self.db.commit()
        with self.lock:
            if not self.get('meta', 'initialized'):
                for order, (key, name, aliases) in enumerate(DEFAULT_CATEGORIES):
                    self.put('category', {'id': key, 'name': name, 'aliases': aliases, 'layer': 'common', 'enabled': True, 'sort_order': order})
                self.put('meta', {'id': 'initialized', 'created_at': now()})
                self.db.commit()
            if seed and not self.get('meta', 'seed'):
                try:
                    self.seed_materials()
                except Exception as exc:
                    self.db.rollback()
                    self.put('meta', {'id': 'source_status', 'state': 'error', 'message': str(exc) if isinstance(exc, ValueError) else '原始材料导入失败，请检查交付目录', 'updated_at': now()})
                    self.db.commit()
            self.credit = CreditService(self)
            self.location_preview = LocationPreview()
            self.migrate_special()
            self.migrate_projects()
            self.export_jobs = ExportJobs(self)

    def close(self):
        self.export_jobs.close()
        with self.lock:
            self.credit.close()
            self.location_preview.close()
            self.db.close()

    def get(self, kind, key):
        row = self.db.execute('SELECT payload FROM objects WHERE kind=? AND id=?', (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def require(self, kind, key):
        result = self.get(kind, key)
        if result is None:
            raise LookupError('记录不存在或已移除')
        return result

    def all(self, kind):
        return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM objects WHERE kind=? ORDER BY rowid', (kind,))]

    def put(self, kind, value):
        self.db.execute('INSERT INTO objects(kind,id,payload) VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload', (kind, value['id'], json.dumps(value, ensure_ascii=False)))
        return value

    def path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('文件路径无效')
        return path

    def internal_materials(self, include_special=False, project=None):
        selected = {mid for row in project['mappings'] for mid in row.get('material_ids', [])} if project else None
        materials = [m for m in self.all('material') if not self.is_special(m)
                     or (include_special and (selected is None or m['id'] in selected))]
        for material in materials:
            material['files'] = [self.require('file', fid) for fid in material.pop('file_ids', [])]
            for f in material['files']:
                f['path'] = str(self.path(f['storage']))
        return materials

    def matching_categories(self, project):
        selected = set(project.get('special_category_ids', []))
        return [c for c in self.all('category') if c['layer'] == 'common' or c['id'] in selected]

    def common_reviewed(self, project):
        rows = [r for r in project['mappings']
                if (self.get('category', r['category_id']) or {}).get('layer') == 'common']
        return bool(rows) and all(r.get('confirmed') and r.get('status') != 'blocked' for r in rows)

    def project_reviewed(self, project):
        rows = project['mappings']
        return (project.get('special_selection_confirmed') is True and bool(rows)
                and all(r.get('confirmed') and r.get('status') != 'blocked' for r in rows))

    def public_file(self, file):
        return {**{key: file[key] for key in ['id', 'name', 'rotation', 'width', 'height']}, 'url': '/api/files/' + file['id'], 'source': file.get('source', '')}

    def public_material(self, material):
        value = copy.deepcopy(material)
        value['files'] = [self.public_file(self.require('file', fid)) for fid in value.pop('file_ids', [])]
        value.pop('source', None)
        return value

    def sync_material_matches(self, project):
        """Fill unresolved automatic matches without changing manual selections."""
        import engine
        materials, categories = self.internal_materials(include_special=True, project=project), self.matching_categories(project)
        original = copy.deepcopy(project['mappings'])
        rows = engine.validate_mappings(original, materials, project['headings'], categories)
        for row in rows:
            if row.get('layer') == 'special':
                continue
            if (row.get('action') not in {'missing', 'fill'} or row.get('material_ids')
                    or row.get('auto_match') is False):
                continue
            candidates = []
            for item in materials:
                if item.get('category_id') != row['category_id']:
                    continue
                probe = dict(row, action='fill', material_ids=[item['id']])
                checked = engine.validate_mappings([probe], materials, project['headings'], categories)[0]
                if not [issue for issue in checked['issues'] if not issue.startswith('缺少要求年度：')]:
                    candidates.append(item)
            ids = []
            if len(candidates) == 1:
                ids = [candidates[0]['id']]
            elif candidates and row.get('required_years'):
                years = row['required_years']
                if all(sum(y in item.get('years', []) for item in candidates) <= 1 for y in years):
                    candidates.sort(key=lambda item: (min(item.get('years') or [9999]), item['id']))
                    ids = [item['id'] for item in candidates]
            elif len(candidates) > 1:
                ids = engine.recommend_by_year_options(row, candidates, project['headings']) or []
            if candidates:
                row.update(action='fill', material_ids=ids)
        rows = engine.validate_mappings(rows, materials, project['headings'], categories)
        for old, row in zip(original, rows):
            if any(old.get(k) != row.get(k) for k in ('action', 'material_ids', 'status', 'issues', 'warnings')):
                row['confirmed'] = False
        if rows != original:
            project['mappings'] = rows
            project['status'] = '已确认' if self.project_reviewed(project) else '待确认'
            project['updated_at'] = now()
            self.put('project', project)
        return project

    def material_changed(self, value):
        if self.is_special(value):
            self.invalidate(material_id=value['id'])
            return
        for project in self.all('project'):
            for row in project['mappings']:
                if (value['id'] in row.get('material_ids', []) or
                        (row['category_id'] == value['category_id'] and
                         row.get('action') != 'skip' and not row.get('material_ids'))):
                    row['confirmed'] = False
                    project['status'] = '待确认'
            self.sync_material_matches(project)
            self.put('project', project)

    def refresh_location_rows(self, project, scanned, layer):
        """Upgrade known automatic locations; never replace a recorded manual choice."""
        proposals = {}
        for row in scanned['mappings']:
            if row.get('layer', 'common') == layer:
                proposals.setdefault(row['category_id'], []).append(row)
        requirements = scanned.get('requirements', {})
        project.setdefault('requirements', {}).update(requirements)
        occupied = {r.get('locator_id'): r['id'] for r in project['mappings'] if r.get('locator_id')}
        for row in project['mappings']:
            if row.get('layer', 'common') != layer:
                continue
            row['requirement'] = copy.deepcopy(requirements.get(row['category_id'], {}))
            old_locator = row.get('locator_id', '')
            match_status = row.get('location_match', {}).get('status')
            manual = match_status == 'manual' or (old_locator and not match_status)
            if manual or row.get('action') == 'skip' or (layer == 'common' and row.get('auto_match') is False and row.get('action')=='missing'):
                continue
            choices = proposals.get(row['category_id'], [])
            same = [r for r in choices if r.get('locator_id') == old_locator and old_locator]
            proposal = same[0] if same else choices[0] if len(choices) == 1 else None
            if proposal:
                locator = proposal.get('locator_id', '')
                match = copy.deepcopy(proposal.get('location_match', {}))
                if locator and occupied.get(locator, row['id']) != row['id']:
                    locator = ''
                    match.update(status='ambiguous', reason='新识别位置已被其他核对项使用，请核对', candidates=[])
                row.update(locator_id=locator, title=proposal['title'], confirmed=False,
                           existing_content=proposal['existing_content'], location_match=match)
                if old_locator and occupied.get(old_locator) == row['id']:
                    occupied.pop(old_locator)
                if locator:
                    occupied[locator] = row['id']
            elif old_locator:
                row.update(locator_id='', confirmed=False, location_match={
                    'status': 'ambiguous', 'reason': '原自动位置已失效，请核对新识别的候选位置',
                    'candidates': [{'locator_id': r['locator_id'], 'title': r['title']} for r in choices if r['locator_id']]})
        if any(not r.get('confirmed') for r in project['mappings']):
            project['status'] = '待确认'

    def sync_special_locations(self, project):
        """Backfill unresolved legacy positions once, preserving every user decision."""
        import engine
        if (not project.get('special_selection_confirmed') or
                project.get('special_locator_version') == engine.SPECIAL_LOCATOR_VERSION):
            return project
        source = self.path(project['template_storage'])
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != project['template_hash']:
            return project
        scanned = engine.scan_document(source, self.matching_categories(project),
            self.internal_materials(include_special=True, project=project), project.get('special_category_ids', []))
        self.refresh_location_rows(project, scanned, 'special')
        # Include newly recognized plain-text headings without dropping old manual choices.
        headings = {h['id']: h for h in project['headings']}
        headings.update({h['id']: h for h in scanned['headings']})
        project['headings'] = list(headings.values())
        project['special_locator_version'] = engine.SPECIAL_LOCATOR_VERSION
        self.put('project', project)
        return project

    def sync_common_locations(self, project):
        """Upgrade unresolved automatic common matches without replacing user choices."""
        import engine
        if project.get('common_locator_version') == engine.COMMON_LOCATOR_VERSION:
            return project
        source = self.path(project['template_storage'])
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != project['template_hash']:
            return project
        scanned = engine.scan_document(source, self.matching_categories(project),
            self.internal_materials(include_special=True, project=project), project.get('special_category_ids', []))
        self.refresh_location_rows(project, scanned, 'common')
        headings = {h['id']: h for h in project['headings']}
        headings.update({h['id']: h for h in scanned['headings']})
        project['headings'] = list(headings.values())
        fresh_authorization = {h['id'] for h in scanned['headings'] if 'authorization' in h.get('category_ids', [])}
        for heading in project['headings']:
            if 'authorization' in heading.get('category_ids', []) and heading['id'] not in fresh_authorization:
                heading['category_ids'] = [cid for cid in heading['category_ids'] if cid != 'authorization']
                heading['authorization_body_present'] = False
                heading['authorization_text'] = ''
        project['common_locator_version'] = engine.COMMON_LOCATOR_VERSION
        self.put('project', project)
        return project

    @staticmethod
    def sync_version():
        import engine
        return [engine.COMMON_LOCATOR_VERSION, engine.SPECIAL_LOCATOR_VERSION, MAPPING_SYNC_VERSION]

    def migrate_project(self, project):
        """Upgrade one stored project to the current rules; a no-op once stamped."""
        import engine
        current = (project.get('sync_version') == self.sync_version()
                   and project.get('common_locator_version') == engine.COMMON_LOCATOR_VERSION
                   and (not project.get('special_selection_confirmed')
                        or project.get('special_locator_version') == engine.SPECIAL_LOCATOR_VERSION))
        if current:
            return project
        project = self.sync_common_locations(project)
        project = self.sync_special_locations(project)
        self.refresh_heading_texts(project)
        project = self.sync_material_matches(project)
        project['sync_version'] = self.sync_version()
        self.put('project', project)
        return project

    def refresh_heading_texts(self, project):
        """Backfill person text read from the unchanged template; locations stay as they are."""
        import engine
        source = self.path(project['template_storage'])
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != project['template_hash']:
            return
        scanned = engine.scan_document(source, self.matching_categories(project),
            self.internal_materials(include_special=True, project=project), project.get('special_category_ids', []))
        fresh = {h['id']: h for h in scanned['headings']}
        for heading in project['headings']:
            if heading['id'] in fresh:
                for key in ('legal_text', 'authorization_text'):
                    heading[key] = fresh[heading['id']].get(key, '')

    def migrate_projects(self):
        """Run rule upgrades at startup so that reading a project never writes."""
        for project in self.all('project'):
            try:
                self.migrate_project(project)
                self.db.commit()
            except Exception:
                # A damaged template must not stop the service; the project keeps
                # its stored state and is retried on the next start.
                self.db.rollback()

    def read_context(self):
        """Shared rows for rendering several projects in one request."""
        materials, categories = self.all('material'), self.all('category')
        special = {c['id'] for c in categories if c['layer'] == 'special'}
        # Special libraries are large; only materials a project selected can be validated.
        selected = {mid for project in self.all('project') for row in project['mappings']
                    for mid in row.get('material_ids', [])}
        files = {f['id']: f for f in self.all('file')}
        internal = {}
        for material in copy.deepcopy(materials):
            if material.get('category_id') in special and material['id'] not in selected:
                continue
            material['files'] = [copy.deepcopy(files[fid]) if fid in files else self.require('file', fid)
                                 for fid in material.pop('file_ids', [])]
            for f in material['files']:
                f['path'] = str(self.path(f['storage']))
            internal[material['id']] = material
        return {'materials': materials, 'internal': internal, 'categories': categories,
                'industries': self.all('industry'), 'exports': self.all('export')}

    def public_project(self, project, context=None):
        """Read-only view: current validation is shown, nothing is persisted."""
        import engine
        context = context or self.read_context()
        categories = context['categories']
        by_category = {c['id']: c for c in categories}
        special_ids = set(project.get('special_category_ids', []))
        selected_ids = {mid for row in project['mappings'] for mid in row.get('material_ids', [])}
        materials = [m for m in context['internal'].values()
                     if (by_category.get(m.get('category_id')) or {}).get('layer') != 'special'
                     or m['id'] in selected_ids]
        matching = [c for c in categories if c['layer'] == 'common' or c['id'] in special_ids]
        value = copy.deepcopy(project)
        rows = engine.validate_mappings(value['mappings'], materials, value['headings'], matching)
        for old, row in zip(project['mappings'], rows):
            # Same rule as sync_material_matches: a changed check needs a new review.
            if any(old.get(k) != row.get(k) for k in ('action', 'material_ids', 'status', 'issues', 'warnings')):
                row['confirmed'] = False
        value['mappings'] = rows
        if project.get('status') != '已导出' or rows != project['mappings']:
            value['status'] = '已确认' if self.project_reviewed(value) else '待确认'
        value.pop('template_storage', None)
        value.pop('template_source_storage', None)
        if project.get('template_import_kind') == 'converted_doc':
            value['warnings'] = ['此模板已从旧版 .doc 转换；导出后请在 Word 中核对表格、分页和目录。', *value.get('warnings', [])]
        value.setdefault('special_category_ids', [])
        value.setdefault('special_selection_confirmed', False)
        industry_ids = {c.get('industry_id') for c in categories if c['id'] in special_ids}
        value['special_industry_ids'] = [i['id'] for i in context['industries'] if i['id'] in industry_ids]
        selected = {mid for row in value['mappings'] if row.get('layer') == 'special'
                    for mid in row.get('material_ids', [])}
        value['selected_special_materials'] = [self.public_material(m) for m in context['materials']
                                               if m['id'] in selected]
        value['exports'] = [v for v in context['exports'] if v['project_id'] == project['id']]
        return value

    def bootstrap(self):
        categories = sorted(self.all('category'), key=lambda c: c['sort_order'])
        context = self.read_context()
        layers = {c['id']: c['layer'] for c in context['categories']}
        materials = [self.public_material(v) for v in context['materials'] if layers.get(v.get('category_id')) != 'special']
        projects = [self.public_project(v, context) for v in reversed(self.all('project'))]
        exports = list(reversed(context['exports']))
        source = self.get('meta', 'source_status') or {'state': 'not_imported', 'message': '此数据目录未导入示例材料'}
        source.pop('id', None)
        return {'categories': categories, 'materials': materials, 'projects': projects, 'exports': exports, 'industries': context['industries'], 'credit_automation': self.credit.status(), 'stats': {'special_material_count': sum(layers.get(m.get('category_id')) == 'special' and not m.get('archived') for m in context['materials']), 'category_count': sum(c['layer'] == 'common' for c in categories), 'material_count': len(materials), 'file_count': sum(len(m['files']) for m in materials), 'project_count': len(projects), 'export_count': len(exports), 'source_status': source}}

    def create_files(self, material_id, files, seed=False):
        if not isinstance(files, list) or len(files) > 100:
            raise ValueError('每次最多添加 100 张图片')
        prepared = []
        for item in files:
            name, raw = (item['name'], item['raw']) if seed else decode_upload(item, MAX_IMAGE)
            width, height, mime = check_image(name, raw, not seed)
            fid = item.get('id') if seed else uid('file')
            if not fid:
                fid = uid('file')
            storage = f'materials/{material_id}/{fid}{Path(name).suffix.lower()}'
            prepared.append(({'id': fid, 'material_id': material_id, 'name': name, 'storage': storage, 'width': width, 'height': height, 'mime': mime, 'rotation': item.get('rotation', 0) if seed else 0, 'sha256': hashlib.sha256(raw).hexdigest(), 'source': item.get('source', '') if seed else ''}, raw))
        for metadata, raw in prepared:
            destination = self.path(metadata['storage'])
            destination.parent.mkdir(exist_ok=True, parents=True)
            destination.write_bytes(raw)
            self.put('file', metadata)
        return [entry[0]['id'] for entry in prepared]

    def seed_materials(self):
        ledger = self.source_root / '标书自动填充/示例/材料台账.xlsx'
        source_materials = self.source_root / '材料'
        if not ledger.is_file() or not source_materials.is_dir():
            raise ValueError('未找到交付包的材料台账或材料目录；可以手动上传材料')
        # A read-only OOXML reader avoids requiring an Excel runtime for the application.
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(ledger) as archive:
            shared = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                shared = [''.join(x.itertext()) for x in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si', ns)]
            root = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            rows = []
            for row in root.findall('s:sheetData/s:row', ns):
                values = {}
                for cell in row:
                    col = re.sub(r'\d', '', cell.get('r', ''))
                    v = cell.find('s:v', ns)
                    raw = v.text if v is not None else ''
                    if cell.get('t') == 's':
                        raw = shared[int(raw)]
                    elif cell.get('t') == 'inlineStr':
                        inline = cell.find('s:is', ns)
                        raw = ''.join(inline.itertext()) if inline is not None else ''
                    values[col] = raw or ''
                rows.append(values)
        association = {'ZZ-001': 'business_license', 'SF-001': 'legal_identity', 'SF-002': 'authorization', 'CW-2023': 'audit_report', 'CW-2024': 'audit_report'}
        count = 0
        for row in rows[1:]:
            source_id = row.get('A', '')
            if source_id not in association:
                continue
            mid = 'seed_' + source_id.lower().replace('-', '_')
            if self.get('material', mid):
                continue
            seen = set()
            assets = []
            for pattern in row.get('E', '').split('；'):
                pattern = pattern.strip()
                if not pattern or PurePosixPath(pattern).is_absolute() or '..' in PurePosixPath(pattern).parts:
                    raise ValueError('原始材料台账包含无效相对路径')
                for match in sorted(source_materials.glob(pattern), key=natural_key):
                    candidates = sorted(match.iterdir(), key=natural_key) if match.is_dir() else [match]
                    for path in candidates:
                        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                            continue
                        resolved = path.resolve()
                        if not resolved.is_relative_to(source_materials.resolve()):
                            raise ValueError('原始材料路径超出材料目录')
                        relative = path.relative_to(source_materials).as_posix()
                        if relative in seen:
                            continue
                        seen.add(relative)
                        rotation = 270 if '左90' in row.get('G', '') else 90 if '右90' in row.get('G', '') else 180 if '180' in row.get('G', '') else 0
                        assets.append({'id': 'seedfile_' + hashlib.sha256((source_id + relative).encode()).hexdigest()[:24], 'name': path.name, 'raw': path.read_bytes(), 'source': '材料/' + relative, 'rotation': rotation})
            if not assets:
                raise ValueError('原始材料台账中的文件缺失')
            category = association[source_id]
            material = {'id': mid, 'category_id': category, 'name': row['B'], 'subject': SUBJECT, 'version': row.get('H', ''), 'years': [int(source_id[-4:])] if source_id.startswith('CW-') else [], 'valid_until': row.get('I', ''), 'mode': 'side_by_side' if row.get('F') == '并排' else 'page', 'content': '', 'description': '证件附件，需核对模板证明或授权正文' if source_id.startswith('SF-') else '由交付包材料台账导入；请核对适用年度及有效性', 'revision': 1, 'created_at': now(), 'updated_at': now(), 'source': {'ledger': '标书自动填充/示例/材料台账.xlsx', 'material_id': source_id}, 'file_ids': self.create_files(mid, assets, seed=True)}
            count += len(material['file_ids'])
            self.put('material', material)
        self.put('meta', {'id': 'seed', 'created_at': now()})
        self.put('meta', {'id': 'source_status', 'state': 'imported', 'message': '已复制交付包材料，原文件保持不变', 'material_count': len(association), 'file_count': sum(len(m['file_ids']) for m in self.all('material') if m['id'].startswith('seed_')), 'updated_at': now()})
        self.db.commit()

    def invalidate(self, material_id=None, category_id=None):
        for project in self.all('project'):
            changed = False
            for mapping in project['mappings']:
                if (material_id and material_id in mapping.get('material_ids', [])) or (category_id and category_id == mapping.get('category_id')):
                    mapping['confirmed'] = False
                    changed = True
            if changed:
                project['status'] = '待确认'
                project['updated_at'] = now()
                self.put('project', project)

    def category(self, body, key=None):
        old = self.require('category', key) if key else None
        if key in MANAGED_IDS:
            if body.get('enabled') is True or any(field != 'enabled' for field in body):
                raise PermissionError('信用查询栏目由全自动检测管理，不能手动启用或修改。')
            if body.get('enabled') is False:
                self.credit.disable('信用查询已停用；重新检测全部通过后才能启用。')
        value = copy.deepcopy(old) if old else {'id': uid('category'), 'layer': 'common', 'enabled': True, 'sort_order': len(self.all('category'))}
        if old and old.get('layer') == 'special':
            raise ValueError('专用栏目按已确认的四行业分类固定管理')
        if 'layer' in body and body['layer'] != 'common':
            raise ValueError('专用栏目按已确认的四行业分类固定管理')
        if 'name' in body or not old:
            value['name'] = text_value(body.get('name', ''), '栏目名称', 120, True)
        if 'aliases' in body or not old:
            aliases = body.get('aliases', [])
            if not isinstance(aliases, list) or len(aliases) > 40:
                raise ValueError('栏目别名最多 40 个')
            value['aliases'] = list(dict.fromkeys(text_value(v, '栏目别名', 120, True) for v in aliases))
        if 'enabled' in body:
            if not isinstance(body['enabled'], bool):
                raise ValueError('栏目启用状态不正确')
            value['enabled'] = body['enabled']
        if any(c['name'] == value['name'] and c['id'] != value['id'] for c in self.all('category')):
            raise ValueError('该栏目名称已存在')
        self.put('category', value)
        if old and old != value:
            self.invalidate(category_id=key)
        self.db.commit()
        return value

    def validate_material_form(self, body):
        import engine
        key = body.get('material_id')
        value = copy.deepcopy(self.require('material', key)) if key else {}
        for field in ('name', 'subject', 'category_id', 'years', 'valid_until', 'mode', 'content', 'description'):
            if field in body:
                value[field] = body[field]
        count = body.get('upload_count', len(value.get('file_ids', [])))
        if type(count) is not int or count < 0:
            raise ValueError('图片数量无效')
        result = self.special_feedback(value, count) if self.is_special(value) else engine.material_form_feedback(value, count)
        category = self.get('category', value.get('category_id', ''))
        if not category or not category.get('enabled') or value.get('category_id') in MANAGED_IDS:
            result['errors']['category_id'] = '请选择已启用的普通材料栏目。'
        result['project_warnings'] = []
        if key and not result['errors']:
            materials = self.internal_materials(include_special=True)
            for item in materials:
                if item['id'] == key:
                    item.update({k: v for k, v in value.items() if k != 'file_ids'})
            for project in self.all('project'):
                for row in engine.validate_mappings(project['mappings'], materials, project['headings'], self.matching_categories(project)):
                    if key in row.get('material_ids', []) and row.get('action') == 'fill':
                        for message in row.get('issues', []) + row.get('warnings', []):
                            result['project_warnings'].append(project['name'] + '：' + message)
        return result

    def material(self, body, key=None):
        old = self.require('material', key) if key else None
        if body.get('category_id') in MANAGED_IDS or (old and old.get('category_id') in MANAGED_IDS):
            raise PermissionError('信用查询材料仅由全自动官方查询归档，不能手工新增或修改。')
        value = copy.deepcopy(old) if old else {'id': uid('material'), 'revision': 0, 'file_ids': [], 'created_at': now()}
        defaults = {'name': '', 'subject': '', 'version': '', 'valid_until': '', 'content': '', 'description': ''}
        for field, default in defaults.items():
            if field in body or not old:
                value[field] = text_value(body.get(field, default), {'name': '材料名称', 'subject': '材料主体', 'version': '版本', 'valid_until': '有效期', 'content': '文字内容', 'description': '材料说明'}[field], 30000 if field == 'content' else 2000 if field == 'description' else 200, field in {'name', 'subject'})
        if 'category_id' in body or not old:
            category_id = body.get('category_id', '')
            self.require('category', category_id)
            value['category_id'] = category_id
        self.special_fields(value, body, old)
        if 'years' in body or not old:
            years = body.get('years', [])
            if not isinstance(years, list) or len(years) > 30 or any(isinstance(y, bool) or not isinstance(y, int) or y < 1900 or y > 2200 for y in years):
                raise ValueError('材料年度须为 1900—2200 之间的年份列表')
            value['years'] = sorted(set(years))
        if 'mode' in body or not old:
            if body.get('mode', 'page') not in {'page', 'side_by_side', 'text'}:
                raise ValueError('插入方式不正确')
            value['mode'] = body.get('mode', 'page')
        if old and 'files' in body:
            raise ValueError('添加附件请使用附件上传入口')
        if 'removed_file_ids' in body:
            removed = body['removed_file_ids']
            if not isinstance(removed, list) or len(set(removed)) != len(removed) or any(fid not in value['file_ids'] for fid in removed):
                raise ValueError('仅可移除当前材料的附件，且不能重复')
            value['file_ids'] = [fid for fid in value['file_ids'] if fid not in removed]
            if self.is_special(value):
                value['removed_file_ids'] = list(dict.fromkeys(value.get('removed_file_ids', []) + removed))
        if 'restore_file_ids' in body:
            restored = body['restore_file_ids']
            if not isinstance(restored, list) or any(fid not in value.get('removed_file_ids', []) for fid in restored):
                raise ValueError('只能恢复本组已移除的图片')
            value['file_ids'] += [fid for fid in dict.fromkeys(restored) if fid not in value['file_ids']]
            value['removed_file_ids'] = [fid for fid in value.get('removed_file_ids', []) if fid not in restored]
        if 'file_order' in body:
            order = body['file_order']
            if not isinstance(order, list) or len(order) != len(value['file_ids']) or set(order) != set(value['file_ids']):
                raise ValueError('附件顺序必须包含该材料全部附件，且不能重复')
            value['file_ids'] = order
        rotations = body.get('rotations', {})
        if not isinstance(rotations, dict):
            raise ValueError('图片旋转参数不正确')
        for fid, rotation in rotations.items():
            if fid not in value['file_ids'] or isinstance(rotation, bool) or rotation not in {0, 90, 180, 270}:
                raise ValueError('仅可旋转当前材料附件，角度为 0、90、180 或 270 度')
        if not old or any(field in body for field in ('name', 'subject', 'category_id', 'years', 'valid_until', 'mode', 'content', 'description')):
            feedback = self.validate_material_form({**value, 'material_id': key,
                'upload_count': len(value['file_ids']) if old else len(body.get('files', []))})
            if feedback['errors']:
                raise ValueError('；'.join(feedback['errors'].values()))
        if not old:
            value['file_ids'] = self.create_files(value['id'], body.get('files', []))
        for fid, rotation in rotations.items():
            file = self.require('file', fid)
            file['rotation'] = rotation
            self.put('file', file)
        value['revision'] += 1
        value['updated_at'] = now()
        self.put('material', value)
        self.material_changed(value)
        self.db.commit()
        return self.public_material(value)

    def add_files(self, key, body):
        value = self.require('material', key)
        if value.get('category_id') in MANAGED_IDS:
            raise PermissionError('信用查询截图仅由全自动官方查询更新，不能手工追加。')
        files = body.get('files')
        if not isinstance(files, list) or not files:
            raise ValueError('请至少选择一张图片')
        value['file_ids'] += self.create_files(key, files)
        value['revision'] += 1
        value['updated_at'] = now()
        self.put('material', value)
        self.material_changed(value)
        self.db.commit()
        return self.public_material(value)

    def scan(self, project):
        import engine
        result = engine.scan_document(str(self.path(project['template_storage'])), self.matching_categories(project),
                                      self.internal_materials(include_special=True, project=project), project.get('special_category_ids', []))
        project.update(result)
        project['special_locator_version'] = engine.SPECIAL_LOCATOR_VERSION
        project['common_locator_version'] = engine.COMMON_LOCATOR_VERSION
        project['sync_version'] = self.sync_version()
        for row in project['mappings']:
            row['confirmed'] = False
        project['updated_at'] = now()
        project['status'] = '待确认'
        return project

    def select_special_categories(self, key, body):
        import engine
        project = self.sync_material_matches(self.require('project', key))
        if not self.common_reviewed(project):
            raise ValueError('请先保存并完成通用材料核对，再选择专用材料分类。')
        if 'industry_ids' in body:
            industries = body['industry_ids']
            if (not isinstance(industries, list) or len(industries) > 100
                    or any(not isinstance(i, str) for i in industries)
                    or len(set(industries)) != len(industries)):
                raise ValueError('行业分类格式不正确或重复。')
            if 'category_ids' in body:
                raise ValueError('选择行业时无需另外提交材料栏目。')
            categories = sorted(self.all('category'), key=lambda c: c['sort_order'])
            ids = []
            for industry_id in industries:
                if not self.get('industry', industry_id):
                    raise ValueError('所选行业不存在，请重新选择行业分类。')
                group = [c['id'] for c in categories if c.get('layer') == 'special'
                         and c.get('industry_id') == industry_id and c.get('enabled')]
                if not group:
                    raise ValueError('所选行业暂无可核对的专用材料，请先完善行业材料设置。')
                ids.extend(group)
        else:
            # Existing clients and project records may still refer to individual columns.
            ids = body.get('category_ids')
        if (not isinstance(ids, list) or len(ids) > 100 or
                any(not isinstance(cid, str) for cid in ids) or len(set(ids)) != len(ids)):
            raise ValueError('专用材料分类格式不正确或重复。')
        if not ids and body.get('no_special_materials') is not True:
            raise ValueError('请选择专用材料分类，或明确勾选“本项目无需专用材料”。')
        if ids and body.get('no_special_materials') is True:
            raise ValueError('选择了专用分类，不能同时标记无需专用材料。')
        for cid in ids:
            category = self.require('category', cid)
            if category.get('layer') != 'special' or not category.get('enabled'):
                raise ValueError('请选择已启用的专用材料分类。')
        if set(ids) != set(project.get('special_category_ids', [])) or not project.get('special_selection_confirmed'):
            original = {r['id']: r for r in project['mappings']}
            project.update(special_category_ids=ids, special_selection_confirmed=True)
            scanned = engine.scan_document(str(self.path(project['template_storage'])), self.matching_categories(project),
                                           self.internal_materials(include_special=True, project=project), ids)
            # Keep every existing common decision, including manual positions and notes.
            common = [r for r in project['mappings']
                      if (self.get('category', r['category_id']) or {}).get('layer') == 'common']
            special = []
            for row in scanned['mappings']:
                if row.get('layer') == 'special':
                    row = copy.deepcopy(original.get(row['id'], row))
                    row['confirmed'] = False
                    special.append(row)
            project.update(mappings=common + special, headings=scanned['headings'], warnings=scanned['warnings'], requirements=scanned.get('requirements', {}))
            project['special_locator_version'] = engine.SPECIAL_LOCATOR_VERSION
            project = self.sync_material_matches(project)
            project['updated_at'] = now()
            project['status'] = '已确认' if self.project_reviewed(project) else '待确认'
            self.put('project', project)
            self.db.commit()
        return self.public_project(project)

    def preview_snapshot(self, key, body):
        """Read current UI choices without saving or clearing confirmations."""
        project = self.require('project', key)
        row = next((m for m in project['mappings'] if m['id'] == body.get('mapping_id')), None)
        if row is None:
            raise ValueError('核对项不存在，请重新打开项目。')
        locator = body.get('locator_id', row.get('locator_id'))
        if not isinstance(locator, str) or (locator and not any(h['id'] == locator for h in project['headings'])):
            raise ValueError('请先选择本项目中的模板插入位置。')
        ids = body.get('material_ids', row.get('material_ids', []))
        if (not isinstance(ids, list) or len(ids) > 100 or
                any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids)):
            raise ValueError('预览材料选择格式不正确。')
        materials = []
        for mid in ids:
            value = self.get('material', mid)
            special = bool(value) and self.is_special(value) and self.is_special(row)
            if (not value or value.get('archived') or
                    (value['category_id'] != row['category_id'] and
                     not (special and row['category_id'] in value.get('linked_category_ids', [])))):
                raise ValueError('预览材料不属于当前核对栏目或已停用，请重新选择。')
            # Current UI choices may include special materials not saved in the
            # project yet. Resolve those IDs after validating their category.
            materials.append(self.public_material(value))
        action = body.get('action', row.get('action', 'fill'))
        if action not in {'fill', 'missing', 'skip'}:
            raise ValueError('预览处理方式不正确。')
        return {'template': self.path(project['template_storage']),
                'template_hash': project['template_hash'], 'template_name': project['template_name'],
                'locator_id': locator, 'action': action,
                'materials': materials,
                'categories': copy.deepcopy(self.matching_categories(project))}

    def create_project(self, body):
        name = text_value(body.get('name', ''), '项目名称', 150, True)
        if body.get('use_sample') is True:
            sample = self.source_root / '填充测试文档.docx'
            if not sample.is_file():
                raise ValueError('未找到示例模板，请上传自己的 Word 模板')
            filename, raw = sample.name, sample.read_bytes()
        else:
            filename, raw = decode_upload({'name': body.get('filename', ''), 'data': body.get('data')}, MAX_DOCX)
        source_raw = raw
        raw, import_kind = normalize_word(filename, raw, MAX_DOCX)
        check_docx(Path(filename).with_suffix('.docx').name, raw)
        key = uid('project')
        storage = f'templates/{key}.docx'
        destination = self.path(storage)
        project = {'id': key, 'name': name, 'template_name': filename, 'template_storage': storage, 'template_hash': hashlib.sha256(raw).hexdigest(), 'created_at': now(), 'updated_at': now(), 'status': '待确认', 'mappings': [], 'headings': [], 'warnings': []}
        project['template_import_kind'] = import_kind
        project['template_source_hash'] = hashlib.sha256(source_raw).hexdigest()
        original = self.path(f'templates/{key}.original.doc') if import_kind != 'docx' else None
        try:
            destination.write_bytes(raw)
            if original:
                original.write_bytes(source_raw)
                project['template_source_storage'] = str(original.relative_to(self.root))
            self.scan(project)
            self.sync_material_matches(project)
            self.put('project', project)
            self.db.commit()
        except Exception:
            self.db.rollback()
            destination.unlink(missing_ok=True)
            if original:
                original.unlink(missing_ok=True)
            raise
        return self.public_project(project)

    def rescan(self, key):
        value = self.sync_material_matches(self.scan(self.require('project', key)))
        self.put('project', value)
        self.db.commit()
        return self.public_project(value)

    def update_mappings(self, key, body):
        import engine
        project = self.require('project', key)
        updates = body.get('mappings')
        if not isinstance(updates, list) or len(updates) > 1000:
            raise ValueError('栏目确认格式不正确')
        by_id = {v['id']: v for v in project['mappings']}
        locator_ids = {v['id'] for v in project['headings']}
        seen = set()
        for update in updates:
            if not isinstance(update, dict) or update.get('id') not in by_id or update['id'] in seen:
                raise ValueError('包含未知或重复栏目，请重新加载项目')
            seen.add(update['id'])
            current = by_id[update['id']]
            if update.get('confirmed') is True:
                # This save is the review the retired text-generation settings required.
                current.pop('authorization', None)
            if 'locator_id' in update and update['locator_id'] != current.get('locator_id'):
                current['location_match'] = {'status': 'manual', 'reason': '位置已由人工调整', 'candidates': []}
            if any(field in update and update[field] != current.get(field) for field in ('action', 'material_ids')):
                current['auto_match'] = False
            # Ignore retired authorization fields from cached browser clients.
            for field in ['locator_id', 'material_ids', 'action', 'confirmed', 'note']:
                if field in update:
                    current[field] = copy.deepcopy(update[field])
            if current.get('locator_id') and current['locator_id'] not in locator_ids:
                raise ValueError('所选插入位置不属于当前模板')
            ids = current.get('material_ids', [])
            if not isinstance(ids, list) or len(ids) > 100 or len(set(ids)) != len(ids):
                raise ValueError('材料选择格式不正确或包含重复材料')
            for mid in ids:
                self.require('material', mid)
            if current.get('action') not in {'fill', 'missing', 'skip'} or not isinstance(current.get('confirmed'), bool):
                raise ValueError('栏目操作或确认状态不正确')
            current['note'] = text_value(current.get('note', ''), '核对说明', 2000)
        requested = {v['id']: v.get('confirmed', False) for v in project['mappings']}
        validated = engine.validate_mappings(project['mappings'], self.internal_materials(include_special=True, project=project),
                                             project['headings'], self.matching_categories(project))
        prospective = dict(project, mappings=validated)
        if not self.common_reviewed(prospective):
            for row in validated:
                if row.get('layer') == 'special':
                    row['confirmed'] = False
                    requested[row['id']] = False
        for row in validated:
            if requested.get(row['id']) and row.get('status') == 'blocked':
                raise ValueError('该栏目尚不能确认：' + '；'.join(row.get('issues', [])))
        project['mappings'] = validated
        # Apply automatic matches now, so the saved review is what later steps see.
        project = self.sync_material_matches(project)
        project['updated_at'] = now()
        project['status'] = '已确认' if self.project_reviewed(project) else '待确认'
        self.put('project', project)
        self.db.commit()
        return self.public_project(project)

    def prepare_export(self, key, body):
        import engine
        mode = body.get('mode', 'draft')
        if mode not in {'draft', 'reviewed'}:
            raise ValueError('导出方式不正确')
        project = self.sync_material_matches(self.require('project', key))
        self.db.commit()
        if not project.get('special_selection_confirmed'):
            raise ValueError('请先完成专用材料分类选择及核对，再导出标书。')
        materials = self.internal_materials(include_special=True, project=project)
        categories = self.matching_categories(project)
        mappings = engine.validate_mappings(project['mappings'], materials, project['headings'], categories)
        if not mappings or any(not v.get('confirmed') for v in mappings):
            raise ValueError('请先逐项确认全部栏目，再导出标书')
        if any(v.get('status') == 'blocked' for v in mappings):
            raise ValueError('存在不满足要求的材料或定位，请修正后再导出')
        if mode == 'reviewed' and any(v.get('action') == 'missing' or v.get('status') == 'missing' for v in mappings):
            raise ValueError('仍有缺项，不能导出已核对版本；可导出带待补标记的草稿')
        template = self.path(project['template_storage'])
        if hashlib.sha256(template.read_bytes()).hexdigest() != project['template_hash']:
            raise ValueError('保存的模板发生变化，请重新上传模板')
        return copy.deepcopy((project, materials, categories, mappings, mode))

    def export(self, key, body, snapshot=None, progress=None):
        import engine
        if snapshot is None:
            with self.lock:
                try:
                    snapshot = self.prepare_export(key, body)
                except Exception:
                    self.db.rollback()
                    raise
        project, materials, categories, mappings, mode = snapshot
        template = self.path(project['template_storage'])
        key_export = uid('export')
        output = self.path(f'exports/{key_export}.docx')
        report_file = self.path(f'exports/{key_export}.json')
        try:
            result = engine.export_document(str(template), str(output), mappings, materials, mode=mode, categories=categories, progress=progress)
            if project.get('template_import_kind') == 'converted_doc':
                result.setdefault('warnings', []).append('此模板已从旧版 .doc 转换；请在 Word 中核对表格、分页和目录。')
            safe_name = re.sub(r'[^\w\-\u4e00-\u9fff]+', '_', project['name'])[:80]
            value = {'id': key_export, 'project_id': key, 'project_name': project['name'], 'filename': safe_name + ('_待补草稿' if mode == 'draft' else '_已核对') + '.docx', 'mode': mode, 'created_at': now(), 'filled_count': result.get('filled_count', 0), 'missing_count': result.get('missing_count', 0), 'skipped_count': result.get('skipped_count', 0), 'download_url': f'/api/exports/{key_export}/download', 'report_url': f'/api/exports/{key_export}/report', 'warnings': result.get('warnings', [])}
            selected = {mid for m in mappings for mid in m.get('material_ids', [])}
            sources = [{'id': m['id'], 'name': m['name'], 'subject': m['subject'], 'revision': m['revision'], 'years': m['years'], 'files': [{'id': f['id'], 'name': f['name'], 'rotation': f['rotation'], 'sha256': f['sha256']} for f in m['files']]} for m in materials if m['id'] in selected]
            evidence = {m['id']: m['credit_evidence'] for m in materials if m.get('credit_evidence')}
            for source in sources:
                if source['id'] in evidence:
                    source['credit_evidence'] = copy.deepcopy(evidence[source['id']])
            report = {'export': value, 'template': {'name': project['template_name'], 'sha256': project['template_hash'],
                      'source_sha256': project.get('template_source_hash', project['template_hash']),
                      'import_kind': project.get('template_import_kind', 'docx')},
                      'special_category_ids': project.get('special_category_ids', []),
                      'special_selection_confirmed': True, 'mappings': mappings, 'materials': sources,
                      'results': result.get('results', []), 'warnings': result.get('warnings', [])}
            # Engine reports are data. Do not expose private filesystem locations even on failure paths.
            sanitized = json.dumps(report, ensure_ascii=False, indent=2).replace(str(self.root), '[本地资料]')
            report_file.write_text(sanitized, encoding='utf-8')
            with self.lock:
                self.put('export', value)
                current = self.require('project', key)
                # A background export must not overwrite edits made after submission.
                if current['mappings'] == project['mappings'] and current.get('special_category_ids') == project.get('special_category_ids'):
                    current['status'] = '已导出'
                    current['updated_at'] = now()
                    self.put('project', current)
                self.db.commit()
            return value
        except Exception:
            output.unlink(missing_ok=True)
            report_file.unlink(missing_ok=True)
            raise


class Handler(BaseHTTPRequestHandler):
    server_version = 'LocalBidManager/1.0'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        # Do not log filenames, payloads or identifying data.
        pass

    def headers_common(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'")

    def send_bytes(self, body, mime, status=200, filename=None):
        self.send_response(status)
        self.headers_common()
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        if filename:
            from urllib.parse import quote
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(filename))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def json_response(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8', status)

    def validate_origin(self, mutation=False):
        expected = f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != expected:
            raise PermissionError('请通过本机入口 127.0.0.1 访问系统')
        if mutation:
            origin = self.headers.get('Origin')
            referer = self.headers.get('Referer')
            if origin and origin != f'http://{expected}':
                raise PermissionError('请求来源不正确，请从本机系统页面操作')
            if referer:
                parsed = urlsplit(referer)
                if parsed.scheme != 'http' or parsed.netloc != expected:
                    raise PermissionError('请求来源不正确，请从本机系统页面操作')
            if self.headers.get('Sec-Fetch-Site') in {'cross-site', 'same-site'}:
                raise PermissionError('不允许跨站修改本机资料')
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('请求须使用 JSON 格式')

    def body(self):
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('不支持分块上传，请重新选择文件')
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise ValueError('请求长度不正确') from None
        if size < 0 or size > MAX_REQUEST:
            self.close_connection = True
            raise ValueError('一次上传不能超过 80 MB，请分批添加图片')
        try:
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError('上传未完成')
            value = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError('请求内容不是有效的 JSON') from None
        if not isinstance(value, dict):
            raise ValueError('请求内容格式不正确')
        return value

    def dispatch(self):
        self.validate_origin(self.command in {'POST', 'PATCH'})
        url = urlsplit(self.path)
        path = unquote(url.path)
        if '\x00' in path or '\\' in path or '..' in PurePosixPath(path).parts:
            raise ValueError('请求路径不正确')
        bits = path.strip('/').split('/')
        method = 'GET' if self.command == 'HEAD' else self.command
        store = self.server.store
        if method == 'GET' and path == '/api/export-jobs':
            return self.json_response(store.export_jobs.list())
        if method == 'GET' and path == '/api/bootstrap':
            return self.json_response(store.bootstrap())
        if method == 'GET' and path == '/api/materials':
            return self.json_response(store.special_list(parse_qs(url.query)))
        if method == 'GET' and len(bits) == 3 and bits[:2] == ['api', 'materials']:
            return self.json_response(store.public_material(store.require('material', bits[2])))
        if method == 'GET' and path == '/api/credit-automation':
            return self.json_response(store.credit.status())
        if method == 'GET' and len(bits) == 3 and bits[:2] == ['api', 'projects']:
            return self.json_response(store.public_project(store.require('project', bits[2])))
        if method == 'GET' and len(bits) == 3 and bits[:2] == ['api', 'files']:
            file = store.require('file', bits[2])
            raw = store.path(file['storage']).read_bytes()
            mime = file['mime']
            if url.query == 'thumb=1':
                with Image.open(io.BytesIO(raw)) as picture:
                    picture = ImageOps.exif_transpose(picture)
                    picture.thumbnail((1000, 1000))
                    out = io.BytesIO()
                    picture.convert('RGB').save(out, format='JPEG', quality=85)
                    raw, mime = out.getvalue(), 'image/jpeg'
            return self.send_bytes(raw, mime)
        if method == 'GET' and len(bits) == 4 and bits[:2] == ['api', 'exports']:
            item = store.require('export', bits[2])
            if bits[3] == 'download':
                return self.send_bytes(store.path(f'exports/{item["id"]}.docx').read_bytes(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', filename=item['filename'])
            if bits[3] == 'report':
                return self.send_bytes(store.path(f'exports/{item["id"]}.json').read_bytes(), 'application/json; charset=utf-8', filename=item['filename'].replace('.docx', '_核对报告.json'))
        if method in {'POST', 'PATCH'}:
            body = self.body()
            if method == 'POST' and path == '/api/credit-automation/check':
                return self.json_response(store.credit.check(body), 202)
            if method == 'POST' and path == '/api/categories':
                return self.json_response(store.category(body), 201)
            if method == 'PATCH' and len(bits) == 3 and bits[:2] == ['api', 'categories']:
                return self.json_response(store.category(body, bits[2]))
            if method == 'POST' and path == '/api/industries':
                name = text_value(body.get('name', ''), '行业名称', 80, True)
                if any(v['name'] == name for v in store.all('industry')):
                    raise ValueError('该行业已存在')
                value = store.put('industry', {'id': uid('industry'), 'name': name, 'created_at': now()})
                store.db.commit()
                return self.json_response(value, 201)
            if method == 'POST' and path == '/api/materials/validate':
                return self.json_response(store.validate_material_form(body))
            if method == 'POST' and path == '/api/materials':
                return self.json_response(store.material(body), 201)
            if method == 'PATCH' and len(bits) == 3 and bits[:2] == ['api', 'materials']:
                return self.json_response(store.material(body, bits[2]))
            if method == 'POST' and len(bits) == 4 and bits[:2] == ['api', 'materials'] and bits[3] == 'files':
                return self.json_response(store.add_files(bits[2], body))
            if method == 'POST' and path == '/api/projects':
                return self.json_response(store.create_project(body), 201)
            if len(bits) == 4 and bits[:2] == ['api', 'projects']:
                if method == 'POST' and bits[3] == 'scan':
                    return self.json_response(store.rescan(bits[2]))
                if method == 'PATCH' and bits[3] == 'mappings':
                    return self.json_response(store.update_mappings(bits[2], body))
                if method == 'PATCH' and bits[3] == 'special-categories':
                    return self.json_response(store.select_special_categories(bits[2], body))
                if method == 'POST' and bits[3] == 'export-jobs':
                    return self.json_response(store.export_jobs.start(bits[2], body), 202)
        if method == 'GET' and not path.startswith('/api/'):
            allowed = {'/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/styles.css': ('styles.css', 'text/css; charset=utf-8')}
            target = allowed.get(path)
            if target and (APP_DIR / 'static' / target[0]).is_file():
                return self.send_bytes((APP_DIR / 'static' / target[0]).read_bytes(), target[1])
            if target and (APP_DIR / target[0]).is_file():
                return self.send_bytes((APP_DIR / target[0]).read_bytes(), target[1])
        raise LookupError('页面或接口不存在')

    def run_request(self):
        preview_route = re.fullmatch(
            r'/api/projects/([^/]+)/location-preview(?:/([a-f0-9]{64})/pages/([0-9]+))?',
            urlsplit(self.path).path)
        export_route = re.fullmatch(r'/api/projects/([^/]+)/export', urlsplit(self.path).path)
        if export_route and self.command == 'POST':
            # Document assembly takes the store lock only to snapshot and to record
            # the result, so other requests keep working during a long export.
            try:
                self.validate_origin(True)
                body = self.body()
                return self.json_response(self.server.store.export(export_route[1], body), 201)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                return self.request_error(exc)
        if preview_route:
            # Rendering must never hold the database lock or freeze other app requests.
            try:
                self.validate_origin(self.command == 'POST')
                key, preview_id, page_number = preview_route.groups()
                store = self.server.store
                if self.command == 'POST' and not preview_id:
                    body = self.body()
                    with store.lock:
                        snapshot = store.preview_snapshot(key, body)
                    return self.json_response(store.location_preview.describe(snapshot))
                if self.command in {'GET', 'HEAD'} and preview_id:
                    with store.lock:
                        project = store.require('project', key)
                        if preview_id != store.location_preview.key(project['template_hash']):
                            raise ValueError('预览不属于此模板，请重新打开。')
                    return self.send_bytes(store.location_preview.page(preview_id, int(page_number)), 'image/png')
                raise LookupError('页面或接口不存在')
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                return self.request_error(exc)
        with self.server.store.lock:
            try:
                self.dispatch()
            except (BrokenPipeError, ConnectionResetError):
                self.server.store.db.rollback()
            except Exception as exc:
                self.server.store.db.rollback()
                self.request_error(exc)

    def request_error(self, exc):
        self.close_connection = True
        if isinstance(exc, PermissionError):
            code, message = 403, str(exc)
        elif isinstance(exc, LookupError):
            code, message = 404, str(exc)
        elif isinstance(exc, (ValueError, TypeError)):
            code, message = 400, str(exc) if isinstance(exc, ValueError) else '请求字段格式不正确'
        elif isinstance(exc, FileNotFoundError):
            code, message = 404, '本地资料文件缺失，请重新上传'
        else:
            code, message = 500, '处理失败，请检查文件格式后重试；原始资料未修改'
        try:
            self.json_response({'error': message}, code)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = run_request
    do_HEAD = run_request
    do_POST = run_request
    do_PATCH = run_request

    def do_OPTIONS(self):
        self.json_response({'error': '不支持跨源访问'}, 403)


def create_server(port=8766, data_dir=DEFAULT_DATA, seed=None, source_root=SOURCE_ROOT):
    seed = Path(data_dir).resolve() == DEFAULT_DATA.resolve() if seed is None else seed
    store = Store(data_dir, seed=seed, source_root=source_root)
    try:
        httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        httpd.daemon_threads = True
        httpd.store = store
        return httpd
    except Exception:
        store.close()
        raise


def main():
    parser = argparse.ArgumentParser(description='本地标书管理系统')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--seed', action='store_true', default=None, help='向自定义数据目录导入交付包材料')
    parser.add_argument('--open', action='store_true', help='启动后打开浏览器')
    args = parser.parse_args()
    server = create_server(args.port, args.data_dir, args.seed)
    def stop_service(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_service)
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'标书管理系统已启动：{url}', flush=True)
    if args.open:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.store.close()


if __name__ == '__main__':
    main()
