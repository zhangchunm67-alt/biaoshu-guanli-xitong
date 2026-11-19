#!/usr/bin/env python3
"""废标红线评测台（本地）。

只监听 127.0.0.1，不上线。用于：
1. 导入招标文件，逐段阅读原文，人工划出废标红线（金标准）；
2. 调用系统同一识别入口，对照金标准计算精确率、召回率、F1；
3. 导出标注表 / 报告，供归档或复核。

提取与计分直接复用 scripts/eval_redline.py，口径与 REDLINE_EVAL.md 一致。

    .venv/bin/python tools/redline_lab/serve.py
    .venv/bin/python tools/redline_lab/serve.py --corpus ./红线评测集 --port 8799
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import re
import sys
import tempfile
import threading
import webbrowser
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
for _path in (ROOT, ROOT / 'scripts'):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import eval_redline as ev  # noqa: E402
from eval_redline import Gold, Prediction, norm  # noqa: E402

STATE_NAME = '标注.json'
SOURCE_NAMES = ('招标文件.docx', '招标文件.doc')
MAX_UPLOAD = 300 * 1024 * 1024
LOCK = threading.RLock()
SOURCE_CACHE: dict[tuple, dict] = {}
CORPUS: Path = ROOT / '红线评测集'
THRESHOLD = ev.THRESHOLD


# ---------------------------------------------------------------- corpus ----

def find_source(folder: Path) -> Path | None:
    for name in SOURCE_NAMES:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    for candidate in sorted(folder.iterdir()):
        if candidate.suffix.lower() in ('.docx', '.doc'):
            return candidate
    return None


def iter_docs():
    if not CORPUS.is_dir():
        return
    for folder in sorted(CORPUS.iterdir()):
        if not folder.is_dir() or folder.name.startswith(('_', '.')):
            continue
        source = find_source(folder)
        if source is not None:
            yield folder, source


def folder_for(doc_id: str) -> Path:
    folder = (CORPUS / doc_id).resolve()
    if folder.parent != CORPUS.resolve() or not folder.is_dir():
        raise FileNotFoundError('文件不存在')
    return folder


def now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def state_path(folder: Path) -> Path:
    return folder / STATE_NAME


def empty_state(source: Path) -> dict:
    return {'version': 1, 'source': source.name, 'updated_at': now(), 'gold': []}


def normalize_rows(rows) -> list[dict]:
    """Trim, drop empties, keep order, renumber ids, add comparison key."""
    result = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get('text') or '').strip()
        if not text:
            continue
        status = row.get('status') if row.get('status') in ev.STATUSES else '有效'
        result.append({
            'id': str(row.get('id') or ''),
            'text': text,
            'status': status,
            'consequence': str(row.get('consequence') or ''),
            'certainty': str(row.get('certainty') or ''),
            'scope': str(row.get('scope') or ''),
            'location': str(row.get('location') or ''),
            'note': str(row.get('note') or ''),
            'key': norm(text),
        })
    for number, row in enumerate(result, 1):
        row['id'] = f'G{number:03d}'
    return result


def save_state(folder: Path, state: dict) -> dict:
    state = dict(state)
    state['version'] = 1
    state['updated_at'] = now()
    state['gold'] = normalize_rows(state.get('gold'))
    with LOCK:
        target = state_path(folder)
        temp = folder / ('.' + STATE_NAME + '.tmp')
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(target)
    return state


def load_annotation(folder: Path, source: Path) -> dict:
    path = state_path(folder)
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(state, dict):
                state = empty_state(source)
        except (OSError, ValueError):
            state = empty_state(source)
    else:
        state = empty_state(source)
        sheet = folder / '红线标注.xlsx'
        if not sheet.is_file():
            sheet = folder / '红线标注.csv'
        if sheet.is_file():
            try:
                golds, _ = ev.read_gold(sheet)
                state['gold'] = [{
                    'id': gold.ident, 'text': gold.text, 'status': gold.status,
                    'consequence': gold.consequence, 'certainty': gold.certainty,
                    'scope': gold.scope, 'location': gold.location, 'note': gold.note,
                } for gold in golds]
            except (OSError, ValueError):
                pass
        state = save_state(folder, state)
    state['gold'] = normalize_rows(state.get('gold'))
    return state


# ------------------------------------------------------------- extraction ---

def analyze(source: Path) -> dict:
    """Parse the tender once: readable units + the system's redline list."""
    stamp = source.stat()
    key = (str(source), stamp.st_mtime_ns, stamp.st_size)
    with LOCK:
        cached = SOURCE_CACHE.get(key)
    if cached is not None:
        return cached

    from metric_source import read_units
    import tender_metrics

    result = {'units': [], 'predictions': [], 'notes': [], 'error': ''}
    try:
        with tempfile.TemporaryDirectory(prefix='redline-lab-') as work:
            readable = ev.readable_source(source, work)
            units, notes = read_units(readable)
            data = tender_metrics.export_review(readable, parsed=(units, notes))
        rows = []
        for unit in units:
            text = unit.original_text or unit.text
            if not text.strip():
                continue
            rows.append({
                'text': text, 'part': unit.part, 'locator': unit.locator,
                'context': unit.context, 'scope': unit.scope, 'kind': unit.kind,
                'is_heading': unit.is_heading, 'key': norm(text),
            })
        predictions = []
        for item in data['requirements']:
            refs = item.get('references') or []
            location = '；'.join(dict.fromkeys(
                f"{ref.get('part', '')} {ref.get('locator', '')}".strip()
                for ref in refs[:3]))
            predictions.append({
                'id': item['id'], 'text': item['text'], 'category': item['category'],
                'label': item.get('label', ''), 'scope': item.get('scope', ''),
                'location': location,
            })
        result = {'units': rows, 'predictions': predictions, 'notes': notes, 'error': ''}
    except Exception as error:  # noqa: BLE001 - show the reason instead of dying
        result = {'units': [], 'predictions': [], 'notes': [],
                  'error': f'{type(error).__name__}: {error}'}

    with LOCK:
        SOURCE_CACHE[key] = result
    return result


def gold_objects(rows) -> tuple[list[Gold], int]:
    golds: list[Gold] = []
    seen = set()
    merged = 0
    for row in normalize_rows(rows):
        item = Gold(row['id'], row['text'], row['status'], row['consequence'],
                    row['certainty'], row['scope'], row['location'], row['note'])
        if not item.key:
            continue
        identity = (item.key, norm(item.scope), item.status)
        if identity in seen:
            merged += 1
            continue
        seen.add(identity)
        golds.append(item)
    return golds, merged


def evaluate_doc(folder: Path, source: Path, rows=None, threshold: float = THRESHOLD) -> dict:
    state = load_annotation(folder, source)
    if rows is not None and not isinstance(rows, list):
        raise ValueError('标注数据格式不正确')
    golds, merged = gold_objects(state['gold'] if rows is None else rows)
    parsed = analyze(source)
    predictions = [Prediction(p['id'], p['text'], p['category'], p['label'], p['scope'], p['location'])
                   for p in parsed['predictions']]
    redlines = [p for p in predictions if p.category == 'redline']
    others = [p for p in predictions if p.category != 'redline']
    texts = [u['text'] for u in parsed['units']]
    units = texts + [a + b for a, b in zip(texts, texts[1:])]
    counts, details = ev.evaluate(golds, redlines, others=others, units=units, threshold=threshold)
    return {
        'id': folder.name, 'name': folder.name, 'source': source.name,
        'counts': counts, 'details': details, 'metrics': ev.metrics(counts),
        'golds': golds, 'redlines': redlines, 'merged': merged, 'error': parsed['error'],
        'predictions': parsed['predictions'],
    }


def gold_dict(gold: Gold | None) -> dict | None:
    if gold is None:
        return None
    return {'id': gold.ident, 'text': gold.text, 'status': gold.status,
            'consequence': gold.consequence, 'certainty': gold.certainty,
            'scope': gold.scope, 'location': gold.location, 'note': gold.note}


def prediction_dict(prediction: Prediction | None) -> dict | None:
    if prediction is None:
        return None
    return {'id': prediction.ident, 'text': prediction.text, 'label': prediction.label,
            'scope': prediction.scope, 'location': prediction.location}


def serialized_details(details) -> list[dict]:
    rows = []
    for row in details:
        rows.append({
            'kind': row['kind'], 'reason': row['reason'],
            'coverage': None if row['coverage'] is None else round(float(row['coverage']), 3),
            'gold': gold_dict(row['gold']), 'prediction': prediction_dict(row['prediction']),
        })
    return rows


def payload_for(result: dict) -> dict:
    metrics = result['metrics']
    statuses = Counter(g.status for g in result['golds'])
    return {
        'id': result['id'], 'source': result['source'], 'error': result['error'],
        'metrics': metrics, 'merged': result['merged'],
        'gold_status': {'有效': statuses.get('有效', 0), '不适用': statuses.get('不适用', 0),
                        '存疑': statuses.get('存疑', 0)},
        'details': serialized_details(result['details']),
        'predictions': result['predictions'],
        'reasons': [{'kind': kind, 'reason': reason, 'count': count}
                    for (kind, reason), count in Counter(
                        (row['kind'], row['reason']) for row in result['details']
                        if row['kind'] not in ('TP', 'IGN')).most_common()],
    }


def evaluate_all(threshold: float = THRESHOLD) -> dict:
    documents, total = [], Counter()
    for folder, source in iter_docs():
        result = evaluate_doc(folder, source, threshold=threshold)
        total.update(result['counts'])
        documents.append({
            'id': folder.name, 'name': folder.name, 'error': result['error'],
            'merged': result['merged'], 'metrics': result['metrics'],
        })
    precision = [d['metrics']['精确率'] for d in documents if d['metrics']['精确率'] is not None]
    recall = [d['metrics']['召回率'] for d in documents if d['metrics']['召回率'] is not None]
    return {
        'documents': documents,
        'micro': ev.metrics(total),
        'macro': {
            '精确率': sum(precision) / len(precision) if precision else None,
            '召回率': sum(recall) / len(recall) if recall else None,
        },
    }


# --------------------------------------------------------------- reports ----

def build_result(threshold: float = THRESHOLD) -> dict:
    documents, total = [], Counter()
    for folder, source in iter_docs():
        result = evaluate_doc(folder, source, threshold=threshold)
        total.update(result['counts'])
        documents.append({
            'name': folder.name, 'split': '', 'type': '',
            'source': source, 'gold': folder / '红线标注.xlsx',
            'merged': result['merged'], 'error': result['error'],
            'counts': result['counts'], 'details': result['details'],
            'metrics': result['metrics'],
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'gold_sha256': '',
        })
    if not documents:
        raise ValueError('评测集里还没有可评测的文件')

    def mean(name):
        values = [d['metrics'][name] for d in documents if d['metrics'][name] is not None]
        return sum(values) / len(values) if values else None

    reasons = Counter((ev.VERDICTS[row['kind']], row['reason'])
                      for d in documents for row in d['details']
                      if row['kind'] not in ('TP', 'IGN'))
    return {
        'generated_at': now(), 'threshold': threshold,
        'git_commit': ev.git_commit(), 'rules_sha256': ev.rules_digest(),
        'split': '全部', 'skipped': [], 'documents': documents,
        'micro': ev.metrics(total),
        'macro': {'精确率': mean('精确率'), '召回率': mean('召回率')},
        'interval': ev.bootstrap(documents),
        'reasons': [{'判定': kind, '原因': reason, '条数': count}
                    for (kind, reason), count in reasons.most_common()],
        'breakdown': {'后果': ev.breakdown(documents, 'consequence'),
                      '确定性': ev.breakdown(documents, 'certainty')},
    }


def annotation_workbook(folder: Path, source: Path, rows) -> bytes:
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.worksheet.datavalidation import DataValidation

    def clean(value):
        return ILLEGAL_CHARACTERS_RE.sub('', str(value or ''))[:32000]

    book = Workbook()
    sheet = book.active
    sheet.title = '红线标注'
    sheet.append(list(ev.COLUMNS))
    for row in normalize_rows(rows):
        sheet.append([clean(row['id']), clean(row['text']), clean(row['status']),
                      clean(row['consequence']), clean(row['certainty']),
                      clean(row['scope']), clean(row['location']), clean(row['note'])])
    for column, values in (('C', ev.STATUSES), ('D', ev.CONSEQUENCES), ('E', ev.CERTAINTY)):
        validation = DataValidation(type='list', formula1='"' + ','.join(values) + '"', allow_blank=True)
        validation.add(f'{column}2:{column}2000')
        sheet.add_data_validation(validation)
    for column, width in zip('ABCDEFGH', (10, 70, 8, 14, 8, 12, 20, 30)):
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = 'A2'

    parsed = analyze(source)
    origin = book.create_sheet('原文段落')
    origin.append(['序号', '分件', '位置', '章节', '原文'])
    for number, unit in enumerate(parsed['units'], 1):
        origin.append([number, clean(unit['part']), clean(unit['locator']),
                       clean(unit['context']), clean(unit['text'])])
    for column, width in zip('ABCDE', (7, 10, 14, 30, 90)):
        origin.column_dimensions[column].width = width
    origin.freeze_panes = 'A2'

    guide = book.create_sheet('标注说明')
    guide.column_dimensions['A'].width = 120
    for number, line in enumerate(ev.GUIDE, 1):
        guide.append([f'{number}. {line}'])
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


# ------------------------------------------------------------------ http ----

class Handler(BaseHTTPRequestHandler):
    server_version = 'RedlineLab/1.0'

    def log_message(self, fmt, *args):
        pass

    # -- helpers
    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message, status=400):
        self.send_json({'error': str(message)}, status)

    def send_bytes(self, data, filename, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{quote(filename)}")
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def read_json(self, limit=20 * 1024 * 1024):
        length = int(self.headers.get('Content-Length') or 0)
        if length > limit:
            raise ValueError('请求内容过大')
        raw = self.rfile.read(length) if length else b''
        try:
            return json.loads(raw or b'{}')
        except ValueError as error:
            raise ValueError('请求不是合法 JSON') from error

    def read_upload(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            raise ValueError('没有收到文件')
        if length > MAX_UPLOAD:
            raise ValueError('文件超过 300MB')
        content_type = self.headers.get('Content-Type') or ''
        match = re.search(r'boundary="?([^";]+)"?', content_type)
        if not match:
            raise ValueError('上传格式不正确')
        body = self.rfile.read(length)
        delimiter = ('--' + match.group(1)).encode()
        for part in body.split(delimiter)[1:]:
            if part.startswith(b'--'):
                break
            part = part[2:] if part.startswith(b'\r\n') else part
            head, separator, data = part.partition(b'\r\n\r\n')
            if not separator:
                continue
            if data.endswith(b'\r\n'):
                data = data[:-2]
            text = head.decode('utf-8', 'replace')
            name = re.search(r'filename\*?=(?:"([^"]*)"|([^;\r\n]*))', text)
            if not name:
                continue
            filename = name.group(1) or name.group(2) or ''
            if filename.startswith("UTF-8''"):
                filename = unquote(filename[7:])
            filename = unquote(filename)
            return filename, data
        raise ValueError('没有找到上传文件')

    # -- routes
    def check_origin(self):
        """Reject writes coming from a page other than this local tool."""
        origin = self.headers.get('Origin')
        if not origin:
            return True
        port = self.server.server_port
        if origin in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
            return True
        self.send_error_json('只接受本机页面发起的请求', 403)
        return False

    def do_GET(self):
        try:
            path = unquote(urlparse(self.path).path)
            query = parse_qs(urlparse(self.path).query)
            if path in ('/', '/index.html'):
                return self.static('index.html', 'text/html; charset=utf-8')
            if path.startswith('/static/'):
                return self.static(path[len('/static/'):])
            if path == '/api/state':
                return self.send_json({
                    'corpus': str(CORPUS),
                    'docs': [{
                        'id': folder.name, 'source': source.name,
                        'gold': len(load_annotation(folder, source)['gold']),
                    } for folder, source in iter_docs()],
                })
            parts = path.strip('/').split('/')
            if len(parts) == 3 and parts[:2] == ['api', 'docs']:
                return self.get_doc(parts[2])
            if len(parts) == 4 and parts[:2] == ['api', 'docs']:
                doc_id, action = parts[2], parts[3]
                if action == 'annotation.xlsx':
                    return self.get_xlsx(doc_id)
            if path == '/api/report.md':
                result = build_result(float(query.get('threshold', [THRESHOLD])[0]))
                return self.send_bytes(ev.report_markdown(result).encode('utf-8'),
                                       '废标红线评测报告.md', 'text/markdown; charset=utf-8')
            if path == '/api/details.csv':
                result = build_result()
                with tempfile.TemporaryDirectory(prefix='redline-lab-') as work:
                    target = Path(work) / 'details.csv'
                    ev.write_details(target, result['documents'])
                    data = target.read_bytes()
                return self.send_bytes(data, '废标红线逐条明细.csv', 'text/csv; charset=utf-8')
            return self.send_error_json('页面不存在', 404)
        except FileNotFoundError as error:
            self.send_error_json(error, 404)
        except Exception as error:  # noqa: BLE001
            self.send_error_json(f'{type(error).__name__}: {error}', 500)

    def do_POST(self):
        if not self.check_origin():
            return
        try:
            path = unquote(urlparse(self.path).path)
            if path == '/api/import':
                return self.post_import()
            if path == '/api/evaluate':
                return self.post_evaluate()
            return self.send_error_json('页面不存在', 404)
        except FileNotFoundError as error:
            self.send_error_json(error, 404)
        except Exception as error:  # noqa: BLE001
            self.send_error_json(f'{type(error).__name__}: {error}', 500)

    def do_PUT(self):
        if not self.check_origin():
            return
        try:
            path = unquote(urlparse(self.path).path)
            parts = path.strip('/').split('/')
            if len(parts) == 4 and parts[:2] == ['api', 'docs'] and parts[3] == 'gold':
                return self.put_gold(parts[2])
            return self.send_error_json('页面不存在', 404)
        except Exception as error:  # noqa: BLE001
            self.send_error_json(f'{type(error).__name__}: {error}', 500)

    def do_DELETE(self):
        if not self.check_origin():
            return
        try:
            path = unquote(urlparse(self.path).path)
            parts = path.strip('/').split('/')
            if len(parts) == 3 and parts[:2] == ['api', 'docs']:
                return self.delete_doc(parts[2])
            return self.send_error_json('页面不存在', 404)
        except Exception as error:  # noqa: BLE001
            self.send_error_json(f'{type(error).__name__}: {error}', 500)

    # -- handlers
    def static(self, name, content_type=None):
        root = (BASE / 'static').resolve()
        target = (root / name).resolve()
        if root not in target.parents:
            raise FileNotFoundError('页面不存在')
        if not target.is_file():
            raise FileNotFoundError('页面不存在')
        data = target.read_bytes()
        kind = content_type or mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        if kind.startswith('text/') or kind in ('application/javascript', 'application/json'):
            kind += '; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def get_doc(self, doc_id):
        folder = folder_for(doc_id)
        source = find_source(folder)
        if source is None:
            raise FileNotFoundError('该目录下没有招标文件')
        state = load_annotation(folder, source)
        parsed = analyze(source)
        self.send_json({
            'id': folder.name, 'source': source.name, 'error': parsed['error'],
            'units': parsed['units'], 'predictions': parsed['predictions'],
            'notes': parsed['notes'], 'gold': state['gold'],
        })

    def put_gold(self, doc_id):
        folder = folder_for(doc_id)
        source = find_source(folder)
        if source is None:
            raise FileNotFoundError('该目录下没有招标文件')
        body = self.read_json()
        state = load_annotation(folder, source)
        state['gold'] = body.get('gold') or []
        state = save_state(folder, state)
        self.send_json({'ok': True, 'gold': state['gold'], 'updated_at': state['updated_at']})

    def post_evaluate(self):
        body = self.read_json()
        threshold = float(body.get('threshold') or THRESHOLD)
        if not 0 < threshold <= 1:
            raise ValueError('匹配阈值须在 0 到 1 之间')
        doc_id = body.get('id')
        if doc_id:
            folder = folder_for(doc_id)
            source = find_source(folder)
            if source is None:
                raise FileNotFoundError('该目录下没有招标文件')
            rows = body.get('gold')
            result = evaluate_doc(folder, source, rows=rows, threshold=threshold)
            payload = payload_for(result)
            payload['threshold'] = threshold
            return self.send_json(payload)
        data = evaluate_all(threshold)
        data['threshold'] = threshold
        return self.send_json(data)

    def post_import(self):
        filename, data = self.read_upload()
        suffix = Path(filename).suffix.lower()
        if suffix not in ('.docx', '.doc'):
            raise ValueError('只支持 .docx 或 .doc')
        stem = re.sub(r'[^\w\u4e00-\u9fff（）()\-—]+', '_', Path(filename).stem).strip('_')[:60]
        stem = stem or '招标文件'
        numbers = [int(m.group(1)) for p in CORPUS.iterdir() if p.is_dir()
                   and (m := re.match(r'^D(\d+)-', p.name))]
        number = (max(numbers) if numbers else 0) + 1
        while (CORPUS / f'D{number:02d}-{stem}').exists():
            number += 1
        folder = CORPUS / f'D{number:02d}-{stem}'
        folder.mkdir(parents=True)
        target = folder / ('招标文件' + suffix)
        target.write_bytes(data)
        save_state(folder, empty_state(target))
        self.send_json({'ok': True, 'id': folder.name, 'source': target.name})

    def get_xlsx(self, doc_id):
        folder = folder_for(doc_id)
        source = find_source(folder)
        if source is None:
            raise FileNotFoundError('该目录下没有招标文件')
        state = load_annotation(folder, source)
        data = annotation_workbook(folder, source, state['gold'])
        (folder / '红线标注.xlsx').write_bytes(data)  # keep the CLI archive in sync
        self.send_bytes(data, f'{folder.name}-红线标注.xlsx',
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def delete_doc(self, doc_id):
        folder = folder_for(doc_id)
        trash = CORPUS / '_回收站'
        trash.mkdir(exist_ok=True)
        target = trash / f'{datetime.now().strftime("%Y%m%d-%H%M%S")}-{folder.name}'
        folder.rename(target)
        self.send_json({'ok': True, 'moved_to': str(target)})


def main(argv=None):
    global CORPUS, THRESHOLD
    parser = argparse.ArgumentParser(description='废标红线评测台（本地）')
    parser.add_argument('--corpus', type=Path, default=ROOT / '红线评测集',
                        help='评测集目录，默认 ./红线评测集')
    parser.add_argument('--port', type=int, default=8799)
    parser.add_argument('--host', default='127.0.0.1', help='默认只监听本机')
    parser.add_argument('--threshold', type=float, default=ev.THRESHOLD)
    parser.add_argument('--no-open', action='store_true', help='启动后不自动打开浏览器')
    args = parser.parse_args(argv)

    CORPUS = args.corpus.expanduser().resolve()
    CORPUS.mkdir(parents=True, exist_ok=True)
    THRESHOLD = args.threshold
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('为避免对外提供服务，只允许绑定本机地址')

    url = f'http://{args.host}:{args.port}/'
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as error:
        parser.exit(1, f'端口 {args.port} 无法监听：{error}\n可加 --port 换一个端口。\n')
    server.daemon_threads = True
    print(f'废标红线评测台：{url}')
    print(f'评测集目录：{CORPUS}')
    print('按 Ctrl+C 停止。只在本地运行，不会上传到外网。')
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
