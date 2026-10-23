"""Industry evidence library; project use requires explicit category and material selection."""
import copy
import hashlib
from pathlib import Path
import re
from datetime import date, datetime, timezone

INDUSTRIES = [('insurance', '保险', 'I'), ('fund', '基金', 'F'), ('bank', '银行', 'B'), ('wealth', '理财', 'W')]


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class SpecialLibrary:
    def migrate_special(self):
        if self.get('meta', 'special_v1'):
            return
        for industry, name, prefix in INDUSTRIES:
            existing = next((i for i in self.all('industry') if i['name'] == name), None)
            key = existing['id'] if existing else industry
            self.put('industry', {**(existing or {}), 'id': key, 'code': industry, 'name': name,
                                  'created_at': (existing or {}).get('created_at', stamp())})
            endings = ['项目业绩与案例', '人员经历与专业证明', '行业专项资格证明',
                       '业务资源与合作授权' if industry in {'fund', 'bank'} else '业务资源与授权证明']
            for i, ending in enumerate(endings, 1):
                code = f'{prefix}{i:02}'
                if not self.get('category', code):
                    self.put('category', {'id': code, 'code': code, 'name': name + ending,
                        'layer': 'special', 'industry_id': key, 'aliases': [], 'enabled': True,
                        'reserved': i == 3, 'sort_order': 100 + len(self.all('category'))})
        self.put('meta', {'id': 'special_v1', 'created_at': stamp()})
        self.db.commit()

    def is_special(self, material):
        return (self.get('category', material.get('category_id', '')) or {}).get('layer') == 'special'

    def special_feedback(self, value, count):
        import engine
        probe = dict(value, subject=engine.EXPECTED_SUBJECT, valid_until='长期')
        result = engine.material_form_feedback(probe, count)
        if not str(value.get('subject', '')).strip():
            result['errors']['subject'] = '请填写实际证明主体；未确认时填写“待核对”。'
        expiry = value.get('valid_until', '')
        if expiry and expiry not in {'长期', '长期有效', '永久'}:
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', expiry):
                    raise ValueError()
                if date.fromisoformat(expiry) < date.today():
                    result['warnings']['valid_until'] = '历史证明已过期，可以归档；使用前须核对。'
            except ValueError:
                result['errors']['valid_until'] = '请填写真实日期 YYYY-MM-DD 或“长期”。'
        elif not expiry:
            result['warnings']['valid_until'] = '有效期待核对；历史合同年度不代表资格有效期。'
        result['warnings']['description'] = '归档不代表审核通过，请核对主体、项目、签章和授权范围。'
        return result

    def special_fields(self, value, body, old):
        from server import text_value
        if old and self.is_special(old) != self.is_special(value):
            raise ValueError('本版本不能在通用层与专用层之间移动材料')
        if not self.is_special(value):
            if any(k in body for k in ('linked_category_ids', 'archived', 'review_status', 'restore_file_ids')):
                raise ValueError('这些操作仅适用于专用材料')
            return
        for key in ('client_name', 'case_name', 'subject_kind'):
            value[key] = text_value(body.get(key, value.get(key, '')), {'client_name':'客户名称','case_name':'项目名称','subject_kind':'主体类型'}[key], 300)
        status = body.get('review_status', value.get('review_status', 'pending'))
        if status not in {'pending', 'checked', 'reference'}:
            raise ValueError('材料核对状态无效')
        value['review_status'] = status
        archived = body.get('archived', value.get('archived', False))
        if type(archived) is not bool:
            raise ValueError('移除状态无效')
        value['archived'] = archived
        links = body.get('linked_category_ids', value.get('linked_category_ids', []))
        if not isinstance(links, list) or len(links) > 16 or any(not isinstance(v, str) for v in links):
            raise ValueError('关联栏目格式无效')
        for cid in links:
            if self.require('category', cid).get('layer') != 'special':
                raise ValueError('只能关联到专用栏目')
        value['linked_category_ids'] = sorted(set(links) - {value['category_id']})

    def special_list(self, query):
        def arg(k, default=''):
            return query.get(k, [default])[0]
        category, industry, search = arg('category_id'), arg('industry_id'), arg('q').casefold()
        review, archive = arg('review_status'), arg('archived', 'false') == 'true'
        page, size = max(1, int(arg('page', '1'))), min(50, max(1, int(arg('page_size', '25'))))
        cats = {c['id']: c for c in self.all('category') if c['layer'] == 'special'}
        counts = {cid: 0 for cid in cats}
        rows = []
        for m in self.all('material'):
            if m['category_id'] not in cats or bool(m.get('archived')) != archive:
                continue
            memberships = [m['category_id']] + m.get('linked_category_ids', [])
            for cid in memberships:
                if cid in counts:
                    counts[cid] += 1
            if category and category not in memberships:
                continue
            if industry and not any(cats.get(cid, {}).get('industry_id') == industry for cid in memberships):
                continue
            if review and m.get('review_status', 'pending') != review:
                continue
            if search and search not in ' '.join(str(m.get(k, '')) for k in ('name', 'subject', 'client_name', 'case_name', 'years', 'description')).casefold():
                continue
            rows.append(m)
        rows.sort(key=lambda m: (m.get('updated_at', ''), m['id']), reverse=True)
        total = len(rows)
        page = min(page, max(1, (total + size - 1) // size))
        result = []
        for m in rows[(page-1)*size:page*size]:
            row = {k: v for k, v in m.items() if k not in {'source', 'sources', 'file_ids', 'content', 'removed_file_ids'}}
            row['file_count'] = len(m.get('file_ids', []))
            result.append(row)
        return {'items': result, 'total': total, 'page': page, 'page_size': size, 'counts': counts}

    def import_special(self, entry, source_dir):
        """Trusted local manifest import, with stable IDs and per-material transaction."""
        key = 'special_' + hashlib.sha256(entry['import_key'].encode()).hexdigest()[:24]
        if self.get('material', key):
            return key, False
        category = self.require('category', entry['category_id'])
        if category.get('layer') != 'special':
            raise ValueError('仅导入专用材料')
        root = Path(source_dir).resolve()
        value = {**{k: entry[k] for k in ('name', 'category_id', 'subject')}, 'id': key,
            'version': '历史标书提取', 'years': entry.get('years', []), 'valid_until': '', 'mode': 'page',
            'content': '', 'description': entry.get('description', ''), 'review_status': 'pending',
            'client_name': entry.get('client_name', ''), 'case_name': entry.get('case_name', ''),
            'subject_kind': entry.get('subject_kind', 'company'), 'linked_category_ids': entry.get('linked_category_ids', []), 'archived': False,
            'sources': entry.get('sources', []), 'issues': entry.get('issues', []),
            'file_ids': [], 'revision': 1, 'created_at': stamp(), 'updated_at': stamp()}
        try:
            for start in range(0, len(entry['files']), 50):
                assets = []
                for item in entry['files'][start:start+50]:
                    path = (root / item['path']).resolve()
                    if not path.is_relative_to(root):
                        raise ValueError('导入图片路径越界')
                    assets.append({'name': f"{len(value['file_ids']) + len(assets) + 1:03d}.png", 'raw': path.read_bytes(), 'source': item.get('source', '')})
                value['file_ids'] += self.create_files(key, assets, seed=True)
            if not value['file_ids']:
                raise ValueError('材料没有图片')
            self.put('material', value)
            self.db.commit()
        except Exception:
            self.db.rollback()
            import shutil
            shutil.rmtree(self.root / 'materials' / key, ignore_errors=True)
            raise
        return key, True
