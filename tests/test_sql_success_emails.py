import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import import_reports as imp
import sql_success_emails as emails
import workflow_alerts as alerts


class SQLSuccessTests(unittest.TestCase):
    def setUp(self):
        self.settings = {'to':'catalog-team@example.invalid', 'cc':'catalog-review@example.invalid; operations@example.invalid', 'bcc':'owner@example.invalid'}
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'notifications.json'
        alerts.save(self.path, {'completed':{}, 'outbox':{}})
        self.entries = [('abc', 'Sample_Diff_2026-10-01_to_2026-11-01', 'Differential_ACES_'+k, n,
                         '2026-11-19 11:00:00.000 -05:00') for n,k in enumerate(imp.KINDS)]

    def test_one_email_with_all_counts_full_list_and_no_repeat(self):
        reports = emails.reports_from_entries(self.entries)
        emails.queue(self.path,reports)
        sender = Mock()
        emails.deliver(self.path,sender,settings=self.settings)
        args = sender.call_args.args
        self.assertEqual(args[1], 'SQL Import Complete | ACES Differentials | Sample_Diff_2026-10-01_to_2026-11-01')
        self.assertEqual(args[0],self.settings['to'])
        self.assertEqual(args[3:],(self.settings['cc'],self.settings['bcc']))
        self.assertIn('<strong>28</strong>',args[2])
        self.assertIn('11:00:00 AM EST',args[2])
        for k in imp.KINDS:
            self.assertIn('dbo.Differential_ACES_'+k,args[2])
            self.assertIn('Sample_Diff_2026-10-01_to_2026-11-01_'+k+'.tsv',args[2])
        self.assertIn('This is an automated email.',args[2])
        self.assertIn('October 01, 2026',args[2])
        self.assertIn(imp.SERVER,args[2])
        self.assertNotIn('analysis',args[2])
        emails.queue(self.path,reports)
        emails.deliver(self.path,sender,settings=self.settings)
        self.assertEqual(sender.call_count,1)

    def test_failed_email_remains_pending_and_retry_does_not_duplicate_queue(self):
        reports = emails.reports_from_entries(self.entries)
        emails.queue(self.path,reports)
        with self.assertRaises(RuntimeError):
            emails.deliver(self.path,Mock(side_effect=OSError('offline')),settings=self.settings)
        emails.queue(self.path,reports)
        self.assertEqual(len(json.loads(self.path.read_text())['outbox']),1)
        sender=Mock()
        emails.deliver(self.path,sender,settings=self.settings)
        self.assertEqual(sender.call_count,1)

    def test_existing_baseline_not_emailed(self):
        alerts.save(self.path,{'completed':{'abc':'baseline'},'outbox':{}})
        emails.queue(self.path,emails.reports_from_entries(self.entries))
        sender=Mock(); emails.deliver(self.path,sender,settings=self.settings)
        sender.assert_not_called()

    def test_partial_or_conflicting_ledger_rejected(self):
        with self.assertRaises(ValueError):
            emails.reports_from_entries(self.entries[:-1])
        self.entries[0]=tuple('different' if n==1 else x for n,x in enumerate(self.entries[0]))
        with self.assertRaises(ValueError):
            emails.reports_from_entries(self.entries)

    def test_missing_state_does_not_resend_history(self):
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            emails.queue(self.path,emails.reports_from_entries(self.entries))

    def test_html_delivery_and_legacy_pending_plain_text(self):
        emails.queue(self.path,emails.reports_from_entries(self.entries))
        with patch.object(emails, 'send_html') as html_sender, patch.object(emails.email_reports, 'send_outlook') as plain_sender:
            emails.deliver(self.path,settings=self.settings)
            html_sender.assert_called_once()
            plain_sender.assert_not_called()
            alerts.save(self.path, {'completed':{}, 'outbox':{'old':{'subject':'Old queued email','body':'Plain text','attempts':0}}})
            emails.deliver(self.path,settings=self.settings)
            plain_sender.assert_called_once()

    def test_dynamic_html_content_is_escaped(self):
        report = next(iter(emails.reports_from_entries(self.entries).values()))
        report['name'] = 'Brand<&>_Diff_2026-10-01_to_2026-11-01'
        result = emails.message(report)
        self.assertIn('Brand&lt;&amp;&gt;',result['body'])
        self.assertNotIn('Brand<&>',result['body'])


if __name__ == '__main__':
    unittest.main()
