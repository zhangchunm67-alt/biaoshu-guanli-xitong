#!/usr/bin/env python3
"""Compare a frozen DOCX with a reference after generation. Never writes a document."""
import argparse
from collections import Counter
from pathlib import Path
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from zipfile import ZipFile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NUMBER = r"[一二三四五六七八九十百〇零两0-9]+"
PREFIX = re.compile(
    rf"^(?:第{NUMBER}(?:章|节|部分|篇)|(?:格式|附件|附表){NUMBER}|"
    rf"[（(]{NUMBER}[)）]|\d+(?:\.\d+)+|{NUMBER}[、.])"
    r"[\s.、：:．-]*"
)
SENSITIVE = re.compile(r"(?<![0-9])[0-9]{17}[0-9Xx](?![0-9])|(?<![0-9])1[3-9][0-9]{9}(?![0-9])")


def norm(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def heading_key(value):
    # Strip a numbering prefix, while retaining all subject words (no synonym guessing).
    value = norm(value)
    while True:
        stripped = PREFIX.sub("", value, count=1)
        if stripped == value:
            return value
        value = stripped


def txt(node):
    return "".join(part.text or "" for part in node.iter(W + "t"))


def val(node, path):
    found = node.find(path)
    return found.get(W + "val") if found is not None else None


def blocks(parent):
    for child in parent:
        if child.tag in {W + "p", W + "tbl"}:
            yield child
        elif child.tag in {W + "sdt", W + "sdtContent", W + "customXml", W + "ins"}:
            yield from blocks(child)


def headings_from_styles(styles):
    metadata = {}
    for style in styles.findall(W + "style"):
        name = val(style, W + "name") or ""
        outline = val(style, W + "pPr/" + W + "outlineLvl")
        match = re.fullmatch(r"(?:heading|标题)\s*([1-9])", name, re.I)
        if outline is None and match:
            outline = str(int(match.group(1)) - 1)
        metadata[style.get(W + "styleId")] = (name, outline, val(style, W + "basedOn"))

    def resolve(style_id):
        seen = set()
        while style_id in metadata and style_id not in seen:
            seen.add(style_id)
            name, level, style_id = metadata[style_id]
            if re.match(r"toc\s*\d|目录\s*\d", name, re.I):
                return "9"
            if level is not None:
                return level
        return None

    return {style_id: resolve(style_id) for style_id in metadata}


def table_data(table):
    rows, layout = [], []
    for row in table.findall(W + "tr"):
        cells = row.findall(W + "tc")
        rows.append(tuple(norm(txt(c)) for c in cells))
        shapes = []
        for cell in cells:
            pr = cell.find(W + "tcPr")
            span, merge = "1", ""
            if pr is not None:
                span = val(pr, W + "gridSpan") or "1"
                if pr.find(W + "vMerge") is not None:
                    merge = val(pr, W + "vMerge") or "continue"
            shapes.append((span, merge))
        layout.append((val(row, W + "trPr/" + W + "gridBefore") or "0", tuple(shapes),
                       val(row, W + "trPr/" + W + "gridAfter") or "0"))
    return (tuple(rows), tuple(layout))


def read_doc(path):
    with ZipFile(path) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        styles = ET.fromstring(archive.read("word/styles.xml")) if "word/styles.xml" in archive.namelist() else ET.Element("styles")
    levels = headings_from_styles(styles)
    headings, tables, paragraphs = [], [], []
    body = document.find(W + "body")
    for block in blocks(body):
        if block.tag == W + "tbl":
            tables.append(table_data(block))
            continue
        line = txt(block).strip()
        if not line:
            continue
        paragraphs.append(norm(line))
        level = val(block, W + "pPr/" + W + "outlineLvl")
        if level is None:
            level = levels.get(val(block, W + "pPr/" + W + "pStyle"))
        if level is not None and level in list("012345678") and heading_key(line):
            headings.append((line, int(level) + 1))
    searchable = "\n".join(norm(txt(p)) for p in body.iter(W + "p"))
    return headings, tables, paragraphs, searchable


def ordered_pairs(left, right):
    # Longest common subsequence. Repeated headings are counted individually.
    n, m = len(left), len(right)
    lengths = [[0] * (m + 1) for _ in range(n + 1)]
    for i in reversed(range(n)):
        for j in reversed(range(m)):
            lengths[i][j] = (1 + lengths[i + 1][j + 1] if left[i] == right[j]
                             else max(lengths[i + 1][j], lengths[i][j + 1]))
    i = j = 0
    pairs = []
    while i < n and j < m:
        if left[i] == right[j]:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif lengths[i + 1][j] >= lengths[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def table_label(table):
    header = table[0][0] if table[0] else ()
    return SENSITIVE.sub("〔隐去〕", "｜".join(header)).strip("｜")[:80] or "〔空白首行〕"


def match_tables(left, right):
    candidates = []
    for i, (rows, shape) in enumerate(left):
        for j, (r_rows, r_shape) in enumerate(right):
            if not rows or not r_rows:
                continue
            same_header = rows[0] == r_rows[0]
            fields, r_fields = set(filter(None, rows[0])), set(filter(None, r_rows[0]))
            overlap = len(fields & r_fields) / max(1, len(fields | r_fields))
            if not same_header and (len(fields & r_fields) < 2 or overlap < 0.5):
                continue
            exact = rows == r_rows and shape == r_shape
            same_rows = sum((Counter(rows) & Counter(r_rows)).values())
            score = (exact, same_header, overlap, shape == r_shape, same_rows, -abs(i - j))
            candidates.append((score, i, j))
    matches, used = {}, set()
    for _, i, j in sorted(candidates, reverse=True):
        if i not in matches and j not in used:
            matches[i] = j
            used.add(j)
    details = []
    for i, (rows, shape) in enumerate(left):
        row = {"参考表": i + 1, "首行": table_label(left[i]), "参考行数": len(rows)}
        if i in matches:
            j = matches[i]
            actual, actual_shape = right[j]
            row.update({"生成表": j + 1, "生成行数": len(actual),
                        "首行相同": rows[0] == actual[0], "行列合并布局相同": shape == actual_shape,
                        "全格文字相同": rows == actual})
        else:
            row["状态"] = "未找到同首行或多数共同字段的原生表；请检查图框、改名、缺失"
        details.append(row)
    return {"参考数": len(left), "生成数": len(right), "逐表": details,
            "未配对生成表": [j + 1 for j in range(len(right)) if j not in used]}


def compare(reference, generated, source=None):
    rh, rt, rp, _ = read_doc(reference)
    gh, gt, gp, gbody = read_doc(generated)
    source_data = read_doc(source) if source else None
    rkeys, gkeys = [heading_key(h) for h, _ in rh], [heading_key(h) for h, _ in gh]
    pairs = ordered_pairs(rkeys, gkeys)
    matched = dict(pairs)
    entries = []
    for i, (title, level) in enumerate(rh):
        item = {"参考序": i + 1, "参考标题": title, "参考级": level}
        if i in matched:
            j = matched[i]
            item.update({"生成序": j + 1, "生成标题": gh[j][0], "生成级": gh[j][1]})
        else:
            item["生成位置"] = ("同名大纲异序或重复数不同" if rkeys[i] in gkeys else
                            "正文出现同词，需核对是否仅目录或说明" if rkeys[i] in gbody else "未命中")
        if source_data:
            item["标题原词见采购正文"] = rkeys[i] in source_data[3]
        entries.append(item)
    used = set(matched.values())
    summary = {"参考标题数": len(rh), "生成标题数": len(gh), "有序同名数": len(pairs),
               "其中同级数": sum(rh[i][1] == gh[j][1] for i, j in pairs),
               "逐项": entries, "未配对生成标题": [name for j, (name, _) in enumerate(gh) if j not in used]}
    report = {"标题": summary, "原生表格": match_tables(rt, gt)}
    if source_data:
        original = set(source_data[2])
        clauses = list(dict.fromkeys(p for p in rp if len(p) >= 35 and p in original))
        missing = [p for p in clauses if p not in gp]
        report["原文核对"] = {"采购与参考完全相同的独立长段": len(clauses), "生成中逐段完全相同": len(clauses) - len(missing),
                            "需对读的段首": [SENSITIVE.sub("〔隐去〕", p[:48]) for p in missing]}
        identifiers = set(SENSITIVE.findall(gbody)) - set(SENSITIVE.findall(source_data[3]))
        report["证据"] = {"新增疑似手机号或身份证号种数": len(identifiers),
                        "人工核验": "对照采购及经核实的材料检查人名、案例、业绩、报价和履约承诺。编号检测仅提示，不证明事实或来源。"}
    else:
        report["证据"] = "未提供招标源文件，无法核对原文或识别采购文件以外的号码。"
    report["口径"] = ("正文Word大纲级别1–9，按 Unicode 及空格、章号归一后严格按序匹配。图形、手工标题、形似词须人工对读。"
                   "原生表按首行相同或至少两项公共字段且交并比≥0.5初配，优先匹配全格与合并布局相同的表；每表仅配一次。"
                   "表式比较不包含字体、边框、浮动文本框和分页。长段采用35个归一化字符为下限。结果不自动判定通过。")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="既有参考框架，限生成冻结后使用")
    parser.add_argument("generated", type=Path, help="独立生成的框架")
    parser.add_argument("--source", type=Path, help="实际招标/采购文件（DOCX）")
    parser.add_argument("--json", action="store_true", help="输出紧凑JSON，默认缩进JSON")
    args = parser.parse_args()
    print(json.dumps(compare(args.reference, args.generated, args.source), ensure_ascii=False,
                     indent=None if args.json else 2))


if __name__ == "__main__":
    main()
