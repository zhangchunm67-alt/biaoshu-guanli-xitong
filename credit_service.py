"""Fail-closed official credit capture; network work never holds the store lock."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit
import uuid

from PIL import Image


POLICY = 'all_three_fully_automatic'
SUBJECT = '北京腾宝科技有限公司'
SOURCES = (
    {'id': 'credit_dishonest', 'name': '失信被执行人查询截图', 'url': 'https://zxgk.court.gov.cn/shixin/', 'aliases': ['失信被执行人查询', '失信被执行人查询记录', '供应商未被列入失信被执行人查询截图']},
    {'id': 'credit_tax_violation', 'name': '重大税收违法失信主体查询截图', 'url': 'https://www.creditchina.gov.cn/zhuanxiangchaxun/zhongdashuishouweifaanjian/', 'aliases': ['重大税收违法失信主体查询', '重大税收违法案件查询截图', '供应商未被列入重大税收违法失信主体查询截图']},
    {'id': 'credit_procurement_violation', 'name': '政府采购严重违法失信行为记录查询截图', 'url': 'https://www.creditchina.gov.cn/zhuanxiangchaxun/zhengfucaigouyanzhongweifashixinmingdan/', 'aliases': ['政府采购严重违法失信行为记录查询', '政府采购严重违法失信行为记录名单查询截图', '供应商未被列入政府采购严重违法失信行为记录名单查询截图']},
)
MANAGED_IDS = frozenset(source['id'] for source in SOURCES)
FLAGS = ('query_completed', 'subject_verified', 'result_verified', 'screenshot_verified')
CAPTURE_TIMEOUT = 100
MAX_SCREENSHOT_BYTES = 15 * 1024 * 1024


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def empty_sources(reason='尚未完成全自动查询验证', status='unverified'):
    return [{**{key: item[key] for key in ('id', 'name', 'url')}, 'status': status,
             'reason': reason, **{flag: False for flag in FLAGS}} for item in SOURCES]


def initial_state():
    return {'id': 'credit_automation', 'policy': POLICY, 'enabled': False,
            'status': 'untested', 'checked_at': None, 'run_id': None,
            'subject': SUBJECT, 'sources': empty_sources(), 'material_ids': [],
            'message': '尚未证明三个来源均可全自动完成查询与截图，信用材料暂不启用。'}


def capture_official(subject, output_dir):
    """Invoke only the checked-in worker and its fixed official entrypoints."""
    script = Path(__file__).resolve().parent / 'scripts' / 'credit_capture.cjs'
    if not script.is_file():
        raise RuntimeError('全自动查询脚本尚未就绪，未启用信用材料。')
    bundle = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    bundled_node = bundle / 'bin/node'
    node = str(bundled_node) if bundled_node.is_file() else shutil.which('node')
    if not node:
        raise RuntimeError('本机缺少 Node.js 运行环境，无法验证全自动查询。')
    env = os.environ.copy()
    if (bundle / 'node_modules').is_dir():
        env['NODE_PATH'] = str(bundle / 'node_modules')
    try:
        completed = subprocess.run(
            [node, str(script), '--subject', subject, '--output-dir', str(output_dir)],
            cwd=str(script.parent.parent), env=env, capture_output=True, text=True,
            timeout=CAPTURE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError('官网查询超过 100 秒，未取得完整可信结果，未启用信用材料。') from None
    if completed.returncode != 0:
        raise RuntimeError('查询脚本未正常完成；请检查浏览器依赖与官网可访问性，信用材料仍停用。')
    if len(completed.stdout) > 1024 * 1024:
        raise RuntimeError('查询脚本返回内容异常，未启用信用材料。')
    try:
        return json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        raise RuntimeError('查询脚本未返回完整的结果记录，未启用信用材料。') from None


def _trusted_url(value, expected):
    if not isinstance(value, str):
        return False
    try:
        parsed, trusted = urlsplit(value), urlsplit(expected)
        return (parsed.scheme == 'https' and parsed.hostname == trusted.hostname
                and parsed.path.rstrip('/') == trusted.path.rstrip('/')
                and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except ValueError:
        return False


def validate_result(payload, subject, output_dir):
    """Validate a private worker result, never a client-supplied success claim."""
    if not isinstance(payload, dict) or payload.get('schema_version') != 1 or payload.get('subject') != subject:
        raise ValueError('查询结果格式或查询主体不一致，未启用信用材料。')
    items = payload.get('sources')
    if not isinstance(items, list) or len(items) != len(SOURCES) or not all(isinstance(item, dict) for item in items):
        raise ValueError('三个官方来源的查询结果不完整，未启用信用材料。')
    by_id = {item.get('id'): item for item in items if isinstance(item.get('id'), str)}
    if set(by_id) != MANAGED_IDS:
        raise ValueError('查询结果包含未知或重复来源，未启用信用材料。')
    finished = payload.get('finished_at')
    started = payload.get('started_at')
    try:
        start_time = datetime.fromisoformat(str(started).replace('Z', '+00:00'))
        finish_time = datetime.fromisoformat(str(finished).replace('Z', '+00:00'))
        if not start_time.tzinfo or not finish_time.tzinfo or finish_time < start_time:
            raise ValueError('time')
    except (ValueError, TypeError):
        raise ValueError('查询结果缺少有效的查询时间，未启用信用材料。') from None
    public, assets = [], {}
    for definition in SOURCES:
        item = by_id[definition['id']]
        entry = {key: definition[key] for key in ('id', 'name', 'url')}
        status = item.get('status')
        if status not in {'no_records', 'records_found', 'blocked', 'unverified', 'error'}:
            status = 'unverified'
        entry.update(status=status, reason=str(item.get('reason') or '')[:600],
                     **{flag: item.get(flag) is True for flag in FLAGS})
        # URL equality authenticates the adapter identity; final URL must remain official.
        if item.get('url') != definition['url'] or not _trusted_url(item.get('final_url'), definition['url']):
            entry.update(status='unverified', reason='查询结果来源地址未通过官方来源校验。',
                         **{flag: False for flag in FLAGS})
        else:
            # Do not expose possible URL query/session parameters in public evidence.
            entry['final_url'] = item['final_url'].split('?', 1)[0].split('#', 1)[0]
        if entry['status'] == 'no_records' and all(entry[flag] for flag in FLAGS):
            name = item.get('screenshot')
            path = output_dir / (name if isinstance(name, str) else '')
            try:
                if name != definition['id'] + '.png' or path.is_symlink() or path.resolve().parent != output_dir.resolve():
                    raise ValueError('path')
                if not path.is_file() or not 0 < path.stat().st_size <= MAX_SCREENSHOT_BYTES:
                    raise ValueError('size')
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                if not isinstance(item.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']) or digest != item['sha256']:
                    raise ValueError('hash')
                with Image.open(io.BytesIO(raw)) as picture:
                    if picture.format != 'PNG' or picture.width * picture.height > 40_000_000:
                        raise ValueError('image')
                    picture.verify()
                assets[definition['id']] = {'raw': raw, 'sha256': digest}
            except Exception:
                entry.update(status='unverified', reason='查询截图缺失、格式无效或校验值不一致。', screenshot_verified=False)
        elif entry['status'] == 'no_records':
            entry.update(status='unverified', reason=entry['reason'] or '查询、主体、结果或截图尚未完成验证。')
        if entry['status'] == 'records_found':
            entry['reason'] = entry['reason'] or '查询出现记录，需核实适用性，未归档为无不良记录材料。'
        public.append(entry)
    verified = payload.get('all_verified') is True and len(assets) == len(SOURCES) and all(
        entry['status'] == 'no_records' and all(entry[flag] for flag in FLAGS) for entry in public)
    return public, assets if verified else {}, verified, finished


class CreditService:
    def __init__(self, store):
        self.store = store
        self.worker = capture_official
        self.thread = None
        self.closed = False
        self._initialize()

    def _initialize(self):
        state = self.store.get('meta', 'credit_automation') or initial_state()
        if state.get('status') == 'running':
            state.update(status='blocked', enabled=False,
                         message='上次检测未完成或服务已重启，请重新检测；信用材料保持停用。')
        for index, source in enumerate(SOURCES, start=len(self.store.all('category'))):
            category = self.store.get('category', source['id'])
            if not category:
                category = {key: copy.deepcopy(source[key]) for key in ('id', 'name', 'aliases')}
                category.update(layer='common', sort_order=index, enabled=False)
            else:
                # These definitions are managed, not user-editable; keep real template aliases current.
                if category.get('name') != source['name'] or category.get('aliases') != source['aliases']:
                    category.update(name=source['name'], aliases=copy.deepcopy(source['aliases']))
                    self.store.invalidate(category_id=source['id'])
            category['managed_by'] = 'credit_automation'
            # A persisted verified state must also reference all three real stored materials.
            category['enabled'] = bool(state.get('enabled') and state.get('status') == 'verified')
            self.store.put('category', category)
        if not self._has_verified_materials(state):
            if state.get('enabled'):
                state.update(status='blocked', message='上次查询材料不完整或已变化，请重新检测。')
            state['enabled'] = False
            self._disable()
        self.store.put('meta', state)
        self.store.db.commit()

    def _has_verified_materials(self, state):
        if state.get('status') != 'verified' or not state.get('enabled'):
            return False
        ids = state.get('material_ids') or []
        if len(ids) != len(SOURCES):
            return False
        categories = set()
        for mid in ids:
            material = self.store.get('material', mid)
            evidence = material.get('credit_evidence') if material else None
            if not evidence or evidence.get('run_id') != state.get('run_id') or evidence.get('verification_status') != 'verified':
                return False
            categories.add(material.get('category_id'))
            files = material.get('file_ids') or []
            if len(files) != 1:
                return False
            record = self.store.get('file', files[0])
            try:
                raw = self.store.path(record['storage']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != evidence.get('screenshot_sha256'):
                    return False
            except (OSError, TypeError, KeyError, ValueError):
                return False
        return categories == MANAGED_IDS

    def status(self):
        result = copy.deepcopy(self.store.get('meta', 'credit_automation') or initial_state())
        result.pop('id', None)
        ids = result.pop('material_ids', [])
        result['materials'] = [self.store.public_material(self.store.require('material', mid))
                               for mid in ids] if result.get('enabled') else []
        return result

    def _disable(self):
        for source in SOURCES:
            category = self.store.require('category', source['id'])
            category['enabled'] = False
            self.store.put('category', category)
            self.store.invalidate(category_id=source['id'])
        for material in self.store.all('material'):
            if material.get('category_id') in MANAGED_IDS and material.get('credit_evidence'):
                material['credit_evidence']['verification_status'] = 'needs_review'
                self.store.put('material', material)

    def disable(self, message):
        self._disable()
        state = self.store.get('meta', 'credit_automation') or initial_state()
        state.update(enabled=False, status='blocked', material_ids=[], message=message)
        # A manual disable cancels the authority of any worker already in flight.
        state['run_id'] = None
        self.store.put('meta', state)
        self.store.db.commit()

    def check(self, body):
        if set(body) - {'subject'}:
            raise ValueError('检测只接受查询主体，不能提交来源、路径或成功结果。')
        subject = body.get('subject', SUBJECT)
        if not isinstance(subject, str) or subject.strip() != SUBJECT:
            raise ValueError('当前标书工作空间仅支持查询北京腾宝科技有限公司。')
        with self.store.lock:
            if self.closed:
                raise ValueError('服务正在关闭，请重新启动后检测。')
            if self.thread and self.thread.is_alive():
                raise ValueError('信用查询正在进行，请等待本次检测结束。')
            run_id = 'credit_' + uuid.uuid4().hex
            self._disable()
            state = initial_state()
            state.update(status='running', run_id=run_id, checked_at=timestamp(),
                         sources=empty_sources('正在验证全自动查询条件'),
                         message='正在检测三个官方来源；全部验证通过后才归档并启用。')
            self.store.put('meta', state)
            self.store.db.commit()
            self.thread = threading.Thread(target=self._run, args=(run_id, SUBJECT), daemon=True)
            self.thread.start()
            return self.status()

    def _archive(self, run_id, subject, sources, assets, finished):
        material_ids = []
        for source in sources:
            sid = source['id']
            mid = 'auto_' + sid
            old = self.store.get('material', mid)
            evidence = {'run_id': run_id, 'source_id': sid, 'source_name': source['name'],
                        'source_url': source['url'], 'final_url': source['final_url'],
                        'queried_at': finished, 'subject': subject, 'result_status': 'no_records',
                        'verification_status': 'verified', 'screenshot_sha256': assets[sid]['sha256']}
            files = self.store.create_files(mid, [{'name': sid + '.png', 'raw': assets[sid]['raw']}], seed=True)
            material = {'id': mid, 'category_id': sid, 'name': source['name'], 'subject': subject,
                        'version': str(finished)[:10] + ' 官方查询', 'years': [int(str(finished)[:4])],
                        'valid_until': '', 'mode': 'page', 'content': '',
                        'description': '官方查询时点未检出记录；请按本项目规定核对查询日期与适用性。',
                        'revision': (old or {}).get('revision', 0) + 1,
                        'created_at': (old or {}).get('created_at', timestamp()), 'updated_at': timestamp(),
                        'file_ids': files, 'credit_evidence': evidence}
            self.store.put('material', material)
            self.store.invalidate(material_id=mid)
            category = self.store.require('category', sid)
            category['enabled'] = True
            self.store.put('category', category)
            material_ids.append(mid)
        return material_ids

    def _run(self, run_id, subject):
        output_dir = self.store.root / 'diagnostics' / 'credit' / run_id
        sources, assets, verified, finished = empty_sources(), {}, False, timestamp()
        message = '未能确认三个来源均可全自动查询，信用材料保持停用。'
        try:
            output_dir = self.store.path(f'diagnostics/credit/{run_id}')
            output_dir.mkdir(parents=True, exist_ok=False)
            payload = self.worker(subject, output_dir)
            sources, assets, verified, finished = validate_result(payload, subject, output_dir)
            # This private diagnostic record is never an exported material.
            (output_dir / 'result.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            message = ('三个官方来源均完成全自动查询与截图校验，已归档并启用信用材料。' if verified
                       else '未满足三个来源全自动完成且未检出记录的条件，信用材料保持停用。')
        except Exception as exc:
            reason = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '查询过程发生异常，未取得完整可信结果。'
            reason = reason.replace(str(self.store.root), '[本地诊断]')[:600]
            sources, message = empty_sources(reason, 'error'), reason
        with self.store.lock:
            if self.closed:
                return
            state = self.store.get('meta', 'credit_automation')
            if not state or state.get('run_id') != run_id:
                return
            material_dirs = [self.store.root / 'materials' / ('auto_' + sid) for sid in MANAGED_IDS]
            previous_paths = {path for directory in material_dirs for path in directory.glob('*')}
            try:
                material_ids = self._archive(run_id, subject, sources, assets, finished) if verified else []
                if not verified:
                    self._disable()
                state.update(status='verified' if verified else 'blocked', enabled=verified,
                             checked_at=timestamp(), sources=sources, material_ids=material_ids, message=message)
                self.store.put('meta', state)
                self.store.db.commit()
            except Exception:
                self.store.db.rollback()
                # Roll back newly copied files too; failed batches stay diagnostic-only.
                for directory in material_dirs:
                    for path in directory.glob('*'):
                        if path not in previous_paths and path.is_file():
                            try:
                                path.unlink(missing_ok=True)
                            except OSError:
                                # The database still rejects these unreferenced files as materials.
                                pass
                self._disable()
                state.update(status='blocked', enabled=False, checked_at=timestamp(), material_ids=[],
                             message='查询材料保存未完成，信用材料保持停用，请重新检测。')
                self.store.put('meta', state)
                self.store.db.commit()

    def close(self):
        with self.store.lock:
            self.closed = True
