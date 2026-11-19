"""LLM item-by-item redline review against the saved bid chapters.

The local scan in redline_scan.py only checks material/empty-chapter signals.
This module asks the configured model to judge each extracted redline against
the adopted chapter text. Fresh satisfied+verified results (with no rule risk)
are auto-acknowledged by redline_scan.describe; the export gate requires a
complete acknowledgment set.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone

import redline_scan
import writing_model

MAX_REVIEW_CHARS = 400000
# 同一项目同时只运行一次模型核对；重复点击不会重复调用模型、产生额外费用。
_RUNNING = set()
_RUNNING_LOCK = threading.Lock()
VERDICTS = {'satisfied', 'risk', 'unclear'}

INSTRUCTIONS = '''你是投标文件废标红线核对员。输入 JSON 中，redlines 是招标文件解读阶段逐条提取的废标/否决条款，chapters 是已保存的投标正文（含章节路径），materials 是材料核对摘要，system_findings 是本地规则扫描提示。

逐条核对投标文件是否对每条红线作出有效响应。只依据输入资料判断，不得推测未写出的内容，不得把历史案例当作本项目承诺。

verdict 只能是：
- satisfied：投标正文明确响应了该红线要求，且未发现矛盾；
- risk：正文或材料存在遗漏、矛盾、缺失，可能触犯该红线；
- unclear：该要求依赖递交、签章、密封、份数、报价数字、原件核验等无法从正文判断的事项，或现有资料不足以判断。

reason：一句话说明判断依据，不超过 200 字。
evidence：从输入 chapters 中逐字摘录与这条红线最相关的一句或一小段原文，最多 200 字；不得改写、概括或编造。找不到直接对应内容时填空字符串。
chapter_ids：只允许引用输入 chapters 中实际作为依据的章节 id，没有则空数组。
suggestion：如需补充或核对，说明应在哪一章补什么，不超过 200 字。

每条红线必须恰好返回一次，id 原样返回，按输入顺序排列。只输出 JSON 对象，不要输出解释。'''

SCHEMA = {
    'type': 'object',
    'properties': {
        'items': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                    'verdict': {'type': 'string', 'enum': ['satisfied', 'risk', 'unclear']},
                    'reason': {'type': 'string'},
                    'evidence': {'type': 'string'},
                    'chapter_ids': {'type': 'array', 'items': {'type': 'string'}},
                    'suggestion': {'type': 'string'},
                },
                'required': ['id', 'verdict', 'reason', 'evidence', 'chapter_ids', 'suggestion'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['items'],
    'additionalProperties': False,
}


def _outline_paths(outline):
    paths, stack = {}, []
    for row in outline or []:
        level = int(row.get('level') or 1)
        stack = stack[:max(0, level - 1)] + [row.get('text') or row.get('id') or '']
        paths[row.get('id')] = ' / '.join(part for part in stack if part)
    return paths


def build_context(project, framework, scan_result):
    """Return the redline/chapter/material snapshot sent to the model."""
    items = {item['id']: item for item in scan_result.get('items') or []}
    redlines = []
    for requirement in framework.get('requirements') or []:
        if requirement.get('category') != 'redline':
            continue
        item = items.get(requirement.get('id')) or {}
        redlines.append({
            'id': requirement.get('id'),
            'text': ((requirement.get('label') or '') + '\n' + (requirement.get('text') or '')).strip()[:4000],
            'system_findings': [str(note)[:400] for note in (item.get('findings') or [])][:8],
            'system_hints': [str(note)[:400] for note in (item.get('hints') or [])][:8],
        })
    paths = _outline_paths(framework.get('outline') or [])
    texts = project.get('chapter_texts') or {}
    chapters = []
    for ident, text in texts.items():
        text = str(text or '').strip()
        if not text:
            continue
        chapters.append({'id': ident, 'path': paths.get(ident, ident), 'text': text})
    mapped = []
    for row in project.get('mappings') or []:
        mapped.append({
            'category': row.get('category_id'),
            'title': row.get('title') or row.get('category_name'),
            'status': row.get('status'),
            'action': row.get('action'),
            'note': str(row.get('note') or '')[:400],
        })
    return {'redlines': redlines, 'chapters': chapters, 'materials': mapped}


_PUNCTUATION = re.compile(r'[\s，。；：、,.!?！？；：“”"‘’\'（）()【】\[\]]+')


def _normalize(value):
    return _PUNCTUATION.sub('', value or '')


_VERDICT_RANK = {'satisfied': 0, 'unclear': 1, 'risk': 2}


def _clean_items(result, redline_ids, chapter_ids, chapter_texts=None):
    if not isinstance(result, dict) or not isinstance(result.get('items'), list):
        raise ValueError('AI 红线核对返回结构无效，请重试。')
    texts = {ident: _normalize(text) for ident, text in (chapter_texts or {}).items()
             if str(text or '').strip()}
    cleaned = {}
    for row in result['items']:
        if not isinstance(row, dict):
            continue
        ident = row.get('id')
        if ident not in redline_ids:
            continue
        verdict = row.get('verdict')
        if verdict not in VERDICTS:
            verdict = 'unclear'
        reason = (row.get('reason') if isinstance(row.get('reason'), str) else '').strip()[:400]
        evidence = (row.get('evidence') if isinstance(row.get('evidence'), str) else '').strip()[:400]
        suggestion = (row.get('suggestion') if isinstance(row.get('suggestion'), str) else '').strip()[:400]
        refs = row.get('chapter_ids') if isinstance(row.get('chapter_ids'), list) else []
        refs = [ref for ref in refs if isinstance(ref, str) and ref in chapter_ids]
        normalized = _normalize(evidence)
        # 优先在 AI 自报的章节里核对引用；未报章节时允许命中任一已保存正文。
        cited = [texts[ref] for ref in refs if ref in texts]
        evidence_verified = bool(normalized) and any(
            normalized in text for text in (cited or list(texts.values())))
        if verdict == 'satisfied' and not evidence:
            # “通过”必须有可回查的正文依据，避免模型无依据地放行。
            verdict = 'unclear'
            reason = 'AI 判定通过但未给出对应正文引用，需人工确认。'
        candidate = {'verdict': verdict, 'reason': reason, 'evidence': evidence,
                     'evidence_verified': evidence_verified,
                     'chapter_ids': list(dict.fromkeys(refs))[:8],
                     'suggestion': suggestion}
        # 模型重复返回同一红线时取最保守结论，避免后到的 risk/unclear 被静默丢弃。
        previous = cleaned.get(ident)
        if previous is None or _VERDICT_RANK[verdict] > _VERDICT_RANK[previous['verdict']]:
            cleaned[ident] = candidate
    for ident in redline_ids:
        if ident not in cleaned:
            cleaned[ident] = {'verdict': 'unclear', 'reason': 'AI 未返回该条核对结果，请人工判断。',
                              'evidence': '', 'evidence_verified': False,
                              'chapter_ids': [], 'suggestion': ''}
    return cleaned


def review(store, key):
    """Run AI redline review for the project and persist it against the input token.

    The model call runs without the store lock; only the snapshot and publish steps
    hold it, so a slow model cannot freeze other requests.
    """
    marker = (id(store), key)
    with _RUNNING_LOCK:
        if marker in _RUNNING:
            raise ValueError('本项目的 AI 红线核对正在进行，请稍候刷新查看结果，无需重复提交。')
        _RUNNING.add(marker)
    try:
        return _review(store, key)
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(marker)


def _review(store, key):
    with store.lock:
        project, framework = redline_scan._context(store, key)
        current = redline_scan.evaluate(project, framework, redline_scan.material_versions(store))
        context = build_context(project, framework, current)
        redline_ids = {row['id'] for row in context['redlines']}
        if not redline_ids:
            raise ValueError('本项目未识别到废标红线条款，无需 AI 核对。')
        if not context['chapters']:
            raise ValueError('请先生成并采纳投标正文，再用 AI 逐条核对红线。')
        size = len(str(context))
        if size > MAX_REVIEW_CHARS:
            raise ValueError(f'正文资料 {size} 字超过 AI 核对容量 {MAX_REVIEW_CHARS} 字；'
                             '可人工逐条核对，或精简正文后重试。')
        input_token = current['input_token']
        chapter_ids = {row['id'] for row in context['chapters']}
        chapter_texts = {row['id']: row['text'] for row in context['chapters']}
    result = writing_model.complete_json(INSTRUCTIONS, context, SCHEMA)
    cleaned = _clean_items(result, redline_ids, chapter_ids, chapter_texts)
    with store.lock:
        project, framework = redline_scan._context(store, key)
        latest = redline_scan.evaluate(project, framework, redline_scan.material_versions(store))
        if latest['input_token'] != input_token:
            raise ValueError('正文或材料已变化，请重新核对红线。')
        stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        project = store.require('project', key)
        project['redline_ai'] = {'version': 2, 'input_token': input_token, 'items': cleaned,
                                 'reviewed_at': stamp}
        project['updated_at'] = stamp
        store.put('project', project)
        store.db.commit()
    with store.lock:
        return redline_scan.describe(store, key)
