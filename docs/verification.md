# Verification

The tests cover the workflow's normal operation and its response to interruptions, invalid inputs, and repeated runs. They use temporary directories, synthetic files, a local browser fixture, and mocked SQL and Outlook connections. Running them does not submit reports to PIM, send email, or import company data.

## Test coverage

| Test module | Behavior covered |
| --- | --- |
| `test_detector.py` | Baseline setup, predecessor ordering, aliases, backdated arrivals, ties, missing inputs, incremental discovery, and locks |
| `test_pim_automation.py` | Matching acceptance messages, retries before Compare, uncertain submissions, interrupted state, and tracker-save failures |
| `test_pim_browser.py` | File selection, confirmation matching, and browser recovery using Chromium and a local HTML fixture |
| `test_collect_reports.py` | Complete output sets, matching prefixes, changing or corrupt ZIPs, unsafe extraction paths, conflicts, and recovery |
| `test_import_reports.py` | Headers, types, text preservation, metadata, rollback, lost commit responses, duplicate prevention, and SQL destination checks |
| `test_collection_runner.py` | Collection/import helper ordering and failure isolation |
| `test_email_reports.py` | Submission notification state, retries, and repeat suppression |
| `test_sql_success_emails.py` | Eight-file completion, row counts, recipients, HTML escaping, notification baselines, and saved email retries |
| `test_workflow.py` | Stage ordering, overlap prevention, timeouts, Windows job objects, tracker backups, owner alerts, daily reminders, and completed-import skips |

The suite includes actual child processes and a Windows job-object termination test. SQL tests use a simulated connection and cursor to exercise the importer's transaction handling. They cover how the code responds to connection failures; they do not reproduce a network outage against a running SQL Server. Error messages from deliberately injected failures can appear during a passing test run.

The sanitized source passed locally on Windows on November 19, 2026, and in the first public [Windows/Python 3.12 CI run](https://github.com/Kuvelet/ACES-Differential-Automation/actions/runs/44599878973). See [setup instructions](reconstruction.md#local-setup) to run the tests, or [GitHub Actions](https://github.com/Kuvelet/ACES-Differential-Automation/actions/workflows/tests.yml) for subsequent results.

## Deployment checks

An internal check on November 19, 2026 recorded 10 completed reports, 80 committed file imports, and 1,613,045 data rows. These are report records accumulated by that date, rather than counts of unique parts or vehicles. An idle run completed without new submissions, collections, duplicate rows, or unresolved tracker issues.

A before-and-after check of the routine-work optimizations recorded:

| Idle stage | Before | After |
| --- | ---: | ---: |
| Export discovery | 9.460 s | 2.750 s |
| Completed-import checks | 3.118 s | 1.273 s |
| Full workflow | Approximately 15 s | Approximately 7 s |

The tracker contents and modification time stayed unchanged, as did the SQL totals. These timings describe one idle-run comparison. Directory size, network latency, new reports, SQL load, and Outlook behavior affect later runs; importing a new report involves additional work.

Detailed production logs and database extracts remain private because they contain operational identifiers. The figures above are a dated record of validation, not a live measure of the ongoing workflow.

## Recovery limits

PIM acceptance is checked through its confirmation message. The implementation has no verified task-history lookup, so an uncertain submission still needs review. Missing output alone cannot establish whether PIM accepted a job.

The SQL transaction covers all eight data imports and their log entries. Files are copied through staging and verification, then published individually. An interrupted copy can leave a partial destination; a retry checks existing content and completes the remaining work. Completed imports are treated as final, without auditing later manual changes to TSVs or SQL rows.

Saved notification state allows email retries, but Outlook accepting a message does not confirm inbox delivery. A crash after that handoff and before saving its result can also cause a repeated email.

Production PIM, SQL, and Outlook were not deliberately interrupted during validation. Failure scenarios were exercised with isolated fixtures and mocks. Monitoring a host that never starts, and rotating old logs and screenshots, require separate operational arrangements.
