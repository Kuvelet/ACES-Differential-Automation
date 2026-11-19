"""Scheduled collection/extraction followed by SQL import, with persistent logs."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import os
from pathlib import Path
import sys

import collect_reports
import import_reports
import detector


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, value):
        for stream in self.streams:
            if stream is not None:
                stream.write(value)
                stream.flush()
        return len(value)

    def flush(self):
        for stream in self.streams:
            if stream is not None:
                stream.flush()


def run(config_path):
    """Import ready reports even if a different report's collection failed."""
    codes = []
    for label, action in [('Collection/extraction', collect_reports.run),
                          ('SQL import', import_reports.run)]:
        print(f'{datetime.now().astimezone().isoformat()} {label} started.', flush=True)
        try:
            code = action(config_path)
        except Exception as exc:
            print(f'{label}: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
            code = 1
        codes.append(code)
        print(f'{label} finished; exit code {code}.', flush=True)
    return 1 if 1 in codes else max(codes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=detector.ROOT / 'config.json')
    args = parser.parse_args()
    config = args.config.resolve()
    logs = config.parent / 'logs'
    logs.mkdir(exist_ok=True)
    path = logs / f'collection_{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.log'
    with path.open('w', encoding='utf-8') as log:
        with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
            print(f'Log: {path}', flush=True)
            return run(config)


if __name__ == '__main__':
    sys.exit(main())
