"""Colleague contributions: quarantined uploads, local review and the shared case library.

Anyone who can open the system, including the 花生壳 mapped port, may submit a material or a
project case. A submission never enters the material library, automatic project matching or AI
writing until a reviewer on the local loopback entry accepts it; the mapped port never receives
submitted file bytes back. Accepted materials pass the normal material rules, and accepted cases
join the case library that step 4 can select from.
"""
from __future__ import annotations

import base64
import copy
import hashlib
from pathlib import Path, PurePosixPath
import shutil

from errors import ConflictError

MAX_PENDING = 100
MAX_PENDING_BYTES = 1024 * 1024 * 1024
MAX_MATERIAL_FILES = 100
MAX_LIBRARY_SELECTION = 20
LIST_LIMIT = 200
STATUSES = ('pending', 'accepted', 'rejected')
MATERIAL_MODES = ('page', 'side_by_side', 'text')
CASE_SUFFIXES = {'.zip', '.doc', '.docx', '.pdf', '.pptx', '.txt', '.md'}
MATERIAL_FIELDS = {'name', 'category_id', 'subject', 'version', 'years', 'valid_until', 'mode', 'content',
                   'description', 'client_name', 'case_name', 'subject_kind', 'linked_category_ids'}


def clean_text(value, label, limit, required=False, multiline=False):
    from server import text_value
    value = text_value(value, label, limit, required)
    allowed = '\n\r\t' if multiline else ''
    if any((ord(c) < 32 and c not in allowed) or ord(c) == 127 for c in value):
        raise ValueError(f'{label}不能包含控制字符')
    return value


def years_value(value):
    if not isinstance(value, list) or len(value) > 30 or any(
            isinstance(y, bool) or not isinstance(y, int) or y < 1900 or y > 2200 for y in value):
        raise ValueError('年度须为 1900—2200 之间的年份列表')
    return sorted(set(value))


def material_proposal(value):
    """What the contributor suggests; the reviewer confirms it against the full material rules."""
    if not isinstance(value, dict):
        raise ValueError('请填写材料信息')
    mode = value.get('mode', 'page')
    if mode not in MATERIAL_MODES:
        raise ValueError('排版方式不正确')
    category_id = value.get('category_id') or ''
    if not isinstance(category_id, str) or len(category_id) > 120:
        raise ValueError('建议栏目格式不正确')
    proposal = {
        'name': clean_text(value.get('name', ''), '材料名称', 200, True),
        'category_id': category_id,
        'subject': clean_text(value.get('subject', ''), '材料主体', 200),
        'version': clean_text(value.get('version', ''), '版本说明', 200),
        'years': years_value(value.get('years', [])),
        'valid_until': clean_text(value.get('valid_until', ''), '有效期', 200),
        'mode': mode,
        'content': clean_text(value.get('content', ''), '材料正文', 30000, multiline=True) if mode == 'text' else '',
        'description': clean_text(value.get('description', ''), '使用说明', 2000, multiline=True),
        'client_name': clean_text(value.get('client_name', ''), '客户名称', 300),
        'case_name': clean_text(value.get('case_name', ''), '项目名称', 300),
    }
    if mode == 'text' and not proposal['content']:
        raise ValueError('请填写文字材料正文')
    return proposal


def case_proposal(value):
    if not isinstance(value, dict):
        raise ValueError('请填写案例信息')
    return {
        'name': clean_text(value.get('name', ''), '案例名称', 200, True),
        'client_name': clean_text(value.get('client_name', ''), '客户名称', 300),
        'description': clean_text(value.get('description', ''), '案例说明', 2000, multiline=True),
    }


def material_files(files):
    """Images are checked with the same rules as a direct material upload."""
    from server import MAX_IMAGE, check_image, decode_upload, uid
    if not isinstance(files, list) or len(files) > MAX_MATERIAL_FILES:
        raise ValueError(f'每次最多提交 {MAX_MATERIAL_FILES} 张图片')
    prepared = []
    for item in files:
        name, raw = decode_upload(item, MAX_IMAGE)
        width, height, mime = check_image(name, raw)
        prepared.append(({'id': uid('subfile'), 'name': name, 'size': len(raw),
                          'sha256': hashlib.sha256(raw).hexdigest(), 'mime': mime,
                          'width': width, 'height': height}, Path(name).suffix.lower(), raw))
    return prepared


def case_files(files):
    """The writing parser rejects unsafe or unreadable packages before anything is stored."""
    import writing_cases
    from server import uid
    parsed = writing_cases.parse_uploads(files)
    prepared = []
    for item, meta in zip(files, parsed['files']):
        raw = base64.b64decode(item['data'], validate=True)
        suffix = PurePosixPath(meta['name']).suffix.lower()
        prepared.append(({'id': uid('subfile'), 'name': meta['name'], 'size': meta['size'],
                          'sha256': meta['sha256']}, suffix if suffix in CASE_SUFFIXES else '.bin', raw))
    summary = {'source_count': len(parsed['sources']),
               'char_count': sum(len(source['text']) for source in parsed['sources']),
               'warnings': parsed['warnings'][:50]}
    return prepared, summary


def folder_name(name):
    value = ''.join('／' if c in '/\\' else c for c in name).strip()
    return value if value not in {'', '.', '..'} else '案例'


class Contributions:
    def __init__(self, store):
        self.store = store

    # Submitting runs outside the shared store lock: images and case documents are checked first,
    # then only the publish step takes the lock.
    def submit(self, body, *, remote):
        from server import now, uid
        if not isinstance(body, dict):
            raise ValueError('提交内容格式不正确')
        kind = body.get('kind')
        if kind not in ('material', 'case'):
            raise ValueError('请选择提交材料或案例')
        contributor = clean_text(body.get('contributor', ''), '提交人', 60, True)
        note = clean_text(body.get('note', ''), '给审核人的说明', 2000, multiline=True)
        files = body.get('files', [])
        if not isinstance(files, list):
            raise ValueError('文件格式不正确，请重新选择文件')
        summary = {}
        if kind == 'material':
            proposal = material_proposal(body.get('material'))
            if proposal['mode'] == 'text':
                if files:
                    raise ValueError('文字材料无需上传图片；提交图片请选择图片排版方式')
                prepared = []
            elif not files:
                raise ValueError('请至少选择一张材料图片，或改为文字内容')
            else:
                prepared = material_files(files)
        else:
            proposal = case_proposal(body.get('case'))
            if not files:
                raise ValueError('请选择案例文件或 ZIP 案例包')
            prepared, summary = case_files(files)
        size = sum(len(raw) for _, _, raw in prepared)
        with self.store.lock:
            if kind == 'material' and proposal['category_id']:
                self.check_category(proposal['category_id'])
            pending = [row for row in self.store.all('submission') if row['status'] == 'pending']
            if len(pending) >= MAX_PENDING or sum(row.get('size', 0) for row in pending) + size > MAX_PENDING_BYTES:
                raise ValueError('待审核投稿已达上限，请联系本机管理员先审核处理，再继续提交')
            key = uid('submission')
            directory = self.store.path('submissions/' + key)
            stamp = now()
            record = {'id': key, 'kind': kind, 'status': 'pending', 'contributor': contributor, 'note': note,
                      'origin': 'remote' if remote else 'local', kind: proposal, 'files': [], 'size': size,
                      'summary': summary, 'revision': 1, 'created_at': stamp, 'updated_at': stamp}
            try:
                directory.mkdir(parents=True)
                for meta, suffix, raw in prepared:
                    meta['storage'] = f'submissions/{key}/{meta["id"]}{suffix}'
                    self.store.path(meta['storage']).write_bytes(raw)
                    record['files'].append(meta)
                self.store.put('submission', record)
                self.store.db.commit()
            except Exception:
                self.store.db.rollback()
                shutil.rmtree(directory, ignore_errors=True)
                raise
            return self.public(record, remote=remote)

    def check_category(self, category_id):
        from credit_service import MANAGED_IDS
        category = self.store.get('category', category_id)
        if not category or not category.get('enabled') or category_id in MANAGED_IDS:
            raise ValueError('建议栏目不存在或已停用；不确定时可留空，由审核人归类')

    def public(self, record, *, remote):
        """The mapped port sees status and metadata only, never submitted file names or bytes."""
        value = copy.deepcopy(record)
        files = value.pop('files', [])
        value['file_count'] = len(files)
        if remote:
            # Reading warnings name the submitted files, so only their number is shown remotely.
            summary = value.get('summary') or {}
            value['summary'] = {k: v for k, v in summary.items() if k != 'warnings'}
            value['summary']['warning_count'] = len(summary.get('warnings', []))
            value['files'] = []
            value['files_intranet_only'] = True
            # 外网只看名称、状态与退回原因；正文和给审核人的说明只在本机审核时显示。
            value.pop('note', None)
            for kind, keep in (('material', {'name', 'category_id', 'mode'}), ('case', {'name'})):
                if isinstance(value.get(kind), dict):
                    value[kind] = {k: v for k, v in value[kind].items() if k in keep}
            return value
        value['files'] = []
        for item in files:
            row = {k: item[k] for k in ('id', 'name', 'size', 'mime', 'width', 'height') if k in item}
            if not record.get('files_removed'):
                row['url'] = f'/api/submissions/{record["id"]}/files/{item["id"]}'
            value['files'].append(row)
        return value

    def list(self, query, *, remote):
        status = (query.get('status') or ['pending'])[0]
        if status not in (*STATUSES, 'all'):
            raise ValueError('投稿状态筛选无效')
        rows = sorted(self.store.all('submission'), key=lambda row: (row.get('created_at', ''), row['id']),
                      reverse=True)
        selected = [row for row in rows if status == 'all' or row['status'] == status]
        return {'items': [self.public(row, remote=remote) for row in selected[:LIST_LIMIT]],
                'total': len(selected), 'status': status,
                'counts': {name: sum(row['status'] == name for row in rows) for name in STATUSES}}

    def describe(self, key, *, remote):
        return self.public(self.store.require('submission', key), remote=remote)

    def file(self, key, file_id):
        record = self.store.require('submission', key)
        meta = next((item for item in record.get('files', []) if item['id'] == file_id), None)
        if meta is None or record.get('files_removed'):
            raise LookupError('投稿文件不存在，或已在审核后移除')
        return meta, self.store.path(meta['storage']).read_bytes(), record['kind']

    def pending(self, key, body):
        record = self.store.require('submission', key)
        if record['status'] != 'pending':
            raise ConflictError('该投稿已处理，请刷新列表')
        if type(body.get('revision')) is not int or body['revision'] != record['revision']:
            raise ConflictError('投稿已更新，请刷新后重新审核')
        return record

    def finish(self, record, status, decision):
        """Commit the decision; quarantined copies are removed only once it is durable."""
        record.update(status=status, decision=decision, files_removed=True, updated_at=decision['at'],
                      revision=record['revision'] + 1)
        self.store.put('submission', record)
        self.store.db.commit()
        shutil.rmtree(self.store.path('submissions/' + record['id']), ignore_errors=True)

    def accept(self, key, body):
        """Create the material or library case and close the submission in one transaction."""
        from server import now, uid
        record = self.pending(key, body)
        stamp = now()
        if record['kind'] == 'material':
            fields = body.get('material')
            if not isinstance(fields, dict):
                raise ValueError('请核对材料信息后再入库')
            material = {name: value for name, value in fields.items() if name in MATERIAL_FIELDS}
            if material.get('mode') == 'text' and record['files']:
                # 改成文字排版会丢弃投稿图片；图片投稿必须保留图片排版。
                raise ValueError('该投稿含图片，入库时请选择“每张独立一页”或“双图并排”，文字排版会丢弃图片')
            if material.get('mode', 'page') != 'text':
                if not record['files']:
                    raise ValueError('该投稿没有图片，请选择文字内容排版并填写正文')
                material['files'] = [{'name': item['name'], 'data': base64.b64encode(
                    self.store.path(item['storage']).read_bytes()).decode()} for item in record['files']]
            provenance = {'submission_id': key, 'contributor': record['contributor'], 'origin': record['origin'],
                          'submitted_at': record['created_at'], 'accepted_at': stamp}
            created = self.store.material(material, provenance=provenance, commit=False)
            self.finish(record, 'accepted', {'at': stamp, 'material_id': created['id']})
            return {'submission': self.public(record, remote=False), 'material': created}
        fields = body.get('case', {})
        if not isinstance(fields, dict):
            raise ValueError('请核对案例信息后再入库')
        proposal = case_proposal({**record['case'], **fields})
        case_id = uid('case')
        target = self.store.path('case-library/' + case_id)
        try:
            target.mkdir(parents=True)
            files = []
            for item in record['files']:
                storage = f'case-library/{case_id}/{item["id"]}{PurePosixPath(item["storage"]).suffix}'
                shutil.copyfile(self.store.path(item['storage']), self.store.path(storage))
                files.append({name: item[name] for name in ('id', 'name', 'size', 'sha256')} | {'storage': storage})
            case = {'id': case_id, **proposal, 'contributor': record['contributor'], 'submission_id': key,
                    'submitted_at': record['created_at'], 'files': files, 'size': record['size'],
                    'summary': record.get('summary', {}), 'archived': False, 'revision': 1,
                    'created_at': stamp, 'updated_at': stamp}
            self.store.put('library_case', case)
            self.finish(record, 'accepted', {'at': stamp, 'case_id': case_id})
        except Exception:
            self.store.db.rollback()
            shutil.rmtree(target, ignore_errors=True)
            raise
        return {'submission': self.public(record, remote=False), 'case': self.public_case(case, remote=False)}

    def reject(self, key, body):
        from server import now
        record = self.pending(key, body)
        reason = clean_text(body.get('reason', ''), '退回原因', 500, True, multiline=True)
        self.finish(record, 'rejected', {'at': now(), 'reason': reason})
        return {'submission': self.public(record, remote=False)}

    def stats(self):
        return {'pending_submission_count': sum(row['status'] == 'pending' for row in self.store.all('submission')),
                'library_case_count': sum(not case.get('archived') for case in self.store.all('library_case'))}

    # Case library ---------------------------------------------------------------------------
    def public_case(self, case, *, remote):
        value = copy.deepcopy(case)
        value['files'] = [{'id': item['id'], 'name': item['name'], 'size': item['size'],
                           **({} if remote else {'url': f'/api/case-library/{case["id"]}/files/{item["id"]}'})}
                          for item in case['files']]
        value['file_count'] = len(case['files'])
        return value

    def cases(self, query, *, remote):
        archived = (query.get('archived') or ['false'])[0] == 'true'
        rows = self.store.all('library_case')
        selected = sorted((case for case in rows if bool(case.get('archived')) == archived),
                          key=lambda case: (case.get('updated_at', ''), case['id']), reverse=True)
        return {'items': [self.public_case(case, remote=remote) for case in selected], 'total': len(selected),
                'archived': archived, 'counts': {'active': sum(not case.get('archived') for case in rows),
                                                 'archived': sum(bool(case.get('archived')) for case in rows)}}

    def library_summary(self):
        """Selectable cases for step 4: metadata only, newest first."""
        rows = sorted((case for case in self.store.all('library_case') if not case.get('archived')),
                      key=lambda case: (case.get('updated_at', ''), case['id']), reverse=True)
        return [{'id': case['id'], 'name': case['name'], 'client_name': case.get('client_name', ''),
                 'contributor': case.get('contributor', ''), 'file_count': len(case['files']),
                 'size': case.get('size', 0), 'updated_at': case.get('updated_at', '')} for case in rows]

    def case_file(self, key, file_id):
        case = self.store.require('library_case', key)
        meta = next((item for item in case['files'] if item['id'] == file_id), None)
        if meta is None:
            raise LookupError('案例文件不存在')
        return meta, self.store.path(meta['storage']).read_bytes()

    def update_case(self, key, body):
        from server import now
        case = self.store.require('library_case', key)
        if type(body.get('revision')) is not int or body['revision'] != case['revision']:
            raise ConflictError('案例已更新，请刷新后再修改')
        for field, label, limit, multiline in (('name', '案例名称', 200, False), ('client_name', '客户名称', 300, False),
                                               ('description', '案例说明', 2000, True)):
            if field in body:
                case[field] = clean_text(body[field], label, limit, field == 'name', multiline)
        if 'archived' in body:
            if type(body['archived']) is not bool:
                raise ValueError('案例库状态无效')
            case['archived'] = body['archived']
        case.update(revision=case['revision'] + 1, updated_at=now())
        self.store.put('library_case', case)
        self.store.db.commit()
        return self.public_case(case, remote=False)

    def package(self, ids):
        """Logical upload names and stored files for the selected cases, in selection order."""
        if (not isinstance(ids, list) or not ids or len(ids) > MAX_LIBRARY_SELECTION
                or any(not isinstance(key, str) for key in ids) or len(set(ids)) != len(ids)):
            raise ValueError(f'请从案例库选择 1—{MAX_LIBRARY_SELECTION} 个不重复的案例')
        items, folders = [], set()
        for key in ids:
            case = self.store.get('library_case', key)
            if not case or case.get('archived'):
                raise ValueError('所选案例已移出案例库，请刷新后重新选择')
            base = folder = folder_name(case['name'])
            number = 2
            while folder in folders:
                folder, number = f'{base}（{number}）', number + 1
            folders.add(folder)
            items.extend({'name': f'案例库/{folder}/{item["name"]}', 'storage': item['storage']} for item in case['files'])
        return items
