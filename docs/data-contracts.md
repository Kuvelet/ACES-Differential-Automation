# Files, SQL tables and metadata

The [TSV schema](../src/sql/tsv_schema.json) lists every original column in order and its SQL type. The [table creation script](../src/sql/001_create_differential_aces_tables.sql) defines the eight destination tables, and the [import-log script](../src/sql/002_create_differential_aces_import_log.sql) defines the completion log. Database targets in both scripts use public placeholders.

## Report files

Each input ZIP filename contains an export UUID, catalog keyword, generation date, and full-export marker. The ZIP must contain an XML file. Before submission, the workflow checks the filename and ID, confirms the file is nonempty and stable, and reads the ZIP member listing. XML schema validation and fitment calculations are outside these checks.

Example archive layout using an invented catalog name:

```text
exports/
  BrandAlpha-ACES/
    2026-10-01_to_2026-11-01/
      123~BrandAlpha-ACES_Diff_2026-10-01_to_2026-11-01_ACESDifferential.zip
      123~BrandAlpha-ACES_Diff_2026-10-01_to_2026-11-01_NetChange.zip
      123~BrandAlpha-ACES_Diff_2026-10-01_to_2026-11-01_reference.error
      123~BrandAlpha-ACES_Diff_2026-10-01_to_2026-11-01_source.error
      123~BrandAlpha-ACES_Diff_2026-10-01_to_2026-11-01_ACESDifferential/
        BrandAlpha-ACES_Diff_2026-10-01_to_2026-11-01_AddedApps.tsv
        ...seven corresponding TSV files
      123~BrandAlpha-ACES_Diff_2026-10-01_to_2026-11-01_NetChange/
        ...remaining extracted PIM outputs
```

## SQL columns

| Column | Type | Population |
| --- | --- | --- |
| Meta_RowID | BIGINT IDENTITY, primary key | SQL-generated row identifier; gaps after rollback are valid |
| Meta_ReportID | UNIQUEIDENTIFIER | Tracker's current-export UUID for this saved comparison |
| Meta_Keyword | NVARCHAR(200) | Catalog keyword/folder label |
| Meta_PreviousExportDate | DATE | Previous export date from saved pair |
| Meta_CurrentExportDate | DATE | Current export date from saved pair |
| Meta_SourceFileName | NVARCHAR(255) | Exact TSV filename |
| Meta_SourceRowNumber | BIGINT | One-based data-record number after the header |
| Meta_ImportedAt | DATETIMEOFFSET(3) | One timestamp per report, Eastern timezone rules with EST/EDT offset |

Original text is preserved, including blanks, leading zeros, Unicode and whitespace. `Part` / `Part Number` use `NVARCHAR(100)`; other original text uses `NVARCHAR(MAX)`. `AppID`, quantity and per-part application counts are integers; year fields use `SMALLINT`. `BodyNumDoors` and `ValvesPerEngine` remain text. Invalid numbers and overlength bounded text are rejected rather than silently truncated.

The importer checks exact headers/order and supports UTF-8 BOM or UTF-16 BOM files. Header-only files still receive import-log entries with a zero count.

## Import log

Each log row records one file imported into a report table: report ID/name, table name, keyword, comparison dates, exact source filename, SHA-256, imported row count and import timestamp. The primary key is report ID plus table name; report name plus table name is also unique. The log commits in the same transaction as the data. This records completion even for empty files and lets a later run recover the result when a commit acknowledgement is lost.

A SHA-256 fingerprint is recorded on first import. Later runs check that the log identifies the expected report and covers all eight tables. They skip the completed files and historical SQL rows, assuming those records remain unchanged outside the pipeline.
