"""Read-only local corpus audit. Temporary exports are removed automatically.

Run: python -B scripts/audit_matching.py --data-dir data --corpus 考题
This checks assembly invariants, not legal/business sufficiency of a bid.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docx import Document
from docx.oxml.ns import qn
import engine
from document_toc import excluded_paragraphs


def audit(data_dir, corpus=None):
    root = Path(data_dir).resolve()
    with sqlite3.connect((root / 'store.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        load = lambda kind: [json.loads(r[0]) for r in db.execute('SELECT payload FROM objects WHERE kind=?', (kind,))]
        categories, materials, projects = load('category'), load('material'), load('project')
        files = {f['id']: f for f in load('file')}
    for material in materials:
        material['files'] = []
        for fid in material.get('file_ids', []):
            file = dict(files[fid])
            path = (root / file['storage']).resolve()
            assert path.is_relative_to(root), 'Material path escapes data directory'
            file['path'] = str(path)
            material['files'].append(file)
    sources = [root / p['template_storage'] for p in projects]
    if corpus:
        sources.extend(Path(corpus).glob('*/考题.docx'))
    unique = {hashlib.sha256(p.read_bytes()).hexdigest(): p for p in sources}
    report = dict(source_files=len(sources), unique_templates=len(unique), material_groups=len(materials),
        image_files=sum(len(m['files']) for m in materials), scanned_locations=0,
        excluded_directory_paragraphs=0, exports=0, inserted_images=0, statuses=Counter(), errors=[])
    selected = [c['id'] for c in categories if c.get('layer') == 'special' and c.get('enabled')]
    for digest, source in unique.items():
        try:
            document = Document(source)
            excluded = excluded_paragraphs(document)
            scanned = engine.scan_document(source, categories, materials, selected)
            excluded_ids = set(scanned['excluded_locator_ids'])
            assert not any(h['id'] in excluded_ids for h in scanned['headings']), 'Directory selectable'
            records, _ = engine._inspect(document, [c for c in categories if c.get('enabled')])
            by_locator = {r['id']: r for r in records}
            report['excluded_directory_paragraphs'] += len(excluded)
            for row in scanned['mappings']:
                report['statuses'][row['status']] += 1
                if not row['locator_id']:
                    continue
                report['scanned_locations'] += 1
                record = by_locator[row['locator_id']]
                assert record['_element'] not in excluded and record['_anchor'] not in excluded, 'Directory target or anchor'
                assert record.get('_region') not in {'scoring', 'contract'}, 'Non-response target'
            ready = [r for r in scanned['mappings'] if r['status'] == 'ready']
            if not ready:
                continue
            original_count = len(list(document.element.body.iter(qn('a:blip'))))
            expected = sum(len(next(m for m in materials if m['id'] == mid)['files'])
                for row in ready for mid in row['material_ids'])
            insert = engine._insert_materials

            def guarded_insert(doc, record, chosen):
                directory = excluded_paragraphs(doc)
                before = {p: p.xml for p in directory}
                assert record['_element'] not in directory and record['_anchor'] not in directory
                insert(doc, record, chosen)
                assert all(p.xml == xml for p, xml in before.items()), 'Directory changed during insertion'

            with tempfile.TemporaryDirectory(prefix='bid-corpus-audit-') as temp:
                output = Path(temp) / 'output.docx'
                with patch.object(engine, '_insert_materials', guarded_insert):
                    engine.export_document(source, output, ready, materials, categories=categories)
                with ZipFile(output) as archive:
                    assert archive.testzip() is None, 'Invalid exported DOCX'
                result = Document(output)
                actual = len(list(result.element.body.iter(qn('a:blip')))) - original_count
                assert actual == expected, f'Image count {actual} != {expected}'
                report['exports'] += 1
                report['inserted_images'] += actual
            assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, 'Source modified'
        except Exception as exc:
            report['errors'].append(dict(template_sha256=digest, error=str(exc)))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--corpus')
    args = parser.parse_args()
    result = audit(args.data_dir, args.corpus)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result['errors']))
