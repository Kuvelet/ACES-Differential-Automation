import contextlib
import io
import json
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

import collect_reports as c
import detector as d


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.destination = self.root / 'destination'
        self.report = 'BrandAlpha-ACES_Diff_2026-10-09_to_2026-11-13'
        self.row = dict(status='Succeeded', keyword='Example Catalog_BrandAlpha-ACES',
                        report_name=self.report, **dict.fromkeys(d.COLLECTION_FIELDS, ''))
        self.rows = [self.row]
        self.saved = []

    def outputs(self, prefix='123', suffixes=c.SUFFIXES, report=None):
        for suffix in suffixes:
            path = self.source / f'{prefix}~{report or self.report}{suffix}'
            if suffix.endswith('.zip'):
                with zipfile.ZipFile(path, 'w') as archive:
                    archive.writestr('report.xml', '<ACES/>')
            else:
                path.write_bytes(b'')

    def collect(self):
        with contextlib.redirect_stdout(io.StringIO()):
            c.collect_rows(self.rows, self.source, self.destination, 0,
                           lambda: self.saved.append([dict(row) for row in self.rows]))

    def test_copy_verify_and_skip(self):
        self.outputs()
        self.collect()
        self.assertEqual(self.row['collection_status'], 'Copied')
        self.assertTrue(self.row['collected_at'])
        target = c.report_destination(self.destination, self.row)
        self.assertEqual(len(list(target.iterdir())), 6)
        for source in self.source.iterdir():
            self.assertEqual(source.read_bytes(), (target / source.name).read_bytes())
        self.assertEqual((target / f'123~{self.report}_ACESDifferential' / 'report.xml').read_text(), '<ACES/>')
        self.assertEqual((target / f'123~{self.report}_NetChange' / 'report.xml').read_text(), '<ACES/>')
        self.assertEqual(self.row['collection_destination'], str(target))
        with patch.object(c, 'copy_set', side_effect=AssertionError('Must skip')):
            self.collect()

    def test_wait_partial_then_complete_and_exact_name(self):
        self.outputs(report=self.report + '_other')
        self.collect()
        self.assertEqual(self.row['collection_status'], 'Waiting')
        self.outputs(suffixes=c.SUFFIXES[:3])
        self.collect()
        self.assertEqual(self.row['collection_status'], 'Incomplete')
        self.assertFalse(self.destination.exists())
        self.outputs(suffixes=c.SUFFIXES[3:])
        self.collect()
        self.assertEqual(self.row['collection_status'], 'Copied')

    def test_long_extraction_paths(self):
        self.destination = self.root / ('long_folder_' * 10)
        self.addCleanup(lambda: shutil.rmtree(c.filesystem_path(self.destination), ignore_errors=True))
        self.outputs()
        self.collect()
        self.assertEqual(self.row['collection_status'], 'Copied', self.row['collection_error'])
        target = c.filesystem_path(c.report_destination(self.destination, self.row))
        self.assertEqual((target / f'123~{self.report}_NetChange' / 'report.xml').read_text(), '<ACES/>')

    def test_prefixes_never_mixed(self):
        self.outputs(suffixes=c.SUFFIXES[:2])
        self.outputs(prefix='456', suffixes=c.SUFFIXES[2:])
        self.collect()
        self.assertEqual(self.row['collection_status'], 'Failed')
        self.assertFalse(self.destination.exists())

    def test_duplicate_tracker_report_rejected(self):
        self.outputs()
        self.rows.append(dict(self.row))
        self.collect()
        self.assertTrue(all(r['collection_status'] == 'Failed' for r in self.rows))

    def test_invalid_zip_copies_nothing(self):
        self.outputs()
        (self.source / f'123~{self.report}_NetChange.zip').write_bytes(b'not a zip')
        self.collect()
        self.assertEqual(self.row['collection_status'], 'Failed')
        self.assertEqual(list((c.report_destination(self.destination, self.row)).iterdir()), [])

    def test_changing_files_deferred(self):
        self.outputs()
        def change(_):
            (self.source / f'123~{self.report}_source.error').write_bytes(b'changed')
        with patch.object(c.time, 'sleep', side_effect=change):
            self.collect()
        self.assertEqual(self.row['collection_status'], 'Incomplete')
        self.assertFalse(self.destination.exists())

    def test_conflict_preserved_then_partial_copy_recovered(self):
        self.outputs()
        target = c.report_destination(self.destination, self.row)
        target.mkdir(parents=True)
        source = next(self.source.iterdir())
        existing = target / source.name
        existing.write_bytes(b'existing different content')
        self.collect()
        self.assertEqual(self.row['collection_status'], 'Failed')
        self.assertEqual(existing.read_bytes(), b'existing different content')
        self.assertEqual(len(list(target.iterdir())), 1)
        existing.write_bytes(source.read_bytes())
        self.collect()
        self.assertEqual(self.row['collection_status'], 'Copied')
        self.assertEqual(len(list(target.iterdir())), 6)

    def test_unsubmitted_rows_untouched(self):
        self.outputs()
        self.row['status'] = 'Baseline'
        before = dict(self.row)
        self.collect()
        self.assertEqual(self.row, before)
        self.assertFalse(self.destination.exists())

    def test_unsafe_zip_member_rejected_before_publishing(self):
        self.outputs()
        archive = self.source / f'123~{self.report}_NetChange.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('../escape.xml', 'unsafe')
        self.collect()
        self.assertEqual(self.row['collection_status'], 'Failed')
        self.assertEqual(list(c.report_destination(self.destination, self.row).iterdir()), [])
        self.assertFalse((self.destination / 'escape.xml').exists())

    def test_extracted_conflict_preserved_and_retry(self):
        self.outputs()
        target = c.report_destination(self.destination, self.row)
        extracted = target / f'123~{self.report}_NetChange' / 'report.xml'
        extracted.parent.mkdir(parents=True)
        extracted.write_text('manual edit')
        self.collect()
        self.assertEqual(self.row['collection_status'], 'Failed')
        self.assertEqual(extracted.read_text(), 'manual edit')
        self.assertEqual(list(target.glob('*.zip')), [])
        extracted.write_text('<ACES/>')
        self.collect()
        self.assertEqual(self.row['collection_status'], 'Copied')

    def test_months_have_separate_folders(self):
        self.outputs()
        later = dict(self.row, report_name='BrandAlpha-ACES_Diff_2026-11-13_to_2026-12-13')
        self.rows.append(later)
        self.outputs(prefix='456', report=later['report_name'])
        self.collect()
        self.assertTrue(all(row['collection_status'] == 'Copied' for row in self.rows))
        self.assertNotEqual(self.row['collection_destination'], later['collection_destination'])
        for row in self.rows:
            self.assertEqual(len(list(Path(row['collection_destination']).glob('*.zip'))), 2)

    def test_invalid_dates_rejected(self):
        for report in ('bad', 'BrandAlpha-ACES_Diff_2026-02-30_to_2026-11-13',
                       'BrandAlpha-ACES_Diff_2026-12-13_to_2026-11-13'):
            with self.assertRaises(ValueError):
                c.report_destination(self.destination, dict(self.row, report_name=report))

    def test_source_outage_recorded(self):
        with patch.object(c, 'index_outputs', side_effect=OSError('unavailable')):
            with self.assertRaises(OSError):
                self.collect()
        self.assertEqual(self.row['collection_status'], 'Failed')
        self.assertEqual(len(self.saved), 1)

    def test_paused_entry_point_never_accesses_tracker_or_share(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps({'collection': {'enabled': False}}))
        with patch.object(d, 'RunLock', side_effect=AssertionError('Must stay paused')):
            self.assertEqual(c.run(config), 0)

    def test_old_csv_upgrade_preserves_existing_fields(self):
        keyword = self.row['keyword']
        items = []
        for day in ('2026-10-09', '2026-11-13'):
            path = self.root / f'{uuid.uuid4()}~{keyword}_{day}_FULL.zip'
            path.write_bytes(b'fixture')
            items.append(d.parse_export(path, [keyword]))
        rows = d.prepare_rows([], items, baseline=True)
        tracker = self.root / 'differential_tracker.csv'
        with patch.object(d, 'FIELDS', [f for f in d.FIELDS if f not in d.COLLECTION_FIELDS]):
            d.save_rows(tracker, [{k: v for k, v in r.items() if k not in d.COLLECTION_FIELDS} for r in rows])
        upgraded = d.read_rows(tracker)
        self.assertEqual(upgraded, rows)
        upgraded[1].update(collection_status='Copied', collected_at='test')
        d.save_rows(tracker, upgraded)
        self.assertEqual(d.read_rows(tracker), upgraded)


if __name__ == '__main__':
    unittest.main()
