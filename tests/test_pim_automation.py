import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

import detector as d
import pim_automation as pim


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.test-', dir=d.ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        exports = []
        for day in ('2026-09-20', '2026-10-18', '2026-11-18'):
            file = self.folder / f'{uuid.uuid4()}~Example Catalog_BrandGamma-ACES_{day}_FULL.zip'
            with zipfile.ZipFile(file, 'w') as archive:
                archive.writestr('export.xml', '<ACES/>')
            exports.append(d.parse_export(file, ['Example Catalog_BrandGamma-ACES']))
        self.rows = d.prepare_rows([], exports, baseline=True)
        self.rows[1]['status'] = 'Pending'
        self.path = self.folder / 'differential_tracker.csv'
        d.save_rows(self.path, self.rows)

    def process(self, submit, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            pim.process_rows(self.rows, self.path, submit, stability_seconds=0, **kwargs)
        return d.read_rows(self.path)

    def success(self, row, before_compare):
        before_compare()
        persisted = d.read_rows(self.path)[1]
        self.assertEqual(persisted['status'], 'NeedsReview')
        return f'Task — Added {d.build_report_name(row)}'

    def test_confirmed_submission_and_no_repeat(self):
        rows = self.process(self.success)
        self.assertEqual(rows[1]['status'], 'Succeeded')
        self.assertTrue(rows[1]['submitted_at'])
        self.assertEqual(rows[1]['attempt_count'], '1')
        self.process(lambda *_: self.fail('Succeeded pair was retried'))

    def test_preclick_failure_does_not_block_next_pair(self):
        self.rows[2]['status'] = 'Pending'
        def submit(row, callback):
            if row['file_id'] == self.rows[1]['file_id']:
                raise RuntimeError('Upload failed')
            callback()
            return f'Task — Added {d.build_report_name(row)}'
        rows = self.process(submit)
        self.assertEqual(rows[1]['status'], 'Failed')
        self.assertEqual(rows[2]['status'], 'Succeeded')

    def test_missing_confirmation_needs_review_without_retry(self):
        def submit(row, callback):
            callback()
            raise TimeoutError('Confirmation not observed')
        rows = self.process(submit)
        self.assertEqual(rows[1]['status'], 'NeedsReview')
        self.process(lambda *_: self.fail('Uncertain pair was retried'))

    def test_wrong_report_confirmation_is_not_success(self):
        def submit(row, callback):
            callback()
            return 'Task — Added unrelated report'
        self.assertEqual(self.process(submit)[1]['status'], 'NeedsReview')

    def test_screenshot_failure_cannot_undo_success(self):
        def evidence(row):
            raise RuntimeError('Screenshot timed out')
        self.assertEqual(self.process(self.success, evidence=evidence)[1]['status'], 'Succeeded')

    def test_missing_input_never_submits_or_increments(self):
        Path(self.rows[1]['previous_file_path']).unlink()
        rows = self.process(lambda *_: self.fail('Missing input submitted'))
        self.assertEqual(rows[1]['status'], 'Failed')
        self.assertEqual(rows[1]['attempt_count'], '0')

    def test_interrupted_running_is_not_retried(self):
        self.rows[1]['status'] = 'Running'
        self.assertEqual(self.process(lambda *_: self.fail('Interrupted pair retried'))[1]['status'], 'NeedsReview')

    def test_filter_preserves_other_pending_rows(self):
        self.rows[2]['status'] = 'Pending'
        rows = self.process(self.success, only_file_id=self.rows[1]['file_id'])
        self.assertEqual(rows[2]['status'], 'Pending')
        self.assertEqual(rows[2]['attempt_count'], '0')

    def test_csv_write_failure_prevents_submission(self):
        with patch.object(d, 'save_rows', side_effect=PermissionError('CSV open')):
            with self.assertRaises(PermissionError):
                self.process(lambda *_: self.fail('Submitted without persistence'))

    def test_failed_retries_once_on_next_run(self):
        calls = []
        def fail(row, callback):
            calls.append(row['file_id'])
            raise RuntimeError('Browser crashed before Compare')
        self.assertEqual(self.process(fail)[1]['status'], 'Failed')
        self.assertEqual(len(calls), 1)
        rows = self.process(self.success)
        self.assertEqual(rows[1]['status'], 'Succeeded')
        self.assertEqual(rows[1]['attempt_count'], '2')

    def test_failed_retry_does_not_touch_needs_review(self):
        self.rows[1]['status'] = 'Failed'
        self.rows[2]['status'] = 'NeedsReview'
        rows = self.process(self.success)
        self.assertEqual(rows[1]['status'], 'Succeeded')
        self.assertEqual(rows[2]['status'], 'NeedsReview')
        self.assertEqual(rows[2]['attempt_count'], '0')

    def test_each_pair_notifies_before_next_submission(self):
        self.rows[2]['status'] = 'Pending'
        events = []
        def submit(row, callback):
            events.append(('submit', row['file_id']))
            callback()
            return f'Task — Added {d.build_report_name(row)}'
        def notify(row):
            saved = next(r for r in d.read_rows(self.path) if r['file_id'] == row['file_id'])
            self.assertEqual(saved['status'], 'Succeeded')
            events.append(('email', row['file_id']))
        self.process(submit, notify=notify)
        self.assertEqual(events, [('submit', self.rows[1]['file_id']), ('email', self.rows[1]['file_id']),
                                  ('submit', self.rows[2]['file_id']), ('email', self.rows[2]['file_id'])])

    def test_notification_failure_does_not_block_next_pair(self):
        self.rows[2]['status'] = 'Pending'
        def submit(row, callback):
            callback()
            return f'Task — Added {d.build_report_name(row)}'
        def notify(row):
            raise RuntimeError('Outlook unavailable')
        rows = self.process(submit, notify=notify)
        self.assertEqual([r['status'] for r in rows[1:]], ['Succeeded', 'Succeeded'])

    def test_missing_input_notifies_immediately(self):
        Path(self.rows[1]['previous_file_path']).unlink()
        events = []
        self.process(lambda *_: self.fail('Missing input submitted'), notify=lambda r: events.append(r['status']))
        self.assertEqual(events, ['Failed'])


if __name__ == '__main__':
    unittest.main()
