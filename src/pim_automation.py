"""Submit fixed CSV pairs through PIM's visible Playwright workflow."""
import os
import logging
from pathlib import Path, PureWindowsPath
import re
import time
import zipfile
from datetime import datetime, timezone

import detector as d

logger = logging.getLogger('aces')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def check_inputs(row, seconds=4):
    """Check both saved inputs again immediately before browser work."""
    paths = [Path(row[key]) for key in ('previous_file_path', 'new_file_path')]
    snapshots = [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
    time.sleep(seconds)
    for path, before in zip(paths, snapshots):
        after = path.stat()
        if before[0] == 0 or before != (after.st_size, after.st_mtime_ns):
            raise ValueError(f'Input is empty or still changing: {path}')
        with zipfile.ZipFile(path) as archive:
            if not any(name.lower().endswith('.xml') for name in archive.namelist()):
                raise ValueError(f'No XML export found in ZIP: {path}')
    d.build_report_name(row)  # Both paths must match the row's keyword.
    for column, identity in [('previous_file_path', 'previous_file_id'), ('new_file_path', 'file_id')]:
        if PureWindowsPath(row[column]).name.split('~', 1)[0].lower() != row[identity]:
            raise ValueError(f'{column} does not match the saved file ID.')


class PIMSubmitter:
    def __init__(self, page, settings, username, password):
        self.page = page
        self.settings = settings
        self.username = username
        self.password = password
        self.stage = 'starting'

    def pause(self, upload=False):
        key = 'upload_delay_seconds' if upload else 'action_delay_seconds'
        seconds = self.settings.get(key, 5 if upload else 2)
        self.page.wait_for_timeout(float(seconds) * 1000)

    def click(self, locator):
        locator.click()
        self.pause()

    def option(self, path):
        filename = PureWindowsPath(path).name
        # Exact ZIP prefix plus the contained XML. Never select by brand/date alone.
        return self.page.get_by_role('option', name=re.compile(
            r'^' + re.escape(filename) + r':\s+.+\.xml$', re.IGNORECASE))

    def dropdown(self, control_id):
        # Syncfusion places a clickable combobox wrapper over its readonly input.
        return self.page.locator('div[role="combobox"]').filter(
            has=self.page.locator('#' + control_id))

    def upload(self, path):
        from playwright.sync_api import expect
        page = self.page
        logger.info('Opening file chooser: %s', PureWindowsPath(path).name)
        with page.expect_file_chooser() as chooser:
            self.click(page.get_by_role('button', name='Browse', exact=True))
        chooser.value.set_files(path)
        self.pause()
        logger.info('File selected; clicking Upload.')
        self.click(page.get_by_role('button', name='Upload', exact=True))
        expect(page.get_by_role('button', name='Upload', exact=True)).to_be_hidden(
            timeout=self.settings.get('upload_timeout_ms', 120000))
        page.wait_for_load_state('networkidle', timeout=self.settings.get('upload_timeout_ms', 120000))
        self.pause(upload=True)
        # Do not open/close dropdowns between uploads: Escape can dismiss PIM's
        # entire modal. Verify each exact uploaded filename during selection below.
        expect(page.locator('#DashboardDialog')).to_be_visible()
        logger.info('Upload control cleared; verifying exact filename during selection.')

    def select(self, control_id, path):
        from playwright.sync_api import expect
        control = self.page.locator('#' + control_id)
        expect(control).to_be_enabled()
        self.click(self.dropdown(control_id))
        option = self.option(path)
        expect(option).to_have_count(1)
        text = option.inner_text().strip()
        logger.info('Selecting %s: %s', control_id, text)
        self.click(option)
        # PIM refreshes dependent controls asynchronously after a selection.
        self.page.wait_for_load_state('networkidle', timeout=self.settings.get('action_timeout_ms', 30000))
        expect(control).to_have_value(text, timeout=self.settings.get('action_timeout_ms', 30000))
        return text

    def submit(self, row, before_compare):
        from playwright.sync_api import expect
        page = self.page
        page.set_default_timeout(self.settings.get('action_timeout_ms', 30000))
        self.stage = 'login'
        logger.info('Stage: login')
        page.goto(self.settings.get('login_url', 'http://pim.example.invalid/Login'))
        for label, value in [('User Name', self.username), ('Password', self.password)]:
            field = page.get_by_role('textbox', name=label, exact=True)
            self.click(field)
            field.press('ControlOrMeta+A')
            field.press_sequentially(value, delay=50)
            field.press('Tab')
            self.pause()
        self.click(page.get_by_role('button', name='Log In', exact=True))
        self.stage = 'open differential'
        logger.info('Stage: open ACES Differential')
        self.click(page.get_by_role('menuitem', name='ACES', exact=True))
        self.click(page.get_by_role('menuitem', name='ACES Reports', exact=True))
        self.click(page.get_by_role('menuitem', name='Differential', exact=True))
        expect(page.locator('#DashboardDialog')).to_be_visible()
        self.stage = 'upload previous file'
        self.upload(row['previous_file_path'])
        self.stage = 'upload new file'
        self.upload(row['new_file_path'])
        self.stage = 'select saved pair'
        logger.info('Stage: select saved pair')
        previous_text = self.select('DropDownListACESCompareReference', row['previous_file_path'])
        new_text = self.select('DropDownListACESCompareSource', row['new_file_path'])
        self.stage = 'report name'
        logger.info('Stage: enter and commit report name')
        report_name = d.build_report_name(row)
        name = page.get_by_role('textbox', name='Report Name', exact=True)
        name.fill('')
        name.press_sequentially(report_name, delay=30)
        name.press('Tab')  # Commit on blur without an Enter-triggered form submission.
        self.pause()
        expect(name).to_have_value(report_name)
        expect(page.locator('#DropDownListACESCompareReference')).to_have_value(previous_text)
        expect(page.locator('#DropDownListACESCompareSource')).to_have_value(new_text)
        # Part File and Priority retain the defaults used in the successful walkthrough.
        self.stage = 'Compare / Task Added confirmation'
        logger.info('Stage: Compare; report=%s', report_name)
        message = f'Added {report_name}'
        popup = page.locator('[id^="toast_"]').filter(
            has=page.get_by_text('Task', exact=True)).filter(
            has=page.get_by_text(message, exact=True))
        # Fresh page per attempt ensures a previous run's toast cannot confirm this run.
        expect(popup).to_have_count(0)
        before_compare()  # Persist uncertainty BEFORE the external submission boundary.
        self.click(page.get_by_role('button', name='Compare', exact=True))
        expect(popup).to_be_visible(timeout=self.settings.get('confirmation_timeout_ms', 60000))
        logger.info('PIM confirmation observed: Task — %s', message)
        return f'Task — {message}'


def process_rows(rows, csv_path, submit, stability_seconds=4, evidence=None, only_file_id=None, notify=None, hold_invalid=False):
    """Durable per-pair state transitions; never retry an uncertain submission."""
    for row in rows:
        if row['status'] == 'Running':
            row.update(status='NeedsReview', last_error='Previous run interrupted. Check PIM before retrying.')
            logger.warning('Interrupted pair needs review: file_id=%s', row['file_id'])
        elif row['status'] == 'Failed' and (not only_file_id or row['file_id'] == only_file_id):
            row['status'] = 'Pending'
            logger.info('Retrying previously Failed pair: file_id=%s prior_attempts=%s', row['file_id'], row['attempt_count'])
    d.save_rows(csv_path, rows)  # Also verify CSV is writable before touching PIM.
    def notify_result(row):
        if notify:
            try:
                notify(row)
            except Exception as exc:
                logger.error('Pair notification failed: file_id=%s error=%s; PIM status unchanged', row['file_id'], exc)

    for row in rows:
        if row['status'] != 'Pending' or (only_file_id and row['file_id'] != only_file_id):
            continue
        try:
            logger.info('Checking inputs: file_id=%s previous=%s new=%s', row['file_id'], row['previous_file_path'], row['new_file_path'])
            check_inputs(row, stability_seconds)
            report_name = d.build_report_name(row)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            invalid = hold_invalid and (isinstance(exc, zipfile.BadZipFile) or (isinstance(exc, ValueError) and 'still changing' not in str(exc)))
            row.update(status='NeedsReview' if invalid else 'Failed', last_error=f'Input check: {exc}')
            logger.error('Input check failed: file_id=%s error=%s', row['file_id'], exc)
            d.save_rows(csv_path, rows)
            notify_result(row)
            continue
        row.update(status='Running', report_name=report_name, last_error='',
                   last_attempt_at=utc_now(), submitted_at='', confirmation='',
                   attempt_count=str(int(row['attempt_count']) + 1))
        d.save_rows(csv_path, rows)
        logger.info('Pair attempt started: file_id=%s attempt=%s report=%s', row['file_id'], row['attempt_count'], report_name)
        crossed_boundary = False
        persistence_failed = False

        def before_compare():
            nonlocal crossed_boundary, persistence_failed
            row.update(status='NeedsReview', last_error='Compare requested; confirmation not recorded yet. Check PIM before retrying.')
            try:
                d.save_rows(csv_path, rows)
            except OSError:
                persistence_failed = True
                raise
            crossed_boundary = True
            logger.info('Submission boundary saved: file_id=%s status=NeedsReview', row['file_id'])

        try:
            confirmation = submit(row, before_compare)
            expected = f'Task — Added {report_name}'
            if confirmation != expected or not crossed_boundary:
                raise RuntimeError('The expected Task Added confirmation was not verified.')
        except Exception as exc:
            if persistence_failed:
                raise
            row.update(status='NeedsReview' if crossed_boundary else 'Failed', last_error=str(exc))
        else:
            row.update(status='Succeeded', submitted_at=utc_now(), confirmation=confirmation, last_error='',
                       collection_status='Waiting', collection_destination='', collected_at='', collection_error='')
        # Failure to persist success must stop the batch, not retry or overwrite its status.
        d.save_rows(csv_path, rows)
        logger.log(logging.INFO if row['status'] == 'Succeeded' else logging.ERROR,
                   'Pair result: file_id=%s attempt=%s status=%s report=%s error=%s',
                   row['file_id'], row['attempt_count'], row['status'], report_name, row['last_error'])
        if evidence:
            try:
                evidence(row)
            except Exception as exc:
                logger.warning('Screenshot unavailable for %s: %s; status unchanged', row['file_id'], type(exc).__name__)
        notify_result(row)
    return rows


def run_pending(config_path, password=None, headed=False, only_file_id=None, email_attempted_ids=None):
    import json
    from email_reports import send_pair_report
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    state = Path(config.get('state_folder', '.'))
    if not state.is_absolute():
        state = config_path.parent / state
    path = state / 'differential_tracker.csv'
    if not (state / '.detector_initialized').exists() or not path.exists():
        raise RuntimeError('Initialize the detector before submitting pairs.')
    with d.RunLock(state / '.detector.lock'):
        rows = d.read_rows(path)
        if only_file_id and not any(r['file_id'] == only_file_id for r in rows):
            raise ValueError('Requested file ID is not in the tracker.')
        selected = [r for r in rows if not only_file_id or r['file_id'] == only_file_id]
        # Handle interrupted rows even when no new pending work remains.
        for row in rows:
            if row['status'] == 'Running':
                row.update(status='NeedsReview', last_error='Previous run interrupted. Check PIM before retrying.')
        d.save_rows(path, rows)
        if not any(r['status'] in {'Pending', 'Failed'} for r in selected):
            print('No Pending or Failed pairs to submit.')
            return sum(r['status'] in {'Failed', 'NeedsReview'} for r in selected)
        settings = config.get('pim', {})
        username = os.environ.get('PIM_USERNAME') or settings.get('username', 'automation-user')
        password = password or os.environ.get('PIM_PASSWORD')
        if not password:
            import getpass
            import sys
            if not sys.stdin.isatty():
                raise RuntimeError('Set PIM_PASSWORD for unattended runs, or use an interactive terminal for the password prompt.')
            password = getpass.getpass(f'PIM password for {username}: ')
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            session = RecoveringBrowser(playwright.chromium, headed=headed)

            def submit(row, before_compare):
                runner = None
                try:
                    page = session.new_page()
                    runner = PIMSubmitter(page, settings, username, password)
                    return runner.submit(row, before_compare)
                except Exception as exc:
                    session.restart_required = True
                    stage = runner.stage if runner else 'browser startup'
                    # Never include the raw Playwright call log: it can contain filled credentials.
                    logger.error('Browser failure: file_id=%s attempt=%s stage=%s exception=%s restart_next_pair=True',
                                 row['file_id'], row['attempt_count'], stage, type(exc).__name__)
                    try:
                        folder = state / 'screenshots'
                        folder.mkdir(exist_ok=True)
                        detail = folder / f'{row["file_id"]}_{row["attempt_count"]}_error.txt'
                        # Login errors omit raw call logs that could contain credentials.
                        description = type(exc).__name__ if stage == 'login' else str(exc).replace(password, '[redacted]')
                        detail.write_text(f'{stage}\n{description}', encoding='utf-8')
                        logger.info('Detailed error saved: %s', detail)
                        if runner and stage != 'login' and not page.is_closed():
                            screenshot_path = folder / f'{row["file_id"]}_{row["attempt_count"]}_Failed.png'
                            page.screenshot(path=str(screenshot_path), timeout=5000)
                            logger.info('Failure screenshot saved: %s', screenshot_path)
                    except Exception as diagnostic_error:
                        logger.warning('Could not capture failure diagnostics: %s', type(diagnostic_error).__name__)
                    raise RuntimeError(f'{stage}: {type(exc).__name__}. '
                                       'If Compare was requested, verify the PIM task before retrying.') from None

            def evidence(row):
                page = session.page
                if page and row['status'] in {'Succeeded', 'NeedsReview'}:
                    folder = state / 'screenshots'
                    folder.mkdir(exist_ok=True)
                    page.screenshot(path=str(folder / f'{row["file_id"]}_{row["attempt_count"]}_{row["status"]}.png'), timeout=5000)
                    logger.info('Screenshot saved: %s', folder / f'{row["file_id"]}_{row["attempt_count"]}_{row["status"]}.png')

            def notify(row):
                if row["status"] == "NeedsReview" and config.get("workflow", {}).get("enabled"):
                    from workflow_alerts import notify_review
                    notify_review(state, config, row)
                if email_attempted_ids is not None:
                    email_attempted_ids.add(row['file_id'])
                send_pair_report(rows, row, path, config.get('email', {}))

            try:
                process_rows(rows, path, submit, config.get('stability_seconds', 4), evidence,
                             only_file_id=only_file_id, notify=notify, hold_invalid=settings.get('hold_invalid', False))
            finally:
                session.close()
        return sum(r['status'] in {'Failed', 'NeedsReview'} or bool(r.get('email_error')) for r in selected)


class RecoveringBrowser:
    """Restart dead browser processes before a later pair; never resubmit a pair."""
    def __init__(self, chromium, headed=False):
        self.chromium = chromium
        self.headed = headed
        self.browser = None
        self.context = None
        self.page = None
        self.restart_required = False

    def close(self):
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass  # A dead process must not hide a saved result.
        self.browser = self.context = self.page = None

    def new_page(self):
        if self.restart_required or (self.browser and not self.browser.is_connected()):
            logger.warning('Restarting browser before the next pair.')
            self.close()
            self.restart_required = False
        elif self.context:
            try:
                self.context.close()
            except Exception:
                self.close()
        self.context = self.page = None
        # Retry browser/context creation once if a process dies between checks.
        for attempt in range(2):
            try:
                if not self.browser:
                    logger.info('Launching browser: headed=%s', self.headed)
                    self.browser = self.chromium.launch(headless=not self.headed)
                self.context = self.browser.new_context()
                self.page = self.context.new_page()
                return self.page
            except Exception:
                logger.warning('Browser/context startup failed; recovery_attempt=%s', attempt + 1)
                self.close()
                if attempt:
                    raise
