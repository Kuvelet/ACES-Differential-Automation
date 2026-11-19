"""Success-list notifications from committed SQL import-log records."""
from collections import defaultdict
from datetime import datetime
import json
from html import escape
from pathlib import Path
import re

import email_reports
import import_reports as imp
import workflow_alerts as alerts



def reports_from_entries(entries):
    groups = defaultdict(list)
    for entry in entries:
        groups[entry[0].lower()].append(entry)
    reports = {}
    expected = {'Differential_ACES_' + k for k in imp.KINDS}
    for identity, rows in groups.items():
        if (len(rows) != 8 or {r[2] for r in rows} != expected
                or len({r[1] for r in rows}) != 1 or len({r[4] for r in rows}) != 1
                or any(r[3] < 0 for r in rows)):
            raise ValueError('Inconsistent SQL import log; success notification withheld.')
        reports[identity] = dict(name=rows[0][1], counts={r[2]:int(r[3]) for r in rows}, imported_at=rows[0][4])
    return reports


def fetch_reports(settings):
    connection = imp.connect(settings)
    try:
        cursor = connection.cursor()
        actual = cursor.execute("SELECT CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')), CONVERT(nvarchar(128),SERVERPROPERTY('InstanceName')), DB_NAME()").fetchone()
        if tuple(actual) != ('CATALOGSQL', 'CATALOG', imp.DATABASE):
            raise ValueError('Unexpected SQL target; notification withheld.')
        # Read committed: these records commit atomically with all report data.
        rows = cursor.execute(f'SELECT CONVERT(varchar(36),Meta_ReportID),ReportName,TableName,ImportedRowCount,CONVERT(varchar(40),ImportedAt,121) FROM {imp.LOG}').fetchall()
        return reports_from_entries(rows)
    finally:
        connection.close()


def message(report):
    stamp = re.sub(r' ([+-]\d{2}:\d{2})$', r'\1', report['imported_at'])
    when = datetime.fromisoformat(stamp).astimezone(alerts.EASTERN).strftime('%B %d, %Y, at %I:%M:%S %p %Z')
    name = report['name']
    label, period = name.rsplit('_Diff_', 1)
    previous, current = [datetime.strptime(value, '%Y-%m-%d').strftime('%B %d, %Y') for value in period.split('_to_')]
    cell = 'padding:9px;border:1px solid #d7dde5;'
    rows = []
    for kind in imp.KINDS:
        table = 'Differential_ACES_' + kind
        rows.append(f'<tr><td style="{cell}overflow-wrap:anywhere;">{escape(name + "_" + kind + ".tsv")}</td>'
                    f'<td style="{cell}">dbo.{escape(table)}</td>'
                    f'<td style="{cell}text-align:right;white-space:nowrap;">{report["counts"][table]:,}</td></tr>')
    total = sum(report['counts'].values())
    body = f'''<html><body style="font-family:Calibri,Arial,sans-serif;font-size:11pt;color:#202b38;">
<p>This is an automated email.</p>
<p>The automated workflow for the ACES differential report below is complete. All eight TSV files have been successfully imported into SQL Server.</p>
<p><strong>Report name:</strong> {escape(name)}<br>
<strong>Previous export date:</strong> {previous}<br>
<strong>Current export date:</strong> {current}<br>
<strong>Import completed:</strong> {when}</p>
<p><strong>Server:</strong> {escape(imp.SERVER)}<br><strong>Database:</strong> {escape(imp.DATABASE)}</p>
<p><strong>Import details</strong></p>
<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-family:Calibri,Arial,sans-serif;font-size:10pt;width:100%;">
<tr style="background-color:#eaf0f6;"><th align="left" style="{cell}">TSV file</th><th align="left" style="{cell}">Destination table</th><th align="right" style="{cell}">Rows imported</th></tr>
{''.join(rows)}
<tr style="background-color:#f3f6f9;"><td colspan="2" style="{cell}"><strong>Total</strong></td><td align="right" style="{cell}"><strong>{total:,}</strong></td></tr></table>
<p><strong>Files imported:</strong> 8 of 8<br><strong>Status:</strong> Completed successfully</p>
<p>No action is required.</p><p>ACES Differential Automation</p></body></html>'''
    return {'subject': 'SQL Import Complete | ACES Differentials | ' + name,
            'body': body, 'format': 'html', 'attempts': 0}


def send_html(recipient, subject, body, cc='', bcc=''):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch('Outlook.Application')
        mail = outlook.CreateItem(0)
        mail.To, mail.CC, mail.BCC = recipient, cc, bcc
        mail.Subject = subject
        mail.HTMLBody = body
        mail.Send()
    finally:
        pythoncom.CoUninitialize()


def queue(path, reports):
    # Missing state is not silently recreated: that could email all historical reports.
    data = json.loads(path.read_text(encoding='utf-8'))
    for identity, report in reports.items():
        if identity not in data['completed'] and identity not in data['outbox']:
            data['outbox'][identity] = message(report)
    alerts.save(path, data)


def deliver(path, sender=None, settings=None):
    if settings is None:
        raise ValueError("Success email recipient configuration is required.")
    recipient, cc, bcc = email_reports.recipients(settings, "Succeeded")
    data = json.loads(path.read_text(encoding='utf-8'))
    failures = 0
    for identity, item in list(data['outbox'].items()):
        item['attempts'] += 1
        alerts.save(path, data)
        try:
            send = sender or (send_html if item.get('format') == 'html' else email_reports.send_outlook)
            send(recipient, item['subject'], item['body'], cc, bcc)
        except Exception as exc:
            item['last_error'] = type(exc).__name__
            failures += 1
        else:
            data['completed'][identity] = alerts.now().isoformat()
            del data['outbox'][identity]
        alerts.save(path, data)
    if failures:
        raise RuntimeError('SQL success email could not be sent; pending notifications will retry next run.')


def run(config_path):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if not config.get('workflow', {}).get('sql_success_email_enabled', False):
        return 0
    state = Path(config['state_folder'])
    if not state.is_absolute():
        state = config_path.parent / state
    path = state / 'sql_success_notifications.json'
    queue(path, fetch_reports(config['sql_import']))
    deliver(path, settings=config["email"])
    return 0
