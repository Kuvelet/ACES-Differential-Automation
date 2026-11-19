import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
import zipfile
from unittest.mock import patch

import detector as d

KEY = 'Example Catalog_BrandEta-ACES'


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.test-', dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.state = self.root / 'state'
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(dict(source_folder=str(self.source),
            state_folder=str(self.state), stability_seconds=0, keywords=[KEY])))

    def export(self, day, modified=1000, key=KEY):
        path = self.source / f'{uuid.uuid4()}~{key}_{day}_FULL.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('export.xml', '<ACES/>')
        os.utime(path, ns=(modified, modified))
        return path

    def scan(self, initialize=False):
        with contextlib.redirect_stdout(io.StringIO()):
            d.run(self.config, initialize)
        return d.read_rows(self.state / 'differential_tracker.csv')

    def test_baseline_new_multiple_and_repeat(self):
        a = self.export('2026-09-01')
        self.assertEqual(self.scan(True)[0]['status'], 'Baseline')
        b = self.export('2026-10-01')
        c = self.export('2026-11-01')
        rows = self.scan()
        self.assertEqual([r['status'] for r in rows], ['Baseline', 'Pending', 'Pending'])
        self.assertEqual(rows[1]['previous_file_path'], str(a))
        self.assertEqual(rows[2]['previous_file_path'], str(b))
        self.assertEqual(self.scan(), rows)

    def test_idle_scan_skips_wait_and_zip_reads(self):
        self.export('2026-09-01')
        rows = self.scan(True)
        with patch.object(d.time, 'sleep') as sleep, patch.object(d.zipfile, 'ZipFile', side_effect=AssertionError('Old ZIP reopened')):
            ready = d.discover(self.source, [KEY], 4, existing=rows)
        sleep.assert_not_called()
        self.assertEqual(len(ready), 1)

    def test_new_export_revalidates_correct_predecessor(self):
        old = self.export('2026-09-01')
        rows = self.scan(True)
        new = self.export('2026-10-01')
        opener = d.zipfile.ZipFile
        with patch.object(d.zipfile, 'ZipFile', wraps=opener) as opened, patch.object(d.time, 'sleep') as sleep:
            ready = d.discover(self.source, [KEY], 4, existing=rows)
        self.assertEqual({call.args[0] for call in opened.call_args_list}, {old, new})
        sleep.assert_called_once_with(4)
        paired = d.prepare_rows(rows, ready)[-1]
        self.assertEqual(paired['previous_file_path'], str(old))
        self.assertEqual(paired['new_file_path'], str(new))

    def test_changed_invalid_predecessor_blocks_new_comparison(self):
        old = self.export('2026-09-01')
        rows = self.scan(True)
        old.write_bytes(b'broken archive')
        self.export('2026-10-01')
        ready = d.discover(self.source, [KEY], 0, existing=rows)
        self.assertEqual(d.prepare_rows(rows, ready), rows)

    def test_removed_export_is_not_used_as_predecessor(self):
        first = self.export('2026-08-01')
        removed = self.export('2026-09-01')
        rows = self.scan(True)
        removed.unlink()
        newest = self.export('2026-10-01')
        ready = d.discover(self.source, [KEY], 0, existing=rows)
        pair = d.prepare_rows(rows, ready)[-1]
        self.assertEqual(pair['previous_file_path'], str(first))
        self.assertEqual(pair['new_file_path'], str(newest))

    def test_renamed_keyword_pairs_with_legacy_predecessor(self):
        key = 'Example Catalog_BrandGamma'
        old = self.export('2026-10-18', key=key + '-ACES')
        new = self.export('2026-11-15', key=key)
        ready = d.discover(self.source, [key], 0)
        rows = d.prepare_rows([], ready, baseline_before='2026-11-01')
        self.assertEqual([r['status'] for r in rows], ['Baseline', 'Pending'])
        self.assertEqual(rows[1]['previous_file_path'], str(old))
        self.assertEqual(d.build_report_name(rows[1]), 'BrandGamma_Diff_2026-10-18_to_2026-11-15')
        self.assertEqual(d.parse_export(new, [key])['keyword'], key)

    def test_cutoff_baselines_late_discovered_old_exports(self):
        self.export('2026-09-01')
        self.export('2026-10-01')
        self.export('2026-11-01')
        ready = d.discover(self.source, [KEY], 0)
        rows = d.prepare_rows([], ready, baseline_before='2026-11-01')
        self.assertEqual([r['status'] for r in rows], ['Baseline', 'Baseline', 'Pending'])

    def test_backdated_does_not_rewrite_pairs(self):
        a = self.export('2026-09-01')
        self.scan(True)
        self.export('2026-11-01')
        old = self.scan()[1]
        self.export('2026-10-01')
        rows = self.scan()
        self.assertEqual(rows[1], old)
        self.assertEqual(rows[2]['previous_file_path'], str(a))

    def test_same_day_modified_and_tie(self):
        a = self.export('2026-10-01', 1000)
        self.scan(True)
        self.export('2026-10-01', 2000)
        self.assertEqual(self.scan()[1]['previous_file_path'], str(a))
        self.export('2026-10-01', 2000)
        self.assertEqual(self.scan()[2]['status'], 'Ambiguous')

    def test_deletion_requeues_and_manual_pending_preserved(self):
        self.export('2026-09-01')
        self.scan(True)
        self.export('2026-10-01')
        rows = self.scan()
        path = self.state / 'differential_tracker.csv'
        rows[1]['status'] = 'Succeeded'
        d.save_rows(path, rows)
        self.assertEqual(self.scan()[1]['status'], 'Succeeded')
        rows[1]['status'] = 'Pending'
        d.save_rows(path, rows)
        self.assertEqual(self.scan()[1]['status'], 'Pending')
        d.save_rows(path, rows[:1])
        self.assertEqual(self.scan()[1]['status'], 'Pending')

    def test_missing_csv_and_reinitialize_refused(self):
        self.export('2026-09-01')
        self.scan(True)
        with self.assertRaises(RuntimeError):
            self.scan(True)
        (self.state / 'differential_tracker.csv').unlink()
        with self.assertRaises(RuntimeError):
            self.scan()

    def test_no_predecessor_and_unrelated_keyword(self):
        self.export('2026-09-01')
        self.scan(True)
        self.export('2026-08-01')
        self.export('2026-10-01', key='Unrelated-ACES')
        rows = self.scan()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['status'], 'NoPredecessor')

    def test_bad_zip_defers_keyword(self):
        self.export('2026-09-01')
        self.scan(True)
        invalid = self.export('2026-10-01')
        invalid.write_bytes(b'incomplete')
        self.export('2026-11-01')
        self.assertEqual(len(self.scan()), 1)

    def test_lock_blocks_overlap(self):
        self.state.mkdir()
        with d.RunLock(self.state / '.detector.lock'):
            with self.assertRaises(RuntimeError):
                with d.RunLock(self.state / '.detector.lock'):
                    pass

    def test_baseline_can_be_set_pending(self):
        a = self.export('2026-09-01')
        self.export('2026-10-01')
        rows = self.scan(True)
        self.assertEqual([r['status'] for r in rows], ['Baseline', 'Baseline'])
        self.assertEqual(rows[1]['previous_file_path'], str(a))
        self.assertFalse(rows[0]['previous_file_id'])
        self.assertIn('No earlier', rows[0]['last_error'])
        rows[1]['status'] = 'Pending'
        d.save_rows(self.state / 'differential_tracker.csv', rows)
        self.assertEqual(self.scan(), rows)

    def test_backfill_preserves_statuses_and_saved_pairs(self):
        self.export('2026-09-01')
        self.export('2026-10-01')
        self.export('2026-11-01')
        rows = self.scan(True)
        rows[1].update(previous_file_id='', previous_file_path='')
        rows[2]['status'] = 'Succeeded'
        d.save_rows(self.state / 'differential_tracker.csv', rows)
        with contextlib.redirect_stdout(io.StringIO()):
            d.run(self.config, backfill=True)
        updated = d.read_rows(self.state / 'differential_tracker.csv')
        self.assertEqual(updated[1]['previous_file_id'], rows[0]['file_id'])
        self.assertEqual(updated[1]['status'], 'Baseline')
        self.assertEqual(updated[2], rows[2])
        self.assertEqual(d.backfill_baselines(updated), updated)

    def test_baseline_ties_remain_baseline_without_pair(self):
        self.export('2026-10-01', 1000)
        self.export('2026-10-01', 1000)
        rows = self.scan(True)
        for row in rows:
            self.assertEqual(row['status'], 'Baseline')
            self.assertFalse(row['previous_file_id'])
            self.assertIn('tie', row['last_error'])

    def test_readable_modified_respects_daylight_saving(self):
        from datetime import datetime, timezone
        for month, expected in [(1, '2026-01-15 07:00:00 EST (UTC-0500)'),
                                (7, '2026-07-15 08:00:00 EDT (UTC-0400)')]:
            ns = int(datetime(2026, month, 15, 12, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
            self.assertEqual(d.readable_modified(ns), expected)

    def test_legacy_csv_read_and_upgrade(self):
        import csv
        self.export('2026-09-01')
        rows = self.scan(True)
        path = self.state / 'differential_tracker.csv'
        legacy = [f for f in d.FIELDS if f not in ('last_modified', 'file_name', 'previous_file_name')]
        with path.open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.DictWriter(handle, fieldnames=legacy, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        upgraded = d.read_rows(path)
        self.assertEqual(upgraded, rows)
        d.save_rows(path, upgraded)
        self.assertEqual(d.read_rows(path), rows)

    def test_full_filename_is_stored(self):
        export = self.export('2026-09-01')
        self.export('2026-10-01')
        rows = self.scan(True)
        self.assertEqual(rows[0]['file_name'], export.name)
        self.assertTrue(rows[0]['file_name'].endswith('_FULL.zip'))
        self.assertEqual(rows[0]['previous_file_name'], '')
        self.assertEqual(rows[1]['previous_file_name'], export.name)

    def test_report_name_uses_saved_pair_dates(self):
        self.export('2026-09-20')
        self.export('2026-10-18')
        rows = self.scan(True)
        self.assertEqual(d.build_report_name(rows[1]),
                         'BrandEta-ACES_Diff_2026-09-20_to_2026-10-18')
        with self.assertRaises(ValueError):
            d.build_report_name(rows[0])

    def test_deleted_file_preserves_old_pair_and_new_uses_available_history(self):
        a = self.export('2026-09-01')
        b = self.export('2026-10-01')
        rows = self.scan(True)
        b.unlink()
        self.export('2026-11-01')
        updated = self.scan()
        self.assertEqual(updated[:2], rows)
        self.assertEqual(updated[2]['previous_file_path'], str(a))

    def test_pending_missing_previous_fails_without_repairing(self):
        self.export('2026-08-01')
        previous = self.export('2026-09-01')
        self.export('2026-10-01')
        rows = self.scan(True)
        rows[2]['status'] = 'Pending'
        d.save_rows(self.state / 'differential_tracker.csv', rows)
        previous.unlink()
        updated = self.scan()
        self.assertEqual(updated[2]['status'], 'Failed')
        self.assertEqual(updated[2]['previous_file_path'], str(previous))
        self.assertIn('Previous file is missing', updated[2]['last_error'])
        self.assertEqual(updated[2]['attempt_count'], '0')
        self.assertEqual(updated[:2], rows[:2])

    def test_missing_new_file_and_manual_retry_after_restoring(self):
        self.export('2026-09-01')
        newest = self.export('2026-10-01')
        rows = self.scan(True)
        rows[1]['status'] = 'Pending'
        d.save_rows(self.state / 'differential_tracker.csv', rows)
        contents = newest.read_bytes()
        newest.unlink()
        failed = self.scan()
        self.assertEqual(failed[1]['status'], 'Failed')
        self.assertIn('New file is missing', failed[1]['last_error'])
        newest.write_bytes(contents)
        self.assertEqual(self.scan()[1]['status'], 'Failed')
        failed[1]['status'] = 'Pending'
        d.save_rows(self.state / 'differential_tracker.csv', failed)
        retried = self.scan()[1]
        self.assertEqual(retried['status'], 'Pending')
        self.assertEqual(retried['last_error'], '')

    def test_availability_error_does_not_fail_pair(self):
        from unittest.mock import patch
        self.export('2026-09-01')
        self.export('2026-10-01')
        rows = self.scan(True)
        rows[1]['status'] = 'Pending'
        with patch.object(Path, 'stat', side_effect=PermissionError('access denied')):
            with self.assertRaises(RuntimeError):
                d.validate_pending_pairs(rows)
        self.assertEqual(rows[1]['status'], 'Pending')


if __name__ == '__main__':
    unittest.main()
