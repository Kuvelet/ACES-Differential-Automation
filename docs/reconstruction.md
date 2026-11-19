# Setup and maintenance

The tests can run locally without company access. Running the full workflow requires an authorized PIM environment with the expected page structure and report format, writable file locations, SQL Server, and a configured Outlook desktop profile. The public example starts disabled.

```text
src/                         Original implementation with sanitized identifiers
  workflow_runner.py         Current entry point
  setup_workflow_schedule.ps1 Hourly task registration
  sql/                       Eight-table DDL, import-log DDL, TSV schema
  tests/pim_fixture.html     Local HTML used by browser tests
tests/                       Original regression tests
config.example.json          Disabled placeholder configuration
docs/                        Workflow, file formats, setup and recovery
assets/                      Workflow illustration (SVG and PNG)
```

`scheduled_runner.py` supplies the DPAPI credential reader used by the current workflow. Its standalone launcher, `main.py`, and `collection_runner.py` retain the earlier entry points. Use the workflow controller for coordinated operation; do not schedule those launchers alongside it.

## Local setup

On Windows with Python 3.12 or later, run these commands from the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
$env:PYTHONPATH = "$pwd/src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The browser tests use a local HTML fixture and require Chromium. Windows is required for the process-containment tests. Company credentials, SQL Server, and Outlook are not required for the tests. No scheduled task is registered by installing dependencies or running tests.

The following command checks the controller's disabled behavior using the example configuration:

```powershell
.\.venv\Scripts\python.exe src/workflow_runner.py --config config.example.json
```

It should report that the workflow is disabled and exit without running stages.

## Deployment configuration

Copy `config.example.json` to `src/config.json` and keep `workflow.enabled` false while configuring an independent environment. Operational state and credentials must remain outside Git. The example stores files under `C:/ACES-Workflow/`.

| Setting | What to configure |
| --- | --- |
| `source_folder`, `keywords`, optional `baseline_before` | Authorized export location, exact keyword streams and any historical cutoff |
| `state_folder` | Local folder for the tracker, locks, logs, and notification state |
| `pim` | PIM URL/account, invalid-input hold, and UI selectors in `pim_automation.py` verified against the authorized environment |
| `collection` | PIM output share, organized destination and stability wait |
| `sql_import` | Driver, reviewed SQL target, batch size and timeout; retain completed-import checks and invalid-input hold |
| `email` | Success recipients; retain `success_only: true` so failures use the owner channel |
| `workflow` | Owner recipient, credential file, stage timeouts, reminder hour, and overdue-output threshold |

The code checks the SQL destination and failure recipient against fixed settings to prevent writes or alerts going to the wrong place. For another deployment, update matching placeholders consistently in configuration, `import_reports.py` (`SERVER`, `DATABASE`, machine/instance checks), `sql_success_emails.py` (target check), both SQL DDL scripts, and `workflow_alerts.py` (owner guard). Keep the tests aligned with the intended guards. Do not simply remove the guards to bypass a mismatch.

The `Example Catalog_` prefix handling and legacy keyword aliases are defined in `detector.py` and `collect_reports.py`. Update them if the export naming rules differ. Also verify browser selectors against the actual PIM page; the local HTML fixture only exercises the expected interaction.

The SQL connection uses Windows authentication and the original encrypted connection settings, including `TrustServerCertificate=yes`. Review certificate validation for the target environment. The importer uses `OPENJSON`; its SQL Server database must support that statement. The public package does not install an ODBC driver or SQL Server.

Review and execute `src/sql/001_create_differential_aces_tables.sql`, followed by `002_create_differential_aces_import_log.sql`, against the intended database. Both scripts guard their target and refuse to overwrite existing objects. They do not create the database or migrate an existing schema.

With the workflow still disabled, initialize the detector once:

```powershell
.\.venv\Scripts\python.exe src/detector.py --config src/config.json --initialize
```

Initialization records ready existing exports as `Baseline`, leaving them out of the submission queue. It refuses an empty baseline, an existing initialization, or unsafe state. The baseline determines which exports count as history and which later arrivals become new work. Recover a missing tracker from reviewed state rather than initializing again.

For an existing collected report, the importer also provides `--validate-only`; it checks the files without connecting to SQL or updating the tracker. `--execute-once` authorizes actual database writes even when the import configuration is paused. It is not a dry run.

Create the encrypted PIM credential with PowerShell `Get-Credential | Export-Clixml` under the Windows account that will own the scheduled task, writing to the private path configured by `workflow.credential_file`. The DPAPI export is tied to that Windows user and computer. Do not commit or copy a working credential into this repository.

Before enabling SQL success emails, initialize `sql_success_notifications.json` in the state folder while the workflow is stopped. The structure is:

```json
{
  "completed": {},
  "outbox": {}
}
```

An empty `completed` map is appropriate only when the SQL import log has no historical reports. For an existing import log, use `sql_success_emails.fetch_reports(settings)` to validate and read the committed groups, then record each returned report UUID in `completed` with a baseline timestamp. Persist with `workflow_alerts.save`; do not overwrite an existing notification file. This baseline suppresses historical success emails. Once notifications are enabled, missing state stops SQL success emails until the state is restored.

The owner-alert state is initialized by its module when absent. Submission success signatures are stored on the tracker rows. Preserve all three forms of notification state when moving or restoring a deployment.

## Enable the schedule

After checking the paths, SQL destination, recipients, baseline, and credentials, enable the intended collection/import/email settings and `workflow.enabled`. A manual controller run performs real submissions, file copies, SQL writes, and emails. Review its stage logs and final JSON summary before registering the schedule.

The registration script schedules that same controller hourly, on the next hour, using the current interactive Windows account and the repository's `.venv`. It uses `IgnoreNew`, starts missed runs when available, and allows six hours overall so the shorter stage timeouts normally handle cleanup first. It refuses active legacy tasks or a different action already registered under its task name.

```powershell
# Registers an active recurring task; run only after deployment validation.
.\src\setup_workflow_schedule.ps1
```

The PC must be awake, the user logged in, and the required network resources reachable. A locked screen is compatible with the interactive account. Registration does not wake the PC. Keep one controller per state directory and do not enable legacy schedules in parallel.

## Troubleshooting

| Observation | Response |
| --- | --- |
| Submission `Failed` before Compare | Fix the transient cause; eligible work retries on a later hourly run. |
| Submission `NeedsReview` | Verify the actual PIM job before changing status. Never assume a missing popup or missing output means rejection. |
| Collection waiting/incomplete | Allow PIM to finish or restore share access. Overdue waits become owner alerts after the configured threshold. |
| Collection conflict or invalid ZIP | Preserve evidence, resolve the source/destination conflict, then allow collection to retry. |
| SQL transient error | Restore connectivity; the next run reconciles the committed log before attempting new inserts. |
| SQL `NeedsReview` | Review malformed source or inconsistent import-log identity; explicitly release the held report only after correction. Do not delete the import log to force a repeat. |
| Tracker missing or unreadable | Stop the workflow and reconcile a backup against PIM and SQL. An old snapshot can misrepresent external work already completed. |
| Email failure | Restore Outlook; queued notifications retry independently. No resubmission or reimport is required. |
| Host or scheduler never starts | Check through independent monitoring; this workflow cannot notify while it is not running. |

See [architecture and recovery](architecture.md) for how transactions, retries, and saved state interact. Arrange log and screenshot retention and backup rotation for the deployment.
