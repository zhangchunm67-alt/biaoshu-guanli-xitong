"""Project-scoped case packages and versioned, reviewable AI writing jobs."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

import chapter_drafts
import writing_chat
import writing_visuals
from errors import ConflictError

# 证件、授权、文书模板等材料的正文含姓名、证件号等个人信息，从不发给 AI，只发名称。
NAME_ONLY_CATEGORIES = {'legal_identity', 'authorization', 'text_template', 'business_license',
                        'tax_social', 'audit_report', 'people', 'I02', 'F02', 'B02', 'W02'}
# 公司简介、资质认证等不含个人信息的公司概况，任何方案章节都可能引用，始终附上正文。
ALWAYS_CATEGORIES = {'company_profile', 'iso_cert', 'qualification'}
# 其余栏目与章节标题的对应关系；章节命中时才附上该栏目材料正文。
CATEGORY_TOPICS = {
    'case': r'案例|业绩|经验|类似项目',
    'people': r'团队|人员|项目经理|负责人|组织|配置|简历',
    'resource': r'资源|渠道|媒体|平台|合作|优势',
}


def material_relevant(material, category_name, chapters):
    """按本组章节标题、路径和原文判断材料是否相关；不相关的材料只发名称。"""
    if material.get('category_id') in ALWAYS_CATEGORIES:
        return True
    text = '\n'.join(f"{c.get('path', '')}\n{c.get('title', '')}\n{c.get('original_text', '')}" for c in chapters)
    pattern = CATEGORY_TOPICS.get(material.get('category_id'))
    if pattern and re.search(pattern, text):
        return True
    names = [n for n in (category_name, material.get('name')) if n and len(n) >= 2]
    return any(name in text for name in names)


def outbound_materials(materials, chapters, categories):
    rows = []
    for material in materials:
        row = dict(material)
        cid = material.get('category_id')
        if cid in NAME_ONLY_CATEGORIES:
            row.pop('subject', None)
        if cid in NAME_ONLY_CATEGORIES or not material_relevant(material, categories.get(cid, ''), chapters):
            row.update(content='', description='', text_available=False, content_withheld=True)
        rows.append(row)
    return rows


EDITION_LIMIT = 10


class WritingService:
    def __init__(self, store):
        self.store = store
        self._status_sources = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bid-writing')
        for job in store.all('writing_job'):
            if job['status'] in {'queued', 'running'}:
                job.update(status='failed', message='生成已中断，请重新生成', error='本地服务曾停止，候选正文未完成。')
                store.put('writing_job', job)
        store.db.commit()

    def close(self):
        self.pool.shutdown(wait=True)

    def _project(self, key):
        project = self.store.require('project', key)
        framework = self.store.get('framework', project.get('framework_id', ''))
        if not framework or framework.get('projects', {}).get('word') != key:
            raise ValueError('请从已确认目录的当前整本项目进入正文编写。')
        if not framework.get('requirements_confirmed'):
            raise ValueError('请先核对确认采购要求和目录，再生成正文。')
        return project, framework

    def _pack(self, key):
        return self.store.get('writing_cases', key) or dict(id=key, revision=0, files=[], sources=[], warnings=[])

    def _indexed_pack(self, key):
        """Index images from retained originals on first use of a pre-image case pack."""
        import writing_cases
        pack = self._pack(key)
        if pack.get('image_index_version') or not pack['files']:
            return pack
        directories = set(pack.get('original_batches', []))
        if pack.get('storage'):
            directories.add(pack['storage'])
        by_hash = {file['sha256']: [] for file in pack['files']}
        for relative in directories:
            for path in self.store.path(relative).glob('[0-9]*.*'):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest in by_hash:
                    by_hash[digest].append(path)
        blobs = {}
        sources = {}
        warnings = []
        for file in pack['files']:
            batch = writing_cases._Batch(images=blobs, images_only=True)
            batch.image_bytes = sum(map(len, blobs.values()))
            matches = by_hash[file['sha256']]
            if not matches:
                warnings.append(file['name'] + '：未找到历史原件，请重新上传以引用配图')
                continue
            writing_cases._parse_file(matches[0].read_bytes(), batch, file['name'])
            sources.update((s['id'], s) for s in batch.sources if s.get('image_id'))
            warnings.extend(batch.warnings)
        directory = self.store.path(pack['storage'])
        writing_visuals.save_images(directory, blobs)
        pack['sources'].extend(sources.values())
        pack['warnings'] = list(dict.fromkeys([*pack['warnings'], *warnings]))
        pack['image_index_version'] = 1
        self.store.put('writing_cases', pack)
        self.store.db.commit()
        return pack

    def _versions(self, key):
        project, framework = self._project(key)
        actual_hash = hashlib.sha256(self.store.path(project['template_storage']).read_bytes()).hexdigest()
        # A draft edit changes only its chapter. Keep the material/source snapshot
        # independent from the whole-document optimistic edit counter.
        inputs = dict(project, chapter_revision=0)
        framework_content = {name: framework.get(name, []) for name in ('requirements', 'outline', 'sources')}
        return dict(framework_fingerprint=self._token(framework_content), template_hash=actual_hash,
                    mapping_revision=self.store.mapping_revision(inputs),
                    case_revision=self._pack(key)['revision'])

    @staticmethod
    def _token(versions):
        return hashlib.sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()

    def _chapter_versions(self, key, ids):
        project = self.store.require('project', key)
        revisions = project.get('chapter_revisions', {})
        return {ident: revisions.get(ident, 0) for ident in ids}

    def _check_request(self, key, body, chapters=False):
        project, _ = self._project(key)
        self.store.require_mapping_revision(project, body)
        if type(body.get('case_revision')) is not int or body['case_revision'] != self._pack(key)['revision']:
            raise ConflictError('案例资料已更新，请重新读取后再操作。')
        if chapters and (type(body.get('revision')) is not int or body['revision'] != project.get('chapter_revision', 0)):
            raise ConflictError('正文已更新，请先重新读取最新正文。')
        return project

    def _stale(self, job):
        """Check the inputs for the chapters being generated, not unrelated edits."""
        try:
            return (self._versions(job['project_id']) != job['expected_versions'] or
                    ('chat_revision' in job and self.store.mapping_revision(
                        self.store.require('project', job['project_id'])) != job['chat_revision']) or
                    self._chapter_versions(job['project_id'], job.get('target_chapter_ids', []))
                    != job.get('target_chapter_revisions', {}))
        except (ValueError, LookupError, OSError):
            return True

    def public_job(self, job):
        if not job:
            return None
        private = {'expected_versions', 'context', 'candidate_versions', 'target_chapter_revisions',
                   'chat_revision', 'redline_result', 'chat_files'}
        result = {k: copy.deepcopy(v) for k, v in job.items() if k not in private}
        if not result.get('label'):
            result['label'] = f"正文批次 {result.get('edition_number') or ''}".strip()
        revisions = self._chapter_versions(job['project_id'], [r['chapter_id'] for r in job['chapters']])
        try:
            token = self._token(self._versions(job['project_id']))
        except (ValueError, LookupError, OSError):
            token = None
        candidate_versions = job.get('candidate_versions', {})
        result['stale_chapter_ids'] = [ident for ident, revision in revisions.items()
            if candidate_versions.get(ident, {}).get('inputs') != token
            or candidate_versions.get(ident, {}).get('revision') != revision]
        result['stale'] = bool(result['stale_chapter_ids'])
        project = self.store.require('project', job['project_id'])
        result['over_capacity_ids'] = [
            row['chapter_id'] for row in job['chapters']
            if len(row['text'].strip()) > chapter_drafts.remaining_capacity(project, [row['chapter_id']])
        ]
        result['chapter_revisions'] = revisions
        accepted = set(job.get('accepted_chapter_ids') or [])
        result['pending_chapter_ids'] = [row['chapter_id'] for row in job.get('chapters') or []
                                        if row['chapter_id'] not in accepted
                                        and row['chapter_id'] not in result['stale_chapter_ids']]
        return result

    def _edition_summary(self, job, current_id):
        chapters = job.get('chapters') or []
        accepted = job.get('accepted_chapter_ids') or []
        label = job.get('label') or f"正文批次 {job.get('edition_number') or ''}".strip()
        return dict(
            id=job['id'], label=label, status=job.get('status'),
            created_at=job.get('created_at'), completed_at=job.get('completed_at'),
            model=job.get('model') or '', chapter_count=len(chapters),
            accepted_count=len(accepted),
            target_chapter_ids=list(job.get('target_chapter_ids') or []),
            is_current=job['id'] == current_id,
            error=job.get('error') or '' if job.get('status') == 'failed' else '')

    def writing_editions(self, key):
        """Recent generation batches for this project, newest first.

        Uses project.writing_edition_ids so status polling never scans every job row.
        """
        project = self.store.require('project', key)
        current = project.get('writing_job_id', '')
        ids = list(project.get('writing_edition_ids') or [])
        if current and current not in ids:
            ids = [current, *ids]
        kept, seen = [], set()
        for ident in ids:
            if ident in seen or len(kept) >= EDITION_LIMIT:
                continue
            job = self.store.get('writing_job', ident)
            if not job or job.get('project_id') != key:
                continue
            kept.append(self._edition_summary(job, current))
            seen.add(ident)
        return kept

    def body_edition(self, key):
        """Stamp describing the saved body redline/export actually use."""
        project = self.store.require('project', key)
        evidence = project.get('writing_evidence') or {}
        texts = project.get('chapter_texts') or {}
        filled = [cid for cid, text in texts.items() if isinstance(text, str) and text.strip()]
        latest = None
        for cid in filled:
            row = evidence.get(cid) or {}
            job_id = row.get('job_id')
            if not job_id:
                continue
            job = self.store.get('writing_job', job_id) or {}
            label = row.get('label') or job.get('label') or f"正文批次 {job.get('edition_number') or ''}".strip()
            accepted_at = row.get('accepted_at')
            entry = dict(chapter_id=cid, job_id=job_id, label=label or '正文批次',
                         accepted_at=accepted_at, model=job.get('model') or '')
            if accepted_at is not None and (latest is None or float(accepted_at) >= float(latest.get('accepted_at') or 0)):
                latest = entry
        current = self.store.get('writing_job', project.get('writing_job_id', '')) or {}
        accepted = set(current.get('accepted_chapter_ids') or [])
        pending = (current.get('status') == 'completed' and
                   any(row.get('chapter_id') not in accepted for row in current.get('chapters') or []))
        return dict(
            chapter_revision=project.get('chapter_revision', 0),
            filled_chapter_count=len(filled),
            latest_accept=latest,
            has_unaccepted_candidates=pending)

    def describe(self, key, view='full', edition_id=None):
        import writing_model
        if view == 'chat':
            project, _ = self._project(key)
            pack = self._pack(key)
            jobs = self.chat_jobs(key)
            return {'case_revision':pack['revision'], 'files':pack['files'], 'warnings':pack['warnings'],
                    'revision':project.get('chapter_revision', 0), 'mapping_revision':self.store.mapping_revision(project),
                    'model':writing_model.configuration(), 'turns':[self.chat_turn(job) for job in jobs],
                    'body_edition': self.body_edition(key),
                    'library_cases':self.store.contributions.library_summary()}
        if view not in {'full', 'status', 'job'}:
            raise ValueError('写作状态视图无效。')
        project = self.store.require('project', key)
        job = self.store.get('writing_job', project.get('writing_job_id', ''))
        state_revision = getattr(self.store, 'writing_state_revision', 0)
        cached = self._status_sources.get(key)
        if view == 'status' and cached and cached['state_revision'] == state_revision:
            result = dict(cached)
        else:
            framework = self.store.get('framework', project.get('framework_id', '')) or {}
            pack = self._pack(key)
            result = dict(case_revision=pack['revision'],
                          source_revision=[pack['revision'], framework.get('revision')],
                          state_revision=state_revision)
            self._status_sources[key] = dict(result)
        # Editions and the saved-body stamp stay outside the cache so polling sees
        # completed batches even when material/case revisions did not change.
        result['writing_editions'] = self.writing_editions(key)
        result['body_edition'] = self.body_edition(key)
        if view == 'status':
            # Polling does not open the Word file, scan materials, or send sources.
            fields = ('id', 'status', 'progress', 'message', 'error', 'version',
                      'target_chapter_ids', 'label', 'model', 'created_at', 'completed_at',
                      'edition_number')
            result['job'] = {name: job[name] for name in fields if name in job} if job else None
            return result
        result['job'] = self.public_job(job)
        if edition_id:
            if not isinstance(edition_id, str) or not edition_id:
                raise ValueError('正文批次无效。')
            edition = self.store.get('writing_job', edition_id)
            if not edition or edition.get('project_id') != key:
                raise ValueError('正文批次不存在或不属于本项目。')
            result['edition_job'] = self.public_job(edition)
        if view == 'job':
            return result
        selected = {mid for r in project.get('mappings', []) if r.get('action') == 'fill' for mid in r.get('material_ids', [])}
        result.update(files=copy.deepcopy(pack['files']),
                    library_case_ids=list(pack.get('library_case_ids', [])),
                    saved_library_case_id=pack.get('saved_library_case_id'),
                    library_cases=self.store.contributions.library_summary(),
                    warnings=pack['warnings'], case_sources=copy.deepcopy(pack['sources']),
                    procurement_sources=copy.deepcopy(framework.get('sources', [])),
                    materials=[{'id':m['id'],'name':m['name']} for m in self.store.all('material') if m['id'] in selected],
                    model=writing_model.configuration())
        return result

    def chat_jobs(self, key):
        return sorted((job for job in self.store.all('writing_job')
                       if job.get('chat') and job['project_id'] == key),
                      key=lambda job: job['created_at'])

    @staticmethod
    def chat_turn(job):
        value = {name: job[name] for name in ('id', 'status', 'model', 'business_notes', 'created_at')}
        value['files'] = job['chat_files']
        if job['status'] == 'failed':
            value['error'] = job.get('error') or '本轮编写未完成，请重试。'
        if job['status'] == 'completed':
            value.update(job['result'])
            value['downloads'] = [{'name':name,
                'url':f"/api/projects/{job['project_id']}/writing/download/{job['id']}/{kind}"}
                for kind, name in writing_chat.ARTIFACT_NAMES.items()]
        return value

    def upload(self, key, body, *, remote=False):
        """Parsing runs outside the store lock; publish only against the same snapshot.

        The package is the uploaded files plus any selected case-library cases, which pass
        through the same parser and are stored the same way as a direct upload.
        """
        import writing_cases
        from contributions import case_proposal
        selection = body.get('library_case_ids') or []
        uploads = body.get('files') if body.get('files') is not None else []
        if not isinstance(uploads, list):
            raise ValueError('案例上传数据格式不正确，请重新选择文件')
        library_case = body.get('save_to_library')
        if library_case is not None:
            if remote:
                raise PermissionError('外网请使用投稿入库，由本机管理员核对案例。')
            if not uploads:
                raise ValueError('请上传要同步入库的案例文件。')
            library_case = case_proposal(library_case)
        with self.store.lock:
            self._check_request(key, body)
            versions = self._versions(key)
            library = self.store.contributions.package(selection) if selection else []
        files = list(uploads) + [{'name': item['name'], 'data': base64.b64encode(
            self.store.path(item['storage']).read_bytes()).decode()} for item in library]
        image_bytes = {}
        parsed = writing_cases.parse_uploads(files, images=image_bytes)
        with self.store.lock:
            self._check_request(key, body)
            if versions != self._versions(key):
                raise ValueError('上传期间项目已更新，请重新上传案例资料。')
            if selection and self.store.contributions.package(selection) != library:
                raise ValueError('所选案例已在案例库中更新，请重新选择。')
            previous = self._indexed_pack(key) if body.get('append') is True else {}
            originals = list(previous.get('original_batches', []))
            if previous.get('storage') and previous['storage'] not in originals:
                originals.append(previous['storage'])
            if previous:
                for field in ('files', 'sources'):
                    joined = {row['id']: row for row in [*previous[field], *parsed[field]]}
                    parsed[field] = list(joined.values())
                if (len(parsed['files']) > writing_cases.MAX_LOGICAL_FILES
                        or sum(row['size'] for row in parsed['files']) > writing_cases.MAX_UPLOAD_BYTES
                        or sum(len(row['text']) for row in parsed['sources']) > writing_cases.MAX_SOURCE_CHARS):
                    raise ValueError('合并后的案例包超过容量，请精简后重新上传。')
                parsed['warnings'] = list(dict.fromkeys([*previous['warnings'], *parsed['warnings']]))
                selection = list(dict.fromkeys([*previous.get('library_case_ids', []), *selection]))
            directory = self.store.root / 'writing-cases' / key / uuid.uuid4().hex
            library_directory = None
            try:
                directory.mkdir(parents=True)
                # Keep the actual submitted package for later evidence inspection.
                writing_visuals.save_images(directory, image_bytes)
                upload_paths = []
                for index, upload in enumerate(files):
                    suffix = Path(upload['name']).suffix.lower()
                    path = directory / f'{index + 1}{suffix}'
                    path.write_bytes(base64.b64decode(upload['data'], validate=True))
                    upload_paths.append(path)
                pack = dict(id=key, revision=versions['case_revision'] + 1,
                            storage=str(directory.relative_to(self.store.root)),
                            original_batches=originals, image_index_version=1,
                            library_case_ids=list(selection), **parsed)
                if library_case is not None:
                    case_id, library_directory = self.store.contributions.save_project_case(
                        key, library_case, parsed, upload_paths[:len(uploads)])
                    pack['saved_library_case_id'] = case_id
                self.store.put('writing_cases', pack)
                self.store.db.commit()
            except Exception:
                self.store.db.rollback()
                shutil.rmtree(directory, ignore_errors=True)
                if library_directory:
                    shutil.rmtree(library_directory, ignore_errors=True)
                raise
            return self.describe(key)

    def _context(self, key, notes, target_ids=None, *, chat=False, previous=None):
        project, framework = self._project(key)
        if not project.get('special_selection_confirmed') or (
                project.get('material_flow') == 'text-first' and not project.get('text_review_confirmed')):
            raise ValueError('请先保存文字材料核对，再生成正文。')
        if any(r.get('status') == 'blocked' for r in project.get('mappings', [])):
            raise ValueError('请先处理材料或插入位置问题，再生成正文。')
        pack = self._indexed_pack(key)
        from writing_cases import readable_source
        if not any(readable_source(row) for row in pack['sources']):
            raise ValueError('请先上传有可读文字的相关项目案例资料。')
        case_sources = [row if readable_source(row) else dict(row, text='') for row in pack['sources']]
        path = self.store.path(project['template_storage'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != project['template_hash']:
            raise ValueError('框架文件已变化，请重新核对目录。')
        document = Document(path)
        originals = {}
        for ident, _, paragraph in chapter_drafts._sections(document):
            lines = []
            for block in paragraph._p.itersiblings():
                style = block.find(qn('w:pPr') + '/' + qn('w:pStyle'))
                if style is not None and re.fullmatch('BFHeading[1-9]', style.get(qn('w:val'), '')):
                    break
                text = ''.join(t.text or '' for t in block.iter(qn('w:t')))
                if text:
                    lines.append(text)
            originals[ident] = '\n'.join(lines)
        sections, active = {}, None
        for block in framework.get('_plan', {}).get('blocks', []):
            if block['kind'] == 'heading':
                active = block.get('bookmark')
                sections[active] = []
            elif active:
                sections[active].append(block)
        outline = {r['id']:r for r in framework.get('outline', [])}
        protected = writing_chat.protected_ids(project, framework) if chat else set()
        baseline = {row['chapter_id']:row['text'] for row in previous.get('chapters', [])} if previous else {}
        parents, chapters, skipped, directory = [], [], [], []
        evidence_only = re.compile(r'^(?:营业执照|法定代表人.*身份证明|.*身份证(?:复印件)?|.*授权委托书|'
                                   r'.*授权书|.*报价(?:单|表|一览表)|审计报告|纳税证明|社保证明|指定格式与待补附件)$')
        narrative = re.compile(r'方案|实施|执行|技术(?:响应|应答)|服务(?:内容|响应|能力|组织|流程)|'
                               r'项目(?:理解|团队|计划)|案例|业绩|能力|经验|(?:公司|单位|企业)(?:介绍|简介)')
        for row in chapter_drafts.read_chapters(self.store, key)['chapters']:
            while parents and parents[-1]['level'] >= row['level']:
                parents.pop()
            parents.append(row)
            spec = outline.get(row['id'], {})
            path = ' / '.join(p['title'] for p in parents)
            if not spec.get('not_applicable'):
                # Titles only: lets the model cite real sibling chapters without sending their text.
                directory.append({'id': row['id'], 'path': path})
            if chat and (row['id'] in protected or spec.get('not_applicable')):
                skipped.append(row['title'])
                continue
            blocks = sections.get(row['id'], [])
            pure_original = bool(blocks) and all(b['kind'] in {'copy', 'break'} for b in blocks)
            title = re.sub(r'^\s*(?:[一二三四五六七八九十百\d]+[、．.)）]\s*)+', '', row['title'])
            # A copied/locked technical chapter can contain instructions or a fixed
            # table and still need a narrative response. Keep the original and append.
            if evidence_only.fullmatch(title) or ((spec.get('locked_format') or pure_original) and not narrative.search(title)):
                skipped.append(row['title'])
                continue
            row.pop('visuals', None)  # The editable text already contains the visual content.
            # Unlocated requirements are bound to the parent heading (for example 技术方案); a
            # sub-chapter added under it must still see them, or a later batch is sent without any.
            requirement_ids = spec.get('requirement_ids') or next(
                (outline[p['id']]['requirement_ids'] for p in reversed(parents[:-1])
                 if outline.get(p['id'], {}).get('requirement_ids')), [])
            chapters.append(dict(row, text=row['text'] or baseline.get(row['id'], ''), path=path,
                requirement_ids=list(requirement_ids), original_text=originals.get(row['id'], ''),
                locked_format=bool(spec.get('locked_format')),
                has_table=bool(spec.get('locked_format')) or any(b['kind'] == 'table' for b in blocks)))
        if not chapters and not (chat and protected):
            raise ValueError('当前目录仅含采购原表或证明材料，没有待编写的方案章节；原表请在 Word 中填写。')
        if target_ids is not None:
            allowed = {row['id'] for row in chapters}
            if (not isinstance(target_ids, list) or not target_ids
                    or any(not isinstance(ident, str) for ident in target_ids)
                    or len(set(target_ids)) != len(target_ids) or not set(target_ids) <= allowed):
                raise ValueError('请选择有效、可编写且不重复的目标章节；采购原表仍须在 Word 中填写。')
            chapters = [row for row in chapters if row['id'] in target_ids]
        if chat and target_ids is None and previous:
            chosen = writing_chat.select_chapters(chapters, writing_chat.chapter_paths(framework['outline']), notes)
            chosen_ids = {row['id'] for row in chosen}
            new_cases = previous.get('expected_versions', {}).get('case_revision') != pack['revision']
            chapters = [row for row in chapters if row['id'] in chosen_ids
                        or (new_cases and writing_chat.PERFORMANCE.search(row['title']))]
        if len(chapters) > 120:
            raise ValueError('待编写章节超过 120 个，请先按包件整理目录。')
        available = chapter_drafts.remaining_capacity(project, [row['id'] for row in chapters])
        if chapters and available < len(chapters):
            raise ValueError(f'正文容量不足，目标章节仅可使用 {max(0, available):,} 字；请编辑已有正文或改选要替换的章节。')
        if chapters:
            quota, extra = divmod(available, len(chapters))
            for index, row in enumerate(chapters):
                row['max_output_chars'] = min(chapter_drafts.CHAPTER_LIMIT, quota + (index < extra))
        selected = {mid for r in project.get('mappings', []) if r.get('action') == 'fill' for mid in r.get('material_ids', [])}
        materials = [{k:m.get(k, '') for k in ('id','name','category_id','subject','content','description','years','valid_until')} |
                     {'text_available':m.get('mode') == 'text'}
                     for m in self.store.all('material') if m['id'] in selected and not m.get('archived')]
        category_names = {c['id']: c.get('name', '') for c in self.store.all('category')}
        # 先按全部目标章节过滤一次；分组发送时再按每组章节过滤。
        materials = outbound_materials(materials, chapters, category_names)
        req_ids = {i for c in chapters for i in c['requirement_ids']}
        requirements = [r for r in framework.get('requirements', []) if r['id'] in req_ids]
        source_ids = {ref.get('source_id', ref.get('id')) for r in requirements for ref in r.get('references', [])}
        context = dict(project={'id':key,'name':project['name']}, chapters=chapters, outline=directory,
            category_names=category_names,
            requirements=requirements, procurement_sources=[s for s in framework.get('sources', []) if s['id'] in source_ids],
            case_sources=case_sources, materials=materials, business_notes=notes)
        if len(json.dumps(context, ensure_ascii=False)) > 650000:
            raise ValueError('本次正文资料超过处理容量，请精简或拆分案例包；未截断资料。')
        return context, skipped

    def outbound(self, key, body):
        """生成前确认：列出本次将发给 AI 服务的资料，不启动生成、不发送任何内容。"""
        import writing_model
        self._check_request(key, body, chapters=True)
        notes = body.get('business_notes', '')
        if not isinstance(notes, str) or len(notes) > 10000:
            raise ValueError('业务补充信息最多 10000 字。')
        model = writing_model.resolve_model(body.get('model'))
        chat = body.get('chat') is True
        previous = self.previous_chat(key) if chat else None
        context, skipped = self._context(key, notes, body.get('chapter_ids'), chat=chat, previous=previous)
        groups = [context['chapters'][i:i+6] for i in range(0, len(context['chapters']), 6)]
        sent = {m['id'] for group in groups
                for m in outbound_materials(context['materials'], group, context['category_names'])
                if not m.get('content_withheld')}
        return dict(
            model=model,
            chapter_count=len(context['chapters']), group_count=len(groups),
            chapters=[c['title'] for c in context['chapters']][:40], skipped=skipped,
            requirement_count=len(context['requirements']),
            procurement_source_count=len(context['procurement_sources']),
            case_files=list(dict.fromkeys(s.get('file_name') or s.get('id') for s in context['case_sources']))[:40],
            case_source_count=len(context['case_sources']),
            materials_with_text=[m['name'] for m in context['materials'] if m['id'] in sent],
            materials_name_only=[m['name'] for m in context['materials'] if m['id'] not in sent],
            business_notes_length=len(notes))

    def previous_chat(self, key):
        return next((job for job in reversed(self.chat_jobs(key)) if job['status'] == 'completed'), None)

    def start(self, key, body):
        with self.store.lock:
            return self._start(key, body)

    def _start(self, key, body):
        import writing_model
        project = self._check_request(key, body, chapters=True)
        if not writing_model.configuration()['ready']:
            raise ValueError(writing_model.configuration()['message'])
        model = writing_model.resolve_model(body.get('model'))
        active = [j for j in self.store.all('writing_job') if j['status'] in {'queued', 'running'}]
        if any(j['project_id'] == key for j in active):
            raise ValueError('本项目正在生成正文，请等待完成。')
        if len(active) >= 3:
            raise ValueError('已有三个正文任务等待处理，请稍后再生成。')
        notes = body.get('business_notes', '')
        if not isinstance(notes, str) or len(notes) > 10000:
            raise ValueError('业务补充信息最多 10000 字。')
        chat = body.get('chat') is True
        previous = self.previous_chat(key) if chat else None
        context, skipped = self._context(key, notes, body.get('chapter_ids'), chat=chat, previous=previous)
        project = self._check_request(key, body, chapters=True)
        target_ids = [row['id'] for row in context['chapters']]
        previous = (previous if chat else self.store.get('writing_job', project.get('writing_job_id', ''))) or {}
        seq = int(project.get('writing_edition_seq') or 0) + 1
        label = f'正文批次 {seq}'
        job = dict(id='writing_'+uuid.uuid4().hex, project_id=key, status='queued', progress=0,
                   evidence_policy=2,
                   message='等待生成', created_at=time.time(), version=1, model=model,
                   label=label, edition_number=seq, completed_at=None,
                   chapters=copy.deepcopy(previous.get('chapters', [])),
                   accepted_chapter_ids=list(previous.get('accepted_chapter_ids', [])),
                   candidate_versions=copy.deepcopy(previous.get('candidate_versions', {})),
                   target_chapter_ids=target_ids,
                   target_chapter_revisions=self._chapter_versions(key, target_ids),
                   notes=[f'以下 {len(skipped)} 章未生成正文，需另行核对：'+'、'.join(skipped)] if skipped else [],
                   expected_versions=self._versions(key), business_notes=notes)
        if chat:
            job.update(chat=True, chat_revision=self.store.mapping_revision(project),
                       chat_files=[item['name'] for item in self._pack(key)['files']])
        self.store.put('writing_job', job)
        project['writing_job_id'] = job['id']
        project['writing_edition_seq'] = seq
        history = [job['id'], *[ident for ident in project.get('writing_edition_ids') or [] if ident != job['id']]]
        project['writing_edition_ids'] = history[:EDITION_LIMIT]
        self.store.put('project', project); self.store.db.commit()
        self.pool.submit(self.run, job['id'], copy.deepcopy(context))
        return self.chat_turn(job) if chat else self.public_job(job)

    def run(self, ident, context):
        import writing_model
        def update(**values):
            with self.store.lock:
                job = self.store.require('writing_job', ident)
                job.update(values)
                if any(name in values for name in ('chapters', 'candidate_versions', 'accepted_chapter_ids')):
                    job['version'] = job.get('version', 0) + 1
                self.store.put('writing_job', job); self.store.db.commit()
                return job
        with self.store.lock:
            initial = self.store.require('writing_job', ident)
            model = initial.get('model')
        update(status='running', progress=2, message='正在根据案例编写正文')
        generated, notes = [], []
        try:
            chunks = [context['chapters'][i:i+6] for i in range(0, len(context['chapters']), 6)]
            for index, chapters in enumerate(chunks):
                with self.store.lock:
                    if self._stale(self.store.require('writing_job', ident)):
                        raise ValueError('生成期间目录、材料、案例或正文已更新，请重新生成。')
                payload = dict(context, chapters=chapters)
                payload['materials'] = outbound_materials(context['materials'], chapters, context['category_names'])
                payload.pop('category_names', None)
                reqs = {r for c in chapters for r in c['requirement_ids']}
                payload['requirements'] = [r for r in context['requirements'] if r['id'] in reqs]
                source_ids = {ref.get('source_id', ref.get('id'))
                    for requirement in payload['requirements'] for ref in requirement.get('references', [])}
                payload['procurement_sources'] = [source for source in context['procurement_sources']
                                                  if source['id'] in source_ids]
                update(message=f'正在编写第 {index+1}/{len(chunks)} 组章节', progress=5+int(85*index/len(chunks)))
                result = writing_model.generate(payload, model=model)
                generated.extend(result['chapters']); notes.extend(result['notes'])
            with self.store.lock:
                chapter_drafts.check_capacity(self.store.require('project', initial['project_id']),
                                               {row['chapter_id']: row['text'] for row in generated})
            if initial.get('chat'):
                writing_chat.publish(self, initial, generated, initial['notes'] + list(dict.fromkeys(notes)))
                return
            with self.store.lock:
                job = self.store.require('writing_job', ident)
                if self._stale(job):
                    raise ValueError('生成期间输入已更新，旧候选正文未发布，请重新生成。')
                replacements = {row['chapter_id']: row for row in generated}
                merged = [replacements.pop(row['chapter_id'], row) for row in job['chapters']]
                merged.extend(replacements.values())
                token = self._token(job['expected_versions'])
                versions = dict(job.get('candidate_versions', {}))
                for row in generated:
                    chapter = row['chapter_id']
                    versions[chapter] = dict(inputs=token, revision=job['target_chapter_revisions'][chapter],
                                             job_id=ident)
                targets = set(job['target_chapter_ids'])
                update(status='completed', progress=100, message='候选正文已生成，请核对后采纳',
                       completed_at=time.time(),
                       chapters=merged, candidate_versions=versions,
                       accepted_chapter_ids=[chapter for chapter in job['accepted_chapter_ids'] if chapter not in targets],
                       notes=job['notes']+list(dict.fromkeys(notes)))
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else '正文生成失败，请重试；已有正文未修改。'
            update(status='failed', message='生成失败', error=message, completed_at=time.time())

    def accept(self, key, body):
        with self.store.lock:
            try:
                return self._accept(key, body)
            except Exception:
                self.store.db.rollback()
                raise

    def _accept(self, key, body):
        from writing_cases import readable_source
        self._check_request(key, body, chapters=True)
        job = self.store.require('writing_job', body.get('job_id', ''))
        if job.get('chat') and job['status'] != 'completed':
            raise ValueError('请先等待本轮工作稿生成完成。')
        if job.get('project_id') != key or self.store.require('project', key).get('writing_job_id') != job['id']:
            raise ValueError('请选择本项目最新生成的候选正文。')
        ids, available = body.get('chapter_ids'), {r['chapter_id']:r for r in job['chapters']}
        if (not isinstance(ids, list) or not ids or any(not isinstance(i,str) for i in ids)
                or len(set(ids)) != len(ids) or not set(ids) <= available.keys()):
            raise ValueError('请选择有效且不重复的候选章节。')
        if (set(ids) & set(self.public_job(job)['stale_chapter_ids'])
                or (job['status'] in {'queued', 'running'} and set(ids) & set(job.get('target_chapter_ids', [])))):
            raise ValueError('所选候选正文未完成或输入已变化，请重新生成这些章节。')
        if set(ids) & set(job['accepted_chapter_ids']):
            raise ValueError('所选章节已采纳，请直接编辑正文；需要重写时重新生成。')
        sources = self._pack(key)['sources']
        if not any(readable_source(source) for source in sources):
            raise ValueError('案例来源缺少可核验文字，请补充案例后重新生成候选正文。')
        visual_sources = {source['id'] for source in sources if not readable_source(source)}
        for ident in ids:
            row = available[ident]
            visual_refs = visual_sources.intersection(row.get('source_refs', []))
            if not visual_refs:
                continue
            origin = job['candidate_versions'][ident].get('job_id', job['id'])
            origin_job = job if origin == job['id'] else self.store.get('writing_job', origin) or {}
            embedded = {block.get('image_id') for block in row.get('visuals', [])}
            if origin_job.get('evidence_policy') != 2 or not visual_refs <= embedded:
                raise ValueError('旧候选引用了无可读文字的案例来源，请重新生成本章。')
        project = self.store.require('project', key)
        chapter_drafts.check_capacity(project, {i: available[i]['text'] for i in ids})
        request = dict(body, chapters=[{'id':i,'text':available[i]['text']} for i in ids])
        editor = chapter_drafts.save_chapters(self.store, key, request, commit=False,
                    adopted_visuals={i:available[i].get('visuals', []) for i in ids})
        project = self.store.require('project', key)
        evidence = project.setdefault('writing_evidence', {})
        pack = self._pack(key)
        accepted_at = time.time()
        for i in ids:
            row = available[i]
            source_job = job['candidate_versions'][i].get('job_id', job['id'])
            evidence[i] = dict(row, job_id=source_job, label=job.get('label') or f"正文批次 {job.get('edition_number') or ''}".strip(),
                               accepted_at=accepted_at, case_revision=pack['revision'],
                               case_sources=[s for s in pack['sources'] if s['id'] in row['source_refs']])
            job['candidate_versions'][i]['revision'] = project.get('chapter_revisions', {}).get(i, 0)
        self.store.put('project', project)
        job['accepted_chapter_ids'] += ids
        job['version'] = job.get('version', 0) + 1
        self.store.put('writing_job', job); self.store.db.commit()
        return {'editor':editor, 'writing':self.describe(key)}
