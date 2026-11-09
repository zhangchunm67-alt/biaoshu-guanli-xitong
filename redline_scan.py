"""Local redline checklist after chapter drafting. No model calls."""
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
    (re.compile(r'纳税|税收|完税'), 'tax_social', '纳税证明'),
    (re.compile(r'社保|社会保险|社会保障'), 'tax_social', '社保凭证'),
    (re.compile(r'ISO|质量管理体系|体系认证'), 'iso_cert', 'ISO相关资质'),
)


def input_token(requirements, chapter_texts, mappings):
    payload = {
        'redlines': [(row['id'], row.get('label', ''), row.get('text', ''))
                     for row in requirements if row.get('category') == 'redline'],
        'chapters': sorted((key, value) for key, value in (chapter_texts or {}).items()),
        'mappings': sorted(
            (row.get('id'), row.get('action'), row.get('status'),
             tuple(row.get('material_ids') or []))
            for row in (mappings or [])),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def scan(requirements, chapter_texts, mappings, outline=None):
    redlines = [row for row in requirements if row.get('category') == 'redline']
    texts = chapter_texts or {}
    items = []
    for req in redlines:
        body = f"{req.get('label') or ''}\n{req.get('text') or ''}"
        findings = []
        for pattern, category_id, title in MATERIAL_PATTERNS:
            if not pattern.search(body):
                continue
            rows = [row for row in (mappings or []) if row.get('category_id') == category_id]
            if not rows:
                findings.append(f'红线涉及「{title}」，当前材料核对未出现对应栏目，请回目录或材料页核对。')
                continue
            for row in rows:
                if row.get('status') == 'blocked':
                    findings.append(f'「{title}」材料仍有阻断问题，可能触犯红线。')
                elif row.get('action') in {'missing', 'skip'} or row.get('status') in {'missing', 'skipped'}:
                    findings.append(f'「{title}」仍为待补或跳过，递交前须确认是否触犯红线。')
        for row in outline or []:
            if req['id'] not in (row.get('requirement_ids') or []):
                continue
            if row.get('locked_format'):
                continue
            if not str(texts.get(row.get('id'), '')).strip():
                findings.append(
                    f'关联章节「{row.get("text") or row.get("id")}」尚无正文，请核对红线是否需在该章响应。')
        items.append({
            'id': req['id'],
            'label': (req.get('label') or req.get('text') or '')[:120],
            'text': req.get('text') or '',
            'status': 'risk' if findings else 'attention',
            'findings': findings or ['系统未自动发现材料缺漏；请对照原文确认正文与材料均未触犯该红线。'],
        })
    return {
        'input_token': input_token(requirements, chapter_texts, mappings),
        'items': items,
        'scanned_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }


def apply_ack(result, acknowledged_ids):
    acknowledged = set(acknowledged_ids or [])
    items = []
    for item in result['items']:
        row = dict(item)
        row['acknowledged'] = item['id'] in acknowledged
        items.append(row)
    pending = [item for item in items if not item['acknowledged']]
    return {
        **result,
        'items': items,
        'acknowledged_ids': [item['id'] for item in items if item['acknowledged']],
        'pending_count': len(pending),
        'complete': not pending,
    }


def describe(store, key):
    project = store.require('project', key)
    framework = store.get('framework', project.get('framework_id', ''))
    if not framework or framework.get('projects', {}).get('word') != key:
        raise ValueError('请从已确认目录的当前整本项目进入废标红线扫描。')
    if framework.get('review_version') and not framework.get('requirements_confirmed'):
        raise ValueError('请先核对确认采购要求，再扫描废标红线。')
    requirements = framework.get('requirements') or []
    result = scan(requirements, project.get('chapter_texts', {}), project.get('mappings', []),
                  framework.get('outline') or [])
    review = project.get('redline_review') or {}
    acknowledged = (review.get('acknowledged_ids') or []
                    if review.get('input_token') == result['input_token'] else [])
    payload = apply_ack(result, acknowledged)
    payload['mapping_revision'] = store.mapping_revision(project)
    payload['chapter_revision'] = project.get('chapter_revision', 0)
    return payload


def acknowledge(store, key, body):
    project = store.require('project', key)
    current = describe(store, key)
    if type(body.get('chapter_revision')) is not int or body['chapter_revision'] != current['chapter_revision']:
        raise ValueError('正文已更新，请重新扫描废标红线后再确认。')
    if body.get('input_token') != current['input_token']:
        raise ValueError('红线扫描依据已变化，请重新读取后再确认。')
    ids = body.get('acknowledged_ids')
    allowed = {item['id'] for item in current['items']}
    if not isinstance(ids, list) or any(not isinstance(item, str) or item not in allowed for item in ids):
        raise ValueError('请选择当前扫描结果中的废标红线条目。')
    if len(ids) != len(set(ids)):
        raise ValueError('废标红线确认存在重复项')
    project = store.require('project', key)
    project['redline_review'] = {
        'input_token': current['input_token'],
        'acknowledged_ids': list(dict.fromkeys(ids)),
        'chapter_revision': current['chapter_revision'],
        'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }
    project['updated_at'] = project['redline_review']['updated_at']
    store.put('project', project)
    store.db.commit()
    return describe(store, key)
