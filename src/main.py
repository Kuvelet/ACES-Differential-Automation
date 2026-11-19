"""Scan ACES exports, submit Pending/retry Failed pairs, and email run results."""
import argparse
from pathlib import Path
import sys

import detector
from pim_automation import run_pending
from email_reports import send_run_report
from automation_logging import configure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=detector.ROOT / 'config.json')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--scan-only', action='store_true')
    mode.add_argument('--submit-only', action='store_true')
    parser.add_argument('--headed', action='store_true', help='Show the Playwright browser.')
    parser.add_argument('--file-id', help='Process only this Pending/Failed new-file ID; other pairs stay unchanged.')
    args = parser.parse_args()
    logger = configure(args.config.resolve().parent)
    logger.info('Run started: scan_only=%s submit_only=%s headed=%s selected_file=%s',
                args.scan_only, args.submit_only, args.headed, args.file_id or 'all')
    exit_code = 0
    run_error = None
    email_attempted_ids = set()
    try:
        if not args.submit_only:
            logger.info('Scanning the shared folder.')
            detector.run(args.config)
            logger.info('Scan completed.')
        if not args.scan_only:
            failures = run_pending(args.config, headed=args.headed, only_file_id=args.file_id,
                                   email_attempted_ids=email_attempted_ids)
            if failures:
                print(f'{failures} pair(s) require attention in differential_tracker.csv.')
                logger.warning('%s pair(s) have submission or notification issues.', failures)
                exit_code = 2
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        logger.error('Run failed: %s: %s', type(exc).__name__, exc)
        run_error = str(exc)
        exit_code = 1
    if not args.scan_only:
        try:
            send_run_report(args.config, run_error=run_error, only_file_id=args.file_id,
                            exclude_file_ids=email_attempted_ids)
        except Exception as exc:
            print(f'EMAIL ERROR: {exc}', file=sys.stderr)
            logger.error('Email report failed: %s', exc)
            exit_code = exit_code or 1
    logger.info('Run finished: exit_code=%s', exit_code)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
