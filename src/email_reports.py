"""Classic Outlook per-pair notifications; email failures never resubmit PIM work."""
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import detector as d
logger = logging.getLogger('aces')


def signature(row):
    fields = ('file_id', 'previous_file_id', 'status', 'attempt_count', 'last_attempt_at',
              'submitted_at', 'report_name', 'confirmation', 'last_error')
    return hashlib.sha256(json.dumps([row.get(key, '') for key in fields]).encode()).hexdigest()


def compose(rows, run_error=None):
    now = datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M:%S %Z')
    lines = ['This is an automated message.', '']
    if run_error:
        lines.extend([f'The ACES differential automation encountered a run error at {now}.',
                      '', f'Error: {run_error}'])
        return 'PIM ACES Differential: Failed - Automation Run', '\n'.join(lines)
    if len(rows) != 1:
        raise ValueError('Pair emails require exactly one result.')
    row = rows[0]
    status = row['status']
    label = {'Succeeded': 'Submitted Successfully', 'Failed': 'Failed',
             'NeedsReview': 'Needs Review'}[status]
    report = row.get('report_name') or row['keyword']
    subject = (f'PIM Submission Successful | ACES Differentials | {report}'
               if status == 'Succeeded' else f'PIM ACES Differential: {label} - {report}')
    if status == 'Succeeded':
        submitted = row.get('submitted_at')
        if submitted:
            when = datetime.fromisoformat(submitted).astimezone(
                ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M:%S %Z')
            timing = f' at {when}'
        else:
            timing = ''
        lines.append('The following files were successfully uploaded, and the ACES differential '
                     f'process was started in the PIM ACES Differential tool{timing}.')
    elif status == 'Failed':
        lines.append('The ACES differential submission failed. This pair will be retried on the next run.')
    else:
        lines.append('The ACES differential submission could not be confirmed. This pair requires '
                     'review before retrying and will not be retried automatically.')
    if status != 'Succeeded' and row.get('last_error'):
        lines.extend(['', f"Error: {row['last_error']}"])
    lines.extend(['', 'Report name:', report, '', 'Previous file path:',
                  row['previous_file_path'], '', 'New file path:', row['new_file_path'], ''])
    return subject, '\n'.join(lines)


def recipients(settings, status):
    if status == 'Succeeded':
        result = (settings.get('to', ''), settings.get('cc', ''), settings.get('bcc', ''))
    else:
        result = (settings.get('error_to', ''), '', '')
    if not result[0]:
        raise ValueError('Email reporting requires a recipient for this outcome.')
    return result


def send_outlook(recipient, subject, body, cc='', bcc=''):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch('Outlook.Application')
        mail = outlook.CreateItem(0)
        mail.To = recipient
        mail.CC = cc
        mail.BCC = bcc
        mail.Subject = subject
        mail.Body = body
        mail.Send()
    finally:
        pythoncom.CoUninitialize()


def send_pair_report(rows, row, path, settings, sender=send_outlook):
    """Caller holds the tracker lock and passes the live rows to avoid stale writes."""
    if (not settings.get('enabled', False)
            or (settings.get('success_only') and row['status'] != 'Succeeded')
            or row['status'] not in {'Succeeded', 'Failed', 'NeedsReview'}
            or signature(row) == row.get('email_sent_signature', '')):
        return False
    recipient, cc, bcc = recipients(settings, row['status'])
    d.save_rows(path, rows)
    subject, body = compose([row])
    logger.info('Sending pair email: recipient=%s file_id=%s subject=%s', recipient, row['file_id'], subject)
    try:
        sender(recipient, subject, body, cc, bcc)
    except Exception as exc:
        row['email_error'] = f'Outlook send failed: {type(exc).__name__}'
        d.save_rows(path, rows)
        raise RuntimeError(f'Outlook email failed ({type(exc).__name__}); submission status unchanged.') from None
    row.update(email_sent_signature=signature(row),
               email_sent_at=datetime.now(timezone.utc).isoformat(), email_error='')
    d.save_rows(path, rows)
    logger.info('Pair email handed to Outlook: file_id=%s recipient=%s', row['file_id'], recipient)
    return True


def send_run_report(config_path, run_error=None, only_file_id=None, sender=send_outlook, exclude_file_ids=None):
    """Retry unsent results individually; run-level errors get a separate email."""
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    settings = config.get('email', {})
    if not settings.get('enabled', False):
        return False
    state = Path(config.get('state_folder', '.'))
    if not state.is_absolute():
        state = config_path.parent / state
    path = state / 'differential_tracker.csv'
    with d.RunLock(state / '.detector.lock'):
        rows = d.read_rows(path)
        reportable = [row for row in rows
                      if row['status'] in {'Succeeded', 'Failed', 'NeedsReview'}
                      and row['file_id'] not in (exclude_file_ids or set())
                      and (not only_file_id or row['file_id'] == only_file_id)
                      and signature(row) != row.get('email_sent_signature', '')]
        if not reportable and not run_error:
            logger.info('Email skipped: no new results or run errors.')
            return False
        errors = []
        for row in reportable:
            try:
                send_pair_report(rows, row, path, settings, sender=sender)
            except Exception as exc:
                errors.append(str(exc))
                logger.error('Pair email failed: file_id=%s error=%s', row['file_id'], exc)
        if run_error:
            subject, body = compose([], run_error)
            recipient, cc, bcc = recipients(settings, 'Failed')
            sender(recipient, subject, body, cc, bcc)
            logger.info('Run-error email handed to Outlook for %s.', recipient)
        if errors:
            raise RuntimeError('; '.join(errors))
        return True
