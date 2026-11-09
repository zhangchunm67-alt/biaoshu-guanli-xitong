#!/usr/bin/env python3
"""标书材料管理服务。默认仅本机访问；花生壳模式可绑定公网域名并强制口令。"""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import html
import io
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, parse_qs, quote
import uuid
import warnings
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image, ImageOps

from contributions import Contributions
from credit_service import CreditService, MANAGED_IDS
from errors import ConflictError
from special import SpecialLibrary
from export_jobs import ExportJobs
from word_import import normalize_word
from location_preview import LocationPreview
import chapter_drafts
import framework_service

APP_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = APP_DIR / 'data'
SOURCE_ROOT = APP_DIR.parent
SUBJECT = '北京腾宝科技有限公司'
MAX_REQUEST = 80 * 1024 * 1024
MAX_IMAGE = 15 * 1024 * 1024
MAX_DOCX = 30 * 1024 * 1024
MAX_PIXELS = 40_000_000
REQUEST_READ_TIMEOUT = 30
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
IMAGE_FORMATS = {'PNG', 'JPEG', 'WEBP', 'BMP', 'TIFF'}
ACCESS_COOKIE = 'bid_access'
LOGIN_PATH = '/login'


class AuthRequired(PermissionError):
    """Remote access requires the configured access token."""


def normalize_public_host(value):
    raw = text_value(value, '公网访问域名', 253, True)
    parsed = urlsplit(raw if '://' in raw else 'http://' + raw)
    host = (parsed.netloc or '').strip().lower().rstrip('.')
    if not host or '/' in host or host.startswith('[') or '@' in host:
        raise ValueError('公网访问域名格式不正确，请填写花生壳域名，例如 xxx.imwork.net')
    return host


def normalize_bind_host(value):
    host = text_value(value, '监听地址', 64, True)
    if host not in {'127.0.0.1', '0.0.0.0'}:
        raise ValueError('监听地址仅支持 127.0.0.1 或 0.0.0.0')
    return host


def normalize_access_token(value):
    token = text_value(value, '访问口令', 200, True)
    if len(token) < 8:
        raise ValueError('访问口令至少 8 个字符')
    return token


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


def check_docx(name, raw, *, procurement=False):
    if Path(name).suffix.lower() != '.docx':
        raise ValueError('仅支持 .docx 格式的 Word 模板，请先转换旧版 .doc 文件')
    size_limit = framework_service.MAX_TENDER if procurement else MAX_DOCX
    if not raw or len(raw) > size_limit:
        raise ValueError(f'Word 文件不能为空，且不能超过 {size_limit // 1024 // 1024} MB')
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
                if (lower.endswith('vbaproject.bin') or '/activex/' in lower
                        or (not procurement and lower.startswith('word/embeddings/'))):
                    raise ValueError('模板包含宏或嵌入文件，请移除后上传')
                if not info.is_dir():
                    if procurement and lower.startswith('word/embeddings/'):
                        framework_service.check_attachment(z.read(info))
                    elif lower.endswith(('.xml', '.rels')):
                        # Template uploads are exported back to Word too. Apply the
                        # same passive-content boundary as procurement uploads.
                        framework_service.check_procurement_xml(info.filename, z.read(info))
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
    ('audit_report', '近三年财务审计报告', ['财务审计报告', '审计报告', '年度审计报告']),
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
        self.writing_state_revision = 0
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
            audit_category = self.get('category', 'audit_report')
            if audit_category and audit_category.get('name') in {'2023-2024年财务审计报告', '财务审计报告'}:
                audit_category['name'] = '近三年财务审计报告'
                self.put('category', audit_category)
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
            self.contributions = Contributions(self)
            self.export_jobs = ExportJobs(self)
            from writing_service import WritingService
            self.writing = WritingService(self)

    def close(self):
        self.writing.close()
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
        if kind in {'project', 'framework', 'material', 'category', 'writing_cases'}:
            self.writing_state_revision += 1
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
        return bool(rows or project.get('framework_id')) and all(r.get('status') != 'blocked' for r in rows)

    def project_reviewed(self, project):
        rows = project['mappings']
        return (project.get('special_selection_confirmed') is True and bool(rows or project.get('framework_id'))
                and all(r.get('confirmed') and r.get('status') != 'blocked' for r in rows))

    def archived_project(self, project):
        framework = self.get('framework', project.get('framework_id', ''))
        return bool(framework and framework.get('projects', {}).get('word') != project['id'])

    def mapping_revision(self, project):
        """Cover both saved choices and evidence that may change their meaning."""
        categories = self.matching_categories(project)
        category_ids = {row['category_id'] for row in project.get('mappings', [])}
        if 'authorization' in category_ids:
            category_ids.add('legal_identity')
        material_versions = sorted(
            (m['id'], m.get('revision', 0), m.get('archived', False), m.get('category_id'),
             sorted(m.get('linked_category_ids', [])))
            for m in self.all('material')
            if m.get('category_id') in category_ids or category_ids.intersection(m.get('linked_category_ids', [])))
        state = {key: project.get(key) for key in (
            'id', 'template_hash', 'mappings', 'headings', 'requirements',
            'special_category_ids', 'special_selection_confirmed', 'chapter_revision')}
        state.update(material_flow=project.get('material_flow'), text_review_confirmed=project.get('text_review_confirmed'))
        state.update(categories=sorted(categories, key=lambda c: c['id']), materials=material_versions)
        return hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':')).encode()).hexdigest()

    def require_mapping_revision(self, project, body):
        revision = body.get('mapping_revision')
        if not isinstance(revision, str) or revision != self.mapping_revision(project):
            raise ConflictError('材料核对或正文已在其他窗口更新，请重新读取当前项目后再保存或导出；本次未覆盖。')

    def require_material_revision(self, material, body):
        revision = body.get('revision')
        if type(revision) is not int or revision != material.get('revision', 0):
            raise ConflictError('材料已在其他窗口更新，请重新读取后再修改；本次未覆盖。')

    def public_file(self, file):
        return {**{key: file[key] for key in ['id', 'name', 'rotation', 'width', 'height']}, 'url': '/api/files/' + file['id'], 'source': file.get('source', '')}

    def public_material(self, material):
        value = copy.deepcopy(material)
        value.setdefault('revision', 0)
        value['files'] = [self.public_file(self.require('file', fid)) for fid in value.pop('file_ids', [])]
        value.pop('source', None)
        return value

    def sync_material_matches(self, project, *, context=None):
        """Fill unresolved automatic matches without changing manual selections."""
        import engine
        from material_rules import has_only_partial_issues, automatic_ids
        context = context or engine.ValidationContext()
        if self.archived_project(project):
            return project
        project = self.sync_authorization_targets(project)
        for heading in project['headings']:
            if (heading.get('kind') == 'common_appendix' and 'audit_report' in heading.get('category_ids', [])
                    and heading.get('title') in {'文末新增材料区 · 2023-2024年财务审计报告', '文末新增材料区 · 财务审计报告'}):
                heading['title'] = '文末新增材料区 · 近三年财务审计报告'
        materials, categories = self.internal_materials(include_special=True, project=project), self.matching_categories(project)
        original = copy.deepcopy(project['mappings'])
        rows = engine.validate_mappings(original, materials, project['headings'], categories, context=context,
                                        preserve_forms=bool(project.get('framework_id')))
        matches_changed = False
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
                checked = engine.validate_mappings([probe], materials, project['headings'], categories, context=context,
                                                   preserve_forms=bool(project.get('framework_id')))[0]
                if has_only_partial_issues(checked):
                    candidates.append(item)
            ids = automatic_ids(row, candidates, project['headings'], categories, validation_context=context,
                                preserve_forms=bool(project.get('framework_id')))
            if candidates:
                matches_changed |= row.get('action') != 'fill' or row.get('material_ids') != ids
                row.update(action='fill', material_ids=ids)
        if matches_changed:
            rows = engine.validate_mappings(rows, materials, project['headings'], categories, context=context,
                                            preserve_forms=bool(project.get('framework_id')))
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
            if self.archived_project(project):
                continue
            for row in project['mappings']:
                if (value['id'] in row.get('material_ids', []) or
                        (row['category_id'] == value['category_id'] and
                         row.get('action') != 'skip' and not row.get('material_ids'))):
                    row['confirmed'] = False
                    project['status'] = '待确认'
            self.sync_material_matches(project)
            self.put('project', project)

    def refresh_location_rows(self, project, scanned, layer):
        """Upgrade automatic locations and invalidate proven directory targets."""
        excluded = set(scanned.get('excluded_locator_ids', []))
        proposals = {}
        for row in scanned['mappings']:
            if row.get('layer', 'common') == layer and row.get('locator_id') not in excluded:
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
            directory = old_locator in excluded
            preserve_choice = (manual or row.get('action') == 'skip' or
                (row.get('action') == 'missing' and (directory or (layer == 'common' and row.get('auto_match') is False))))
            choices = proposals.get(row['category_id'], [])
            if directory and preserve_choice:
                row.update(locator_id='', confirmed=False, location_match={
                    'status': 'ambiguous', 'reason': '原位置属于目录，不能插入材料；请核对正文位置',
                    'candidates': [{'locator_id': r['locator_id'], 'title': r['title']} for r in choices if r['locator_id']]})
                if occupied.get(old_locator) == row['id']:
                    occupied.pop(old_locator)
                continue
            if preserve_choice:
                continue
            same = [r for r in choices if r.get('locator_id') == old_locator and old_locator]
            proposal = same[0] if same else choices[0] if len(choices) == 1 else None
            if proposal:
                locator = proposal.get('locator_id', '')
                match = copy.deepcopy(proposal.get('location_match', {}))
                if locator and occupied.get(locator, row['id']) != row['id']:
                    locator = ''
                    match.update(status='ambiguous', reason=('原位置属于目录，正文位置已被其他核对项使用，请核对' if directory else '新识别位置已被其他核对项使用，请核对'), candidates=[])
                row.update(locator_id=locator, title=proposal['title'], confirmed=False,
                           existing_content=proposal['existing_content'], location_match=match)
                if old_locator and occupied.get(old_locator) == row['id']:
                    occupied.pop(old_locator)
                if locator:
                    occupied[locator] = row['id']
            elif old_locator:
                row.update(locator_id='', confirmed=False, location_match={
                    'status': 'ambiguous', 'reason': ('原位置属于目录，不能插入材料；请核对正文位置' if directory else '原自动位置已失效，请核对新识别的候选位置'),
                    'candidates': [{'locator_id': r['locator_id'], 'title': r['title']} for r in choices if r['locator_id']]})
                if occupied.get(old_locator) == row['id']:
                    occupied.pop(old_locator)
        if any(not r.get('confirmed') for r in project['mappings']):
            project['status'] = '待确认'
        # Shared tax/social and text-template categories can acquire distinct
        # body slots under the new rules. Keep existing decisions and surface
        # newly discovered slots instead of silently dropping them.
        represented = {(r['category_id'], r.get('locator_id')) for r in project['mappings']}
        existing_ids = {r['id'] for r in project['mappings']}
        for fresh in scanned['mappings']:
            if (fresh.get('layer', 'common') == layer
                    and fresh['category_id'] in {'tax_social', 'text_template'}
                    and fresh.get('locator_id')
                    and (fresh['category_id'], fresh['locator_id']) not in represented
                    and fresh['id'] not in existing_ids):
                project['mappings'].append(copy.deepcopy(fresh))
                project['status'] = '待确认'

    def sync_special_locations(self, project):
        """Backfill unresolved legacy positions once, preserving every user decision."""
        import engine
        if (self.archived_project(project) or not project.get('special_selection_confirmed') or
                project.get('special_locator_version') == engine.SPECIAL_LOCATOR_VERSION):
            return project
        source = self.path(project['template_storage'])
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != project['template_hash']:
            return project
        scanned = self.scan_project_template(project)
        self.refresh_location_rows(project, scanned, 'special')
        # Keep old manual targets unless the new scan proves they are directory entries.
        excluded = set(scanned.get('excluded_locator_ids', []))
        headings = {h['id']: h for h in project['headings'] if h['id'] not in excluded}
        headings.update({h['id']: h for h in scanned['headings'] if h['id'] not in excluded})
        project['headings'] = list(headings.values())
        project['special_locator_version'] = engine.SPECIAL_LOCATOR_VERSION
        self.put('project', project)
        return project

    def sync_common_locations(self, project):
        """Upgrade unresolved automatic common matches without replacing user choices."""
        import engine
        if self.archived_project(project) or project.get('common_locator_version') == engine.COMMON_LOCATOR_VERSION:
            return project
        financial_only = project.get('common_locator_version') == 11 and engine.COMMON_LOCATOR_VERSION == 12
        financial_rows = [row for row in project['mappings'] if row['category_id'] == 'audit_report']
        if financial_only and not financial_rows:
            project['common_locator_version'] = engine.COMMON_LOCATOR_VERSION
            self.put('project', project)
            return project
        source = self.path(project['template_storage'])
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != project['template_hash']:
            return project
        scanned = self.scan_project_template(project)
        if financial_only:
            # Version 12 changes financial requirement scopes, not insertion
            # locations. Do not invalidate unrelated, already reviewed materials.
            fresh_headings = {h['id']: h for h in scanned['headings']}
            fresh_rows = {row.get('locator_id'): row for row in scanned['mappings']
                          if row['category_id'] == 'audit_report'}
            for heading in project['headings']:
                fresh = fresh_headings.get(heading['id'], {})
                if 'audit_report' in fresh.get('category_ids', []) and fresh.get('requirement'):
                    heading['requirement'] = copy.deepcopy(fresh['requirement'])
            requirement = scanned.get('requirements', {}).get('audit_report', {})
            project.setdefault('requirements', {})['audit_report'] = copy.deepcopy(requirement)
            changed = False
            materials = self.internal_materials(include_special=True, project=project)
            categories = self.matching_categories(project)
            for row in financial_rows:
                fresh = fresh_headings.get(row.get('locator_id'), {})
                scoped = (fresh.get('requirement') or fresh_rows.get(row.get('locator_id'), {}).get('requirement')
                          or requirement)
                before = (copy.deepcopy(row.get('requirement')), row.get('required_years'))
                row['requirement'] = copy.deepcopy(scoped)
                checked = engine.validate_mappings([row], materials, project['headings'], categories,
                                                   preserve_forms=bool(project.get('framework_id')))[0]
                row['required_years'] = checked['required_years']
                if before != (row['requirement'], row['required_years']):
                    row['confirmed'] = False
                    changed = True
            if changed:
                project['status'] = '待确认'
            project['common_locator_version'] = engine.COMMON_LOCATOR_VERSION
            self.put('project', project)
            return project
        self.refresh_location_rows(project, scanned, 'common')
        excluded = set(scanned.get('excluded_locator_ids', []))
        headings = {h['id']: h for h in project['headings'] if h['id'] not in excluded}
        headings.update({h['id']: h for h in scanned['headings'] if h['id'] not in excluded})
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

    def sync_authorization_targets(self, project):
        """Refresh only legacy authorization headings; retain all user choices."""
        targets = [h for h in project.get('headings', []) if h.get('authorization_source')]
        if (self.archived_project(project) or not targets or
                all('authorization_original_body_present' in h for h in targets)):
            return project
        source = self.path(project['template_storage'])
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != project['template_hash']:
            return project
        fresh = {h['id']: h for h in self.scan_project_template(project)['headings']}
        ids = {h['id'] for h in targets} | {h['authorization_source'] for h in targets}
        project['headings'] = [fresh.get(h['id'], h) if h['id'] in ids else h for h in project['headings']]
        self.put('project', project)
        return project

    def public_project(self, project):
        if not self.archived_project(project):
            project = self.sync_common_locations(project)
            project = self.sync_special_locations(project)
            project = self.sync_material_matches(project)
        self.db.commit()
        value = copy.deepcopy(project)
        value['mapping_revision'] = self.mapping_revision(project)
        value.pop('template_storage', None)
        value.pop('template_source_storage', None)
        value.pop('chapter_texts', None)
        if project.get('template_import_kind') == 'converted_doc':
            value['warnings'] = ['此模板已从旧版 .doc 转换；导出后请在 Word 中核对表格、分页和目录。', *value.get('warnings', [])]
        value.setdefault('special_category_ids', [])
        value.setdefault('special_selection_confirmed', False)
        selected_categories = set(value['special_category_ids'])
        industry_ids = {c.get('industry_id') for c in self.all('category') if c['id'] in selected_categories}
        value['special_industry_ids'] = [i['id'] for i in self.all('industry') if i['id'] in industry_ids]
        selected = {mid for row in value['mappings'] if row.get('layer') == 'special'
                    for mid in row.get('material_ids', [])}
        value['selected_special_materials'] = [self.public_material(m) for m in self.all('material')
                                               if m['id'] in selected]
        value['exports'] = [v for v in self.all('export') if v['project_id'] == project['id']]
        return value

    def bootstrap(self):
        categories = sorted(self.all('category'), key=lambda c: c['sort_order'])
        materials = [self.public_material(v) for v in self.all('material') if not self.is_special(v)]
        historical = framework_service.archived_project_ids(self)
        projects = [self.project_summary(v) for v in reversed(self.all('project')) if v['id'] not in historical]
        exports = list(reversed(self.all('export')))
        source = self.get('meta', 'source_status') or {'state': 'not_imported', 'message': '此数据目录未导入示例材料'}
        source.pop('id', None)
        return {'categories': categories, 'materials': materials, 'projects': projects, 'exports': exports, 'industries': self.all('industry'), 'credit_automation': self.credit.status(), 'stats': {'special_material_count': sum(self.is_special(m) and not m.get('archived') for m in self.all('material')), 'category_count': sum(c['layer'] == 'common' for c in categories), 'material_count': len(materials), 'file_count': sum(len(m['files']) for m in materials), 'project_count': len(projects), 'export_count': len(exports), 'source_status': source, **self.contributions.stats()}}

    @staticmethod
    def project_summary(project):
        """List saved metadata only; opening a project performs its version upgrades."""
        return {key: project[key] for key in (
            'id', 'name', 'template_name', 'status', 'created_at', 'updated_at',
            'framework_id', 'framework_artifact_id') if key in project}

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
            if self.archived_project(project):
                continue
            changed = False
            for mapping in project['mappings']:
                if (material_id and material_id in mapping.get('material_ids', [])) or (category_id and category_id == mapping.get('category_id')):
                    mapping['confirmed'] = False
                    if project.get('material_flow') == 'text-first' and mapping.get('layer') == 'special':
                        project['text_review_confirmed'] = False
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
        context = engine.ValidationContext()
        key = body.get('material_id')
        value = copy.deepcopy(self.require('material', key)) if key else {}
        for field in ('name', 'subject', 'category_id', 'years', 'valid_until', 'mode', 'content', 'description'):
            if field in body:
                value[field] = body[field]
        count = body.get('upload_count', len(value.get('file_ids', [])))
        if type(count) is not int or count < 0:
            raise ValueError('图片数量无效')
        result = engine.material_form_feedback(value, count, special=self.is_special(value), context=context)
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
                for row in engine.validate_mappings(project['mappings'], materials, project['headings'], self.matching_categories(project), context=context,
                                                    preserve_forms=bool(project.get('framework_id'))):
                    if key in row.get('material_ids', []) and row.get('action') == 'fill':
                        for message in row.get('issues', []) + row.get('warnings', []):
                            result['project_warnings'].append(project['name'] + '：' + message)
        return result

    def material(self, body, key=None, *, provenance=None, commit=True):
        """Create or edit a material. A reviewed contribution passes its provenance and commits
        together with closing the submission."""
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
            if provenance:
                value['contribution'] = copy.deepcopy(provenance)
        for fid, rotation in rotations.items():
            file = self.require('file', fid)
            file['rotation'] = rotation
            self.put('file', file)
        value['revision'] += 1
        value['updated_at'] = now()
        self.put('material', value)
        self.material_changed(value)
        if commit:
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

    def scan_project_template(self, project):
        import engine
        categories = self.matching_categories(project)
        materials = self.internal_materials(include_special=True, project=project)
        results = engine.scan_document(self.path(project['template_storage']), categories, materials,
                                       project.get('special_category_ids', []),
                                       preserve_forms=bool(project.get('framework_id')))
        if project.get('framework_id'):
            # A procurement-defined part may be a standalone quotation with no evidence slots.
            # Unmatched library defaults must not become additional submission requirements.
            rows = [row for row in results['mappings'] if row.get('locator_id')
                    or row.get('location_match', {}).get('candidates')
                    or row.get('requirement', {}).get('status') in {'required', 'conditional', 'optional'}]
            for row in rows:
                if row.get('layer') == 'special' and row['action'] == 'fill' and not row['material_ids']:
                    row['action'] = 'missing'
            results['mappings'] = engine.validate_mappings(rows, materials, results['headings'], categories,
                                                         preserve_forms=True)
        return results

    def scan(self, project):
        import engine
        result = self.scan_project_template(project)
        project.update(result)
        project['special_locator_version'] = engine.SPECIAL_LOCATOR_VERSION
        project['common_locator_version'] = engine.COMMON_LOCATOR_VERSION
        for row in project['mappings']:
            row['confirmed'] = False
        project['updated_at'] = now()
        project['status'] = '待确认'
        return project

    def prepare_text_materials(self, key, text_first=False):
        """Open text review directly using the enabled library categories."""
        categories = sorted(self.all('category'), key=lambda c: c['sort_order'])
        text_categories = {'text_template'}
        for material in self.all('material'):
            if material.get('mode') == 'text' and not material.get('archived'):
                text_categories.update([material['category_id'], *material.get('linked_category_ids', [])])
        ids = [c['id'] for c in categories if c.get('layer') == 'special' and c.get('enabled')
               and c['id'] in text_categories]
        result = self.select_special_categories(key, {
            'category_ids': ids, 'no_special_materials': not ids}, text_first=text_first)
        return result

    def select_special_categories(self, key, body, text_first=False):
        import engine
        project = self.sync_material_matches(self.require('project', key))
        if not text_first and not self.common_reviewed(project):
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
        if (set(ids) != set(project.get('special_category_ids', [])) or not project.get('special_selection_confirmed')
                or (text_first and (project.get('material_flow') != 'text-first' or 'text_review_confirmed' not in project))):
            original = {r['id']: r for r in project['mappings']}
            project.update(special_category_ids=ids, special_selection_confirmed=True)
            if text_first:
                project['material_flow'] = 'text-first'
                project['text_review_confirmed'] = not ids
            scanned = self.scan_project_template(project)
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
        import authorization
        project = self.sync_authorization_targets(self.require('project', key))
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
        action = body.get('action', row.get('action', 'fill'))
        if action not in {'fill', 'missing', 'skip'}:
            raise ValueError('预览处理方式不正确。')
        heading = next((h for h in project['headings'] if h['id'] == locator), None)
        retained_form = bool(project.get('framework_id') and row['category_id'] == 'text_template'
                             and authorization.preserves_form(heading))
        preserve_original = bool(retained_form or (row['category_id'] == 'text_template' and heading
            and action == 'skip' and heading.get('authorization_original_body_present')))
        if preserve_original:
            action, ids = 'skip', []
        materials = []
        for mid in ids:
            value = self.get('material', mid)
            special = bool(value) and self.is_special(value) and self.is_special(row)
            # The picker and export validator also accept the legal representative's
            # identity images in this combined identity/authorization material row.
            identity_image = bool(value) and row['category_id'] == 'authorization' and value.get('category_id') == 'legal_identity' and value.get('mode') in {'page', 'side_by_side'}
            if (not value or value.get('archived') or
                    (value['category_id'] != row['category_id'] and
                     not identity_image and
                     not (special and row['category_id'] in value.get('linked_category_ids', [])))):
                raise ValueError('预览材料不属于当前核对栏目或已停用，请重新选择。')
            # Current UI choices may include special materials not saved in the
            # project yet. Resolve those IDs after validating their category.
            materials.append(self.public_material(value))
        authorization_body = None
        preview_rows = [dict(r, locator_id=locator, action=action, material_ids=ids)
                        if r['id'] == row['id'] else r for r in project['mappings']]
        resolved, bodies = authorization.link_body_targets(preview_rows, project['headings'],
            self.internal_materials(include_special=True, project=project),
            preserve_forms=bool(project.get('framework_id')))
        locator = next(r['locator_id'] for r in resolved if r['id'] == row['id'])
        if locator.startswith('auth-body:'):
            authorization_body = bodies.get(locator)
            if not authorization_body:
                raise ValueError('请先保存授权书核对：保留采购原表，或选用授权书文字模板。')
            if authorization_body.get('from_original'):
                authorization_body = None
        return {'template': self.path(project['template_storage']),
                'mapping_revision': self.mapping_revision(project),
                'template_hash': project['template_hash'], 'template_name': project['template_name'],
                'locator_id': locator, 'action': action,
                'preserve_original': preserve_original,
                'materials': materials,
                'authorization_body': authorization_body,
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
        value = self.scan(self.require('project', key))
        self.put('project', value)
        self.db.commit()
        return self.public_project(value)

    def update_mappings(self, key, body):
        import authorization
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
            if current.get('action') not in {'fill', 'missing', 'skip'} or not isinstance(current.get('confirmed'), bool):
                raise ValueError('栏目操作或确认状态不正确')
            heading = next((h for h in project['headings'] if h['id'] == current.get('locator_id')), None)
            if (project.get('framework_id') and current['category_id'] == 'text_template'
                    and authorization.preserves_form(heading)):
                current.update(action='skip', material_ids=[])
                ids = []
            for mid in ids:
                self.require('material', mid)
            current['note'] = text_value(current.get('note', ''), '核对说明', 2000)
        requested = {v['id']: v.get('confirmed', False) for v in project['mappings']}
        validated = engine.validate_mappings(project['mappings'], self.internal_materials(include_special=True, project=project),
                                             project['headings'], self.matching_categories(project),
                                             preserve_forms=bool(project.get('framework_id')))
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
        if project.get('material_flow') == 'text-first':
            project['text_review_confirmed'] = all(r['status'] != 'blocked' for r in validated if r.get('layer') == 'special')
        project['updated_at'] = now()
        project['status'] = '已确认' if self.project_reviewed(project) else '待确认'
        self.put('project', project)
        self.db.commit()
        return self.public_project(project)

    def prepare_export(self, key, body):
        import engine
        context = engine.ValidationContext()
        mode = body.get('mode', 'draft')
        if mode not in {'draft', 'reviewed'}:
            raise ValueError('导出方式不正确')
        project = self.sync_material_matches(self.require('project', key), context=context)
        self.db.commit()
        if not project.get('special_selection_confirmed'):
            raise ValueError('请先进入文字材料核对，再导出标书。')
        if project.get('material_flow') == 'text-first' and not project.get('text_review_confirmed'):
            raise ValueError('请先保存文字材料核对，再导出标书。')
        materials = self.internal_materials(include_special=True, project=project)
        categories = self.matching_categories(project)
        mappings = engine.validate_mappings(project['mappings'], materials, project['headings'], categories, context=context,
                                            preserve_forms=bool(project.get('framework_id')))
        if not mappings and not (project.get('framework_id') and mode == 'draft'):
            raise ValueError('没有可导出的栏目，请先扫描模板')
        if mode == 'reviewed' and any(not v.get('confirmed') for v in mappings):
            raise ValueError('未经人工核对的材料请导出初稿')
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
                snapshot = self.prepare_export(key, body)
        project, materials, categories, mappings, mode = snapshot
        template = self.path(project['template_storage'])
        key_export = uid('export')
        output = self.path(f'exports/{key_export}.docx')
        report_file = self.path(f'exports/{key_export}.json')
        try:
            result = engine.export_document(str(template), str(output), mappings, materials, mode=mode, categories=categories, progress=progress,
                                            framework_drafts=project.get('chapter_texts', {}) if project.get('framework_id') else None)
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
            if project.get('framework_id'):
                report['chapter_revision'] = project.get('chapter_revision', 0)
                report['written_chapters'] = list(project.get('chapter_texts', {}))
            # Engine reports are data. Do not expose private filesystem locations even on failure paths.
            sanitized = json.dumps(report, ensure_ascii=False, indent=2).replace(str(self.root), '[本地资料]')
            report_file.write_text(sanitized, encoding='utf-8')
            with self.lock:
                self.put('export', value)
                current = self.require('project', key)
                # A background export must not overwrite edits made after submission.
                if (current['mappings'] == project['mappings'] and current.get('special_category_ids') == project.get('special_category_ids')
                        and current.get('chapter_revision') == project.get('chapter_revision')):
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

    def setup(self):
        super().setup()
        self.connection.settimeout(REQUEST_READ_TIMEOUT)

    def log_message(self, fmt, *args):
        # Do not log filenames, payloads or identifying data.
        pass

    def headers_common(self, preview=False, *, login=False):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'SAMEORIGIN' if preview else 'DENY')
        self.send_header('Cache-Control', 'no-store')
        if login:
            policy = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        elif preview:
            policy = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
        else:
            policy = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                      "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'")
        self.send_header('Content-Security-Policy', policy)

    def send_bytes(self, body, mime, status=200, filename=None, *, preview=False, login=False, extra_headers=None):
        self.send_response(status)
        self.headers_common(preview, login=login)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        if filename:
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(filename))
        for key, value in (extra_headers or []):
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def json_response(self, value, status=200, extra_headers=None):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8',
                        status, extra_headers=extra_headers)

    def local_hosts(self):
        port = self.server.server_port
        return {f'127.0.0.1:{port}', f'localhost:{port}'}

    def access_mode(self):
        """remote = 花生壳映射口（材料永不可下）；local = 本机环回口；None = 未启用花生壳。"""
        return getattr(self.server, 'access_mode', None)

    def allowed_hosts(self):
        mode = self.access_mode()
        if mode == 'remote':
            public = getattr(self.server, 'public_host', None)
            return {public.lower()} if public else set()
        if mode == 'local':
            return self.local_hosts()
        hosts = self.local_hosts()
        public = getattr(self.server, 'public_host', None)
        if public:
            hosts.add(public.lower())
        return hosts

    def origin_key(self, value):
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        if port is None:
            port = 443 if parsed.scheme == 'https' else 80
        return parsed.scheme, parsed.hostname.lower().rstrip('.'), port

    def allowed_origin_keys(self):
        mode = self.access_mode()
        if mode == 'remote':
            keys = set()
        else:
            keys = {('http', '127.0.0.1', self.server.server_port),
                    ('http', 'localhost', self.server.server_port)}
            if mode == 'local':
                return keys
        public = getattr(self.server, 'public_host', None)
        if public and mode != 'local':
            try:
                parsed = urlsplit('//' + public)
                host = (parsed.hostname or '').lower().rstrip('.')
                port = parsed.port
            except ValueError:
                host, port = '', None
            if host:
                if port is None:
                    keys.add(('http', host, 80))
                    keys.add(('https', host, 443))
                else:
                    keys.add(('http', host, port))
                    keys.add(('https', host, port))
        return keys

    def log_origin_reject(self, origin, referer):
        print(f'[origin-reject] {self.command} {urlsplit(self.path).path} '
              f'host={self.request_host()!r} origin={origin!r} referer={referer!r} '
              f'sec_fetch_site={self.headers.get("Sec-Fetch-Site")!r}',
              file=sys.stderr, flush=True)

    def request_host(self):
        return (self.headers.get('Host') or '').strip().lower()

    def is_public_request(self):
        # 花生壳映射口与本机环回口拆开：不靠 Host 判断，避免穿透流量伪造 Host 绕过材料门禁。
        mode = self.access_mode()
        if mode == 'remote':
            return True
        if mode == 'local':
            return False
        public = getattr(self.server, 'public_host', None)
        return bool(public) and self.request_host() == public.lower()

    def require_intranet_files(self):
        """身份证、营业执照等材料原件仅允许本机环回入口读取，花生壳映射口不可下载。"""
        if self.is_public_request():
            raise PermissionError('营业执照、身份证等材料原件仅限内网查看；花生壳外网不可下载或导出含材料文件。')

    def require_reviewer(self):
        """投稿可从花生壳提交，但查看投稿文件、入库和退回只在本机环回入口进行。"""
        if self.is_public_request():
            raise PermissionError('投稿审核与文件查看仅限本机入口；花生壳外网只能提交和查看处理状态。')

    def send_image(self, raw, mime, thumb=False):
        if thumb:
            with Image.open(io.BytesIO(raw)) as picture:
                picture = ImageOps.exif_transpose(picture)
                picture.thumbnail((1000, 1000))
                out = io.BytesIO()
                picture.convert('RGB').save(out, format='JPEG', quality=85)
                raw, mime = out.getvalue(), 'image/jpeg'
        return self.send_bytes(raw, mime)

    def _redact_material_files(self, material):
        if not isinstance(material, dict) or 'files' not in material:
            return
        count = len(material.get('files') or [])
        material['files'] = []
        material['files_intranet_only'] = True
        material['file_count'] = count

    def redact_public_payload(self, value):
        """Hide material file URLs from peanut-shell clients while keeping metadata."""
        if not self.is_public_request() or not isinstance(value, dict):
            return value
        payload = copy.deepcopy(value)
        for key in ('materials', 'selected_special_materials', 'items'):
            for material in payload.get(key) or []:
                self._redact_material_files(material)
        self._redact_material_files(payload)
        intranet_port = getattr(self.server, 'intranet_port', None)
        local_hint = (f'http://127.0.0.1:{intranet_port}/'
                      if intranet_port else '本机环回入口')
        payload['remote_access'] = {
            'public': True,
            'materials_intranet_only': True,
            'intranet_port': intranet_port,
            'message': f'外网可编制标书；营业执照、身份证等材料原件与含材料导出请在本机打开 {local_hint}',
        }
        return payload

    def access_authenticated(self):
        token = getattr(self.server, 'access_token', None)
        if not token:
            return True
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer ') and secrets.compare_digest(auth[7:], token):
            return True
        cookie = SimpleCookie()
        cookie.load(self.headers.get('Cookie', ''))
        morsel = cookie.get(ACCESS_COOKIE)
        return bool(morsel and secrets.compare_digest(unquote(morsel.value), token))

    def login_page(self, message='', status=401):
        intranet_port = getattr(self.server, 'intranet_port', None)
        if self.access_mode() == 'remote' and intranet_port:
            default = f'请输入访问口令。材料原件请在本机打开 http://127.0.0.1:{intranet_port}/'
        else:
            default = '请输入访问口令。营业执照、身份证等材料原件仅本机可查看。'
        notice = html.escape(message or default)
        body = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>标书管理系统 · 访问验证</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;margin:0;background:#f4f6f8;color:#1f2937}}
main{{max-width:420px;margin:12vh auto;padding:28px;background:#fff;border-radius:12px;box-shadow:0 8px 30px rgba(15,23,42,.08)}}
h1{{font-size:1.25rem;margin:0 0 8px}}p{{color:#64748b;margin:0 0 18px}}label{{display:block;margin-bottom:12px}}
input{{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #cbd5e1;border-radius:8px;font:inherit}}
button{{width:100%;padding:11px 12px;border:0;border-radius:8px;background:#0f766e;color:#fff;font:inherit;cursor:pointer}}
.err{{color:#b91c1c;margin-bottom:12px}}</style>
<main><h1>标书管理系统</h1><p>本机服务器远程访问。材料原件不出内网。</p>
{"<p class=err>"+notice+"</p>" if message else "<p>"+notice+"</p>"}
<form method="post" action="{LOGIN_PATH}"><label>访问口令<input type="password" name="token" required minlength="8" autocomplete="current-password"></label>
<button type="submit">进入系统</button></form></main>'''.encode()
        self.send_bytes(body, 'text/html; charset=utf-8', status, login=True)

    def set_access_cookie(self, token):
        # 花生壳免费映射是 HTTP；只有反向代理明确告知 HTTPS 时才加 Secure，
        # 否则浏览器会丢弃该 Cookie，导致登录后仍停在验证页。
        forwarded_proto = (self.headers.get('X-Forwarded-Proto') or '').split(',')[0].strip().lower()
        attrs = f'{ACCESS_COOKIE}={quote(token, safe="")}; Path=/; HttpOnly; SameSite=Lax'
        if forwarded_proto == 'https':
            attrs += '; Secure'
        return [('Set-Cookie', attrs)]

    def handle_login(self):
        if self.command == 'GET' or self.command == 'HEAD':
            return self.login_page()
        if self.command != 'POST':
            raise LookupError('页面或接口不存在')
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('不支持分块上传')
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise ValueError('请求长度不正确') from None
        if size < 0 or size > 4096:
            raise ValueError('登录请求过大')
        raw = self.rfile.read(size) if size else b''
        if len(raw) != size:
            raise ValueError('登录请求未完成')
        ctype = self.headers.get_content_type()
        token = ''
        if ctype == 'application/x-www-form-urlencoded':
            token = (parse_qs(raw.decode('utf-8', 'replace')).get('token') or [''])[0]
        elif ctype == 'application/json':
            try:
                payload = json.loads(raw.decode() if raw else '{}')
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError('请求内容不是有效的 JSON') from None
            token = payload.get('token', '') if isinstance(payload, dict) else ''
        else:
            raise ValueError('请使用登录表单提交口令')
        expected = getattr(self.server, 'access_token', None)
        if not expected or not isinstance(token, str) or not secrets.compare_digest(token, expected):
            return self.login_page('口令不正确', 401)
        body = ('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=/">'
                '<p>验证成功，正在进入…</p>').encode()
        self.send_bytes(body, 'text/html; charset=utf-8', 303, login=True,
                        extra_headers=self.set_access_cookie(expected) + [('Location', '/')])

    def require_access(self):
        if not getattr(self.server, 'access_token', None):
            return
        if self.access_authenticated():
            return
        path = unquote(urlsplit(self.path).path)
        if path.startswith('/api/'):
            raise AuthRequired('请先登录：打开系统页面输入访问口令，或使用 Authorization: Bearer <口令>')
        raise AuthRequired('请先登录')

    def validate_origin(self, mutation=False):
        host = self.request_host()
        if host not in self.allowed_hosts():
            raise PermissionError('请通过本机入口或已配置的花生壳域名访问系统')
        if mutation:
            origin = (self.headers.get('Origin') or '').strip()
            referer = (self.headers.get('Referer') or '').strip()
            path = unquote(urlsplit(self.path).path)
            allowed = self.allowed_origin_keys()
            if origin:
                if origin == 'null':
                    # 部分手机内置浏览器/WebView 提交登录表单时会给 Origin: null。
                    # 登录接口不依赖 Cookie，放行它不会影响已登录操作的 CSRF 边界。
                    if path != LOGIN_PATH:
                        self.log_origin_reject(origin, referer)
                        raise PermissionError('请求来源不正确，请从系统页面操作')
                elif self.origin_key(origin) not in allowed:
                    self.log_origin_reject(origin, referer)
                    raise PermissionError('请求来源不正确，请从系统页面操作')
            if referer and self.origin_key(referer) not in allowed:
                self.log_origin_reject(origin, referer)
                raise PermissionError('请求来源不正确，请从系统页面操作')
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.log_origin_reject(origin, referer)
                raise PermissionError('不允许跨站修改本机资料')
            if path != LOGIN_PATH and self.headers.get_content_type() != 'application/json':
                raise ValueError('请求须使用 JSON 格式')

    def body(self):
        if hasattr(self, '_request_body'):
            return self._request_body
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
        except TimeoutError:
            raise ValueError('上传超时，请重新选择文件后重试') from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError('请求内容不是有效的 JSON') from None
        if not isinstance(value, dict):
            raise ValueError('请求内容格式不正确')
        self._request_body = value
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
        if method == 'GET' and len(bits) == 4 and bits[:2] == ['api', 'projects'] and bits[3] == 'writing':
            return self.json_response(store.writing.describe(bits[2], view=parse_qs(url.query).get('view', ['full'])[0]))
        if method == 'GET' and path == '/api/frameworks':
            return self.json_response(framework_service.list_frameworks(store))
        if method == 'GET' and bits[:2] == ['api', 'frameworks'] and len(bits) >= 3:
            if len(bits) == 3:
                return self.json_response(framework_service.describe(store, bits[2]))
            if (len(bits) == 4 and bits[3] == 'source') or (len(bits) == 5 and bits[3] == 'artifacts'):
                file, mime, name = framework_service.asset(store, bits[2], bits[-1])
                preview = mime.startswith('text/html') and url.query != 'download=1'
                return self.send_bytes(file.read_bytes(), mime, filename=None if preview else name, preview=preview)
        if method == 'GET' and len(bits) == 4 and bits[:2] == ['api', 'projects'] and bits[3] == 'chapter-drafts':
            return self.json_response(chapter_drafts.read_chapters(store, bits[2]))
        if method == 'GET' and len(bits) == 4 and bits[:2] == ['api', 'projects'] and bits[3] == 'redline-scan':
            import redline_scan
            return self.json_response(redline_scan.describe(store, bits[2]))
        if method == 'GET' and path == '/api/export-jobs':
            return self.json_response(store.export_jobs.list())
        if method == 'GET' and path == '/api/bootstrap':
            return self.json_response(self.redact_public_payload(store.bootstrap()))
        if method == 'GET' and path == '/api/materials':
            return self.json_response(self.redact_public_payload(store.special_list(parse_qs(url.query))))
        if method == 'GET' and len(bits) == 3 and bits[:2] == ['api', 'materials']:
            return self.json_response(self.redact_public_payload(store.public_material(store.require('material', bits[2]))))
        if method == 'GET' and path == '/api/credit-automation':
            return self.json_response(store.credit.status())
        if method == 'GET' and len(bits) == 3 and bits[:2] == ['api', 'projects']:
            return self.json_response(self.redact_public_payload(store.public_project(store.require('project', bits[2]))))
        if method == 'GET' and len(bits) == 3 and bits[:2] == ['api', 'files']:
            self.require_intranet_files()
            file = store.require('file', bits[2])
            return self.send_image(store.path(file['storage']).read_bytes(), file['mime'], url.query == 'thumb=1')
        if method == 'GET' and path == '/api/submissions':
            return self.json_response(store.contributions.list(parse_qs(url.query), remote=self.is_public_request()))
        if method == 'GET' and len(bits) == 3 and bits[:2] == ['api', 'submissions']:
            return self.json_response(store.contributions.describe(bits[2], remote=self.is_public_request()))
        if method == 'GET' and len(bits) == 5 and bits[:2] == ['api', 'submissions'] and bits[3] == 'files':
            self.require_reviewer()
            meta, raw, kind = store.contributions.file(bits[2], bits[4])
            if kind == 'material':
                return self.send_image(raw, meta['mime'], url.query == 'thumb=1')
            return self.send_bytes(raw, 'application/octet-stream', filename=PurePosixPath(meta['name']).name)
        if method == 'GET' and path == '/api/case-library':
            return self.json_response(store.contributions.cases(parse_qs(url.query), remote=self.is_public_request()))
        if method == 'GET' and len(bits) == 5 and bits[:2] == ['api', 'case-library'] and bits[3] == 'files':
            self.require_reviewer()
            meta, raw = store.contributions.case_file(bits[2], bits[4])
            return self.send_bytes(raw, 'application/octet-stream', filename=PurePosixPath(meta['name']).name)
        if method == 'GET' and len(bits) == 4 and bits[:2] == ['api', 'exports']:
            item = store.require('export', bits[2])
            if bits[3] == 'download':
                self.require_intranet_files()
                return self.send_bytes(store.path(f'exports/{item["id"]}.docx').read_bytes(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', filename=item['filename'])
            if bits[3] == 'report':
                self.require_intranet_files()
                return self.send_bytes(store.path(f'exports/{item["id"]}.json').read_bytes(), 'application/json; charset=utf-8', filename=item['filename'].replace('.docx', '_核对报告.json'))
        if method in {'POST', 'PATCH'}:
            body = self.body()
            if len(bits) >= 3 and bits[:2] == ['api', 'projects']:
                project = store.require('project', bits[2])
                framework = store.get('framework', project.get('framework_id', ''))
                if framework and framework.get('projects', {}).get('word') != project['id']:
                    raise ValueError('目录已更新，此工作稿已归档；请重新打开当前整本项目后修改')
                if framework and framework.get('review_version') and not framework.get('requirements_confirmed'):
                    raise ValueError('招标要求尚未重新确认，请返回招标解读核对')
                if len(bits) == 4 and bits[3] in {'mappings', 'special-categories', 'text-materials',
                                                'scan', 'export-jobs', 'export', 'chapter-drafts',
                                                'redline-scan'}:
                    store.require_mapping_revision(project, body)
            if len(bits) >= 3 and bits[:2] == ['api', 'materials'] and (
                    (method == 'PATCH' and len(bits) == 3) or
                    (method == 'POST' and len(bits) == 4 and bits[3] == 'files')):
                store.require_material_revision(store.require('material', bits[2]), body)
            if method == 'POST' and len(bits) == 4 and bits[:2] == ['api', 'submissions'] and bits[3] in {'accept', 'reject'}:
                self.require_reviewer()
                decide = store.contributions.accept if bits[3] == 'accept' else store.contributions.reject
                return self.json_response(decide(bits[2], body))
            if method == 'PATCH' and len(bits) == 3 and bits[:2] == ['api', 'case-library']:
                self.require_reviewer()
                return self.json_response(store.contributions.update_case(bits[2], body))
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
                if body.get('files'):
                    self.require_intranet_files()
                return self.json_response(self.redact_public_payload(store.material(body)), 201)
            if method == 'PATCH' and len(bits) == 3 and bits[:2] == ['api', 'materials']:
                return self.json_response(self.redact_public_payload(store.material(body, bits[2])))
            if method == 'POST' and len(bits) == 4 and bits[:2] == ['api', 'materials'] and bits[3] == 'files':
                self.require_intranet_files()
                return self.json_response(self.redact_public_payload(store.add_files(bits[2], body)))
            if method == 'POST' and len(bits) == 4 and bits[:2] == ['api', 'frameworks']:
                if bits[3] == 'confirm':
                    return self.json_response(framework_service.confirm(store, bits[2], body))
                if bits[3] == 'remove':
                    return self.json_response(framework_service.remove(store, bits[2]))
            if method == 'POST' and len(bits) == 5 and bits[:2] == ['api', 'projects'] and bits[3] == 'writing':
                if bits[4] == 'generate':
                    return self.json_response(store.writing.start(bits[2], body), 202)
                if bits[4] == 'accept':
                    return self.json_response(store.writing.accept(bits[2], body))
            if method == 'PATCH' and len(bits) == 4 and bits[:2] == ['api', 'projects'] and bits[3] == 'chapter-drafts':
                return self.json_response(chapter_drafts.save_chapters(store, bits[2], body))
            if method == 'POST' and path == '/api/projects':
                return self.json_response(self.redact_public_payload(store.create_project(body)), 201)
            if len(bits) == 4 and bits[:2] == ['api', 'projects']:
                if method == 'POST' and bits[3] == 'scan':
                    return self.json_response(self.redact_public_payload(store.rescan(bits[2])))
                if method == 'PATCH' and bits[3] == 'mappings':
                    return self.json_response(self.redact_public_payload(store.update_mappings(bits[2], body)))
                if method == 'PATCH' and bits[3] == 'special-categories':
                    return self.json_response(self.redact_public_payload(store.select_special_categories(bits[2], body)))
                if method == 'POST' and bits[3] == 'text-materials':
                    return self.json_response(self.redact_public_payload(
                        store.prepare_text_materials(bits[2], text_first=body.get('text_first') is True)))
                if method == 'POST' and bits[3] == 'redline-scan':
                    import redline_scan
                    return self.json_response(redline_scan.acknowledge(store, bits[2], body))
                if method == 'POST' and bits[3] == 'export-jobs':
                    self.require_intranet_files()
                    return self.json_response(store.export_jobs.start(bits[2], body), 202)
                if method == 'POST' and bits[3] == 'export':
                    self.require_intranet_files()
                    return self.json_response(store.export(bits[2], body), 201)
        if method == 'GET' and not path.startswith('/api/'):
            allowed = {'/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/workflow.js': ('workflow.js', 'text/javascript; charset=utf-8'), '/workflow.css': ('workflow.css', 'text/css; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/styles.css': ('styles.css', 'text/css; charset=utf-8')}
            target = allowed.get(path)
            if target and (APP_DIR / 'static' / target[0]).is_file():
                return self.send_bytes((APP_DIR / 'static' / target[0]).read_bytes(), target[1])
            if target and (APP_DIR / target[0]).is_file():
                return self.send_bytes((APP_DIR / target[0]).read_bytes(), target[1])
        raise LookupError('页面或接口不存在')

    def run_request(self):
        # A keep-alive handler serves multiple requests. Never reuse a prior body.
        self.__dict__.pop('_request_body', None)
        path = unquote(urlsplit(self.path).path)
        try:
            if path == LOGIN_PATH:
                self.validate_origin(self.command == 'POST')
                return self.handle_login()
            self.validate_origin(False)
            self.require_access()
        except AuthRequired as exc:
            if path.startswith('/api/'):
                return self.request_error(exc)
            return self.login_page(str(exc), 401)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            return self.request_error(exc)
        if self.command in {'POST', 'PATCH'}:
            try:
                self.validate_origin(True)
                if path != LOGIN_PATH:
                    self.body()  # Receive and validate bytes before holding the shared DB lock.
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                return self.request_error(exc)
        if self.command == 'POST' and urlsplit(self.path).path == '/api/submissions':
            # Image and case checks may take seconds; only publishing takes the store lock.
            try:
                return self.json_response(self.server.store.contributions.submit(
                    self.body(), remote=self.is_public_request()), 201)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                return self.request_error(exc)
        case_route = re.fullmatch(r'/api/projects/([^/]+)/writing/cases', urlsplit(self.path).path)
        if case_route and self.command == 'POST':
            try:
                return self.json_response(self.server.store.writing.upload(case_route[1], self.body()))
            except Exception as exc:
                return self.request_error(exc)
        review_route = re.fullmatch(r'/api/frameworks/([^/]+)/(requirements|outline)',
                                   urlsplit(self.path).path)
        if review_route and self.command == 'PATCH':
            try:
                self.validate_origin(True)
                return self.json_response(framework_service.revise(
                    self.server.store, review_route[1], self.body(), review_route[2]))
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                return self.request_error(exc)
        if self.command == 'POST' and urlsplit(self.path).path == '/api/frameworks':
            # The skill may take seconds on large tenders. Only its publish step holds the store lock.
            try:
                self.validate_origin(True)
                return self.json_response(framework_service.create(self.server.store, self.body()), 201)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                return self.request_error(exc)
        preview_route = re.fullmatch(
            r'/api/projects/([^/]+)/location-preview(?:/([a-f0-9]{64})/pages/([0-9]+))?',
            urlsplit(self.path).path)
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
                    result = store.location_preview.describe(snapshot)
                    if snapshot.get('authorization_body'):
                        with store.lock:
                            current = store.require('project', key)
                            if store.mapping_revision(current) != snapshot['mapping_revision']:
                                raise ValueError('授权书核对内容已变化，请重新加载预览。')
                            grants = store.location_preview.composed_grants
                            grants[(key, result['preview_id'])] = (snapshot['template_hash'], snapshot['mapping_revision'])
                            while len(grants) > 64:
                                grants.popitem(last=False)
                    return self.json_response(result)
                if self.command in {'GET', 'HEAD'} and preview_id:
                    with store.lock:
                        project = store.require('project', key)
                        composed = store.location_preview.composed_grants.get((key, preview_id))
                        if (preview_id != store.location_preview.key(project['template_hash']) and
                                composed != (project['template_hash'], store.mapping_revision(project))):
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
        if isinstance(exc, AuthRequired):
            code, message = 401, str(exc)
        elif isinstance(exc, ConflictError):
            code, message = 409, str(exc)
        elif isinstance(exc, PermissionError):
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


def _attach_server(httpd, *, store, bind_host, public, token, access_mode=None, intranet_port=None,
                   owns_store=True):
    httpd.daemon_threads = True
    httpd.store = store
    httpd.bind_host = bind_host
    httpd.public_host = public
    httpd.access_token = token
    httpd.access_mode = access_mode
    httpd.intranet_port = intranet_port
    httpd.owns_store = owns_store
    return httpd


def create_server(port=8766, data_dir=DEFAULT_DATA, seed=None, source_root=SOURCE_ROOT,
                  host='127.0.0.1', public_host=None, access_token=None, access_mode=None,
                  intranet_port=None, store=None):
    seed = Path(data_dir).resolve() == DEFAULT_DATA.resolve() if seed is None else seed
    bind_host = normalize_bind_host(host)
    public = normalize_public_host(public_host) if public_host else None
    token = normalize_access_token(access_token) if access_token else None
    if public and not token:
        raise ValueError('启用花生壳公网域名时必须设置访问口令（--access-token）')
    if token and not public and bind_host == '0.0.0.0':
        raise ValueError('绑定 0.0.0.0 时请同时配置 --public-host 与 --access-token')
    if public and access_mode is None:
        access_mode = 'remote'
    if access_mode not in {None, 'remote', 'local'}:
        raise ValueError('access_mode 仅支持 remote 或 local')
    if access_mode and not public:
        raise ValueError('未启用公网域名时不能指定 access_mode')
    if access_mode == 'local':
        bind_host = '127.0.0.1'
    owns_store = store is None
    store = store or Store(data_dir, seed=seed, source_root=source_root)
    try:
        httpd = ThreadingHTTPServer((bind_host, port), Handler)
        return _attach_server(
            httpd, store=store, bind_host=bind_host, public=public, token=token,
            access_mode=access_mode, intranet_port=intranet_port, owns_store=owns_store)
    except Exception:
        if owns_store:
            store.close()
        raise


def create_oray_servers(port=8766, intranet_port=None, data_dir=DEFAULT_DATA, seed=None,
                        source_root=SOURCE_ROOT, host='0.0.0.0', public_host=None, access_token=None):
    """花生壳模式：映射口（remote）永不下发材料；本机环回口（local）可查看材料。"""
    public = normalize_public_host(public_host) if public_host else None
    if not public:
        raise ValueError('create_oray_servers 需要 public_host')
    remote_port = int(port)
    requested_local = None if intranet_port in (None, 0) else int(intranet_port)
    if remote_port < 0 or remote_port > 65535:
        raise ValueError('端口号无效')
    if requested_local is not None and (requested_local < 1 or requested_local > 65535):
        raise ValueError('本机材料端口号无效')
    if requested_local is not None and remote_port not in {0} and requested_local == remote_port:
        raise ValueError('本机材料端口不能与花生壳映射端口相同')
    seed_flag = Path(data_dir).resolve() == DEFAULT_DATA.resolve() if seed is None else seed
    store = Store(data_dir, seed=seed_flag, source_root=source_root)
    remote = None
    try:
        # 先绑映射口，再决定本机口：未指定时用映射口+1；映射口为 0（测试）时交给系统分配。
        remote = create_server(
            port=remote_port, data_dir=data_dir, seed=seed_flag, source_root=source_root,
            host=host, public_host=public, access_token=access_token, access_mode='remote',
            intranet_port=None, store=store)
        remote.owns_store = True
        if requested_local is None:
            local_port = 0 if remote_port == 0 else remote.server_port + 1
        else:
            local_port = requested_local
        if local_port == remote.server_port:
            raise ValueError('本机材料端口不能与花生壳映射端口相同')
        local = create_server(
            port=local_port, data_dir=data_dir, seed=False, source_root=source_root,
            host='127.0.0.1', public_host=public, access_token=access_token, access_mode='local',
            intranet_port=None, store=store)
        local.owns_store = False
        remote.intranet_port = local.server_port
        local.intranet_port = local.server_port
        return remote, local
    except Exception:
        if remote is not None:
            remote.server_close()
        store.close()
        raise


def main():
    import writing_model
    writing_model.load_local_env(APP_DIR)
    parser = argparse.ArgumentParser(description='标书管理系统（默认仅本机；花生壳模式可远程访问）')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--intranet-port', type=int, default=0,
                        help='花生壳模式下本机材料入口端口，默认映射口+1')
    parser.add_argument('--host', default='127.0.0.1', help='监听地址：127.0.0.1（默认）或 0.0.0.0（花生壳）')
    parser.add_argument('--public-host', default='', help='花生壳访问域名，例如 xxx.imwork.net')
    parser.add_argument('--access-token', default='', help='远程访问口令；启用公网域名时必填')
    parser.add_argument('--data-dir', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--seed', action='store_true', default=None, help='向自定义数据目录导入交付包材料')
    parser.add_argument('--open', action='store_true', help='启动后打开浏览器')
    args = parser.parse_args()
    public_host = args.public_host.strip() or None
    access_token = args.access_token.strip() or None
    intranet_port = args.intranet_port or None
    local_server = None
    if public_host:
        server, local_server = create_oray_servers(
            port=args.port, intranet_port=intranet_port, data_dir=args.data_dir, seed=args.seed,
            host=args.host, public_host=public_host, access_token=access_token)
    else:
        server = create_server(args.port, args.data_dir, args.seed, host=args.host,
                               public_host=public_host, access_token=access_token)

    def stop_service(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_service)
    open_url = (f'http://127.0.0.1:{local_server.server_port}'
                if local_server else f'http://127.0.0.1:{server.server_port}')
    if local_server:
        print(f'本机材料入口：{open_url} （仅 127.0.0.1，勿映射到花生壳）', flush=True)
        print(f'花生壳映射口：http://127.0.0.1:{server.server_port}/ （外网不可下载材料）', flush=True)
        print(f'花生壳远程入口：http://{server.public_host}/ （须映射到 127.0.0.1:{server.server_port}）', flush=True)
        print('外网可编制标书；营业执照、身份证等材料原件与含材料导出仅限本机材料入口。', flush=True)
        threading.Thread(target=local_server.serve_forever, daemon=True).start()
    else:
        print(f'标书管理系统已启动：{open_url}', flush=True)
    if args.open:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, args=(open_url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if local_server:
            local_server.shutdown()
            local_server.server_close()
        server.server_close()
        if getattr(server, 'owns_store', True):
            server.store.close()


if __name__ == '__main__':
    main()
