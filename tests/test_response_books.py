"""Source-preserving book partition and the real review-to-outline transition."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
import base64

from docx import Document
from lxml import html

import framework_review
import framework_service as service
from server import Store

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/bid-response-framework/scripts'
sys.path.insert(0, str(SCRIPTS))
from response_books import organize, book_directories, requires_split


def split_source(text='投标文件须分为商务标和技术标，分别成册。'):
    return SimpleNamespace(nodes=[SimpleNamespace(tag='p', text=text, full=text, index=100)])


def h(text, level=1, **fields):
    return {'kind': 'heading', 'text': text, 'level': level, **fields}


def normalized(blocks):
    plan = {'blocks': blocks}
    framework_review.assign_bookmarks(plan)
    result = organize(plan, split_source())
    framework_review.assign_bookmarks(result)
    return result


class BookPartitionTests(unittest.TestCase):
    def test_content_partition_keeps_identity_pair_forms_and_stable_ids(self):
        plan = {'blocks': [h('营业执照'), {'kind': 'slot', 'text': '执照材料'},
            h('项目方案'), h('人员安排', 2), {'kind': 'copy', 'start': 2, 'end': 5},
            h('公司介绍'), {'kind': 'text', 'text': '原公司介绍'},
            h('项目报价单'), {'kind': 'copy', 'start': 9, 'end': 9}]}
        framework_review.assign_bookmarks(plan)
        ids = {b['text']: b['bookmark'] for b in plan['blocks'] if b['kind'] == 'heading'}
        result = organize(plan, split_source())
        framework_review.assign_bookmarks(result)
        books = {b['role']: b for b in book_directories(result)}
        self.assertEqual(['营业执照', '公司介绍', '项目报价单'], [b['text'] for b in books['commercial']['chapters']])
        self.assertEqual(['项目方案', '人员安排'], [b['text'] for b in books['technical']['chapters']])
        self.assertTrue(all(ids[b['text']] == b['id'] for book in books.values() for b in book['chapters']))
        self.assertEqual([b for b in plan['blocks'] if b['kind'] == 'copy'],
                         [b for b in result['blocks'] if b['kind'] == 'copy'][::-1])
        self.assertEqual(result, organize(result))

    def test_prescribed_books_keep_order_original_forms_and_pair_with_company(self):
        blocks = [h('技术标', book_role='technical'), h('公司介绍', 2),
                  {'kind': 'copy', 'start': 1, 'end': 2, 'preserve_format': True},
                  h('服务方案', 2), h('商务标', book_role='commercial'), h('报价函', 2),
                  h('资格标', book_role='qualification'), h('营业执照', 2),
                  {'kind': 'copy', 'start': 8, 'end': 9, 'preserve_format': True}]
        plan = normalized(blocks)
        books = book_directories(plan)
        self.assertEqual(['技术标', '商务标', '资格标'], [b['name'] for b in books])
        self.assertEqual(['营业执照', '公司介绍', '服务方案'], [b['text'] for b in books[0]['chapters']])
        self.assertEqual(['报价函'], [b['text'] for b in books[1]['chapters']])
        self.assertEqual('source', plan['book_basis'])

    def test_lot_containers_split_without_losing_or_duplicating_copied_tables(self):
        plan = normalized([h('甲平台'), h('项目方案', 2), {'kind': 'copy', 'start': 1},
                           h('分项报价', 2), {'kind': 'copy', 'start': 2},
                           h('乙平台'), h('项目方案', 2), {'kind': 'copy', 'start': 3},
                           h('分项报价', 2), {'kind': 'copy', 'start': 4}])
        books = book_directories(plan)
        self.assertEqual(['甲平台', '分项报价', '乙平台', '分项报价'], [c['text'] for c in books[0]['chapters']])
        self.assertEqual(['甲平台', '项目方案', '乙平台', '项目方案'], [c['text'] for c in books[1]['chapters']])
        self.assertEqual([1, 2, 3, 4], sorted(b['start'] for b in plan['blocks'] if b['kind'] == 'copy'))
        ids = [b['bookmark'] for b in plan['blocks'] if b['kind'] == 'heading']
        self.assertEqual(len(ids), len(set(ids)))

    def test_explicit_conflicting_placement_retains_required_original_and_adds_adjacent_copy(self):
        plan = normalized([h('技术标', book_role='technical'), h('公司介绍', 2, source_index=2),
                           h('商务标', book_role='commercial'), h('项目报价单', 2),
                           h('资格标', book_role='qualification'), h('营业执照', 2, source_index=8),
                           {'kind': 'copy', 'start': 9, 'end': 10, 'preserve_format': True}])
        books = {b['role']: b for b in book_directories(plan)}
        self.assertEqual(['营业执照', '公司介绍'], [c['text'] for c in books['technical']['chapters']])
        self.assertEqual(['营业执照'], [c['text'] for c in books['qualification']['chapters']])
        self.assertEqual(plan, organize(plan))

    def test_review_transition_persists_two_directories_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / '邀请函.docx'
            doc = Document()
            for line in ['内容服务采购邀请函', '特邀请贵公司参加采购。',
                         '投标文件须分为商务标和技术标，分别成册。',
                         '报价人应准备投标文件，包括公司介绍、历史项目、方案、报价。',
                         '服务需求：提供内容生产、投放及数据监测服务。',
                         '报价有效期为90天。']:
                doc.add_paragraph(line)
            doc.save(source)
            store = Store(root / 'store')
            try:
                first = service.create(store, {'name': '分册验证', 'filename': source.name,
                    'data': base64.b64encode(source.read_bytes()).decode()})
                saved = service.revise(store, first['id'], {'revision': first['revision'],
                    'items': first['requirements'], 'confirmed': True, 'notes_acknowledged': True,
                    'acknowledged_removed_ids': []}, 'requirements')
                again = service.revise(store, first['id'], {'revision': saved['revision'],
                    'items': saved['outline']}, 'outline')
                self.assertEqual(saved['outline'], again['outline'])
                self.assertEqual(saved['requirements'], again['requirements'])
                self.assertEqual(saved['books'], again['books'])
                repeated = service.revise(store, first['id'], {'revision': again['revision'],
                    'items': again['requirements'], 'confirmed': True, 'notes_acknowledged': True,
                    'acknowledged_removed_ids': []}, 'requirements')
                self.assertEqual(again['outline'], repeated['outline'])
                self.assertEqual(['commercial', 'technical'], [b['role'] for b in again['books']])
                page = html.parse(str(service.asset(store, first['id'], 'html')[0]))
                commercial = page.xpath('//section[@data-book-role="commercial"]//li/text()')
                self.assertEqual('营业执照', commercial[commercial.index('公司介绍') - 1])
                self.assertTrue(page.xpath('//section[@data-book-role="technical"]//li'))
                built = Document(service.asset(store, first['id'], 'word')[0])
                titles = [p.text for p in built.paragraphs if p._p.xpath('.//w:bookmarkStart')]
                self.assertTrue(any(t.endswith('商务标') for t in titles))
                self.assertTrue(any(t.endswith('技术标') for t in titles))
                self.assertTrue(all(m['count'] == len(page.xpath('//*[@id=$x]/details', x=m['key'])) for m in again['metrics']))
            finally:
                store.close()

    def test_no_buyer_split_keeps_unified_outline_and_rolls_back_inferred_books(self):
        self.assertFalse(requires_split(split_source('无需将投标文件分为商务标和技术标。'), []))
        self.assertFalse(requires_split(split_source('商务技术要求应全部响应。'), []))
        plan = normalized([h('营业执照'), h('项目方案'), h('公司介绍'), h('项目报价单')])
        for b in plan['blocks']:
            if b.get('book_role'):
                b['book_origin'] = 'inferred'
        result = organize(plan, split_source('请提供公司介绍、方案和报价。'))
        self.assertEqual([], book_directories(result))
        titles = [b['text'] for b in result['blocks'] if b['kind'] == 'heading']
        self.assertNotIn('商务标', titles)
        self.assertNotIn('技术标', titles)
        self.assertEqual('营业执照', titles[titles.index('公司介绍') - 1])
        self.assertEqual(result, organize(result, split_source('请提供公司介绍、方案和报价。')))


if __name__ == '__main__':
    unittest.main()
