"""Validate and append collected TSV reports to SQL Server. Disabled by default."""
import argparse
import csv
from datetime import date, datetime
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid
from zoneinfo import ZoneInfo

import collect_reports as collection
import detector as d

KINDS = ('AddedApps', 'DeletedApps', 'AddedParts', 'DeletedParts', 'AllParts',
         'AnalysisParts', 'UniqueNotes', 'VCDBNotes')
META = [('Meta_RowID', 'BIGINT'), ('Meta_ReportID', 'UNIQUEIDENTIFIER'),
        ('Meta_Keyword', 'NVARCHAR(200)'), ('Meta_PreviousExportDate', 'DATE'),
        ('Meta_CurrentExportDate', 'DATE'), ('Meta_SourceFileName', 'NVARCHAR(255)'),
        ('Meta_SourceRowNumber', 'BIGINT'), ('Meta_ImportedAt', 'DATETIMEOFFSET(3)')]
SERVER = r'CATALOGSQL\CATALOG'
DATABASE = 'CatalogWarehouse'
LOG = '[dbo].[Differential_ACES_ImportLog]'


def schema():
    result = json.loads((d.ROOT / 'sql' / 'tsv_schema.json').read_text(encoding='utf-8'))
    if set(result) != set(KINDS):
        raise ValueError('Unexpected TSV schema contract.')
    return result


def identifier(name):
    return '[' + name.replace(']', ']]') + ']'


def table(kind):
    if kind not in KINDS:
        raise ValueError('Unknown report type.')
    return '[dbo].' + identifier('Differential_ACES_' + kind)


def bounded(value, limit, label):
    if len(value.encode('utf-16-le')) // 2 > limit:
        raise ValueError(f'{label} exceeds NVARCHAR({limit}); refusing truncation.')
    return value


def convert(value, sql_type):
    if sql_type in {'INT', 'SMALLINT'}:
        numeric = value.strip()
        if not numeric:
            return None
        if not re.fullmatch(r'[+-]?[0-9]+', numeric):
            raise ValueError(f'Invalid {sql_type} value.')
        number = int(numeric)
        bits = 16 if sql_type == 'SMALLINT' else 32
        if not -(2 ** (bits - 1)) <= number < 2 ** (bits - 1):
            raise ValueError(f'Value outside {sql_type} range.')
        return number
    if sql_type == 'NVARCHAR(100)':
        return bounded(value, 100, 'Part number')
    if sql_type != 'NVARCHAR(MAX)':
        raise ValueError(f'Unsupported source SQL type: {sql_type}')
    return value  # Preserve Unicode, blanks, leading zeros and trailing whitespace.


def read_tsv(path, columns):
    with path.open('rb') as handle:
        prefix = handle.read(4)
    encoding = 'utf-16' if prefix.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    csv.field_size_limit(2 ** 31 - 1)
    with path.open(encoding=encoding, newline='') as handle:
        reader = csv.reader(handle, delimiter='\t', strict=True)
        if next(reader, None) != [name for name, _ in columns]:
            raise ValueError(f'{path.name}: headers/order do not match the approved schema.')
        for number, values in enumerate(reader, 1):
            if len(values) != len(columns):
                raise ValueError(f'{path.name}, data record {number}: wrong column count.')
            converted = []
            for (name, sql_type), value in zip(columns, values):
                try:
                    converted.append(convert(value, sql_type))
                except ValueError as exc:
                    raise ValueError(f'{path.name}, data record {number}, {name}: {exc}') from exc
            yield converted


def prepare_report(row, destination_root, workspace, contract):
    """Freeze eight source files locally and validate all records before connecting."""
    if row['status'] != 'Succeeded' or row['collection_status'] != 'Copied':
        raise ValueError('Only successfully submitted and collected reports can be imported.')
    report_id = str(uuid.UUID(row['file_id']))
    report_name = bounded(row['report_name'], 255, 'Report name')
    if d.build_report_name(row) != report_name:
        raise ValueError('Report name does not match the saved export pair.')
    normal = collection.report_destination(destination_root, row)
    if Path(row['collection_destination']).resolve() != normal.resolve():
        raise ValueError('Tracker destination does not match the expected comparison folder.')
    folder = collection.filesystem_path(normal)
    archives = [p for p in folder.glob('*_ACESDifferential.zip')
                if re.fullmatch(r'\d+~' + re.escape(report_name) + r'_ACESDifferential\.zip', p.name)]
    if len(archives) != 1:
        raise ValueError('Expected exactly one matching ACESDifferential ZIP.')
    extracted = folder / archives[0].stem
    dates = normal.name.split('_to_')
    plan = dict(report_id=report_id, report_name=report_name,
                keyword=bounded(collection.folder_label(row), 200, 'Keyword'),
                previous_date=date.fromisoformat(dates[0]).isoformat(),
                current_date=date.fromisoformat(dates[1]).isoformat(), files=[])
    for kind in KINDS:
        name = bounded(f'{report_name}_{kind}.tsv', 255, 'Source filename')
        source = extracted / name
        before = collection.fingerprint(source)
        snapshot = workspace / f'{kind}.tsv'
        shutil.copyfile(source, snapshot)
        checksum = collection.digest(snapshot).hex()
        if collection.fingerprint(source) != before or collection.digest(source).hex() != checksum:
            raise ValueError(f'{name}: source changed while preparing the import.')
        prepared = workspace / f'{kind}.jsonl'
        count = 0
        with prepared.open('w', encoding='utf-8', newline='\n') as output:
            for values in read_tsv(snapshot, contract[kind]):
                output.write(json.dumps(values, ensure_ascii=False) + '\n')
                count += 1
        plan['files'].append(dict(kind=kind, name=name, sha256=checksum,
                                  count=count, prepared=prepared))
    return plan


def connect(settings):
    if settings.get('server') != SERVER or settings.get('database') != DATABASE:
        raise ValueError('SQL import target must be CATALOGSQL\\CATALOG / CatalogWarehouse.')
    import pyodbc  # Not imported in paused/validate-only mode or fixture tests.
    driver = settings.get('driver', 'ODBC Driver 18 for SQL Server')
    if driver not in {'ODBC Driver 17 for SQL Server', 'ODBC Driver 18 for SQL Server'}:
        raise ValueError('Unsupported SQL Server ODBC driver.')
    connection = pyodbc.connect(
        f'DRIVER={{{driver}}};SERVER={SERVER};DATABASE={DATABASE};'
        'Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes;'
        'APP=ACES Differential TSV Import;', timeout=30, autocommit=True)
    connection.timeout = int(settings.get('query_timeout_seconds', 120))
    return connection


def verify_target(cursor, contract):
    actual = cursor.execute("SELECT CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')), "
                            "CONVERT(nvarchar(128),SERVERPROPERTY('InstanceName')), DB_NAME()").fetchone()
    if tuple(actual) != ('CATALOGSQL', 'CATALOG', DATABASE):
        raise ValueError('Connected to an unexpected SQL Server or database.')
    if not cursor.execute("SELECT OBJECT_ID(N'dbo.Differential_ACES_ImportLog', N'U')").fetchone()[0]:
        raise ValueError('Import log is missing. Review and execute sql/002_create_differential_aces_import_log.sql first.')
    for kind in KINDS:
        actual = cursor.execute(
            "SELECT c.name,t.name,c.max_length,c.scale,c.is_nullable,c.is_identity "
            "FROM sys.columns c JOIN sys.types t ON t.user_type_id=c.user_type_id "
            "WHERE c.object_id=OBJECT_ID(?) ORDER BY c.column_id",
            f'dbo.Differential_ACES_{kind}').fetchall()
        expected = META + [tuple(c) for c in contract[kind]]
        if len(actual) != len(expected):
            raise ValueError(f'{kind}: database column count differs from approved schema.')
        for position, (found, (name, typ)) in enumerate(zip(actual, expected)):
            base = typ.split('(')[0].lower()
            valid = (found[0] == name and found[1] == base
                     and bool(found[4]) == (position >= len(META))
                     and bool(found[5]) == (position == 0))
            if base == 'nvarchar':
                width = typ.split('(')[1].rstrip(')')
                valid = valid and found[2] == (-1 if width == 'MAX' else int(width) * 2)
            if base == 'datetimeoffset':
                valid = valid and found[3] == 3
            if not valid:
                raise ValueError(f'{kind}.{name}: database type/order/nullability differs from approved schema.')


def existing_import(cursor, plan):
    """Check the SQL ledger, including empty files, and refuse partial/changed imports."""
    entries = cursor.execute(
        f'SELECT TableName,SourceFileName,SourceSHA256,ImportedRowCount,Meta_Keyword,'
        'CONVERT(char(10),Meta_PreviousExportDate,23),CONVERT(char(10),Meta_CurrentExportDate,23),'
        f'CONVERT(varchar(40),ImportedAt,121),CONVERT(varchar(36),Meta_ReportID),ReportName FROM {LOG} '
        'WHERE Meta_ReportID=? OR ReportName=?', plan['report_id'], plan['report_name']).fetchall()
    if entries and len(entries) != 8:
        raise ValueError('Incomplete import log; review required. No rows appended.')
    by_table = {entry[0]: entry for entry in entries}
    if entries and len(by_table) != 8:
        raise ValueError('Conflicting report identities in import log.')
    timestamps = set()
    for item in plan['files']:
        entry = by_table.get('Differential_ACES_' + item['kind'])
        if entries:
            expected = (item['name'], item['sha256'], item['count'], plan['keyword'],
                        plan['previous_date'], plan['current_date'])
            if (entry is None or tuple(entry[1:7]) != expected
                    or entry[8].lower() != plan['report_id'] or entry[9] != plan['report_name']):
                raise ValueError('Previously imported report contents or identity changed; review required.')
            timestamps.add(entry[7])
        counts = cursor.execute(
            f'SELECT COUNT_BIG(*),MIN(Meta_SourceRowNumber),MAX(Meta_SourceRowNumber) '
            f'FROM {table(item["kind"])} WHERE Meta_ReportID=?', plan['report_id']).fetchone()
        expected_count = item['count'] if entries else 0
        if tuple(counts) != (expected_count, 1 if expected_count else None,
                             expected_count if expected_count else None):
            raise ValueError(f'{item["kind"]}: existing rows do not match the import log; review required.')
    if entries:
        if len(timestamps) != 1:
            raise ValueError('Import log timestamps disagree; review required.')
        # SQL style 121 preserves the offset but places a space before it.
        stamp = re.sub(r' ([+-]\d{2}:\d{2})$', r'\1', next(iter(timestamps)))
        return datetime.fromisoformat(stamp).isoformat(timespec='milliseconds')
    return None


def completed_ledger(connection):
    """Read committed import metadata once; never scan the imported data tables."""
    cursor = connection.cursor()
    try:
        actual = cursor.execute("SELECT CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')), CONVERT(nvarchar(128),SERVERPROPERTY('InstanceName')), DB_NAME()").fetchone()
        if tuple(actual) != ('CATALOGSQL', 'CATALOG', DATABASE):
            raise ValueError('Connected to an unexpected SQL Server or database.')
        entries = cursor.execute(f'SELECT TableName,CONVERT(varchar(36),Meta_ReportID),ReportName,Meta_Keyword,CONVERT(char(10),Meta_PreviousExportDate,23),CONVERT(char(10),Meta_CurrentExportDate,23),SourceFileName,SourceSHA256,ImportedRowCount,CONVERT(varchar(40),ImportedAt,121) FROM {LOG}').fetchall()
        by_id, by_name = {}, {}
        for index, entry in enumerate(entries):
            by_id.setdefault(entry[1].lower(), set()).add(index)
            by_name.setdefault(entry[2], set()).add(index)
        return entries, by_id, by_name
    finally:
        cursor.close()


def completed_report(connection, row, ledger=None):
    """Validate this report against the shared snapshot or a standalone read."""
    if d.build_report_name(row) != row['report_name']:
        raise ValueError('Report identity differs from saved pair; review required.')
    entries, by_id, by_name = ledger if ledger is not None else completed_ledger(connection)
    indices = by_id.get(row['file_id'].lower(), set()) | by_name.get(row['report_name'], set())
    entries = [entries[index] for index in sorted(indices)]
    if not entries:
        return None
    dates = row['report_name'].rsplit('_Diff_', 1)[1].split('_to_')
    expected = {'Differential_ACES_' + k for k in KINDS}
    if len(entries) != 8 or {e[0] for e in entries} != expected:
        raise ValueError('Incomplete/conflicting SQL import log; review required.')
    for e in entries:
        kind = e[0].removeprefix('Differential_ACES_')
        if (e[1].lower() != row['file_id'].lower() or e[2] != row['report_name']
            or e[3] != collection.folder_label(row) or list(e[4:6]) != dates
            or e[6] != row['report_name'] + '_' + kind + '.tsv'
            or not re.fullmatch('[0-9a-fA-F]{64}', e[7]) or e[8] < 0):
            raise ValueError('SQL import log identity mismatch; review required.')
    if len({e[9] for e in entries}) != 1:
        raise ValueError('SQL import log timestamps disagree; review required.')
    stamp = re.sub(r' ([+-]\d{2}:\d{2})$', r'\1', entries[0][9])
    return datetime.fromisoformat(stamp).isoformat(timespec='milliseconds')


def insert_file(cursor, plan, item, columns, timestamp, batch_size):
    fields = META[1:] + [tuple(column) for column in columns]
    names = ','.join(identifier(name) for name, _ in fields)
    projection = ','.join(f"{identifier(name)} {typ} 'strict $[{index}]'"
                          for index, (name, typ) in enumerate(fields))
    statement = (f'INSERT INTO {table(item["kind"])} ({names}) SELECT {names} '
                 f'FROM OPENJSON(CAST(? AS NVARCHAR(MAX))) WITH ({projection})')
    limit = batch_size
    batch = []
    def flush():
        if batch:
            # One parameterized, set-based insert per batch. Explicit OPENJSON
            # types preserve Unicode/MAX text, NULLs and datetimeoffset values.
            cursor.execute(statement, json.dumps(batch, ensure_ascii=False, separators=(',', ':')))
            batch.clear()
    with item['prepared'].open(encoding='utf-8') as handle:
        for number, line in enumerate(handle, 1):
            batch.append([plan['report_id'], plan['keyword'], plan['previous_date'],
                          plan['current_date'], item['name'], number, timestamp] + json.loads(line))
            if len(batch) == limit:
                flush()
                if number % 10000 == 0:
                    print(f'{plan["report_name"]} {item["kind"]}: {number:,}/{item["count"]:,} rows staged (not committed).', flush=True)
        flush()
    count = cursor.execute(f'SELECT COUNT_BIG(*) FROM {table(item["kind"])} WHERE Meta_ReportID=?',
                           plan['report_id']).fetchone()[0]
    if count != item['count']:
        raise ValueError(f'{item["kind"]}: inserted row count does not match source.')
    cursor.execute(
        f'INSERT INTO {LOG} (Meta_ReportID,TableName,ReportName,Meta_Keyword,Meta_PreviousExportDate,'
        'Meta_CurrentExportDate,SourceFileName,SourceSHA256,ImportedRowCount,ImportedAt) '
        'VALUES (?,?,?,?,CONVERT(date,?,23),CONVERT(date,?,23),?,?,?,CONVERT(datetimeoffset(3),?,127))',
        plan['report_id'], 'Differential_ACES_' + item['kind'], plan['report_name'], plan['keyword'],
        plan['previous_date'], plan['current_date'], item['name'], item['sha256'], item['count'], timestamp)


def import_plan(connection, plan, contract, batch_size=250):
    if not 1 <= batch_size <= 1000:
        raise ValueError('batch_size must be between 1 and 1000.')
    cursor = connection.cursor()
    try:
        # Explicit transaction because this connection uses autocommit=True.
        cursor.execute('SET NOCOUNT ON; SET XACT_ABORT ON; SET ANSI_WARNINGS ON; BEGIN TRANSACTION;')
        result = cursor.execute(
            "DECLARE @result int; EXEC @result=sys.sp_getapplock "
            "@Resource=N'ACES_Differential_TSV_Import',@LockMode='Exclusive',"
            "@LockOwner='Transaction',@LockTimeout=0; SELECT @result;").fetchone()[0]
        if result < 0:
            raise RuntimeError('Another SQL import is active; retry later.')
        verify_target(cursor, contract)
        imported_at = existing_import(cursor, plan)
        skipped = imported_at is not None
        if not skipped:
            imported_at = datetime.now(ZoneInfo('America/New_York')).isoformat(timespec='milliseconds')
            for item in plan['files']:
                insert_file(cursor, plan, item, contract[item['kind']], imported_at, batch_size)
        cursor.execute('COMMIT TRANSACTION;')
        return imported_at, skipped
    except BaseException:
        # A connection failure at COMMIT can have an uncertain outcome. Never
        # automatically resend in this run; the SQL ledger resolves the next run.
        try:
            cursor.execute('IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;')
        except Exception:
            pass
        raise
    finally:
        cursor.close()


def run(config_path, execute_once=False, validate_only=False, only_file_id=None):
    config_path = config_path.resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    settings = config.get('sql_import', {})
    if not validate_only and not execute_once and settings.get('enabled') is not True:
        print('SQL import is paused. No database connection or tracker changes made.')
        return 0
    contract = schema()
    batch_size = int(settings.get('batch_size', 250))
    if not 1 <= batch_size <= 1000:
        raise ValueError('batch_size must be between 1 and 1000.')
    state = Path(config.get('state_folder', '.'))
    if not state.is_absolute():
        state = config_path.parent / state
    failures = 0
    with d.RunLock(state / '.detector.lock'):
        tracker = state / 'differential_tracker.csv'
        if not (state / '.detector_initialized').exists():
            raise RuntimeError('Missing detector initialization marker.')
        rows = d.read_rows(tracker)
        selected = [row for row in rows if row['status'] == 'Succeeded'
                    and row['collection_status'] == 'Copied'
                    and row.get('sql_import_status') != 'NeedsReview'
                    and (only_file_id is None or row['file_id'] == only_file_id)]
        if only_file_id and not selected:
            raise ValueError('Requested file ID is not an eligible collected report.')
        # Do not trust the CSV as the duplicate authority: reconcile against SQL,
        # including after a successful commit followed by a CSV save failure.
        ledger = None
        if selected and settings.get('skip_completed') and not validate_only:
            connection = connect(settings)
            try:
                ledger = completed_ledger(connection)
            finally:
                connection.close()
        changed = False
        skipped_count = 0
        for row in selected:
            try:
                if ledger is not None:
                    timestamp = completed_report(None, row, ledger=ledger)
                    if timestamp:
                        updates = dict(sql_import_status='Imported', sql_imported_at=timestamp, sql_import_error='')
                        changed = changed or any(row.get(key) != value for key, value in updates.items())
                        row.update(updates)
                        skipped_count += 1
                        continue
                with tempfile.TemporaryDirectory(prefix='aces-sql-') as tmp:
                    plan = prepare_report(row, Path(config['collection']['destination_folder']),
                                          Path(tmp), contract)
                    if validate_only:
                        print(row['report_name'], {f['kind']: f['count'] for f in plan['files']})
                        continue
                    connection = connect(settings)
                    try:
                        timestamp, skipped = import_plan(connection, plan, contract, batch_size)
                    finally:
                        connection.close()
                row.update(sql_import_status='Imported', sql_imported_at=timestamp, sql_import_error='')
                print(f'{row["report_name"]}: ' + ('Already imported; verified and skipped.' if skipped else 'Imported.'))
            except Exception as exc:
                failures += 1
                if not validate_only:
                    review = settings.get('hold_invalid') and isinstance(exc, (ValueError, UnicodeError)) and 'source changed' not in str(exc)
                    row.update(sql_import_status='NeedsReview' if review else 'Failed', sql_import_error=str(exc))
                print(f'{row["report_name"]}: {type(exc).__name__}: {exc}', file=sys.stderr)
            if not validate_only:
                # A save failure stops the run. SQL remains authoritative.
                d.save_rows(tracker, rows)
                changed = False
        if changed:
            d.save_rows(tracker, rows)
        if skipped_count:
            print(f"Already imported: {skipped_count} reports; checked one SQL ledger snapshot; no TSVs read.")
    return 2 if failures else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=d.ROOT / 'config.json')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute-once', action='store_true', help='Explicitly authorize one import run while config remains paused.')
    mode.add_argument('--validate-only', action='store_true', help='Read/validate TSVs locally; no SQL connection or CSV updates.')
    parser.add_argument('--file-id', help='Limit to one collected tracker UUID.')
    args = parser.parse_args()
    try:
        sys.exit(run(args.config, args.execute_once, args.validate_only, args.file_id))
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
