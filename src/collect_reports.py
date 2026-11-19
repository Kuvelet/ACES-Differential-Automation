"""Copy completed PIM differential outputs; independent of submission and email."""
import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sys
import tempfile
import time
import zipfile

import detector as d

SUFFIXES = ('_ACESDifferential.zip', '_NetChange.zip', '_reference.error', '_source.error')


def fingerprint(path):
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.digest()


def filesystem_path(path):
    """Support full ZIP/member names beyond Windows' legacy 260-character limit."""
    if sys.platform != 'win32':
        return path
    value = str(path.absolute())
    if value.startswith('\\\\?\\'):
        return path
    if value.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    return Path('\\\\?\\' + value)


def folder_label(row):
    label = row['keyword'].removeprefix('Example Catalog_')
    if (not label or label in {'.', '..'} or re.search(r'[<>:"/\\|?*\x00-\x1f]', label)
            or label.endswith((' ', '.'))):
        raise ValueError('Unsafe keyword folder name.')
    return label


def report_destination(root, row):
    match = re.fullmatch(r'.+_Diff_(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})', row['report_name'])
    if not match:
        raise ValueError('Submitted report name must contain its comparison dates.')
    older, newer = (date.fromisoformat(value) for value in match.groups())
    if older > newer:
        raise ValueError('Report comparison dates are reversed.')
    return root / folder_label(row) / f'{older}_to_{newer}'


def member_path(name):
    """Validate ZIP member paths before writing on Windows or other platforms."""
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or '\\' in name
            or any(part in {'.', '..'} or re.search(r'[<>:"|?*\x00-\x1f]', part)
                   or part.endswith((' ', '.')) or PureWindowsPath(part).is_reserved()
                   for part in path.parts)):
        raise ValueError(f'Unsafe ZIP member path: {name}')
    return Path(*path.parts)


def index_outputs(source):
    groups = {}
    for path in source.iterdir():
        if not path.is_file():
            continue
        for suffix in SUFFIXES:
            if path.name.casefold().endswith(suffix.casefold()):
                stem = path.name[:-len(suffix)]
                match = re.fullmatch(r'(\d+)~(.+)', stem)
                if match:
                    prefix, report = match.groups()
                    group = groups.setdefault(report.casefold(), {}).setdefault(prefix, {})
                    if suffix in group:
                        raise ValueError(f'Duplicate output suffix: {path.name}')
                    group[suffix] = path
                break
    return groups


def copy_set(files, destination, seconds):
    """Stage, validate and hash all files before publishing; never overwrite a conflict."""
    destination = filesystem_path(destination)
    before = {path: fingerprint(path) for path in files}
    time.sleep(seconds)
    if any(fingerprint(path) != before[path] for path in files):
        return False
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.collect-', dir=destination) as staging:
        staged = []
        extracted_names = set()
        for source in files:
            temporary = Path(staging) / source.name
            shutil.copyfile(source, temporary)
            if source.suffix.casefold() == '.zip':
                with zipfile.ZipFile(temporary) as archive:
                    bad = archive.testzip()
                    if bad:
                        raise ValueError(f'ZIP CRC check failed: {source.name}: {bad}')
                    kind = next((suffix[1:-4] for suffix in SUFFIXES[:2]
                                 if source.name.casefold().endswith(suffix.casefold())), None)
                    if kind is None:
                        raise ValueError(f'Unexpected ZIP type: {source.name}')
                    for member in archive.infolist():
                        relative = Path(source.stem) / member_path(member.filename)
                        if member.is_dir():
                            continue
                        key = str(relative).casefold()
                        if key in extracted_names:
                            raise ValueError(f'Duplicate ZIP member: {member.filename}')
                        extracted_names.add(key)
                        extracted = Path(staging) / relative
                        extracted.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as incoming, extracted.open('xb') as outgoing:
                            shutil.copyfileobj(incoming, outgoing)
                        staged.append((extracted, destination / relative, digest(extracted)))
            checksum = digest(temporary)
            if checksum != digest(source) or fingerprint(source) != before[source]:
                return False
            staged.append((temporary, destination / source.name, checksum))
        if any(fingerprint(path) != before[path] for path in files):
            return False
        # Preflight every existing file before publishing any new files.
        for temporary, target, checksum in staged:
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError(f'Destination escapes report folder: {target}')
            if target.exists() and digest(target) != checksum:
                raise ValueError(f'Destination conflict; existing file left untouched: {target}')
        for temporary, target, checksum in staged:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                # Windows rename fails if a competing writer creates the destination.
                if sys.platform == 'win32':
                    temporary.rename(target)
                else:
                    import os
                    os.link(temporary, target)
            if digest(target) != checksum:
                raise ValueError(f'Destination verification failed: {target}')
        if any(fingerprint(path) != before[path] for path in files):
            return False
    return True


def collect_rows(rows, source, destination, seconds, save):
    candidates = [row for row in rows if row['status'] == 'Succeeded'
                  and row['collection_status'] != 'Copied']
    if not candidates:
        return
    try:
        groups = index_outputs(source)
    except (OSError, ValueError) as exc:
        for row in candidates:
            row.update(collection_status='Failed', collection_error=f'Source scan failed: {exc}')
        save()
        raise
    names = Counter(row['report_name'].casefold() for row in rows if row['status'] == 'Succeeded')
    for row in candidates:
        row.update(collected_at='', collection_error='')
        try:
            target = report_destination(destination, row)
            row['collection_destination'] = str(target)
            report = row['report_name']
            if not report:
                raise ValueError('Submitted report name is missing; review the tracker.')
            if names[report.casefold()] != 1:
                raise ValueError('Multiple tracker rows share this report name; review required.')
            matches = groups.get(report.casefold(), {})
            if not matches:
                row['collection_status'] = 'Waiting'
            elif len(matches) != 1:
                raise ValueError('Multiple numeric prefixes match this report; review required.')
            else:
                group = next(iter(matches.values()))
                missing = [suffix for suffix in SUFFIXES if suffix not in group]
                if missing:
                    row.update(collection_status='Incomplete', collection_error='Missing: ' + ', '.join(missing))
                elif not copy_set([group[suffix] for suffix in SUFFIXES], target, seconds):
                    row.update(collection_status='Incomplete', collection_error='Source files are still changing; retry later.')
                else:
                    row.update(collection_status='Copied', collected_at=datetime.now(timezone.utc).isoformat())
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, EOFError) as exc:
            row.update(collection_status='Failed', collection_error=str(exc))
        save()  # Persist each report; identical already-copied files are safe on retry.
        print(f'{row["report_name"]}: {row["collection_status"]} {row["collection_error"]}')


def run(config_path):
    config_path = config_path.resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    settings = config.get('collection', {})
    if settings.get('enabled') is not True:
        print('Collection is paused (collection.enabled=false). No files or tracker changes made.')
        return 0
    seconds = float(settings.get('stability_seconds', 10))
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('collection.stability_seconds must be finite and nonnegative.')
    state = Path(config.get('state_folder', '.'))
    if not state.is_absolute():
        state = config_path.parent / state
    with d.RunLock(state / '.detector.lock'):
        tracker = state / 'differential_tracker.csv'
        if not (state / '.detector_initialized').exists() or not tracker.exists():
            raise RuntimeError('Missing initialization marker or tracker.')
        rows = d.read_rows(tracker)
        collect_rows(rows, Path(settings['source_folder']), Path(settings['destination_folder']),
                     seconds, lambda: d.save_rows(tracker, rows))
        return 2 if any(row['collection_status'] == 'Failed' for row in rows) else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=d.ROOT / 'config.json')
    args = parser.parse_args()
    try:
        sys.exit(run(args.config))
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
