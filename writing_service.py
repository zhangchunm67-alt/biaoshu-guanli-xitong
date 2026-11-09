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
            raise ValueError('案例资料已更新，请重新读取后再操作。')
        if chapters and (type(body.get('revision')) is not int or body['revision'] != project.get('chapter_revision', 0)):
            raise ValueError('正文已更新，请先重新读取最新正文。')
        return project

    def _stale(self, job):
        """Check the inputs for the chapters being generated, not unrelated edits."""
        try:
            return (self._versions(job['project_id']) != job['expected_versions'] or
                    self._chapter_versions(job['project_id'], job.get('target_chapter_ids', []))
                    != job.get('target_chapter_revisions', {}))
        except (ValueError, LookupError, OSError):
            return True

    def public_job(self, job):
        if not job:
            return None
        private = {'expected_versions', 'context', 'candidate_versions', 'target_chapter_revisions'}
        result = {k: copy.deepcopy(v) for k, v in job.items() if k not in private}
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
        result['chapter_revisions'] = revisions
        return result

    def describe(self, key, view='full'):
        import writing_model
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
        if view == 'status':
            # Polling does not open the Word file, scan materials, or send sources.
            fields = ('id', 'status', 'progress', 'message', 'error', 'version', 'target_chapter_ids')
            result['job'] = {name: job[name] for name in fields if name in job} if job else None
            return result
        result['job'] = self.public_job(job)
        if view == 'job':
            return result
        selected = {mid for r in project.get('mappings', []) if r.get('action') == 'fill' for mid in r.get('material_ids', [])}
        result.update(files=copy.deepcopy(pack['files']),
                    library_case_ids=list(pack.get('library_case_ids', [])),
                    library_cases=self.store.contributions.library_summary(),
                    warnings=pack['warnings'], case_sources=copy.deepcopy(pack['sources']),
                    procurement_sources=copy.deepcopy(framework.get('sources', [])),
                    materials=[{'id':m['id'],'name':m['name']} for m in self.store.all('material') if m['id'] in selected],
                    model=writing_model.configuration())
        return result

    def upload(self, key, body):
        """Parsing runs outside the store lock; publish only against the same snapshot.

        The package is the uploaded files plus any selected case-library cases, which pass
        through the same parser and are stored the same way as a direct upload.
        """
        import writing_cases
        selection = body.get('library_case_ids') or []
        uploads = body.get('files') if body.get('files') is not None else []
        if not isinstance(uploads, list):
            raise ValueError('案例上传数据格式不正确，请重新选择文件')
        with self.store.lock:
            self._check_request(key, body)
            versions = self._versions(key)
            library = self.store.contributions.package(selection) if selection else []
        files = list(uploads) + [{'name': item['name'], 'data': base64.b64encode(
            self.store.path(item['storage']).read_bytes()).decode()} for item in library]
        parsed = writing_cases.parse_uploads(files)
        with self.store.lock:
            self._check_request(key, body)
            if versions != self._versions(key):
                raise ValueError('上传期间项目已更新，请重新上传案例资料。')
            if selection and self.store.contributions.package(selection) != library:
                raise ValueError('所选案例已在案例库中更新，请重新选择。')
            directory = self.store.root / 'writing-cases' / key / uuid.uuid4().hex
            try:
                directory.mkdir(parents=True)
                # Keep the actual submitted package for later evidence inspection.
                for index, upload in enumerate(files):
                    suffix = Path(upload['name']).suffix.lower()
                    (directory / f'{index + 1}{suffix}').write_bytes(base64.b64decode(upload['data'], validate=True))
                pack = dict(id=key, revision=versions['case_revision'] + 1,
                            storage=str(directory.relative_to(self.store.root)),
                            library_case_ids=list(selection), **parsed)
                self.store.put('writing_cases', pack)
                self.store.db.commit()
            except Exception:
                self.store.db.rollback()
                shutil.rmtree(directory, ignore_errors=True)
                raise
            return self.describe(key)

    def _context(self, key, notes, target_ids=None):
        project, framework = self._project(key)
        if not project.get('special_selection_confirmed') or (
                project.get('material_flow') == 'text-first' and not project.get('text_review_confirmed')):
            raise ValueError('请先保存文字材料核对，再生成正文。')
        if any(r.get('status') == 'blocked' for r in project.get('mappings', [])):
            raise ValueError('请先处理材料或插入位置问题，再生成正文。')
        pack = self._pack(key)
        if not pack['sources']:
            raise ValueError('请先上传相关项目案例资料。')
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
        parents, chapters, skipped = [], [], []
        evidence_only = re.compile(r'^(?:营业执照|法定代表人.*身份证明|.*身份证(?:复印件)?|.*授权委托书|'
                                   r'.*授权书|.*报价(?:单|表|一览表)|审计报告|纳税证明|社保证明|指定格式与待补附件)$')
        narrative = re.compile(r'方案|实施|执行|技术(?:响应|应答)|服务(?:内容|响应|能力|组织|流程)|'
                               r'项目(?:理解|团队|计划)|案例|业绩|能力|经验|(?:公司|单位|企业)(?:介绍|简介)')
        for row in chapter_drafts.read_chapters(self.store, key)['chapters']:
            while parents and parents[-1]['level'] >= row['level']:
                parents.pop()
            parents.append(row)
            spec = outline.get(row['id'], {})
            blocks = sections.get(row['id'], [])
            pure_original = bool(blocks) and all(b['kind'] in {'copy', 'break'} for b in blocks)
            title = re.sub(r'^\s*(?:[一二三四五六七八九十百\d]+[、．.)）]\s*)+', '', row['title'])
            # A copied/locked technical chapter can contain instructions or a fixed
            # table and still need a narrative response. Keep the original and append.
            if evidence_only.fullmatch(title) or ((spec.get('locked_format') or pure_original) and not narrative.search(title)):
                skipped.append(row['title'])
                continue
            chapters.append(dict(row, path=' / '.join(p['title'] for p in parents),
                requirement_ids=spec.get('requirement_ids', []), original_text=originals.get(row['id'], ''),
                locked_format=bool(spec.get('locked_format')),
                has_table=bool(spec.get('locked_format')) or any(b['kind'] == 'table' for b in blocks)))
        if not chapters:
            raise ValueError('当前目录仅含采购原表或证明材料，没有待编写的方案章节；原表请在 Word 中填写。')
        if target_ids is not None:
            allowed = {row['id'] for row in chapters}
            if (not isinstance(target_ids, list) or not target_ids
                    or any(not isinstance(ident, str) for ident in target_ids)
                    or len(set(target_ids)) != len(target_ids) or not set(target_ids) <= allowed):
                raise ValueError('请选择有效、可编写且不重复的目标章节；采购原表仍须在 Word 中填写。')
            chapters = [row for row in chapters if row['id'] in target_ids]
        if len(chapters) > 120:
            raise ValueError('待编写章节超过 120 个，请先按包件整理目录。')
        selected = {mid for r in project.get('mappings', []) if r.get('action') == 'fill' for mid in r.get('material_ids', [])}
        materials = [{k:m.get(k, '') for k in ('id','name','category_id','subject','content','description','years','valid_until')} |
                     {'text_available':m.get('mode') == 'text'}
                     for m in self.store.all('material') if m['id'] in selected and not m.get('archived')]
        req_ids = {i for c in chapters for i in c['requirement_ids']}
        requirements = [r for r in framework.get('requirements', []) if r['id'] in req_ids]
        source_ids = {ref.get('source_id', ref.get('id')) for r in requirements for ref in r.get('references', [])}
        context = dict(project={'id':key,'name':project['name']}, chapters=chapters,
            requirements=requirements, procurement_sources=[s for s in framework.get('sources', []) if s['id'] in source_ids],
            case_sources=pack['sources'], materials=materials, business_notes=notes)
        if len(json.dumps(context, ensure_ascii=False)) > 650000:
            raise ValueError('本次正文资料超过处理容量，请精简或拆分案例包；未截断资料。')
        return context, skipped

    def start(self, key, body):
        import writing_model
        project = self._check_request(key, body, chapters=True)
        if not writing_model.configuration()['ready']:
            raise ValueError(writing_model.configuration()['message'])
        active = [j for j in self.store.all('writing_job') if j['status'] in {'queued', 'running'}]
        if any(j['project_id'] == key for j in active):
            raise ValueError('本项目正在生成正文，请等待完成。')
        if len(active) >= 3:
            raise ValueError('已有三个正文任务等待处理，请稍后再生成。')
        notes = body.get('business_notes', '')
        if not isinstance(notes, str) or len(notes) > 10000:
            raise ValueError('业务补充信息最多 10000 字。')
        context, skipped = self._context(key, notes, body.get('chapter_ids'))
        target_ids = [row['id'] for row in context['chapters']]
        previous = self.store.get('writing_job', project.get('writing_job_id', '')) or {}
        job = dict(id='writing_'+uuid.uuid4().hex, project_id=key, status='queued', progress=0,
                   message='等待生成', created_at=time.time(), version=1,
                   chapters=copy.deepcopy(previous.get('chapters', [])),
                   accepted_chapter_ids=list(previous.get('accepted_chapter_ids', [])),
                   candidate_versions=copy.deepcopy(previous.get('candidate_versions', {})),
                   target_chapter_ids=target_ids,
                   target_chapter_revisions=self._chapter_versions(key, target_ids),
                   notes=[f'以下 {len(skipped)} 章未生成正文，需另行核对：'+'、'.join(skipped)] if skipped else [],
                   expected_versions=self._versions(key), business_notes=notes)
        self.store.put('writing_job', job)
        project['writing_job_id'] = job['id']
        self.store.put('project', project); self.store.db.commit()
        self.pool.submit(self.run, job['id'], copy.deepcopy(context))
        return self.public_job(job)

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
        update(status='running', progress=2, message='正在根据案例编写正文')
        generated, notes = [], []
        try:
            chunks = [context['chapters'][i:i+6] for i in range(0, len(context['chapters']), 6)]
            for index, chapters in enumerate(chunks):
                with self.store.lock:
                    if self._stale(self.store.require('writing_job', ident)):
                        raise ValueError('生成期间目录、材料、案例或正文已更新，请重新生成。')
                payload = dict(context, chapters=chapters)
                reqs = {r for c in chapters for r in c['requirement_ids']}
                payload['requirements'] = [r for r in context['requirements'] if r['id'] in reqs]
                source_ids = {ref.get('source_id', ref.get('id'))
                    for requirement in payload['requirements'] for ref in requirement.get('references', [])}
                payload['procurement_sources'] = [source for source in context['procurement_sources']
                                                  if source['id'] in source_ids]
                update(message=f'正在编写第 {index+1}/{len(chunks)} 组章节', progress=5+int(85*index/len(chunks)))
                result = writing_model.generate(payload)
                generated.extend(result['chapters']); notes.extend(result['notes'])
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
                       chapters=merged, candidate_versions=versions,
                       accepted_chapter_ids=[chapter for chapter in job['accepted_chapter_ids'] if chapter not in targets],
                       notes=job['notes']+list(dict.fromkeys(notes)))
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else '正文生成失败，请重试；已有正文未修改。'
            update(status='failed', message='生成失败', error=message)

    def accept(self, key, body):
        self._check_request(key, body, chapters=True)
        job = self.store.require('writing_job', body.get('job_id', ''))
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
        request = dict(body, chapters=[{'id':i,'text':available[i]['text']} for i in ids])
        editor = chapter_drafts.save_chapters(self.store, key, request)
        project = self.store.require('project', key)
        evidence = project.setdefault('writing_evidence', {})
        pack = self._pack(key)
        for i in ids:
            row = available[i]
            evidence[i] = dict(row, job_id=job['candidate_versions'][i].get('job_id', job['id']), case_revision=pack['revision'],
                               case_sources=[s for s in pack['sources'] if s['id'] in row['source_refs']])
            job['candidate_versions'][i]['revision'] = project.get('chapter_revisions', {}).get(i, 0)
        self.store.put('project', project)
        job['accepted_chapter_ids'] += ids
        job['version'] = job.get('version', 0) + 1
        self.store.put('writing_job', job); self.store.db.commit()
        return {'editor':editor, 'writing':self.describe(key)}
