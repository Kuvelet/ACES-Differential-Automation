# Verification and evidence

## Published source validation

On November 19, 2026, the sanitized source passed **113 tests** on Windows in **12.072 seconds**. The run used the existing local Python environment, isolated temporary workspaces, synthetic files, a local browser fixture, and mocked SQL/Outlook interfaces. It did not submit reports, send email, or import data into company systems.

| Test module | Tests | Main behavior covered |
| --- | ---: | --- |
| `test_detector.py` | 25 | Baseline, predecessor ordering, aliases, backdated arrivals, ties, missing inputs, incremental discovery and locks |
| `test_pim_automation.py` | 14 | Exact acceptance, pre-click retry, uncertainty hold, interrupted state and persistence failures |
| `test_pim_browser.py` | 2 | Real Chromium against a local fixture; file selection, confirmation matching and browser recovery |
| `test_collect_reports.py` | 16 | Complete sets, matching prefixes, changing or corrupt ZIPs, unsafe paths, conflicts and recovery |
| `test_import_reports.py` | 18 | Headers/types, text preservation, metadata, rollback, lost commit response, duplicates and SQL target checks |
| `test_collection_runner.py` | 5 | Original collection/import helper ordering and failure isolation |
| `test_email_reports.py` | 6 | Submission notification state, retries and repeat suppression |
| `test_sql_success_emails.py` | 7 | Eight-file completion, per-file counts, audience, HTML escaping, baseline and durable retries |
| `test_workflow.py` | 20 | Stage ordering, overlap, timeouts, Windows job containment, tracker backup, owner alerts, daily reminders and ledger skips |
| **Total** | **113** | |

The suite includes real child processes and a real Windows job-object termination test. SQL transactions are tested through an executable connection/cursor double; these tests do not prove SQL Server behavior under an actual network outage. Expected error messages in a passing run are deliberately injected test scenarios.

[Run instructions](../README.md#inspect-and-test-the-source) · [GitHub Actions](https://github.com/Kuvelet/ACES-Differential-Automation/actions/workflows/tests.yml)

## Recorded deployment checkpoint

The internal November 19 validation recorded 10 completed reports, 80 committed file-import records, and 1,613,045 data rows. An idle verification completed without new submissions, new collections, duplicate rows or unresolved tracker issues. These values describe that checkpoint, not a live status endpoint or unique catalog entities.

The optimization comparison recorded:

| Idle stage | Before | After |
| --- | ---: | ---: |
| Discovery | 9.460 s | 2.750 s |
| SQL completed-import checks | 3.118 s | 1.273 s |
| Complete workflow | Approximately 15 s | Approximately 7 s |

The tracker contents and modification time remained unchanged, and SQL totals did not change. This is one observed comparison. Directory size, share latency, new reports, SQL load and Outlook behavior all affect runtime. It is not a throughput benchmark for importing new data.

Detailed production logs, screenshots and database extracts are excluded because they reveal operational identifiers. The public aggregate figures are transcribed from the recorded internal validation; public readers can independently run the regression tests, but cannot reproduce the private deployment checkpoint from this repository.

## Claims and limits

- An acceptance toast proves what the script observed in PIM. There is no verified task-history reconciliation API.
- A committed import-log group is the authority for routine duplicate checks. Later manual edits to historical TSVs or SQL data are not audited.
- SQL atomicity covers the eight data imports and their log entries. It does not create a transaction spanning PIM, file shares and Outlook.
- File publication is staged and verified, then performed file by file. A process interruption can leave a partial destination; retry verifies existing content and completes it.
- Notification persistence supports retry. Outlook handoff is not proof of delivery, and a crash after handoff can produce a duplicate email.
- The deployed run did not deliberately interrupt production PIM, SQL or Outlook. Failure tests use isolated fixtures and mocks.
- No external monitor detects a computer that never starts the workflow. No automatic log-retention policy is included.

The overview image is a workflow illustration. It is not a screenshot, a recording, or evidence of a particular run.
