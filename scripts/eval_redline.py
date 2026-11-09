"""废标红线识别评测：对照人工金标准计算精确率（准确率）与召回率。

只读评测。系统结果取自上传后第一步的同一入口 tender_metrics.export_review（本地规则、不调用模型），
即人工修订前看到的废标红线列表；不读写 data/，报告默认写入评测集目录下的 _报告/。

  python3 scripts/eval_redline.py init  招标文件.docx 红线标注.xlsx
  python3 scripts/eval_redline.py run   评测集目录 [--split test] [--out 报告目录]
  python3 scripts/eval_redline.py agree 评测集目录 --a 红线标注_A --b 红线标注_B

评测集目录下每份招标文件一个子目录：1 个 .docx（或 .doc）与「红线标注.xlsx」（或 .csv）。
可选 manifest.csv，列为：目录、集合（dev/test）、文件、类型、备注。口径与流程见 REDLINE_EVAL.md。
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/bid-response-framework/scripts'
for _path in (SCRIPTS, ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

GOLD_STEM = '红线标注'
COLUMNS = ('编号', '原文条款', '状态', '后果', '确定性', '包件', '位置', '备注')
STATUSES = ('有效', '不适用', '存疑')
CONSEQUENCES = ('否决/无效', '不予受理/拒收', '取消资格', '视为放弃/撤回', '其他')
CERTAINTY = ('一律', '可能')
CATEGORY_NAMES = {'redline': '废标红线条款', 'qualification': '强制资格要求', 'score': '评分细则要点',
                  'spec': '商务技术规范', 'format': '指定格式'}
THRESHOLD = 0.8
MAX_WORD = 50 * 1024 * 1024
PREFIX = re.compile(r'^(?:第[零一二三四五六七八九十百\d]+条|[（(][零一二三四五六七八九十百\d]+[)）]|'
                    r'\d+(?:\.\d+)+[、.．]?|[一二三四五六七八九十百\d]+[、.．](?!\d))')
DETAIL_COLUMNS = ('文件', '判定', '原因', '金标编号', '金标状态', '金标条款', '金标包件', '系统ID',
                  '系统条款', '系统标签', '系统包件', '系统位置', '覆盖率', '仲裁结论', '仲裁备注')
VERDICTS = {'TP': '命中', 'FN': '漏报', 'FP': '误报', 'FP_NA': '误报', 'FRAG': '误报', 'DUP': '重复', 'IGN': '忽略'}
GUIDE = (
    '每行一条废标红线：原文写明会或可能导致否决投标、投标/响应无效、不予受理/拒收、取消（投标/中标/成交）资格、视为放弃/撤回的一个触发条件。',
    '「原文条款」逐字复制触发条件所在的完整句子或列表项，不含编号；列表项不拼接引导语（如「有下列情形之一的，投标无效：」）。',
    '一句里用「或」「、」并列的多个条件算 1 条；分别编号的子项各算 1 条。',
    '同一条文在多处出现只标 1 条，可在「位置」注明多处；分别适用于不同包件的各标 1 条并填写「包件」。',
    '★条款：原文声明★为实质性要求且不满足即无效/否决时，每个★条款各算 1 条，复制★条款本身。',
    '审查表：表头或表前说明写明任一项不符合即无效/否决时，表内每个审查项各算 1 条，复制该行要求。',
    '不标：仅有标题或引导语、程序性说明（如评委会对否决情况作出说明）、否定或豁免表述（如不作为否决依据）、'
    '兜底条款（如法律法规规定的其他情形）、评分扣分、合同履约违约、项目整体废标（如有效供应商不足三家）。',
    '状态：「有效」计入评测；「不适用」指原文有、但本项目设置已排除（如写明不收取保证金时的保证金否决情形），系统列出即算误报；'
    '「存疑」指无法确定是否属于红线，不计入任何指标。',
    '以 Word 原件为准通读全文（含表格、格式附件、补遗）；「原文段落」页只便于查找复制。原件有、但该页找不到的条款照常标注，并在备注写「原文段落中未找到」。',
    '标注时不要参考系统识别结果；两人背靠背标注，分歧交第三人仲裁。',
)


def norm(text):
    """Comparison key: width-folded, no whitespace, numbering or punctuation."""
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text or '')))
    text = PREFIX.sub('', text, count=1)
    return re.sub(r'[\W_]+', '', text).lower()


def coverage(gold, system):
    """Share of the gold clause found, in order, in the system text (matching runs of 2+ characters)."""
    if not gold or not system:
        return 0.0
    if gold in system:
        return 1.0
    blocks = SequenceMatcher(None, gold, system, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks if block.size >= 2) / len(gold)


def bounded_coverage(gold, gold_chars, system, system_chars, threshold):
    """Skip the alignment when even the shared character multiset cannot reach the threshold."""
    if sum((gold_chars & system_chars).values()) < threshold * len(gold):
        return 0.0
    return coverage(gold, system)


@dataclass
class Gold:
    ident: str
    text: str
    status: str = '有效'
    consequence: str = ''
    certainty: str = ''
    scope: str = ''
    location: str = ''
    note: str = ''

    def __post_init__(self):
        self.key = norm(self.text)
        self.chars = Counter(self.key)


@dataclass
class Prediction:
    ident: str
    text: str
    category: str = 'redline'
    label: str = ''
    scope: str = ''
    location: str = ''

    def __post_init__(self):
        self.key = norm(self.text)
        self.chars = Counter(self.key)


def read_gold(path):
    """Read one annotation sheet. Identical clauses in the same scope count once, like the system."""
    path = Path(path)
    if path.suffix.lower() == '.xlsx':
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = book[GOLD_STEM] if GOLD_STEM in book.sheetnames else book.worksheets[0]
            rows = [['' if value is None else str(value) for value in row]
                    for row in sheet.iter_rows(values_only=True)]
        finally:
            book.close()
    else:
        with path.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream))
    if not rows:
        raise ValueError(f'{path.name} 为空')
    header = [cell.strip() for cell in rows[0]]
    if '原文条款' not in header:
        raise ValueError(f'{path.name} 缺少「原文条款」列')
    position = {name: header.index(name) for name in COLUMNS if name in header}
    golds, seen, merged = [], set(), 0
    for number, row in enumerate(rows[1:], 2):
        def value(name):
            index = position.get(name)
            return row[index].strip() if index is not None and index < len(row) else ''
        text = value('原文条款')
        if not text:
            continue
        status = value('状态') or '有效'
        if status not in STATUSES:
            raise ValueError(f'{path.name} 第{number}行状态「{status}」无效，应为：' + '、'.join(STATUSES))
        item = Gold(value('编号') or f'第{number}行', text, status, value('后果'), value('确定性'),
                    value('包件'), value('位置'), value('备注'))
        if not item.key:
            raise ValueError(f'{path.name} 第{number}行「原文条款」没有可比对的文字')
        identity = (item.key, norm(item.scope), item.status)
        if identity in seen:
            merged += 1
            continue
        seen.add(identity)
        golds.append(item)
    return golds, merged


def readable_source(path, workdir):
    """Use the upload normalization for .doc; .docx is read as-is."""
    path = Path(path)
    if path.suffix.lower() == '.docx':
        return path
    from word_import import normalize_word
    raw, _ = normalize_word(path.name, path.read_bytes(), MAX_WORD)
    target = Path(workdir) / (path.stem + '.docx')
    target.write_bytes(raw)
    return target


def extract(source):
    """Rule results of every category as first shown after upload, plus the readable source text.

    Redlines come from the upload's own export_review grouping. Fixed-form items are a separate
    category derived from the outline planner; they are skipped so a planner problem in one tender
    cannot mask its redline result (the redline group is identical either way).
    """
    from unittest.mock import patch
    import tender_metrics
    with patch.object(tender_metrics, 'format_items', lambda *args, **kwargs: iter(())):
        data = tender_metrics.export_review(source)
    predictions = []
    for row in data['requirements']:
        refs = row.get('references') or []
        location = '；'.join(dict.fromkeys(f"{ref.get('part', '')} {ref.get('locator', '')}".strip()
                                          for ref in refs[:3]))
        predictions.append(Prediction(row['id'], row['text'], row['category'], row.get('label', ''),
                                      row.get('scope', ''), location))
    texts = []
    for source_row in data['sources']:
        for value in dict.fromkeys((source_row.get('text', ''), source_row.get('original_text', ''))):
            if value:
                texts.append(value)
    # A clause split across two neighbouring paragraphs is still readable text.
    units = texts + [a + b for a, b in zip(texts, texts[1:])]
    return predictions, units


def evaluate(golds, predictions, *, others=(), units=(), threshold=THRESHOLD):
    """One-to-one matching of valid gold clauses to system redlines, with error attribution."""
    valid = [g for g in golds if g.status == '有效']
    excluded = {status: [g for g in golds if g.status == status] for status in ('不适用', '存疑')}
    matrix = [[bounded_coverage(g.key, g.chars, p.key, p.chars, threshold) for p in predictions] for g in valid]
    # Prefer the tightest item, so one that merges several clauses is what remains as a duplicate.
    candidates = [sorted((pi for pi, value in enumerate(row) if value >= threshold),
                         key=lambda pi: (-row[pi], len(predictions[pi].key))) for row in matrix]
    owner = {}

    def augment(gi, visited):
        for pi in candidates[gi]:
            if pi in visited:
                continue
            visited.add(pi)
            if pi not in owner or augment(owner[pi], visited):
                owner[pi] = gi
                return True
        return False

    for gi in sorted(range(len(valid)), key=lambda gi: len(candidates[gi])):
        augment(gi, set())
    matched = {gi: pi for pi, gi in owner.items()}
    counts = Counter(gold=len(valid), predicted=len(predictions))
    details = []

    def record(kind, reason, gold=None, prediction=None, value=None):
        counts[kind] += 1
        details.append({'kind': kind, 'reason': reason, 'gold': gold, 'prediction': prediction,
                        'coverage': value})

    unit_cache = [(key, Counter(key)) for key in map(norm, units) if key]
    for gi, gold in enumerate(valid):
        if gi in matched:
            pi = matched[gi]
            record('TP', '', gold, predictions[pi], matrix[gi][pi])
            counts['covered'] += 1
            continue
        if candidates[gi]:
            counts['covered'] += 1
            pi = candidates[gi][0]
            record('FN', '与其他条款合并为一条', gold, predictions[pi], matrix[gi][pi])
            continue
        best = max(((bounded_coverage(gold.key, gold.chars, o.key, o.chars, threshold), o) for o in others),
                   key=lambda pair: pair[0], default=(0.0, None))
        if best[0] >= threshold:
            record('FN', '归入' + CATEGORY_NAMES.get(best[1].category, best[1].category), gold, best[1], best[0])
        elif any(bounded_coverage(gold.key, gold.chars, key, chars, threshold) >= threshold
                 for key, chars in unit_cache):
            record('FN', '未识别', gold)
        else:
            record('FN', '未读取（解析文本中找不到；或标注文字与原文不一致）', gold)

    def best_of(rows, prediction, inside=False):
        """Best gold covered by the prediction, or (inside) best gold containing the prediction."""
        scored = [(coverage(prediction.key, g.key) if inside else
                   bounded_coverage(g.key, g.chars, prediction.key, prediction.chars, threshold), g) for g in rows]
        return max(scored, key=lambda pair: pair[0], default=(0.0, None))

    # A maximum matching leaves no unmatched prediction covering an unmatched valid clause,
    # so a leftover prediction that covers a valid clause duplicates an existing hit.
    checks = (('DUP', '与已命中条款重复', valid, False), ('FRAG', '只截取了条款片段', valid, True),
              ('FP_NA', '本项目不适用，未被过滤', excluded['不适用'], False),
              ('IGN', '对应存疑条款，不计入', excluded['存疑'], False))
    for pi, prediction in enumerate(predictions):
        if pi in owner:
            continue
        for kind, reason, rows, inside in checks:
            value, gold = best_of(rows, prediction, inside)
            if value >= threshold:
                record(kind, reason, gold, prediction, value)
                break
        else:
            record('FP', '误报（仲裁时归因）', None, prediction)
    return counts, details


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def f_score(precision, recall, beta=1.0):
    if precision is None or recall is None or precision + recall == 0:
        return None
    weight = beta * beta
    return (1 + weight) * precision * recall / (weight * precision + recall)


def metrics(counts):
    evaluated = counts['predicted'] - counts['IGN']
    precision = ratio(counts['TP'], evaluated)
    recall = ratio(counts['TP'], counts['gold'])
    return {
        '金标条数': counts['gold'], '系统条数': evaluated, '命中': counts['TP'], '漏报': counts['FN'],
        '误报': counts['FP'] + counts['FP_NA'] + counts['FRAG'], '重复': counts['DUP'], '忽略': counts['IGN'],
        '精确率': precision, '召回率': recall, 'F1': f_score(precision, recall), 'F2': f_score(precision, recall, 2),
        '宽松精确率': ratio(counts['TP'] + counts['DUP'], evaluated),
        '宽松召回率': ratio(counts['covered'], counts['gold']),
    }


def bootstrap(documents, rounds=2000, seed=0):
    """Percentile intervals by resampling whole documents; clauses in one tender are not independent."""
    if len(documents) < 5:
        return None
    rng = random.Random(seed)
    precision, recall = [], []
    for _ in range(rounds):
        total = Counter()
        for _ in documents:
            total.update(rng.choice(documents)['counts'])
        value = metrics(total)
        if value['精确率'] is not None:
            precision.append(value['精确率'])
        if value['召回率'] is not None:
            recall.append(value['召回率'])

    def interval(values):
        if not values:
            return None
        values.sort()
        return [values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]]
    return {'精确率': interval(precision), '召回率': interval(recall)}


def breakdown(documents, field):
    groups = {}
    for document in documents:
        for row in document['details']:
            gold = row['gold']
            if row['kind'] not in ('TP', 'FN') or not getattr(gold, field):
                continue
            group = groups.setdefault(getattr(gold, field), Counter())
            group['total'] += 1
            group['hit'] += row['kind'] == 'TP'
    return {name: {'条数': value['total'], '召回率': ratio(value['hit'], value['total'])}
            for name, value in sorted(groups.items())}


def discover(corpus, gold_stem=GOLD_STEM):
    corpus = Path(corpus)
    manifest = {}
    if (corpus / 'manifest.csv').is_file():
        with (corpus / 'manifest.csv').open(encoding='utf-8-sig', newline='') as stream:
            manifest = {row.get('目录', '').strip(): row for row in csv.DictReader(stream)}
    documents, skipped = [], []
    for folder in sorted(p for p in corpus.iterdir() if p.is_dir() and not p.name.startswith(('_', '.'))):
        gold = annotation_file(folder, gold_stem)
        if gold is None:
            skipped.append(folder.name)
            continue
        meta = manifest.get(folder.name, {})
        if (meta.get('文件') or '').strip():
            source = folder / meta['文件'].strip()
            if not source.is_file():
                raise ValueError(f'{folder.name}：找不到 manifest.csv 指定的文件「{source.name}」')
        else:
            words = [p for p in folder.iterdir()
                     if p.suffix.lower() in ('.docx', '.doc') and not p.name.startswith('~$')]
            if len(words) != 1:
                raise ValueError(f'{folder.name}：须恰有 1 个 Word 招标文件，或在 manifest.csv 的「文件」列指定')
            source = words[0]
        documents.append({'name': folder.name, 'source': source, 'gold': gold,
                          'split': (meta.get('集合') or '').strip(), 'type': (meta.get('类型') or '').strip()})
    return documents, skipped


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rules_digest():
    """Same fingerprint the upload uses to bind a framework to the bundled rules."""
    value = hashlib.sha256()
    for script in sorted(SCRIPTS.glob('*.py')):
        value.update(script.name.encode() + b'\0' + hashlib.sha256(script.read_bytes()).digest())
    return value.hexdigest()


def git_commit():
    try:
        result = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'],
                                capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(['git', '-C', str(ROOT), 'status', '--porcelain'],
                               capture_output=True, text=True, timeout=10)
        return result.stdout.strip() + ('（有未提交修改）' if dirty.stdout.strip() else '')
    except (OSError, subprocess.SubprocessError):
        return ''


def detail_rows(document):
    for row in document['details']:
        gold, prediction = row['gold'], row['prediction']
        yield {
            '文件': document['name'], '判定': VERDICTS[row['kind']], '原因': row['reason'],
            '金标编号': gold.ident if gold else '', '金标状态': gold.status if gold else '',
            '金标条款': gold.text if gold else '', '金标包件': gold.scope if gold else '',
            '系统ID': prediction.ident if prediction else '', '系统条款': prediction.text if prediction else '',
            '系统标签': prediction.label if prediction else '', '系统包件': prediction.scope if prediction else '',
            '系统位置': prediction.location if prediction else '',
            '覆盖率': f"{row['coverage']:.2f}" if row['coverage'] is not None else '',
            '仲裁结论': '', '仲裁备注': '',
        }


def percent(value):
    return '—' if value is None else f'{value:.1%}'


def write_details(path, documents):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, DETAIL_COLUMNS)
        writer.writeheader()
        for document in documents:
            writer.writerows(detail_rows(document))


def evaluate_corpus(corpus, *, split=None, gold_stem=GOLD_STEM, threshold=THRESHOLD):
    found, skipped = discover(corpus, gold_stem)
    documents = [d for d in found if not split or d['split'] == split]
    if not documents:
        raise ValueError('没有可评测的文件：请检查目录、标注文件名与 --split 参数')
    for document in documents:
        golds, merged = read_gold(document['gold'])
        document.update(merged=merged, error='', gold_sha256=digest(document['gold']),
                        source_sha256=digest(document['source']))
        try:
            with tempfile.TemporaryDirectory(prefix='redline-eval-') as work:
                predictions, units = extract(readable_source(document['source'], work))
        except Exception as error:  # Counted as an empty system result: every valid clause is missed.
            predictions, units = [], []
            document['error'] = f'{type(error).__name__}: {error}'
        redlines = [p for p in predictions if p.category == 'redline']
        others = [p for p in predictions if p.category != 'redline']
        counts, details = evaluate(golds, redlines, others=others, units=units, threshold=threshold)
        document.update(counts=counts, details=details, metrics=metrics(counts))
    total = Counter()
    for document in documents:
        total.update(document['counts'])

    def mean(name):
        values = [d['metrics'][name] for d in documents if d['metrics'][name] is not None]
        return sum(values) / len(values) if values else None
    reasons = Counter((VERDICTS[row['kind']], row['reason']) for d in documents for row in d['details']
                      if row['kind'] not in ('TP', 'IGN'))
    return {
        'generated_at': datetime.now().isoformat(timespec='seconds'), 'threshold': threshold,
        'git_commit': git_commit(), 'rules_sha256': rules_digest(), 'split': split or '全部',
        'skipped': skipped, 'documents': documents, 'micro': metrics(total),
        'macro': {'精确率': mean('精确率'), '召回率': mean('召回率')},
        'interval': bootstrap(documents),
        'reasons': [{'判定': kind, '原因': reason, '条数': count} for (kind, reason), count in reasons.most_common()],
        'breakdown': {'后果': breakdown(documents, 'consequence'), '确定性': breakdown(documents, 'certainty')},
    }


def summary_json(result):
    documents = [{'文件': d['name'], '集合': d['split'], '类型': d['type'], '招标文件': d['source'].name,
                  '招标文件SHA256': d['source_sha256'], '标注SHA256': d['gold_sha256'],
                  '金标合并重复': d['merged'], '提取失败': d['error'], **d['metrics']}
                 for d in result['documents']]
    keep = ('generated_at', 'threshold', 'git_commit', 'rules_sha256', 'split', 'skipped', 'micro', 'macro',
            'interval', 'reasons', 'breakdown')
    return {**{key: result[key] for key in keep}, 'documents': documents}


def report_markdown(result):
    micro, macro, interval = result['micro'], result['macro'], result['interval']
    lines = ['# 废标红线识别评测报告', '',
             f"- 生成时间：{result['generated_at']}　集合：{result['split']}　匹配阈值：{result['threshold']}",
             f"- 代码版本：{result['git_commit'] or '未知'}　规则指纹：{result['rules_sha256'][:16]}", '',
             '## 总体', '', '| 口径 | 精确率 | 召回率 | F1 | F2 |', '| --- | --- | --- | --- | --- |',
             f"| 严格·微平均（主指标） | {percent(micro['精确率'])} | {percent(micro['召回率'])} | "
             f"{percent(micro['F1'])} | {percent(micro['F2'])} |",
             f"| 宽松·微平均 | {percent(micro['宽松精确率'])} | {percent(micro['宽松召回率'])} | — | — |",
             f"| 严格·宏平均（按文件） | {percent(macro['精确率'])} | {percent(macro['召回率'])} | — | — |", '',
             f"金标 {micro['金标条数']} 条，系统 {micro['系统条数']} 条（另有存疑忽略 {micro['忽略']} 条）："
             f"命中 {micro['命中']}，漏报 {micro['漏报']}，误报 {micro['误报']}，重复 {micro['重复']}。", '']
    if interval:
        lines += [f"95% 置信区间（按文件重抽样）：精确率 {percent(interval['精确率'][0])}–{percent(interval['精确率'][1])}，"
                  f"召回率 {percent(interval['召回率'][0])}–{percent(interval['召回率'][1])}。", '']
    else:
        lines += ['评测文件少于 5 份，不计算置信区间。', '']
    lines += ['## 分文件', '', '| 文件 | 集合 | 金标 | 系统 | 命中 | 漏报 | 误报 | 重复 | 精确率 | 召回率 | 备注 |',
              '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for document in result['documents']:
        m = document['metrics']
        note = '；'.join(filter(None, [f"提取失败：{document['error']}" if document['error'] else '',
                                       f"金标重复合并 {document['merged']} 条" if document['merged'] else '']))
        lines.append(f"| {document['name']} | {document['split'] or '—'} | {m['金标条数']} | {m['系统条数']} | "
                     f"{m['命中']} | {m['漏报']} | {m['误报']} | {m['重复']} | {percent(m['精确率'])} | "
                     f"{percent(m['召回率'])} | {note} |")
    lines += ['', '## 漏报与误报原因', '', '| 判定 | 原因 | 条数 |', '| --- | --- | --- |']
    lines += [f"| {row['判定']} | {row['原因']} | {row['条数']} |" for row in result['reasons']] or ['| — | — | 0 |']
    for field, rows in result['breakdown'].items():
        if rows:
            lines += ['', f'## 按{field}的召回率', '', f'| {field} | 条数 | 召回率 |', '| --- | --- | --- |']
            lines += [f"| {name} | {row['条数']} | {percent(row['召回率'])} |" for name, row in rows.items()]
    if result['skipped']:
        lines += ['', '未找到标注文件、未评测的目录：' + '、'.join(result['skipped'])]
    lines += ['', '逐条明细见 details.csv：仲裁人填写「仲裁结论」（系统错／金标错／存疑）。金标错的改正标注后重跑；'
              '误报在「仲裁备注」写明类型（程序性说明、否定表述、标题引导语、兜底条款、合同履约等）。', '']
    return '\n'.join(lines)


def command_run(args):
    result = evaluate_corpus(args.corpus, split=args.split, gold_stem=args.gold, threshold=args.threshold)
    out = Path(args.out) if args.out else Path(args.corpus) / '_报告' / datetime.now().strftime('%Y%m%d-%H%M%S')
    out.mkdir(parents=True, exist_ok=False)
    (out / 'summary.json').write_text(json.dumps(summary_json(result), ensure_ascii=False, indent=2), encoding='utf-8')
    write_details(out / 'details.csv', result['documents'])
    (out / 'report.md').write_text(report_markdown(result), encoding='utf-8')
    micro = result['micro']
    for document in result['documents']:
        m = document['metrics']
        print(f"{document['name']}：精确率 {percent(m['精确率'])}，召回率 {percent(m['召回率'])}"
              + (f"（提取失败：{document['error']}）" if document['error'] else ''))
    print(f"合计 {len(result['documents'])} 份：精确率 {percent(micro['精确率'])}，召回率 {percent(micro['召回率'])}，"
          f"F1 {percent(micro['F1'])}；报告：{out}")
    return result, out


def annotation_file(folder, stem):
    return next((folder / (stem + ext) for ext in ('.xlsx', '.csv') if (folder / (stem + ext)).is_file()), None)


def command_agree(args):
    """Inter-annotator agreement with the same matching: B's valid clauses are scored against A."""
    pairs = [(folder, annotation_file(folder, args.a), annotation_file(folder, args.b))
             for folder in sorted(p for p in Path(args.corpus).iterdir()
                                  if p.is_dir() and not p.name.startswith(('_', '.')))]
    pairs = [pair for pair in pairs if pair[1] and pair[2]]
    if not pairs:
        raise ValueError(f'没有同时包含「{args.a}」与「{args.b}」的目录')
    total, documents = Counter(), []
    for folder, first, second in pairs:
        golds, _ = read_gold(first)
        predictions = [Prediction(g.ident, g.text, scope=g.scope) for g in read_gold(second)[0] if g.status == '有效']
        counts, details = evaluate(golds, predictions, threshold=args.threshold)
        total.update(counts)
        document = {'name': folder.name, 'counts': counts, 'details': details, 'metrics': metrics(counts)}
        documents.append(document)
        m = document['metrics']
        print(f"{folder.name}：一致性 F1 {percent(m['F1'])}（A {m['金标条数']} 条，B {m['系统条数']} 条，一致 {m['命中']} 条）")
    out = Path(args.corpus) / '_报告' / ('一致性-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True, exist_ok=False)
    write_details(out / 'details.csv', documents)
    print(f"合计一致性 F1 {percent(metrics(total)['F1'])}；分歧明细：{out / 'details.csv'}"
          '（「漏报」= A 有 B 无，「误报」= B 有 A 无）')
    return metrics(total), out


def command_init(args):
    """Annotation workbook. It deliberately contains no system result, to keep annotation blind."""
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.worksheet.datavalidation import DataValidation
    from metric_source import read_units
    output = Path(args.output)
    if output.exists():
        raise ValueError(f'{output} 已存在；为免覆盖已有标注，请换一个文件名')
    if output.suffix.lower() != '.xlsx':
        raise ValueError('标注表请使用 .xlsx')
    with tempfile.TemporaryDirectory(prefix='redline-init-') as work:
        units, _ = read_units(readable_source(args.source, work))
    clean = lambda value: ILLEGAL_CHARACTERS_RE.sub('', str(value or ''))[:32000]
    book = Workbook()
    sheet = book.active
    sheet.title = GOLD_STEM
    sheet.append(list(COLUMNS))
    for column, values in (('C', STATUSES), ('D', CONSEQUENCES), ('E', CERTAINTY)):
        validation = DataValidation(type='list', formula1='"' + ','.join(values) + '"', allow_blank=True)
        validation.add(f'{column}2:{column}2000')
        sheet.add_data_validation(validation)
    for column, width in zip('ABCDEFGH', (10, 70, 8, 14, 8, 12, 20, 30)):
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = 'A2'
    source = book.create_sheet('原文段落')
    source.append(['序号', '分件', '位置', '章节', '原文'])
    for number, unit in enumerate(units, 1):
        source.append([number, clean(unit.part), clean(unit.locator), clean(unit.context),
                       clean(unit.original_text or unit.text)])
    for column, width in zip('ABCDE', (7, 10, 14, 30, 90)):
        source.column_dimensions[column].width = width
    source.freeze_panes = 'A2'
    guide = book.create_sheet('标注说明')
    guide.column_dimensions['A'].width = 120
    for number, line in enumerate(GUIDE, 1):
        guide.append([f'{number}. {line}'])
    book.save(output)
    print(f'已生成标注表：{output}（原文段落 {len(units)} 行，不含系统识别结果）')
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description='废标红线识别评测（精确率／召回率）')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='为一份招标文件生成空白标注表')
    init.add_argument('source', type=Path, help='招标文件 .docx 或 .doc')
    init.add_argument('output', type=Path, help='标注表 .xlsx')
    run = commands.add_parser('run', help='对照金标准评测系统识别结果')
    run.add_argument('corpus', type=Path, help='评测集目录')
    run.add_argument('--split', help='只评测 manifest.csv 中该集合的文件，如 test')
    run.add_argument('--gold', default=GOLD_STEM, help='标注文件名（不含扩展名）')
    run.add_argument('--out', type=Path, help='报告目录，默认 评测集目录/_报告/时间戳')
    run.add_argument('--threshold', type=float, default=THRESHOLD, help='判定命中的最低覆盖率')
    agree = commands.add_parser('agree', help='两名标注人之间的一致性')
    agree.add_argument('corpus', type=Path)
    agree.add_argument('--a', required=True, help='标注人 A 的文件名（不含扩展名）')
    agree.add_argument('--b', required=True, help='标注人 B 的文件名（不含扩展名）')
    agree.add_argument('--threshold', type=float, default=THRESHOLD)
    args = parser.parse_args(argv)
    if not 0 < getattr(args, 'threshold', THRESHOLD) <= 1:
        parser.error('--threshold 须在 0 到 1 之间')
    try:
        return {'init': command_init, 'run': command_run, 'agree': command_agree}[args.command](args)
    except ValueError as error:
        parser.exit(2, f'错误：{error}\n')


if __name__ == '__main__':
    main()
