#!/usr/bin/env python3
"""Generate a Word response framework, with separate submissions when the buyer requires them."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile
from urllib.parse import unquote

SCRIPTS = Path(__file__).resolve().parent
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def verify_docx(path):
    with ZipFile(path) as doc:
        failed = doc.testzip()
        if failed:
            raise ValueError(f"损坏的文件部件 {failed}")
        root = ET.fromstring(doc.read("word/document.xml"))
        body = root.find(W + "body")
        rels = {e.get("Id"): e for e in ET.fromstring(doc.read("word/_rels/document.xml.rels"))}
        if body is None or not any(n.text for n in body.iter(W + "t")):
            raise ValueError("框架正文为空")
        for node in root.iter():
            for key, val in node.attrib.items():
                if not key.startswith(R):
                    continue
                rel = rels.get(val)
                if rel is None:
                    raise ValueError(f"文档含有无效的引用 {val}")
                if rel.get("TargetMode") != "External":
                    target = unquote(rel.get("Target").split("#", 1)[0])
                    name = target[1:] if target.startswith("/") else posixpath.normpath(posixpath.join("word", target))
                    if name not in doc.namelist():
                        raise ValueError(f"缺少引用的文件部件 {name}")
        return len(list(body.iter(W + "tbl")))


def build_parts(source, data, directory):
    """Build independent submissions before publishing any output."""
    directory.mkdir()
    result = []
    names = set()
    for i, part in enumerate(data['parts'], 1):
        title = part.get('name', '')
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', title).strip(' .')
        if not name or len(name.encode('utf-8')) > 220 or name.casefold() in names:
            raise ValueError('分件名称缺失、重复或过长，请按采购包件和文件类型命名。')
        names.add(name.casefold())
        blocks = part.get('blocks')
        if not isinstance(blocks, list) or not any(b.get('kind') == 'heading' for b in blocks):
            raise ValueError(f'分件缺少可填写的应答章节：{name}')
        plan = {'source_sha256': data['source_sha256'], 'blocks': blocks}
        docx = directory / (name + '.docx')
        if docx.exists():
            raise ValueError(f'分件文件名冲突：{name}')
        from build_framework import build
        build(source, plan, docx)
        verify_docx(docx)
        result.append(docx)
    return result


def publish(candidate, out, parts, report, report_path):
    target = out.with_name(out.stem + '_独立响应文件') if parts else None
    if target:
        target.mkdir()  # exclusive reservation; never reuse another run's files
    linked = []
    try:
        for src in parts:
            dst = target / src.name
            os.link(src, dst)
            linked.append((src, dst))
        os.link(report, report_path)
        linked.append((report, report_path))
        os.link(candidate, out)
    except BaseException:
        for src, dst in reversed(linked):
            if dst.exists() and dst.samefile(src):
                dst.unlink()
        if target and not any(target.iterdir()):
            target.rmdir()
        raise


def generate_framework(source, output, plan=None, *, scope='whole', parsed=None,
                       extra_notes=(), reminder=''):
    """Shared in-process builder; the CLI and service use this same publication path."""
    src = Path(source).resolve(strict=True)
    out = Path(output).resolve()
    if src.suffix.lower() != ".docx" or out.suffix.lower() != ".docx":
        raise ValueError("输入和输出均需使用 .docx。")
    report_path = out.with_name(out.stem + '_目录与五类要求.html')
    if out.exists() or report_path.exists() or out == src:
        raise ValueError("请指定新的输出文件名，保留已有的 Word 或指标汇总。")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bid-framework-", dir=out.parent) as work:
        work = Path(work)
        if plan is None:
            from plan_framework import Source, generate
            data = generate(Source(src))
        elif isinstance(plan, dict):
            data = copy.deepcopy(plan)
        else:
            data = json.loads(Path(plan).read_text(encoding="utf-8"))
        if data.get("source_sha256") != hashlib.sha256(src.read_bytes()).hexdigest():
            raise ValueError("编排计划与招标文件版本不匹配，请从当前招标件重新核对。")
        from required_sections import ensure_business_license
        data = ensure_business_license(data)
        if scope != "whole":
            from scope_framework import apply_scope
            data = apply_scope(data, scope)
        blocks = data.get("blocks")
        if not isinstance(blocks, list) or not any(b.get("kind") == "heading" for b in blocks):
            raise ValueError("编排计划缺少可导航的应答章节。")
        candidate = work / "framework.docx"
        from build_framework import build
        build(src, data, candidate)
        tables = verify_docx(candidate)
        parts = build_parts(src, data, work / 'parts') if data.get('parts') else []
        from tender_metrics import save_report
        report = work / 'summary.html'
        counts = save_report(src, report, data, parsed=parsed, extra_notes=extra_notes, reminder=reminder)
        publish(candidate, out, parts, report, report_path)
        return {'tables': tables, 'counts': counts, 'part_count': len(parts), 'review': data.get('review', [])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="单一采购、招标或召集文件 DOCX")
    parser.add_argument("output", type=Path, help="新建的框架 DOCX")
    parser.add_argument("--plan", type=Path, help="已按招标原件复核并绑定其 SHA-256 的计划")
    parser.add_argument("--scope", choices=("whole", "identity-and-authorization"), default="whole",
                        help="完整框架，或仅抽出投标函、主体资格及授权材料")
    args = parser.parse_args()
    result = generate_framework(args.source, args.output, args.plan, scope=args.scope)
    out = args.output
    tables, counts = result['tables'], result['counts']
    report_path = out.with_name(out.stem + '_目录与五类要求.html')
    if result['part_count']:
        print(f"已生成 {result['part_count']} 份独立 Word 文件：{out.stem}_独立响应文件")
    from tender_metrics import CATEGORIES
    print(f"已生成 {out.name}（{tables} 张表）")
    print(f'内部编制页：{report_path.name}')
    print('，'.join(f'{CATEGORIES[k][0]} {v}{CATEGORIES[k][1]}' for k,v in counts.items()))
    for message in result['review']:
        if isinstance(message, dict):
            print(f"编制提示（源位置 {message.get('source_index', '未标')}）{message.get('text', '')}")
        else:
            print("编制提示", message)


if __name__ == "__main__":
    try:
        main()
    except (BadZipFile, ET.ParseError, KeyError, ValueError, OSError) as exc:
        sys.exit(f"未生成框架。{exc}")
