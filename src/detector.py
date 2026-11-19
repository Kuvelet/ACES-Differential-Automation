"""Detect PIM exports and prepare comparisons. Never submits work to PIM."""
import argparse
import csv
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import sys
import time
import uuid
import zipfile
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
COLLECTION_FIELDS = ['collection_status', 'collection_destination', 'collected_at', 'collection_error']
SQL_IMPORT_FIELDS = ['sql_import_status', 'sql_imported_at', 'sql_import_error']
FIELDS = ['file_id', 'file_name', 'keyword', 'generation_date', 'last_modified_ns', 'last_modified', 'size_bytes',
          'new_file_path', 'previous_file_id', 'previous_file_name', 'previous_file_path', 'status',
          'first_detected_at', 'attempt_count', 'last_error', 'report_name',
          'last_attempt_at', 'submitted_at', 'confirmation',
          'email_sent_signature', 'email_sent_at', 'email_error'] + COLLECTION_FIELDS + SQL_IMPORT_FIELDS
STATUSES = {'Baseline', 'Pending', 'Running', 'Succeeded', 'Failed',
            'NoPredecessor', 'Ambiguous', 'NeedsReview'}


def readable_modified(nanoseconds):
    seconds = int(nanoseconds) // 1_000_000_000
    return datetime.fromtimestamp(seconds, timezone.utc).astimezone(
        ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M:%S %Z (UTC%z)')


class RunLock:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.handle = self.path.open('a+b')
        self.handle.seek(0, 2)
        if not self.handle.tell():
            self.handle.write(b'0')
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise RuntimeError('Another tracker process is active.')
        return self

    def __exit__(self, *args):
        self.handle.close()


RENAMED_KEYWORDS = {
    'Example Catalog_' + name
    for name in ('BrandLambda', 'BrandGamma', 'BrandDelta', 'BrandBeta')
}


def canonical_keyword(name, keywords):
    lookup = {k.casefold(): k for k in keywords}
    for key in RENAMED_KEYWORDS:
        if key.casefold() in lookup:
            lookup[(key + '-ACES').casefold()] = lookup[key.casefold()]
    return lookup.get(name.casefold())


def parse_export(path, keywords):
    match = re.fullmatch(
        r'([0-9a-f-]{36})~(.+)_(\d{4}-\d{2}-\d{2})_FULL\.zip',
        path.name, re.IGNORECASE)
    if not match:
        return None
    file_id, keyword, generated = match.groups()
    canonical = canonical_keyword(keyword, keywords)
    if canonical is None:
        return None
    uuid.UUID(file_id)
    date.fromisoformat(generated)
    stat = path.stat()
    return dict(file_id=file_id.lower(), file_name=path.name, keyword=canonical,
                generation_date=generated, last_modified_ns=str(stat.st_mtime_ns),
                last_modified=readable_modified(stat.st_mtime_ns),
                size_bytes=str(stat.st_size), new_file_path=str(path.resolve()))


def discover(source, keywords, stable_seconds, existing=None):
    # A failed directory listing aborts the run; it never establishes an empty baseline.
    candidates = []
    for path in source.iterdir():
        if not path.is_file():
            continue
        try:
            item = parse_export(path, keywords)
            if item:
                candidates.append(item)
        except (ValueError, OSError) as exc:
            print(f'Skipped {path.name}: {exc}')
    known = {row['file_id']: row for row in (existing or [])}
    # Keep a current directory inventory for predecessor selection. Only skip deep
    # checks when path, size and timestamp match the recorded export exactly.
    def unchanged(item):
        old = known.get(item['file_id'])
        return old is not None and all(str(old[key]) == str(item[key]) for key in
            ('new_file_path', 'size_bytes', 'last_modified_ns', 'keyword', 'generation_date'))
    check_keywords = {item['keyword'] for item in candidates if not unchanged(item)}
    ids = [item['file_id'] for item in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate file IDs on the shared drive; no CSV changes made.')
    if check_keywords:
        time.sleep(stable_seconds)
    ready = []
    blocked_keywords = set()
    for item in candidates:
        if item['keyword'] not in check_keywords:
            ready.append(item)
            continue
        path = Path(item['new_file_path'])
        try:
            stat = path.stat()
            if (stat.st_size == 0 or str(stat.st_size) != item['size_bytes']
                    or str(stat.st_mtime_ns) != item['last_modified_ns']):
                print(f'Not ready: {path.name}')
                blocked_keywords.add(item['keyword'])
                continue
            with zipfile.ZipFile(path) as archive:
                archive.infolist()  # Read the ZIP directory without extracting exports.
            ready.append(item)
        except (OSError, zipfile.BadZipFile) as exc:
            blocked_keywords.add(item['keyword'])
            print(f'Not ready: {path.name}: {exc}')
    # Wait for this keyword's exports together rather than skipping an unfinished predecessor.
    ready = [item for item in ready if item['keyword'] not in blocked_keywords]
    ids = [item['file_id'] for item in ready]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate file IDs on the shared drive; no CSV changes made.')
    return ready


def read_rows(path):
    with path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        optional = {'last_modified', 'file_name', 'previous_file_name', 'report_name',
                    'last_attempt_at', 'submitted_at', 'confirmation',
                    'email_sent_signature', 'email_sent_at', 'email_error'}
        optional.update(COLLECTION_FIELDS)
        optional.update(SQL_IMPORT_FIELDS)
        header = reader.fieldnames or []
        if (header != [f for f in FIELDS if f in header]
                or not set(FIELDS).difference(optional).issubset(header)):
            raise ValueError('CSV columns do not match the expected format.')
        rows = list(reader)
    seen = set()
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError('Incomplete or malformed CSV row.')
        for field in ('report_name', 'last_attempt_at', 'submitted_at', 'confirmation',
                      'email_sent_signature', 'email_sent_at', 'email_error'):
            row.setdefault(field, '')
        identity = str(uuid.UUID(row['file_id']))
        for field in COLLECTION_FIELDS + SQL_IMPORT_FIELDS:
            row.setdefault(field, '')
        if row['sql_import_status'] not in {'', 'Imported', 'Failed', 'NeedsReview'}:
            raise ValueError('Invalid SQL import status in CSV.')
        if row['collection_status'] not in {'', 'Waiting', 'Incomplete', 'Copied', 'Failed'}:
            raise ValueError('Invalid collection status in CSV.')
        if identity != row['file_id'] or identity in seen:
            raise ValueError('Invalid or duplicate file ID in CSV.')
        seen.add(identity)
        date.fromisoformat(row['generation_date'])
        int(row['last_modified_ns'])
        row['last_modified'] = readable_modified(row['last_modified_ns'])
        row['file_name'] = PureWindowsPath(row['new_file_path']).name
        row['previous_file_name'] = PureWindowsPath(row['previous_file_path']).name if row['previous_file_path'] else ''
        int(row['size_bytes'])
        if int(row['attempt_count']) < 0 or row['status'] not in STATUSES:
            raise ValueError('Invalid status or attempt count in CSV.')
        if row['status'] in {'Pending', 'Running', 'Succeeded', 'Failed', 'NeedsReview'}:
            if not row['previous_file_id'] or not row['previous_file_path']:
                raise ValueError('An analysis row must contain a previous file.')
    return rows


def order(item):
    return item['generation_date'], int(item['last_modified_ns'])


def build_report_name(row):
    """Name a saved pair for the PIM Report Name field; never submits it."""
    keyword = row['keyword']
    dates = []
    for column in ('previous_file_path', 'new_file_path'):
        name = PureWindowsPath(row[column]).name
        match = re.fullmatch(r'[0-9a-f-]{36}~(.+)_(\d{4}-\d{2}-\d{2})_FULL\.zip', name, re.IGNORECASE)
        if not match or canonical_keyword(match.group(1), [keyword]) != keyword:
            raise ValueError(f'Cannot name report: {column} does not match the keyword/export format.')
        dates.append(date.fromisoformat(match.group(2)).isoformat())
    prefix = 'Example Catalog_'
    label = keyword[len(prefix):] if keyword.casefold().startswith(prefix.casefold()) else keyword
    return f'{label}_Diff_{dates[0]}_to_{dates[1]}'


def assign_predecessor(row, candidates, baseline=False):
    same = [other for other in candidates
            if other['keyword'] == row['keyword']
            and other['file_id'] != row['file_id']]
    earlier = [other for other in same if order(other) < order(row)]
    previous = max(earlier, key=order) if earlier else None
    tied = any(order(other) == order(row) for other in same)
    previous_tied = previous and sum(order(x) == order(previous) for x in earlier) > 1
    row.update(previous_file_id='', previous_file_name='', previous_file_path='', last_error='')
    if tied or previous_tied:
        row['last_error'] = 'Generation date and last modified tie.'
        status = 'Ambiguous'
    elif previous:
        row.update(previous_file_id=previous['file_id'],
                   previous_file_name=PureWindowsPath(previous['new_file_path']).name,
                   previous_file_path=previous['new_file_path'])
        status = 'Pending'
    else:
        row['last_error'] = 'No earlier export is available for this keyword.'
        status = 'NoPredecessor'
    row['status'] = 'Baseline' if baseline else status


def backfill_baselines(existing):
    """Fill unpaired baseline rows from recorded history without queuing work."""
    rows = [dict(row) for row in existing]
    for row in rows:
        if row['status'] == 'Baseline' and not row['previous_file_id']:
            assign_predecessor(row, existing, baseline=True)
    return rows


def prepare_rows(existing, ready, baseline=False, baseline_before=None):
    rows = [dict(row) for row in existing]
    known = {row['file_id'] for row in rows}
    now = datetime.now(timezone.utc).isoformat()
    for item in sorted(ready, key=lambda x: (x['keyword'], order(x), x['file_id'])):
        if item['file_id'] in known:
            continue
        row = {field: '' for field in FIELDS}
        row.update(item, status='Baseline', first_detected_at=now, attempt_count='0')
        assign_predecessor(row, ready, baseline=baseline or bool(
            baseline_before and item['generation_date'] < baseline_before))
        rows.append(row)
        known.add(item['file_id'])
    return rows


def save_rows(path, rows):
    temporary = path.with_suffix('.csv.tmp')
    try:
        with temporary.open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(dict(row, last_modified=readable_modified(row['last_modified_ns']),
                                  file_name=PureWindowsPath(row['new_file_path']).name,
                                  previous_file_name=PureWindowsPath(row['previous_file_path']).name if row['previous_file_path'] else '')
                             for row in rows)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            import shutil
            backup = path.with_suffix(".csv.bak")
            staged_backup = backup.with_suffix(".bak.tmp")
            shutil.copyfile(path, staged_backup)
            os.replace(staged_backup, backup)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_pending_pairs(rows):
    """Validate saved paths without substituting inputs or starting analysis.

    Access/network errors abort the scan; only confirmed missing/non-file inputs
    fail a pair. Analysis attempts are unchanged by this availability check.
    """
    result = [dict(row) for row in rows]
    for row in result:
        if row['status'] != 'Pending':
            continue
        missing = []
        for label, column in [('Previous', 'previous_file_path'), ('New', 'new_file_path')]:
            path = Path(row[column])
            try:
                if not stat.S_ISREG(path.stat().st_mode):
                    missing.append(f'{label} input is not a file: {path}')
            except FileNotFoundError:
                missing.append(f'{label} file is missing: {path}')
            except OSError as exc:
                raise RuntimeError(f'Unable to check {label.lower()} file: {path}. '
                                   f'No CSV changes saved: {exc}') from exc
        if missing:
            row.update(status='Failed', last_error='; '.join(missing))
        else:
            row['last_error'] = ''
    return result


def run(config_path, initialize=False, backfill=False):
    config_path = config_path.resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    source = Path(config['source_folder'])
    state = Path(config.get('state_folder', '.'))
    if not state.is_absolute():
        state = config_path.parent / state
    state.mkdir(parents=True, exist_ok=True)
    csv_path = state / 'differential_tracker.csv'
    marker = state / '.detector_initialized'
    seconds = float(config.get('stability_seconds', 4))
    if seconds < 0:
        raise ValueError('stability_seconds must be nonnegative.')
    with RunLock(state / '.detector.lock'):
        if initialize:
            if marker.exists() or csv_path.exists():
                raise RuntimeError('State already exists. Initialization will not overwrite it.')
            existing = []
        else:
            if not marker.exists() or not csv_path.exists():
                raise RuntimeError('Missing initialization marker or CSV. For first setup use --initialize; otherwise restore the missing state file.')
            existing = read_rows(csv_path)
        if backfill:
            rows = backfill_baselines(existing)
            if rows != existing:
                save_rows(csv_path, rows)
            print(f'Baseline predecessors populated: {sum(bool(r["previous_file_id"]) and r["status"] == "Baseline" for r in rows)}')
            print('Statuses unchanged. No PIM analysis was submitted.')
            return
        ready = discover(source, config['keywords'], seconds, existing=existing)
        if initialize and not ready:
            raise RuntimeError('No ready matching exports found. Refusing an empty baseline; check the source and filenames.')
        cutoff = config.get('baseline_before')
        if cutoff:
            date.fromisoformat(cutoff)
        rows = prepare_rows(existing, ready, baseline=initialize, baseline_before=cutoff)
        rows = validate_pending_pairs(rows)
        if initialize or rows != existing:
            save_rows(csv_path, rows)
        if initialize:
            marker.write_text(datetime.now(timezone.utc).isoformat(), encoding='utf-8')
        print(f'Ready exports: {len(ready)}; new records: {len(rows) - len(existing)}; '
              f'pending pairs: {sum(r["status"] == "Pending" for r in rows)}; '
              f'failed pairs: {sum(r["status"] == "Failed" for r in rows)}')
        print(f'CSV: {csv_path}')
        print('Detection only. No PIM analysis was submitted.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--initialize', action='store_true', help='Record current ready exports as baseline once.')
    mode.add_argument('--backfill-baseline', action='store_true', help='Fill missing baseline predecessors from the saved inventory without changing statuses.')
    args = parser.parse_args()
    try:
        run(args.config, args.initialize, args.backfill_baseline)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
