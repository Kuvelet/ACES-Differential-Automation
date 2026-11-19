"""Candidate sequential orchestrator. Disabled unless explicitly enabled in config.

No schedule registration occurs here. Each stage is isolated in a timed child.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import threading

import detector as d
import workflow_alerts as alerts
from process_guard import Job

ROOT = Path(__file__).resolve().parent
STAGES = ('discover', 'submit', 'collect', 'import', 'notify_sql_success', 'notify_success')
STAGE_LABELS = {
    'discover': 'Checking for new export files',
    'submit': 'Submitting pending reports to PIM',
    'collect': 'Collecting and extracting finished reports',
    'import': 'Checking completed imports and importing new TSVs',
    'notify_sql_success': 'Sending SQL import success notifications',
    'notify_success': 'Checking PIM submission notifications',
    'notify_failures': 'Checking failure alerts and reminders',
}


def progress(message):
    print(f'[{datetime.now(alerts.EASTERN):%H:%M:%S %Z}] {message}', flush=True)


def display_log(log_path, finished, stage):
    """Mirror child output without changing its durable log or timeout handling."""
    last_update = time.monotonic()
    try:
        with log_path.open(encoding='utf-8', errors='replace') as stream:
            while True:
                line = stream.readline()
                if line:
                    print('  ' + line.rstrip('\r\n'), flush=True)
                    last_update = time.monotonic()
                    continue
                if finished.is_set():
                    break
                if time.monotonic() - last_update >= 30:
                    progress(STAGE_LABELS[stage] + ' - still working...')
                    last_update = time.monotonic()
                finished.wait(0.2)
    except (OSError, ValueError):
        # Console display must not interrupt processing; the stage log remains primary.
        pass


def run_child(stage, config, state, timeout, log_path):
    command = [sys.executable, '-u', str(ROOT / 'workflow_runner.py'), '--config', str(config), '--stage', stage, '--contained']
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                   env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        try:
            job = Job(process)
            process.stdin.write(b'contained\n')
            process.stdin.close()
        except BaseException:
            process.kill()
            process.wait(timeout=30)
            raise
        finished = threading.Event()
        display = threading.Thread(target=display_log, args=(log_path, finished, stage), daemon=True)
        display.start()
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Terminate only this owned child and descendants (including its browser).
            if os.name == 'nt':
                result = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                        capture_output=True, timeout=30)
                if result.returncode and process.poll() is None:
                    raise RuntimeError('Could not stop timed-out stage; refusing further work.')
            else:
                process.kill()
            process.wait(timeout=30)
            return 124
        finally:
            job.close()
            finished.set()
            display.join(timeout=2)


def tracker(state):
    with d.RunLock(state / '.detector.lock'):
        rows = d.read_rows(state / 'differential_tracker.csv')
        interrupted = False
        for row in rows:
            if row['status'] == 'Running':
                row.update(status='NeedsReview', last_error='Previous run interrupted. Check PIM before retrying.')
                interrupted = True
        if interrupted:
            d.save_rows(state / 'differential_tracker.csv', rows)
        return rows


def run(config_path, executor=run_child):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    settings = config.get('workflow', {})
    if settings.get('enabled') is not True:
        print('Candidate workflow is disabled; no stages or notifications run.')
        return 0
    state = Path(config.get('state_folder', '.'))
    if not state.is_absolute():
        state = config_path.parent / state
    logs = state / 'logs'
    logs.mkdir(exist_ok=True)
    stamp = datetime.now(alerts.EASTERN).strftime('%Y%m%d_%H%M%S') + '_' + str(os.getpid())
    results, current = [], []
    guard = d.RunLock(state / '.workflow.lock')
    try:
        guard.__enter__()
    except RuntimeError:
        print('Another workflow is running; this start is skipped.')
        return 0
    try:
        workflow_started = time.monotonic()
        progress('ACES Differentials workflow started.')
        progress(f'Logs: {logs}')
        # Stop if alert state is corrupt rather than silently losing pending notifications.
        alerts.load(state / 'workflow_alerts.json')
        observed = False
        for stage in STAGES:
            try:
                rows = tracker(state)
            except Exception as exc:
                progress(f'ERROR: Cannot use the tracker: {exc}')
                current.append(alerts.issue('tracker', 'global', 'Workflow', str(exc), 'Restore tracker/access; processing stopped safely.'))
                break
            start = time.monotonic()
            progress(f'[{STAGES.index(stage) + 1}/{len(STAGES) + 1}] {STAGE_LABELS[stage]}...')
            log = logs / f'workflow_{stamp}_{stage}.log'
            fatal = False
            try:
                code = executor(stage, config_path, state, settings['stage_timeout_seconds'][stage], log)
            except Exception as exc:
                progress(f'ERROR: {exc}')
                code, fatal = 1, True
                current.append(alerts.issue(stage, 'global', 'Workflow', str(exc), 'Investigate runner; next scheduled run retries.'))
            results.append(dict(stage=stage, code=code, seconds=round(time.monotonic()-start, 3), log=str(log)))
            outcome = 'Completed' if code == 0 else f'Needs attention (exit code {code})'
            progress(f'{outcome}: {stage} - {time.monotonic()-start:.1f} seconds.')
            if code:
                current.append(alerts.issue(stage, 'run', 'Workflow', f'Stage exit code {code}. See stage logs.',
                                           'Timeout; next run reconciles state.' if code == 124 else 'Inspect report details/logs; eligible work retries next run.'))
            # Queue alerts after every stage, so a subsequent crash cannot erase them.
            try:
                rows = tracker(state)
                alerts.reconcile(state, config, current + alerts.row_issues(rows, config, alerts.now()), complete=False)
            except Exception as exc:
                progress(f'ERROR: Could not save workflow status: {exc}')
                current.append(alerts.issue('tracker', 'global', 'Workflow', str(exc), 'Processing stopped; review tracker/alert storage.'))
                break
            if fatal:
                break
        else:
            observed = True
        if observed:
            current += alerts.row_issues(rows, config, alerts.now())
        alerts.reconcile(state, config, current, complete=observed)
        # Bound Outlook calls too. Pending messages survive a killed/hung sender.
        progress(f'[{len(STAGES) + 1}/{len(STAGES) + 1}] {STAGE_LABELS["notify_failures"]}...')
        code = executor('notify_failures', config_path, state, 120, logs / f'workflow_{stamp}_alerts.log')
        results.append(dict(stage='notify_failures', code=code))
        alerts.save(logs / f'workflow_{stamp}_summary.json', dict(results=results, issues=current, finished=alerts.now().isoformat()))
        progress(f'Workflow finished in {time.monotonic()-workflow_started:.1f} seconds. '
                 + ('Issues require attention; see the summary.' if current or code else 'All stages completed successfully.'))
        progress(f'Summary: {logs / f"workflow_{stamp}_summary.json"}')
        return 2 if current or code else 0
    finally:
        guard.__exit__()


def stage_main(stage, config_path):
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if config.get('workflow', {}).get('enabled') is not True:
        raise RuntimeError('Workflow disabled.')
    state = Path(config['state_folder'])
    if not state.is_absolute():
        state = config_path.parent / state
    if stage == 'discover':
        d.run(config_path)
        return 0
    if stage == 'submit':
        import pim_automation
        from scheduled_runner import read_credential
        # Avoid requesting credentials/browser startup when there is no eligible work.
        rows = tracker(state)
        if not any(r['status'] in {'Pending', 'Failed'} for r in rows):
            print('No pending reports to submit.', flush=True)
            return 0
        username, password = read_credential(Path(config['workflow']['credential_file']))
        os.environ['PIM_USERNAME'] = username
        return pim_automation.run_pending(config_path, password=password, headed=True)
    if stage == 'collect':
        import collect_reports
        return collect_reports.run(config_path)
    if stage == 'import':
        import import_reports
        return import_reports.run(config_path)
    if stage == 'notify_sql_success':
        import sql_success_emails
        return sql_success_emails.run(config_path)
    if stage == 'notify_success':
        import email_reports
        email_reports.send_run_report(config_path)
        return 0
    if stage == 'notify_failures':
        alerts.deliver(state, config)
        return 0
    raise ValueError('Unknown workflow stage')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    parser.add_argument('--stage', choices=STAGES + ('notify_failures',))
    parser.add_argument('--contained', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.stage and (not args.contained or sys.stdin.readline().strip() != 'contained'):
            raise RuntimeError('Stages must be launched by the workflow controller.')
        sys.exit(stage_main(args.stage, args.config.resolve()) if args.stage else run(args.config))
    except Exception as exc:
        print(f'Workflow error: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
