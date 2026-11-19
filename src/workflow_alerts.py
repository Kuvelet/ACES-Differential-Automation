"""Durable failure outbox. Only failures/recovery use the single owner recipient."""
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import email_reports

EASTERN = ZoneInfo('America/New_York')


def now():
    return datetime.now(EASTERN)


def save(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def load(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'issues': {}, 'outbox': [], 'reminder_date': ''}


def issue(stage, identity, report, error, action):
    return dict(key=stage + ':' + identity, stage=stage, report=report,
                error=error, action=action)


def row_issues(rows, config, clock):
    result = []
    for row in rows:
        identity = row['file_id']
        report = row.get('report_name') or row['keyword']
        status = row['status']
        if status in {'Failed', 'NeedsReview', 'Running', 'Ambiguous', 'NoPredecessor'}:
            review = status != 'Failed'
            result.append(issue('submission', identity, report, row.get('last_error') or status,
                                'Review required; no automatic submission.' if review else 'Retry next run.'))
        if row.get('collection_status') == 'Failed':
            result.append(issue('collection', identity, report, row.get('collection_error'), 'Retry next run.'))
        elif status == 'Succeeded' and row.get('collection_status') != 'Copied' and row.get('submitted_at'):
            age = clock - datetime.fromisoformat(row['submitted_at'])
            if age >= timedelta(hours=config['workflow'].get('waiting_alert_hours', 24)):
                result.append(issue('collection', identity, report, 'PIM outputs remain missing/incomplete beyond waiting threshold.', 'Check PIM processing; collection continues next run.'))
        if row.get('sql_import_status') in {'Failed', 'NeedsReview'}:
            result.append(issue('import', identity, report, row.get('sql_import_error'),
                                'Review required; import held.' if row['sql_import_status'] == 'NeedsReview' else 'Check SQL ledger and retry next run.'))
        if row.get('email_error'):
            result.append(issue('success-email', identity, report, row['email_error'], 'Retry notification only.'))
    return result


def reconcile(state, config, current, clock=None, complete=True):
    """Persist messages before sending. Incomplete observations cannot resolve old issues."""
    clock = clock or now()
    path = Path(state) / 'workflow_alerts.json'
    data = load(path)
    seen = {x['key'] for x in current}
    new, recovered = [], []
    for item in current:
        fingerprint = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
        old = data['issues'].get(item['key'])
        if not old or old['fingerprint'] != fingerprint:
            new.append(item)
        data['issues'][item['key']] = dict(item, fingerprint=fingerprint)
    if complete:
        for key in list(data['issues']):
            if key not in seen:
                recovered.append(data['issues'].pop(key))
    def enqueue(label, items):
        if items:
            body = '\n\n'.join(f"Report: {x['report']}\nStage: {x['stage']}\nProblem: {x['error']}\nNext action: {x['action']}" for x in items)
            data['outbox'].append(dict(subject='PIM ACES Workflow: ' + label,
                                       body=f'{clock.isoformat()}\n\n{body}', attempts=0))
    enqueue('Action required', new)
    enqueue('Recovered', [dict(x, action='Issue resolved; no action needed.') for x in recovered])
    day = clock.date().isoformat()
    # New alerts cover today's issues; no simultaneous duplicate daily reminder.
    if data['issues'] and clock.hour >= config['workflow'].get('reminder_hour_eastern', 9) and data['reminder_date'] != day:
        if not new:
            enqueue('Daily unresolved issues', list(data['issues'].values()))
        data['reminder_date'] = day
    save(path, data)
    return data


def deliver(state, config, sender=None):
    sender = sender or email_reports.send_outlook
    path = Path(state) / 'workflow_alerts.json'
    data = load(path)
    recipient = config['workflow']['failure_recipient']
    if recipient.casefold() != 'owner@example.invalid':
        raise ValueError('Workflow failure alerts must go only to owner@example.invalid.')
    for message in list(data['outbox']):
        message['attempts'] += 1
        save(path, data)
        try:
            sender(recipient, message['subject'], message['body'], '', '')
        except Exception as exc:
            message['last_error'] = type(exc).__name__
            save(path, data)
            raise RuntimeError('Outlook notification failed; durable outbox will retry.') from None
        data['outbox'].remove(message)
        save(path, data)


def notify_review(state, config, row):
    items = [issue('submission', row['file_id'], row['report_name'], row['last_error'],
                   'Review required; no automatic submission.')]
    reconcile(state, config, items, complete=False)
    deliver(state, config)
