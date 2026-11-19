"""Isolated workflow tests: no production SQL, PIM or Outlook calls."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import detector as d
import import_reports as imp
import workflow_alerts as a
import workflow_runner as w
import test_import_reports as fixtures
import test_pim_automation as pim_fixtures
from process_guard import Job


class AlertsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.config = {'workflow': {'failure_recipient': 'owner@example.invalid', 'reminder_hour_eastern': 9}}
        self.clock = datetime(2026, 11, 19, 14, tzinfo=a.EASTERN)
        self.item = a.issue('import', 'report1', 'Sample', 'SQL offline', 'Retry next run.')

    def sync(self, items, clock=None, **kwargs):
        return a.reconcile(self.state, self.config, items, clock or self.clock, **kwargs)

    def test_initial_suppressed_repeat_daily_recovery_and_owner_only(self):
        self.assertEqual(len(self.sync([self.item])['outbox']), 1)
        sender = Mock()
        a.deliver(self.state, self.config, sender)
        self.assertEqual(sender.call_args.args[0], 'owner@example.invalid')
        self.assertEqual(sender.call_args.args[3:], ('', ''))
        self.assertEqual(self.sync([self.item], self.clock + timedelta(minutes=30))['outbox'], [])
        tomorrow = self.clock.replace(hour=8) + timedelta(days=1)
        self.assertEqual(self.sync([self.item], tomorrow)['outbox'], [])
        self.assertEqual(len(self.sync([self.item], tomorrow.replace(hour=9))['outbox']), 1)
        a.deliver(self.state, self.config, sender)
        self.assertEqual(self.sync([self.item], tomorrow.replace(hour=10))['outbox'], [])
        data = self.sync([], tomorrow.replace(hour=11))
        self.assertEqual(data['issues'], {})
        self.assertIn('Recovered', data['outbox'][0]['subject'])

    def test_email_failure_persists_and_retries_only_email(self):
        self.sync([self.item])
        with self.assertRaises(RuntimeError):
            a.deliver(self.state, self.config, Mock(side_effect=OSError('offline')))
        self.assertEqual(len(a.load(self.state/'workflow_alerts.json')['outbox']), 1)
        sender = Mock()
        a.deliver(self.state, self.config, sender)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(a.load(self.state/'workflow_alerts.json')['outbox'], [])

    def test_partial_observation_does_not_claim_recovery(self):
        self.sync([self.item])
        data = self.sync([], complete=False)
        self.assertIn(self.item['key'], data['issues'])
        self.assertEqual(len(data['outbox']), 1)

    def test_changed_problem_alerts_again(self):
        self.sync([self.item])
        item = dict(self.item, error='Invalid column')
        self.assertEqual(len(self.sync([item])['outbox']), 2)

    def test_wrong_recipient_fails_closed(self):
        self.config['workflow']['failure_recipient'] = 'someone@example.com'
        sender = Mock()
        with self.assertRaises(ValueError):
            a.deliver(self.state, self.config, sender)
        sender.assert_not_called()

    def test_missing_outputs_only_alert_after_threshold(self):
        row = dict(file_id='1', keyword='sample', status='Succeeded', collection_status='Waiting', submitted_at=self.clock.isoformat())
        self.assertEqual(a.row_issues([row], self.config, self.clock), [])
        self.assertEqual(a.row_issues([row], self.config, self.clock+timedelta(hours=25))[0]['stage'], 'collection')


class RunnerTests(unittest.TestCase):
    def setUp(self):
        AlertsTests.setUp(self)
        self.config['state_folder'] = str(self.state)
        self.config['workflow'].update(enabled=True, stage_timeout_seconds={s: 2 for s in w.STAGES})
        self.path = self.state/'config.json'
        self.path.write_text(json.dumps(self.config))

    def test_order_and_continue_after_submission_failure(self):
        calls = []
        def execute(stage, *args):
            calls.append(stage)
            return 1 if stage == 'submit' else 0
        with patch.object(w, 'tracker', return_value=[]):
            self.assertEqual(w.run(self.path, execute), 2)
        self.assertEqual(calls, list(w.STAGES)+['notify_failures'])
        data = a.load(self.state/'workflow_alerts.json')
        self.assertIn('submit:run', data['issues'])

    def test_tracker_failure_stops_processing_but_attempts_alert(self):
        execute = Mock(return_value=0)
        with patch.object(w, 'tracker', side_effect=OSError('tracker inaccessible')):
            self.assertEqual(w.run(self.path, execute), 2)
        self.assertEqual([c.args[0] for c in execute.call_args_list], ['notify_failures'])

    def test_overlapping_workflow_is_skipped(self):
        execute = Mock()
        with d.RunLock(self.state/'.workflow.lock'):
            self.assertEqual(w.run(self.path, execute), 0)
        execute.assert_not_called()

    def test_disabled_candidate_does_nothing(self):
        self.config['workflow']['enabled'] = False
        self.path.write_text(json.dumps(self.config))
        execute = Mock()
        self.assertEqual(w.run(self.path, execute), 0)
        execute.assert_not_called()

    def test_timeout_kills_owned_tree_before_continuing(self):
        process = Mock(pid=12345)
        process.wait.side_effect = [subprocess.TimeoutExpired('test', 2), 1]
        with patch.object(w, 'Job'), patch.object(w.subprocess, 'Popen', return_value=process), patch.object(w.subprocess, 'run', return_value=Mock(returncode=0)) as kill:
            self.assertEqual(w.run_child('submit', self.path, self.state, 2, self.state/'stage.log'), 124)
        self.assertEqual(kill.call_args.args[0], ['taskkill', '/PID', '12345', '/T', '/F'])

    def test_real_child_stages_with_isolated_empty_workspace(self):
        source = self.state/'source'
        source.mkdir()
        self.config.update(source_folder=str(source), keywords=[], stability_seconds=0,
                           collection={'enabled':False}, sql_import={'enabled':False}, email={'enabled':False})
        self.config['workflow']['stage_timeout_seconds'] = {s:30 for s in w.STAGES}
        self.path.write_text(json.dumps(self.config))
        d.save_rows(self.state/'differential_tracker.csv', [])
        (self.state/'.detector_initialized').touch()
        self.assertEqual(w.run(self.path), 0)
        summary = json.loads(next((self.state/'logs').glob('*summary.json')).read_text())
        self.assertTrue(all(x['code'] == 0 for x in summary['results']))

    def test_windows_job_close_terminates_owned_process(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            job = Job(process)
            job.close()
            process.wait(timeout=10)
            self.assertIsNotNone(process.returncode)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


class PersistenceTests(unittest.TestCase):
    def test_tracker_failed_replace_preserves_original_and_backup(self):
        fixture = pim_fixtures.SubmissionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'differential_tracker.csv'
            d.save_rows(path, fixture.rows)
            original = path.read_bytes()
            replace = d.os.replace
            def fail_tracker(source, dest):
                if Path(dest) == path:
                    raise OSError('Simulated interrupted tracker replacement')
                return replace(source, dest)
            with patch.object(d.os, 'replace', side_effect=fail_tracker):
                with self.assertRaises(OSError):
                    d.save_rows(path, fixture.rows)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(path.with_suffix('.csv.bak').read_bytes(), original)


class FastSkipTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ImportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.row = self.fixture.row
        self.stamp = '2026-11-19 09:00:00.000 -05:00'
        self.entries = [('Differential_ACES_'+k, self.row['file_id'], self.row['report_name'], 'BrandAlpha-ACES', '2026-10-09', '2026-11-13', self.row['report_name']+'_'+k+'.tsv', 'a'*64, 0, self.stamp) for k in imp.KINDS]

    def connection(self):
        cursor = Mock()
        cursor.execute.return_value = cursor
        cursor.fetchone.return_value = ('CATALOGSQL', 'CATALOG', imp.DATABASE)
        cursor.fetchall.return_value = self.entries
        connection = Mock()
        connection.cursor.return_value = cursor
        return connection

    def test_completed_ledger_never_reads_files(self):
        with patch.object(imp, 'prepare_report', side_effect=AssertionError('TSV opened')):
            self.assertEqual(imp.completed_report(self.connection(), self.row), '2026-11-19T09:00:00.000-05:00')

    def test_missing_ledger_is_new(self):
        self.entries = []
        self.assertIsNone(imp.completed_report(self.connection(), self.row))

    def test_partial_ledger_held(self):
        self.entries.pop()
        with self.assertRaises(ValueError):
            imp.completed_report(self.connection(), self.row)

    def test_identity_mismatch_held(self):
        self.entries[0] = tuple('wrong' if i == 2 else x for i,x in enumerate(self.entries[0]))
        with self.assertRaises(ValueError):
            imp.completed_report(self.connection(), self.row)

    def test_actual_run_skips_preparation_and_repairs_tracker(self):
        f = self.fixture
        cfg = {'state_folder': str(f.root), 'sql_import': {'enabled': True, 'skip_completed': True}, 'collection': {'destination_folder': str(f.base)}}
        path = f.root/'config.json'
        path.write_text(json.dumps(cfg))
        (f.root/'.detector_initialized').touch()
        self.row['sql_import_status'] = 'Failed'
        with patch.object(d, 'read_rows', return_value=[self.row]), patch.object(d, 'save_rows') as save, patch.object(imp, 'connect', return_value=self.connection()), patch.object(imp, 'prepare_report', side_effect=AssertionError('TSV opened')):
            self.assertEqual(imp.run(path), 0)
            self.assertEqual(self.row['sql_import_status'], 'Imported')
            save.assert_called_once()

    def test_multiple_completed_reports_use_one_query_and_no_tracker_save(self):
        import copy
        import uuid
        f = self.fixture
        second = copy.deepcopy(self.row)
        second_id = str(uuid.uuid4())
        second.update(file_id=second_id,
                      report_name=self.row['report_name'].replace('2026-11-13','2026-12-13'),
                      new_file_path=self.row['new_file_path'].replace(self.row['file_id'],second_id).replace('2026-11-13','2026-12-13'))
        self.entries += [(e[0],second_id,second['report_name'],e[3],e[4],'2026-12-13',e[6].replace('2026-11-13','2026-12-13'),e[7],e[8],e[9]) for e in list(self.entries)]
        rows=[self.row, second]
        for row in rows:
            row.update(sql_import_status='Imported',sql_imported_at='2026-11-19T09:00:00.000-05:00',sql_import_error='')
        path=f.root/'config.json'
        path.write_text(json.dumps({'state_folder':str(f.root),'sql_import':{'enabled':True,'skip_completed':True},'collection':{'destination_folder':str(f.base)}}))
        (f.root/'.detector_initialized').touch()
        connection=self.connection()
        with patch.object(d,'read_rows',return_value=rows), patch.object(d,'save_rows') as save, patch.object(imp,'connect',return_value=connection) as connect, patch.object(imp,'prepare_report',side_effect=AssertionError('TSV opened')):
            self.assertEqual(imp.run(path),0)
        connect.assert_called_once()
        save.assert_not_called()
        selects=[c for c in connection.cursor.return_value.execute.call_args_list if 'FROM [dbo].[Differential_ACES_ImportLog]' in c.args[0]]
        self.assertEqual(len(selects),1)


if __name__ == '__main__':
    unittest.main()
