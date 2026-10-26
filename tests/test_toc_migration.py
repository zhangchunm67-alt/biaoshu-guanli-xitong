"""Old projects cannot retain an insertion target newly identified as a directory."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

import engine
from server import Store


class TocMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bid-toc-migration-')
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data', seed=False)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def row(self, layer='common', locator='p:1:toc', **updates):
        value = dict(id='old-row', category_id='business_license' if layer == 'common' else 'I01',
                     layer=layer, locator_id=locator, title='旧位置', existing_content=False,
                     material_ids=['chosen-picture'], note='保留商务备注', action='fill',
                     confirmed=True, location_match={'status': 'matched'})
        value.update(updates)
        return value

    def scanned(self, layer='common', targets=('p:8:body',)):
        rows = [self.row(layer, target, id=f'new-{index}', title=f'正文{index}',
                         material_ids=[], note='', confirmed=False)
                for index, target in enumerate(targets)]
        return dict(mappings=rows, requirements={}, excluded_locator_ids=['p:1:toc', 'at:1:toc', 'ph:1:toc:0'],
                    headings=[{'id': row['locator_id'], 'title': row['title']} for row in rows])

    def assert_preserved(self, row):
        self.assertEqual('old-row', row['id'])
        self.assertEqual(['chosen-picture'], row['material_ids'])
        self.assertEqual('保留商务备注', row['note'])
        self.assertFalse(row['confirmed'])

    def test_shared_category_upgrade_adds_new_body_slot_without_overwriting_choice(self):
        for cid, layer in [('tax_social', 'common'), ('text_template', 'special')]:
            with self.subTest(category=cid):
                old = self.row(layer, 'p:8:body', category_id=cid)
                scanned = self.scanned(layer, ('p:8:body', 'p:12:body'))
                for row in scanned['mappings']:
                    row['category_id'] = cid
                project = {'mappings': [old]}
                self.store.refresh_location_rows(project, scanned, layer)
                self.assertEqual(2, len(project['mappings']))
                self.assertEqual(['chosen-picture'], old['material_ids'])
                self.assertEqual('保留商务备注', old['note'])
                self.assertEqual('p:12:body', project['mappings'][1]['locator_id'])
                self.store.refresh_location_rows(project, scanned, layer)
                self.assertEqual(2, len(project['mappings']))

    def test_automatic_directory_target_moves_to_unique_body_in_both_layers(self):
        for layer in ('common', 'special'):
            with self.subTest(layer=layer):
                row = self.row(layer)
                project = {'mappings': [row]}
                self.store.refresh_location_rows(project, self.scanned(layer), layer)
                self.assertEqual('p:8:body', row['locator_id'])
                self.assertEqual('matched', row['location_match']['status'])
                self.assertEqual('待确认', project['status'])
                self.assert_preserved(row)

    def test_directory_cannot_survive_manual_skip_or_missing_decision(self):
        for layer in ('common', 'special'):
            for decision in ({'location_match': {'status': 'manual'}}, {'location_match': {}},
                             {'action': 'skip'}, {'action': 'missing'}, {'action': 'missing', 'auto_match': False}):
                with self.subTest(layer=layer, decision=decision):
                    row = self.row(layer, **decision)
                    old_action = row['action']
                    self.store.refresh_location_rows({'mappings': [row]}, self.scanned(layer), layer)
                    self.assertEqual('', row['locator_id'])
                    self.assertIn('目录', row['location_match']['reason'])
                    self.assertEqual(old_action, row['action'])
                    self.assert_preserved(row)

    def test_multiple_body_candidates_do_not_guess_a_replacement(self):
        row = self.row()
        self.store.refresh_location_rows({'mappings': [row]},
                                         self.scanned(targets=('p:8:body', 'p:20:body')), 'common')
        self.assertEqual('', row['locator_id'])
        self.assertIn('目录', row['location_match']['reason'])
        self.assertEqual(2, len(row['location_match']['candidates']))
        self.assert_preserved(row)

    def test_existing_body_mapping_does_not_receive_duplicate_migration(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                row = self.row()
                body = self.row(locator='p:8:body', id='body-row', location_match={'status': 'manual'})
                rows = [row, body] if not reverse else [body, row]
                self.store.refresh_location_rows({'mappings': rows}, self.scanned(), 'common')
                self.assertEqual('', row['locator_id'])
                self.assertEqual('p:8:body', body['locator_id'])
                self.assertTrue(body['confirmed'])
                self.assert_preserved(row)

    def test_non_directory_manual_positions_and_legacy_scan_are_preserved(self):
        for legacy_scan in (False, True):
            with self.subTest(legacy_scan=legacy_scan):
                row = self.row(locator='p:5:manual', location_match={'status': 'manual'})
                scanned = self.scanned()
                if legacy_scan:
                    scanned.pop('excluded_locator_ids')
                self.store.refresh_location_rows({'mappings': [row]}, scanned, 'common')
                self.assertEqual('p:5:manual', row['locator_id'])
                self.assertTrue(row['confirmed'])

    def test_sync_removes_directory_options_even_without_mappings_and_persists(self):
        source = self.store.root / 'templates' / 'framework.docx'
        Document().save(source)
        for layer in ('common', 'special'):
            with self.subTest(layer=layer):
                old = self.row(layer, location_match={'status': 'manual'})
                headings = [dict(id=locator, title='目录项') for locator in
                            ('p:1:toc', 'at:1:toc', 'ph:1:toc:0')]
                headings.append(dict(id='p:5:manual', title='保留人工位置'))
                project = dict(id=f'legacy-{layer}', mappings=[old], headings=headings,
                               template_storage='templates/framework.docx',
                               template_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
                               special_selection_confirmed=True, special_category_ids=['I01'],
                               common_locator_version=-1, special_locator_version=-1)
                scanned = self.scanned(layer)
                with patch.object(engine, 'scan_document', return_value=copy.deepcopy(scanned)) as scan:
                    sync = getattr(self.store, f'sync_{layer}_locations')
                    result = sync(project)
                    # A second read at this version does not repeat the migration.
                    sync(result)
                    self.assertEqual(1, scan.call_count)
                ids = {h['id'] for h in result['headings']}
                self.assertEqual({'p:5:manual', 'p:8:body'}, ids)
                self.assertEqual('', result['mappings'][0]['locator_id'])
                persisted = self.store.require('project', project['id'])
                self.assertEqual(ids, {h['id'] for h in persisted['headings']})
                self.assertFalse(persisted['mappings'][0]['confirmed'])


if __name__ == '__main__':
    unittest.main()
