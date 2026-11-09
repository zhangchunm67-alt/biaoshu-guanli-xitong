"""Check Word chapter anchors, snapshot exports and concurrent edits."""
import tempfile
import unittest
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

import chapter_drafts
import engine
from server import Store


class ChapterDraftTests(unittest.TestCase):
    def test_responses_stay_in_the_selected_section_and_leave_the_original_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                template = Path(directory) / 'templates' / 'framework.docx'
                document = Document()
                for key in ('BFHeading1', 'BFBody', 'BFSlot'):
                    document.styles.add_style(key, 1)
                document.add_paragraph('专项方案')  # Same name in the TOC.
                for number in (1, 2):
                    p = document.add_paragraph('专项方案', 'BFHeading1')
                    bookmark = OxmlElement('w:bookmarkStart')
                    bookmark.set(qn('w:name'), 'BidSection' + str(number))
                    bookmark.set(qn('w:id'), str(number))
                    p._p.append(bookmark)
                    document.add_paragraph('采购范围说明')
                    document.add_paragraph('【待编】填写本节的方案。', 'BFSlot')
                    document.add_table(rows=1, cols=1).cell(0, 0).text = '采购方固定表式'
                document.save(template)
                before = template.read_bytes()
                store.put('project', {'id': 'p', 'framework_id': 'f',
                                     'template_storage': 'templates/framework.docx'})
                store.db.commit()
                initial = chapter_drafts.read_chapters(store, 'p')
                self.assertEqual(['BidSection1', 'BidSection2'], [c['id'] for c in initial['chapters']])
                request = dict(revision=0, chapters=[dict(id='BidSection2', text='第一段\n第二段')])
                saved = chapter_drafts.save_chapters(store, 'p', request)
                self.assertEqual(1, saved['revision'])
                with self.assertRaisesRegex(ValueError, '其他窗口'):
                    chapter_drafts.save_chapters(store, 'p', request)
                with self.assertRaisesRegex(ValueError, '不属于'):
                    chapter_drafts.save_chapters(store, 'p', dict(revision=1, chapters=[dict(id='wrong', text='正文')]))
                result = Path(directory) / 'draft.docx'
                engine.export_document(template, result, [], [], framework_drafts=store.require('project','p')['chapter_texts'])
                exported = Document(result)
                self.assertEqual(['专项方案', '专项方案', '采购范围说明', '【待编】填写本节的方案。',
                                  '专项方案', '采购范围说明', '第一段', '第二段'],
                                 [p.text for p in exported.paragraphs][1:])
                self.assertEqual(2, len(exported.tables))
                self.assertEqual(before, template.read_bytes())
                chapter_drafts.save_chapters(store, 'p', dict(revision=1, chapters=[dict(id='BidSection2', text='')]))
                self.assertEqual({}, store.require('project','p')['chapter_texts'])
            finally:
                store.close()
