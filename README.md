# ACES Differential Automation

**A web automation and data pipeline for recurring automotive catalog comparisons.**

Automotive catalogs change from month to month: applications are added or removed, parts change, and fitment notes are updated. Comparing successive catalog exports produces differential reports that capture those changes. Getting those reports into a usable history involves several systems, from a product information management (PIM) website to shared folders and SQL Server.

I built this automation to handle that process from the arrival of a new export through to organized report archives and analysis-ready SQL data. It selects the previous and current exports, submits them through the PIM website, collects the completed reports, and appends their contents to SQL Server. PIM performs the comparison; the pipeline manages the work around it.

The workflow is deployed on Windows and runs hourly, checking for new exports and continuing reports still in progress. Catalog exports follow a monthly cadence, with each comparison added to the existing history. The implementation uses Python, Playwright, SQL Server, Windows Task Scheduler, and Outlook.

![Pipeline illustration: catalog exports flow through automation of the PIM website, report collection, and SQL Server imports](assets/workflow-overview.png)

## How it works

The source files use ACES, the Aftermarket Catalog Exchange Standard, to describe automotive part-to-vehicle applications. A single part can have many applications, with details such as engine, position, quantity, and fitment notes. Each configured catalog keyword has its own stream of exports and comparison history.

1. **Find the next comparison.** The pipeline identifies a new export and pairs it with the appropriate previous version using the keyword, generation date, and modification time. It saves that pair so later runs continue with the same files.
2. **Submit through the website.** Playwright uploads both ZIPs, selects their exact filenames, enters the report name, and checks PIM's acceptance message. PIM processes the report separately; a later scheduled run can pick up the results.
3. **Archive and extract the results.** Once the two ZIPs and two accompanying `.error` files are complete and stable, the pipeline copies them into a folder for that keyword and comparison period. It preserves the originals and extracts each ZIP into a folder bearing its original name.
4. **Append the report to SQL Server.** The importer validates the eight TSV files and loads them into their corresponding tables. Each report adds to the accumulated history, including a record of files that contain no data rows.
5. **Send completion emails.** Submission and SQL import confirmations go to the configured recipient list. The SQL email includes the report name, full source filenames, destination tables, and row counts. Failures and reports needing attention go to the workflow owner.

The eight SQL tables hold PIM's application additions and deletions, part additions and deletions, all-parts output, part comparison output, unique notes, and VCDB notes. Each row includes `Meta_` columns identifying its report, catalog keyword, export dates, source file, source row, and import time. These identifiers make it possible to follow a SQL record back to the archived report without joining another table first.

The [business workflow](docs/business-workflow.md) includes a worked example. The [data contracts](docs/data-contracts.md) describe the files, destination tables, and metadata in detail.

## Ongoing operation

A saved tracker records where each report is in the process. One scheduled controller runs the stages in sequence and prevents overlapping runs. Reports can progress across multiple hourly runs while PIM finishes processing or a temporarily unavailable service recovers.

Retries depend on what happened. A temporary failure before submission can be retried on a later run. If PIM may have accepted a comparison but the browser did not capture confirmation, the report is held as `NeedsReview` and the owner is notified. This avoids submitting the same comparison again without knowing its status.

All eight TSV imports and their import-log entries commit together in one SQL transaction. After an interrupted connection, the next run checks the database log to determine whether the report was committed before attempting another import. Completed reports are skipped without reopening their TSV files, keeping routine checks from repeatedly processing the growing archive.

Email failures are handled separately: unsent notifications remain queued, and sending them again does not repeat the submission or import. Unresolved issues receive a daily reminder. Reports held for review still require a decision, and the Windows host, network access, and application connections need to remain available.

The [architecture documentation](docs/architecture.md) explains the retry rules, transaction boundaries, and recovery behavior.

## Code and documentation

This repository contains the original implementation with private company identifiers replaced. Credentials, production catalogs, licensed reference data, and operational state are excluded. The example configuration is disabled and must be adapted to an authorized environment before use.

The [workflow controller](src/workflow_runner.py) coordinates [export detection](src/detector.py), [PIM submission](src/pim_automation.py), [report collection](src/collect_reports.py), and [SQL import](src/import_reports.py). Table definitions and the TSV schema are in [src/sql](src/sql).

- [Setup and configuration](docs/reconstruction.md) — dependencies, local test commands, deployment settings, and scheduling.
- [Verification](docs/verification.md) — test coverage, recorded validation results, and operational limitations.
- [Sanitization](docs/sanitization.md) — what was retained, replaced, and excluded from the public source.

Built by [Can Kuvelet](https://kuvelet.github.io/portfolio/).
