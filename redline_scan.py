"""Local redline checklist after chapter drafting. No model calls.

自动检查只覆盖两类：材料缺漏（营业执照、身份证明等栏目是否已就绪），以及关联章节是否已有正文。
保证金、签章、报价上限、有效期等无法自动判断的红线，给出针对性的人工核对提示。

每条红线有独立的核对依据（basis）：该红线原文、关联章节正文、相关材料栏目。
只有依据变化的红线需要重新确认，改动其他章节或材料不影响已确认的红线。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

MATERIAL_PATTERNS = (
    (re.compile(r'营业执照'), 'business_license', '营业执照'),
    (re.compile(r'法定代表人.{0,8}身份|法人.{0,6}身份证明'), 'legal_identity', '法定代表人身份证明'),
    (re.compile(r'授权委托|授权书'), 'authorization', '授权委托书'),
    (re.compile(r'审计报告|财务报告|财务报表'), 'audit_report', '财务审计报告'),
    (re.compile(r'(?:纳税|税收|完税|缴税).{0,12}(?:证明|凭证|记录|缴纳|材料)|(?:缴纳|依法缴纳).{0,6}税'), 'tax_social', '纳税证明'),
    (re.compile(r'社保|社会保险|社会保障资金'), 'tax_social', '社保凭证'),
    (re.compile(r'ISO|质量管理体系|体系认证'), 'iso_cert', 'ISO相关资质'),
)

# 这些说法讲的是"不得有违法失信记录"，不是要求提交纳税、社保凭证，匹配材料前先去掉。
CONTEXT_EXCLUSIONS = re.compile(
    r'(?:重大)?税收违法(?:案件)?(?:当事人)?(?:名单)?|(?:偷|逃|漏|欠|骗)税|'
    r'(?:严重)?违法失信(?:行为|名单|记录)?|失信被执行人|(?:社保|社会保险)(?:欺诈|违法)')

# 不能自动判断的红线类型：命中后给出具体的人工核对要点。
CHECK_HINTS = (
    (re.compile(r'保证金'), '人工核对：投标保证金的金额、缴纳方式与到账截止时间，并在响应文件中附缴纳凭证。'),
    (re.compile(r'签字|签章|盖章|公章|电子签|骑缝'), '人工核对：所有指定位置已签字、盖章（含骑缝章、电子签章），签署人与授权一致。'),
    (re.compile(r'最高限价|控制价|拦标价|预算(?:金额|价)?|超出预算|报价.{0,8}(?:超过|高于|超出)'),
     '人工核对：报价未超过最高限价或预算，分项合计与总价、大小写金额一致。'),
    (re.compile(r'有效期'), '人工核对：响应文件写明的投标有效期不短于要求。'),
    (re.compile(r'工期|交付期|交货期|服务期|完成时间|响应时间|到场时间'), '人工核对：正文承诺的工期、交付或响应时间满足要求。'),
    (re.compile(r'★|实质性'), '人工核对：带★或实质性条款逐条满足，未作负偏离。'),
    (re.compile(r'联合体|分包|转包'), '人工核对：联合体、分包安排符合要求（不允许时不得出现）。'),
    (re.compile(r'格式|装订|密封|份数|正本|副本'), '人工核对：响应文件格式、份数、密封与标记符合要求。'),
)

GENERAL_HINT = '系统未自动发现材料缺漏；请对照原文确认正文与材料均未触犯该红线。'


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _mapping_key(row):
    return (row.get('id'), row.get('category_id'), row.get('action'), row.get('status'),
            tuple(row.get('material_ids') or []))


def material_versions(store):
    """材料 id -> 修订号与附件 id；用于确认和 AI 结果的失效判断。"""
    return {row['id']: (row.get('revision', 0), tuple(row.get('file_ids') or []))
            for row in store.all('material')}


def input_token(requirements, chapter_texts, mappings, outline=None, versions=None):
    """整体输入指纹，用于扫描版本和 AI 核对新鲜度；人工确认是否失效按每条 basis 判断。"""
    versions = versions or {}
    referenced = sorted({mid for row in (mappings or []) for mid in (row.get('material_ids') or [])})
    payload = {
        'redlines': [(row['id'], row.get('label', ''), row.get('text', ''))
                     for row in requirements if row.get('category') == 'redline'],
        'chapters': sorted((key, value) for key, value in (chapter_texts or {}).items()),
        'mappings': sorted(
            (*_mapping_key(row), row.get('title') or row.get('category_name') or '',
             row.get('note') or '')
            for row in (mappings or [])),
        'materials': [(mid, versions.get(mid)) for mid in referenced],
        'outline': [(row.get('id'), row.get('text'), row.get('level'),
                     tuple(row.get('requirement_ids') or []))
                    for row in (outline or [])],
    }
    return _digest(payload)


def _legacy_input_token(requirements, chapter_texts, mappings):
    """旧版（0ade6d4）整体指纹，仅用于迁移已保存的 acknowledged_ids。"""
    payload = {
        'redlines': [(row['id'], row.get('label', ''), row.get('text', ''))
                     for row in requirements if row.get('category') == 'redline'],
        'chapters': sorted((key, value) for key, value in (chapter_texts or {}).items()),
        'mappings': sorted(
            (row.get('id'), row.get('action'), row.get('status'),
             tuple(row.get('material_ids') or []))
            for row in (mappings or [])),
    }
    return _digest(payload)


def _material_body(body):
    return CONTEXT_EXCLUSIONS.sub('', body)


def _descendant_ids(outline):
    """Map each outline row to its descendants (until the next same-or-higher level)."""
    result = {}
    for index, row in enumerate(outline or []):
        level = int(row.get('level') or 1)
        ids = []
        for child in outline[index + 1:]:
            if int(child.get('level') or 1) <= level:
                break
            if child.get('id'):
                ids.append(child['id'])
        result[row.get('id')] = ids
    return result


def scan(requirements, chapter_texts, mappings, outline=None, versions=None):
    redlines = [row for row in requirements if row.get('category') == 'redline']
    texts = chapter_texts or {}
    outline = outline or []
    versions = versions or {}
    descendants = _descendant_ids(outline)
    items = []
    for req in redlines:
        body = f"{req.get('label') or ''}\n{req.get('text') or ''}"
        material_body = _material_body(body)
        findings, hints = [], []
        categories = []
        for pattern, category_id, title in MATERIAL_PATTERNS:
            if not pattern.search(material_body):
                continue
            categories.append(category_id)
            rows = [row for row in (mappings or []) if row.get('category_id') == category_id]
            if not rows:
                findings.append(f'红线涉及「{title}」，当前材料核对未出现对应栏目，请回目录或材料页核对。')
                continue
            for row in rows:
                if row.get('status') == 'blocked':
                    findings.append(f'「{title}」材料仍有阻断问题，可能触犯红线。')
                elif row.get('action') in {'missing', 'skip'} or row.get('status') in {'missing', 'skipped'}:
                    findings.append(f'「{title}」仍为待补或跳过，递交前须确认是否触犯红线。')
        linked, empty = [], []
        for row in outline:
            if req['id'] not in (row.get('requirement_ids') or []):
                continue
            linked.append(row.get('id'))
            if row.get('locked_format'):
                continue
            if str(texts.get(row.get('id'), '')).strip():
                continue
            # 顶层章节自身可能只是标题，正文写在其子章节中；子章节有正文即视为已响应。
            child_ids = descendants.get(row.get('id')) or []
            if any(str(texts.get(child, '')).strip() for child in child_ids):
                continue
            empty.append(row.get('id'))
            findings.append(
                f'关联章节「{row.get("text") or row.get("id")}」尚无正文，请核对红线是否需在该章响应。')
        for pattern, hint in CHECK_HINTS:
            if pattern.search(body) and hint not in hints:
                hints.append(hint)
        manual_check = bool(hints)
        findings = list(dict.fromkeys(findings))
        relevant = sorted(_mapping_key(row) for row in (mappings or []) if row.get('category_id') in categories)
        # 确认依据包含关联章节及其全部子章节：任何一处正文变化都会让确认失效。
        linked_texts, seen = [], set()
        for ident in linked:
            for chapter_id in [ident, *(descendants.get(ident) or [])]:
                if chapter_id in seen:
                    continue
                seen.add(chapter_id)
                linked_texts.append((chapter_id, texts.get(chapter_id, '')))
        material_ids = sorted({mid for key in relevant for mid in key[4]})
        material_state = [(mid, versions.get(mid)) for mid in material_ids]
        basis = _digest({
            'id': req['id'], 'label': req.get('label', ''), 'text': req.get('text', ''),
            'chapters': linked_texts,
            'mappings': relevant, 'materials': material_state, 'findings': findings,
        })
        items.append({
            'id': req['id'],
            'label': (req.get('label') or req.get('text') or '')[:120],
            'text': req.get('text') or '',
            'status': 'risk' if findings else 'attention',
            'findings': findings,
            'hints': hints + ([GENERAL_HINT] if not findings and not hints else []),
            'manual_check': manual_check,
            'auto_checked': bool(categories or linked),
            'linked_chapter_ids': linked,
            'empty_chapter_ids': empty,
            'material_category_ids': list(dict.fromkeys(categories)),
            'material_ids': material_ids,
            'basis': basis,
        })
    return {
        'input_token': input_token(requirements, chapter_texts, mappings, outline, versions),
        'items': items,
        'scanned_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'scope': '自动检查：材料栏目是否缺漏或待补，关联章节是否已有正文；其余为人工核对提示。',
    }


def apply_ack(result, acknowledged):
    """acknowledged：{红线 id: 确认时的 basis}。依据变化的红线自动回到待确认。"""
    acknowledged = acknowledged or {}
    items = []
    for item in result['items']:
        row = dict(item)
        row['acknowledged'] = acknowledged.get(item['id']) == item['basis']
        row['ack_expired'] = item['id'] in acknowledged and not row['acknowledged']
        items.append(row)
    pending = [item for item in items if not item['acknowledged']]
    return {
        **result,
        'items': items,
        'acknowledged_ids': [item['id'] for item in items if item['acknowledged']],
        'pending_count': len(pending),
        'risk_ids': [item['id'] for item in items if item['status'] == 'risk'],
        'complete': not pending,
    }


def auto_pass_ids(result, ai_review):
    """规则无风险且 AI 通过、正文引用可核实的条目，可自动写入确认。"""
    ai = ai_review if isinstance(ai_review, dict) else {}
    eligible = {}
    for item in result.get('items') or []:
        if item.get('status') == 'risk' or item.get('manual_check') or item.get('auto_checked') is not True:
            continue
        row = ai.get(item['id']) or {}
        if row.get('verdict') == 'satisfied' and row.get('evidence_verified') is True:
            eligible[item['id']] = item['basis']
    return eligible


def _fresh_ai(project, input_token):
    review = project.get('redline_ai') or {}
    if (isinstance(review, dict) and review.get('version') == 2
            and review.get('input_token') == input_token
            and isinstance(review.get('items'), dict)):
        return review['items'], review.get('reviewed_at')
    return None, None


def _saved_acks(review, result, legacy_token):
    """读取确认记录；旧版 input_token + acknowledged_ids 在输入未变时按当前 basis 迁移。"""
    if not isinstance(review, dict):
        return {}
    acks = review.get('acknowledged')
    if review.get('auto'):
        return {}
    if isinstance(acks, dict):
        return dict(acks)
    ids = review.get('acknowledged_ids')
    if isinstance(ids, list) and review.get('input_token') in {result['input_token'], legacy_token}:
        by_id = {item['id']: item for item in result['items']}
        return {ident: by_id[ident]['basis'] for ident in ids
                if isinstance(ident, str) and ident in by_id}
    return {}


def _context(store, key):
    project = store.require('project', key)
    framework = store.get('framework', project.get('framework_id', ''))
    if not framework or framework.get('projects', {}).get('word') != key:
        raise ValueError('请从已确认目录的当前整本项目进入废标红线扫描。')
    if framework.get('review_version') and not framework.get('requirements_confirmed'):
        raise ValueError('请先核对确认采购要求，再扫描废标红线。')
    return project, framework


def evaluate(project, framework, versions=None):
    requirements = framework.get('requirements') or []
    chapter_texts = project.get('chapter_texts', {})
    mappings = project.get('mappings', [])
    result = scan(requirements, chapter_texts, mappings, framework.get('outline') or [], versions)
    legacy_token = _legacy_input_token(requirements, chapter_texts, mappings)
    saved = _saved_acks(project.get('redline_review') or {}, result, legacy_token)
    ai, _ = _fresh_ai(project, result['input_token'])
    automatic = auto_pass_ids(result, ai)
    applied = apply_ack(result, {**saved, **automatic})
    applied['review_revision'] = _digest([result['input_token'], saved, automatic])
    for item in applied['items']:
        ident = item['id']
        item['manually_acknowledged'] = saved.get(ident) == item['basis']
        item['auto_acknowledged'] = ident in automatic
    return applied


def describe(store, key):
    project, framework = _context(store, key)
    payload = evaluate(project, framework, material_versions(store))
    ai, reviewed_at = _fresh_ai(project, payload['input_token'])
    if ai:
        payload['ai_review'] = ai
        payload['ai_reviewed_at'] = reviewed_at
    elif isinstance(project.get('redline_ai'), dict) and project['redline_ai'].get('input_token'):
        payload['ai_review_stale'] = True
    payload['mapping_revision'] = store.mapping_revision(project)
    payload['chapter_revision'] = project.get('chapter_revision', 0)
    return payload


def acknowledge(store, key, body):
    """保存人工确认。每条确认须带上页面看到的 basis；依据已变化的条目拒绝并提示重新核对。"""
    project, framework = _context(store, key)
    current = evaluate(project, framework, material_versions(store))
    from errors import ConflictError
    revision = body.get('review_revision')
    if revision is not None and revision != current['review_revision']:
        raise ConflictError('红线核对已更新，请重新读取后再保存。')
    by_id = {item['id']: item for item in current['items']}
    submitted = body.get('acknowledged')
    if submitted is None and isinstance(body.get('acknowledged_ids'), list):
        # 旧版页面：只有整体 input_token，要求与当前扫描完全一致。
        if body.get('input_token') != current['input_token']:
            raise ValueError('红线扫描依据已变化，请点击“重新扫描”后再确认。')
        ids = body['acknowledged_ids']
        if any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('废标红线确认存在重复或无效条目')
        submitted = {i: by_id[i]['basis'] for i in ids if i in by_id}
        if len(submitted) != len(ids):
            raise ValueError('请选择当前扫描结果中的废标红线条目。')
    if not isinstance(submitted, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in submitted.items()):
        raise ValueError('废标红线确认格式不正确，请刷新页面后重试')
    unknown = [k for k in submitted if k not in by_id]
    if unknown:
        raise ValueError('请选择当前扫描结果中的废标红线条目。')
    changed = [by_id[k]['label'] for k, v in submitted.items() if by_id[k]['basis'] != v]
    if changed:
        raise ValueError('以下红线的正文或材料已变化，请重新核对后再勾选：' + '；'.join(changed[:5]))
    if revision is None:
        # Legacy callers can add current confirmations, but cannot overwrite or clear another user's work.
        submitted = {**{row['id']: row['basis'] for row in current['items'] if row['manually_acknowledged']},
                     **submitted}
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    project = store.require('project', key)
    project['redline_review'] = {
        'acknowledged': {k: v for k, v in submitted.items()},
        'updated_at': stamp,
    }
    project['updated_at'] = stamp
    store.put('project', project)
    store.db.commit()
    return describe(store, key)


def require_complete(store, project, enforce=True):
    """导出时生成废标红线摘要。

    默认 enforce=True：初稿与已核对版本均要求红线全部确认（含 AI 自动过关）且依据未变。
    enforce=False 仅用于只读摘要，不再作为导出门禁。
    """
    framework = store.get('framework', project.get('framework_id', ''))
    if not framework or framework.get('projects', {}).get('word') != project['id']:
        return None
    result = evaluate(project, framework, material_versions(store))
    ai, _ = _fresh_ai(project, result['input_token'])
    if enforce and not result['complete']:
        raise ValueError(f'尚有 {result["pending_count"]} 条废标红线未自动过关或确认后正文、材料已变化，'
                         '请补齐材料/正文并完成 AI 核对后再导出。')
    if enforce:
        risks = [item for item in result['items'] if item.get('status') == 'risk']
        if risks:
            raise ValueError(f'尚有 {len(risks)} 条废标红线存在材料缺漏或空章风险，请修复后再导出。')
        if result['items'] and not ai:
            chapters = [text for text in (project.get('chapter_texts') or {}).values()
                        if str(text or '').strip()]
            if chapters:
                raise ValueError('请先完成废标红线 AI 逐条核对后再导出。')
        if ai:
            blocked = [item for item in result['items']
                       if not item.get('acknowledged')
                       and (ai.get(item['id']) or {}).get('verdict') in {'risk', 'unclear'}]
            if blocked:
                raise ValueError(f'尚有 {len(blocked)} 条废标红线 AI 判定为有风险或需确认，请修订正文后再导出。')
    review = project.get('redline_review') or {}
    return {
        'items': [{key: item.get(key) for key in
                   ('id', 'label', 'status', 'findings', 'basis', 'acknowledged', 'ack_expired')}
                  for item in result['items']],
        'acknowledged_ids': result['acknowledged_ids'],
        'pending_count': result['pending_count'],
        'complete': result['complete'],
        'input_token': result['input_token'],
        'updated_at': review.get('updated_at') if isinstance(review, dict) else None,
    }
