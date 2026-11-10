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


def input_token(requirements, chapter_texts, mappings):
    """整体输入指纹，仅用于显示扫描版本；确认是否失效按每条红线的 basis 判断。"""
    payload = {
        'redlines': [(row['id'], row.get('label', ''), row.get('text', ''))
                     for row in requirements if row.get('category') == 'redline'],
        'chapters': sorted((key, value) for key, value in (chapter_texts or {}).items()),
        'mappings': sorted(_mapping_key(row) for row in (mappings or [])),
    }
    return _digest(payload)


def _material_body(body):
    return CONTEXT_EXCLUSIONS.sub('', body)


def scan(requirements, chapter_texts, mappings, outline=None):
    redlines = [row for row in requirements if row.get('category') == 'redline']
    texts = chapter_texts or {}
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
        for row in outline or []:
            if req['id'] not in (row.get('requirement_ids') or []):
                continue
            linked.append(row.get('id'))
            if row.get('locked_format'):
                continue
            if not str(texts.get(row.get('id'), '')).strip():
                empty.append(row.get('id'))
                findings.append(
                    f'关联章节「{row.get("text") or row.get("id")}」尚无正文，请核对红线是否需在该章响应。')
        for pattern, hint in CHECK_HINTS:
            if pattern.search(body) and hint not in hints:
                hints.append(hint)
        findings = list(dict.fromkeys(findings))
        relevant = sorted(_mapping_key(row) for row in (mappings or []) if row.get('category_id') in categories)
        basis = _digest({
            'id': req['id'], 'label': req.get('label', ''), 'text': req.get('text', ''),
            'chapters': [(ident, texts.get(ident, '')) for ident in linked],
            'mappings': relevant, 'findings': findings,
        })
        material_ids = sorted({mid for key in relevant for mid in key[4]})
        items.append({
            'id': req['id'],
            'label': (req.get('label') or req.get('text') or '')[:120],
            'text': req.get('text') or '',
            'status': 'risk' if findings else 'attention',
            'findings': findings or ([] if hints else [GENERAL_HINT]),
            'hints': hints,
            'auto_checked': bool(categories or linked),
            'linked_chapter_ids': linked,
            'empty_chapter_ids': empty,
            'material_category_ids': list(dict.fromkeys(categories)),
            'material_ids': material_ids,
            'basis': basis,
        })
    return {
        'input_token': input_token(requirements, chapter_texts, mappings),
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


def _saved_acks(review):
    """兼容旧版只保存 input_token + acknowledged_ids 的确认记录（旧记录一律视为需重新确认）。"""
    acks = review.get('acknowledged') if isinstance(review, dict) else None
    return dict(acks) if isinstance(acks, dict) else {}


def _context(store, key):
    project = store.require('project', key)
    framework = store.get('framework', project.get('framework_id', ''))
    if not framework or framework.get('projects', {}).get('word') != key:
        raise ValueError('请从已确认目录的当前整本项目进入废标红线扫描。')
    if framework.get('review_version') and not framework.get('requirements_confirmed'):
        raise ValueError('请先核对确认采购要求，再扫描废标红线。')
    return project, framework


def evaluate(project, framework):
    requirements = framework.get('requirements') or []
    result = scan(requirements, project.get('chapter_texts', {}), project.get('mappings', []),
                  framework.get('outline') or [])
    return apply_ack(result, _saved_acks(project.get('redline_review') or {}))


def describe(store, key):
    project, framework = _context(store, key)
    payload = evaluate(project, framework)
    payload['mapping_revision'] = store.mapping_revision(project)
    payload['chapter_revision'] = project.get('chapter_revision', 0)
    return payload


def acknowledge(store, key, body):
    """保存人工确认。每条确认须带上页面看到的 basis；依据已变化的条目拒绝并提示重新核对。"""
    project, framework = _context(store, key)
    current = evaluate(project, framework)
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


def require_complete(store, project):
    """导出前服务端校验：框架项目的废标红线须全部确认，且确认后依据未变。"""
    framework = store.get('framework', project.get('framework_id', ''))
    if not framework or framework.get('projects', {}).get('word') != project['id']:
        return
    result = evaluate(project, framework)
    if not result['complete']:
        raise ValueError(f'尚有 {result["pending_count"]} 条废标红线未确认或确认后正文、材料已变化，'
                         '请在第四步“废标红线扫描”中核对并保存后再导出。')
