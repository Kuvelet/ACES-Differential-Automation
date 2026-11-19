"""Local fixtures and an in-memory SQL double only. Never connects to SQL Server."""
import copy
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import uuid
from zoneinfo import ZoneInfo

import detector as d
import import_reports as imp


class Database:
    def __init__(self, contract):
        self.contract = contract
        self.data = {kind: [] for kind in imp.KINDS}
        self.log = []
        self.calls = []
        self.fail_kind = None
        self.lock_result = 0
        self.commit_disconnect = False
        self.identity = ('CATALOGSQL', 'CATALOG', imp.DATABASE)
        self.has_log = True

    def close(self):
        pass

    def cursor(self):
        return Cursor(self)


class Cursor:
    def __init__(self, db):
        self.db = db
        self.results = []

    def fetchone(self):
        return self.results[0]

    def fetchall(self):
        return self.results

    def close(self):
        pass

    def execute(self, sql, *args):
        db = self.db
        db.calls.append((sql, args))
        self.results = []
        if 'BEGIN TRANSACTION' in sql:
            db.working_data, db.working_log = copy.deepcopy((db.data, db.log))
        elif 'sp_getapplock' in sql:
            self.results = [(db.lock_result,)]
        elif "SERVERPROPERTY('MachineName')" in sql:
            self.results = [db.identity]
        elif 'SELECT OBJECT_ID' in sql:
            self.results = [(42 if db.has_log else None,)]
        elif 'FROM sys.columns' in sql:
            kind = args[0].split('Differential_ACES_')[1]
            for index, (name, typ) in enumerate(imp.META + db.contract[kind]):
                base = typ.split('(')[0].lower()
                width = -1
                if base == 'nvarchar' and 'MAX' not in typ:
                    width = int(re.search(r'\((\d+)\)', typ).group(1)) * 2
                self.results.append((name, base, width, 3 if base == 'datetimeoffset' else 0,
                                     index >= 8, index == 0))
        elif sql.startswith('SELECT TableName'):
            # Style 127 normalizes SQL datetimeoffset to UTC; 121 preserves its offset.
            assert 'CONVERT(varchar(40),ImportedAt,121)' in sql
            for x in db.working_log:
                if x[0] == args[0] or x[2] == args[1]:
                    stamp = x[9].replace('T', ' ')
                    stamp = stamp[:-6] + ' ' + stamp[-6:]
                    self.results.append((x[1], x[6], x[7], x[8], x[3], x[4], x[5], stamp, x[0], x[2]))
        elif sql.startswith('SELECT COUNT_BIG'):
            kind = re.search(r'\[Differential_ACES_(\w+)\]', sql).group(1)
            records = [x for x in db.working_data[kind] if x[0] == args[0]]
            if 'MIN(' in sql:
                numbers = [x[5] for x in records]
                self.results = [(len(records), min(numbers, default=None), max(numbers, default=None))]
            else:
                self.results = [(len(records),)]
        elif sql.startswith('INSERT INTO [dbo].[Differential_ACES_ImportLog]'):
            db.working_log.append(args)
        elif sql.startswith('INSERT INTO'):
            kind = re.search(r'\[Differential_ACES_(\w+)\]', sql).group(1)
            if kind == db.fail_kind:
                raise RuntimeError('Simulated insert failure')
            assert 'OPENJSON(CAST(? AS NVARCHAR(MAX))) WITH (' in sql
            assert len(args) == 1
            records = json.loads(args[0])
            assert all(len(record) == 7 + len(db.contract[kind]) for record in records)
            db.working_data[kind].extend(records)
        elif sql == 'COMMIT TRANSACTION;':
            db.data, db.log = db.working_data, db.working_log
            if db.commit_disconnect:
                db.commit_disconnect = False
                raise RuntimeError('Simulated lost commit response')
        elif 'ROLLBACK TRANSACTION' in sql:
            db.working_data, db.working_log = copy.deepcopy((db.data, db.log))
        else:
            raise AssertionError(sql)
        return self


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.contract = imp.schema()
        self.id = str(uuid.uuid4())
        self.keyword = 'Example Catalog_BrandAlpha-ACES'
        self.report = 'BrandAlpha-ACES_Diff_2026-10-09_to_2026-11-13'
        self.base = self.root / 'exports'
        self.folder = self.base / 'BrandAlpha-ACES' / '2026-10-09_to_2026-11-13'
        self.extracted = self.folder / ('123~' + self.report + '_ACESDifferential')
        self.extracted.mkdir(parents=True)
        self.extracted.with_suffix('.zip').touch()
        self.row = dict(file_id=self.id, keyword=self.keyword, report_name=self.report,
                        status='Succeeded', collection_status='Copied', collection_destination=str(self.folder),
                        previous_file_path=f'{uuid.uuid4()}~{self.keyword}_2026-10-09_FULL.zip',
                        new_file_path=f'{self.id}~{self.keyword}_2026-11-13_FULL.zip')
        for kind in imp.KINDS:
            self.write(kind, 70 if kind == 'AddedApps' else 1)
        self.workspace = self.root / 'staging'
        self.workspace.mkdir()

    def write(self, kind, count=1, changes=None):
        path = self.extracted / f'{self.report}_{kind}.tsv'
        with path.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f, delimiter='\t')
            writer.writerow([name for name, _ in self.contract[kind]])
            for i in range(count):
                values = {'AppID': str(i + 1), 'From Year': '2026', 'To Year': '2026',
                          'Quantity': '2', 'Part': '00123', 'Part Number': '00123',
                          'Notes': 'Café\tquoted\nline  ', 'Application Differential': '104.40%'}
                values.update(changes or {})
                writer.writerow([values.get(name, '') for name, _ in self.contract[kind]])
        return path

    def plan(self):
        return imp.prepare_report(self.row, self.base, self.workspace, self.contract)

    def test_all_files_metadata_and_duplicate_skip(self):
        self.write('AddedParts', 0)
        plan = self.plan()
        db = Database(self.contract)
        timestamp, skipped = imp.import_plan(db, plan, self.contract)
        self.assertFalse(skipped)
        self.assertEqual(len(db.log), 8)
        self.assertEqual(db.data['AddedParts'], [])
        self.assertEqual(len(db.data['AddedApps']), 70)
        first = db.data['AddedApps'][0]
        self.assertEqual(first[:6], [self.id, 'BrandAlpha-ACES', '2026-10-09', '2026-11-13',
                                    self.report + '_AddedApps.tsv', 1])
        self.assertEqual(first[6], timestamp)
        values = dict(zip([n for n, _ in self.contract['AddedApps']], first[7:]))
        self.assertEqual(values['Part'], '00123')
        self.assertEqual(values['Notes'], 'Café\tquoted\nline  ')
        self.assertEqual(values['AppID'], 1)
        self.assertEqual(values['BodyNumDoors'], '')
        self.assertIsNotNone(datetime.fromisoformat(timestamp).utcoffset())
        inserts = sum(s.startswith('INSERT') for s, _ in db.calls)
        self.assertEqual(imp.import_plan(db, plan, self.contract), (timestamp, True))
        self.assertEqual(sum(s.startswith('INSERT') for s, _ in db.calls), inserts)

    def test_changed_source_rejected(self):
        db = Database(self.contract)
        imp.import_plan(db, self.plan(), self.contract)
        before = copy.deepcopy(db.data)
        self.write('UniqueNotes', changes={'Note': 'changed'})
        with self.assertRaisesRegex(ValueError, 'changed'):
            imp.import_plan(db, self.plan(), self.contract)
        self.assertEqual(db.data, before)

    def test_late_failure_rolls_back_all_eight_files(self):
        db = Database(self.contract)
        db.fail_kind = 'VCDBNotes'
        with self.assertRaisesRegex(RuntimeError, 'insert failure'):
            imp.import_plan(db, self.plan(), self.contract)
        self.assertEqual(db.log, [])
        self.assertTrue(all(not rows for rows in db.data.values()))

    def test_lost_commit_response_retry_does_not_duplicate(self):
        db = Database(self.contract)
        db.commit_disconnect = True
        plan = self.plan()
        with self.assertRaisesRegex(RuntimeError, 'commit response'):
            imp.import_plan(db, plan, self.contract)
        self.assertEqual(len(db.log), 8)
        self.assertTrue(imp.import_plan(db, plan, self.contract)[1])
        self.assertEqual(len(db.data['AddedApps']), 70)

    def test_partial_ledger_and_unlogged_rows_rejected(self):
        plan = self.plan()
        db = Database(self.contract)
        imp.import_plan(db, plan, self.contract)
        db.log.pop()
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            imp.import_plan(db, plan, self.contract)
        db.log.clear()
        with self.assertRaisesRegex(ValueError, 'existing rows'):
            imp.import_plan(db, plan, self.contract)

    def test_concurrent_import_is_deferred(self):
        db = Database(self.contract)
        db.lock_result = -1
        with self.assertRaisesRegex(RuntimeError, 'Another SQL import'):
            imp.import_plan(db, self.plan(), self.contract)
        self.assertEqual(db.log, [])

    def test_invalid_numeric_and_length_values(self):
        for value, typ in [('2147483648', 'INT'), ('32768', 'SMALLINT'), ('1.2', 'INT'), ('text', 'INT')]:
            with self.assertRaises(ValueError):
                imp.convert(value, typ)
        self.assertIsNone(imp.convert('', 'INT'))
        self.assertEqual(imp.convert('0', 'INT'), 0)
        with self.assertRaises(ValueError):
            imp.convert('x' * 101, 'NVARCHAR(100)')
        with self.assertRaises(ValueError):
            imp.convert('😀' * 51, 'NVARCHAR(100)')
        self.assertEqual(imp.convert('x' * 5000, 'NVARCHAR(MAX)'), 'x' * 5000)

    def test_bad_source_validation_before_database_work(self):
        self.write('AddedApps', changes={'AppID': 'invalid'})
        with self.assertRaisesRegex(ValueError, 'AppID'):
            self.plan()
        self.write('AddedApps')
        self.write('UniqueNotes').write_text('Wrong header\nvalue\n')
        with self.assertRaisesRegex(ValueError, 'headers'):
            self.plan()

    def test_json_batches_preserve_long_unicode_and_nullable_numbers(self):
        text = '😀' * 2500 + ' trailing  '
        self.write('AddedApps', 2, changes={'AppID': '', 'Notes': text})
        db = Database(self.contract)
        imp.import_plan(db, self.plan(), self.contract)
        values = dict(zip([n for n, _ in self.contract['AddedApps']], db.data['AddedApps'][0][7:]))
        self.assertIsNone(values['AppID'])
        self.assertEqual(values['Notes'], text)

    def test_missing_file_and_wrong_destination(self):
        self.write('VCDBNotes').unlink()
        with self.assertRaises(FileNotFoundError):
            self.plan()
        self.row['collection_destination'] = str(self.root)
        with self.assertRaisesRegex(ValueError, 'destination'):
            self.plan()

    def test_paused_run_never_connects_or_reads_tracker(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps({'sql_import': {'enabled': False}}))
        with patch.object(imp, 'connect', side_effect=AssertionError('No connection')):
            with patch.object(d, 'read_rows', side_effect=AssertionError('No tracker read')):
                self.assertEqual(imp.run(config), 0)

    def test_eastern_offset_rules(self):
        winter = datetime(2026, 11, 16, 13, tzinfo=ZoneInfo('America/New_York'))
        summer = datetime(2026, 7, 16, 13, tzinfo=ZoneInfo('America/New_York'))
        self.assertTrue(winter.isoformat().endswith('-05:00'))
        self.assertTrue(summer.isoformat().endswith('-04:00'))

    def local_config(self):
        (self.root / '.detector_initialized').touch()
        config = self.root / 'config.json'
        config.write_text(json.dumps({'state_folder': str(self.root),
                                     'collection': {'destination_folder': str(self.base)},
                                     'sql_import': {'enabled': False}}))
        return config

    def test_validate_only_never_connects_or_saves(self):
        with patch.object(d, 'read_rows', return_value=[self.row]), \
             patch.object(d, 'save_rows', side_effect=AssertionError('No save')), \
             patch.object(imp, 'connect', side_effect=AssertionError('No connection')):
            self.assertEqual(imp.run(self.local_config(), validate_only=True), 0)

    def test_csv_save_failure_after_commit_is_recoverable(self):
        db = Database(self.contract)
        with patch.object(d, 'read_rows', return_value=[self.row]), \
             patch.object(imp, 'connect', return_value=db), \
             patch.object(d, 'save_rows', side_effect=OSError('CSV locked')):
            with self.assertRaisesRegex(OSError, 'CSV locked'):
                imp.run(self.local_config(), execute_once=True)
        self.assertEqual(len(db.log), 8)
        with patch.object(d, 'read_rows', return_value=[self.row]), \
             patch.object(imp, 'connect', return_value=db), patch.object(d, 'save_rows') as save:
            self.assertEqual(imp.run(self.local_config(), execute_once=True), 0)
            save.assert_called_once()
        self.assertEqual(len(db.data['AddedApps']), 70)
        self.assertEqual(self.row['sql_import_status'], 'Imported')

    def test_wrong_server_missing_log_and_schema_drift(self):
        plan = self.plan()
        db = Database(copy.deepcopy(self.contract))
        db.identity = ('OTHER', 'CATALOG', imp.DATABASE)
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            imp.import_plan(db, plan, self.contract)
        db.identity = ('CATALOGSQL', 'CATALOG', imp.DATABASE)
        db.has_log = False
        with self.assertRaisesRegex(ValueError, 'log is missing'):
            imp.import_plan(db, plan, self.contract)
        db.has_log = True
        db.contract['AddedApps'][0][1] = 'NVARCHAR(MAX)'
        with self.assertRaisesRegex(ValueError, 'database type'):
            imp.import_plan(db, plan, self.contract)
        self.assertEqual(db.log, [])

    def test_same_report_with_different_uuid_rejected(self):
        db = Database(self.contract)
        plan = self.plan()
        imp.import_plan(db, plan, self.contract)
        plan['report_id'] = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            imp.import_plan(db, plan, self.contract)
        self.assertEqual(len(db.log), 8)

    def test_source_change_during_snapshot_rejected(self):
        with patch.object(imp.collection, 'fingerprint', side_effect=[(100, 1), (100, 2)]):
            with self.assertRaisesRegex(ValueError, 'source changed'):
                self.plan()

    def test_contract_matches_created_table_script(self):
        sql = (d.ROOT / 'sql' / '001_create_differential_aces_tables.sql').read_text()
        blocks = re.findall(r'CREATE TABLE \[dbo\]\.\[Differential_ACES_(\w+)\]\s*\((.*?)\n    \);', sql, re.S)
        self.assertEqual(len(blocks), 8)
        for kind, body in blocks:
            original = [[n, t] for n, t in re.findall(r'^        \[([^]]+)\] (.+?) NULL,?$', body, re.M)
                        if not n.startswith('Meta_')]
            self.assertEqual(original, self.contract[kind])


if __name__ == '__main__':
    unittest.main()
