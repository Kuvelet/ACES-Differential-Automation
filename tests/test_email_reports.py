import json
import unittest

import detector as d
import email_reports as email
import test_pim_automation


class EmailReportsTests(unittest.TestCase):
    def setUp(self):
        self.data = test_pim_automation.SubmissionTests()
        self.data.setUp()
        self.addCleanup(self.data.doCleanups)
        self.config = self.data.folder / 'config.json'
        self.config.write_text(json.dumps({'state_folder': '.', 'email': {
            'enabled': True, 'to': 'test@example.invalid',
            'cc': 'copy@example.invalid; second@example.invalid',
            'bcc': 'owner@example.invalid', 'error_to': 'owner@example.invalid'}}))
        self.rows = self.data.rows
        self.rows[1].update(status='Succeeded', report_name=d.build_report_name(self.rows[1]),
                            submitted_at='2026-10-27T21:15:48+00:00',
                            confirmation='Task — Added ' + d.build_report_name(self.rows[1]))
        d.save_rows(self.data.path, self.rows)
        self.sent = []

    def sender(self, *args):
        self.sent.append(args)

    def test_sends_once_and_preserves_submission_state(self):
        self.assertTrue(email.send_run_report(self.config, sender=self.sender))
        self.assertFalse(email.send_run_report(self.config, sender=self.sender))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], 'test@example.invalid')
        self.assertEqual(self.sent[0][3:], ('copy@example.invalid; second@example.invalid', 'owner@example.invalid'))
        self.assertEqual(self.sent[0][1], 'PIM Submission Successful | ACES Differentials | ' + self.rows[1]['report_name'])
        self.assertTrue(self.sent[0][2].startswith('This is an automated message.'))
        self.assertIn(self.rows[1]['previous_file_path'], self.sent[0][2])
        self.assertIn(self.rows[1]['new_file_path'], self.sent[0][2])
        self.assertIn('2026-10-27 17:15:48 EDT', self.sent[0][2])
        after = d.read_rows(self.data.path)
        self.assertEqual(after[1]['status'], 'Succeeded')
        self.assertTrue(after[1]['email_sent_at'])

    def test_email_failure_retries_without_resetting_pim(self):
        def fail(*args):
            raise RuntimeError('Outlook unavailable')
        with self.assertRaises(RuntimeError):
            email.send_run_report(self.config, sender=fail)
        after = d.read_rows(self.data.path)
        self.assertEqual(after[1]['status'], 'Succeeded')
        self.assertFalse(after[1]['email_sent_signature'])
        self.assertTrue(after[1]['email_error'])
        self.assertTrue(email.send_run_report(self.config, sender=self.sender))
        self.assertEqual(d.read_rows(self.data.path)[1]['email_error'], '')

    def test_new_attempt_reports_but_unchanged_needsreview_is_quiet(self):
        self.rows[1]['status'] = 'NeedsReview'
        d.save_rows(self.data.path, self.rows)
        email.send_run_report(self.config, sender=self.sender)
        self.assertEqual(self.sent[-1][1], 'PIM ACES Differential: Needs Review - ' + self.rows[1]['report_name'])
        self.assertIn('will not be retried automatically', self.sent[-1][2])
        self.assertEqual((self.sent[-1][0], *self.sent[-1][3:]), ('owner@example.invalid', '', ''))
        self.assertFalse(email.send_run_report(self.config, sender=self.sender))
        rows = d.read_rows(self.data.path)
        rows[1].update(status='Failed', attempt_count='2', last_attempt_at='2026-10-27T20:00:00+00:00')
        d.save_rows(self.data.path, rows)
        self.assertTrue(email.send_run_report(self.config, sender=self.sender))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[-1][1], 'PIM ACES Differential: Failed - ' + self.rows[1]['report_name'])
        self.assertIn('retried on the next run', self.sent[-1][2])
        self.assertEqual((self.sent[-1][0], *self.sent[-1][3:]), ('owner@example.invalid', '', ''))

    def test_run_error_is_reported_without_changing_pairs(self):
        email.send_run_report(self.config, sender=self.sender)
        self.assertTrue(email.send_run_report(self.config, run_error='Share unavailable', sender=self.sender))
        self.assertIn('Share unavailable', self.sent[-1][2])
        self.assertEqual((self.sent[-1][0], *self.sent[-1][3:]), ('owner@example.invalid', '', ''))

    def test_multiple_unsent_results_send_separate_messages(self):
        self.rows[2].update(status='Failed', report_name=d.build_report_name(self.rows[2]))
        d.save_rows(self.data.path, self.rows)
        email.send_run_report(self.config, sender=self.sender)
        self.assertEqual(len(self.sent), 2)
        self.assertIn(self.rows[1]['report_name'], self.sent[0][1])
        self.assertNotIn(self.rows[2]['file_name'], self.sent[0][2])
        self.assertIn(self.rows[2]['report_name'], self.sent[1][1])

    def test_end_of_run_skips_already_attempted_notifications(self):
        self.assertFalse(email.send_run_report(self.config, sender=self.sender,
            exclude_file_ids={self.rows[1]['file_id']}))
        self.assertEqual(self.sent, [])


if __name__ == '__main__':
    unittest.main()
