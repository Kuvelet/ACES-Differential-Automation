# Files, SQL tables and metadata

The schema contract in [tsv_schema.json](../src/sql/tsv_schema.json) lists every original column, in order, and its approved SQL type. The [table DDL](../src/sql/001_create_differential_aces_tables.sql) and [import-log DDL](../src/sql/002_create_differential_aces_import_log.sql) preserve that implementation with public placeholder targets.

## Input and output identities

Input ZIP names encode an export UUID, configured catalog keyword, generation date and full-export marker. The ZIP must contain an XML member. The submission workflow checks filenames and IDs, nonzero/stable size and timestamps, and the ZIP member listing. It does not validate the XML against an ACES schema or recalculate fitment.

Illustrative layout (these are names, not bundled company files):

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

## Metadata in every data table

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

## Import-log contract

One log row identifies one report/table import: report ID/name, table name, keyword, comparison dates, exact source filename, SHA-256, imported row count and import timestamp. The primary key is report ID plus table name; report name plus table name is also unique. The log commits with the data, so zero-row files and uncertain commit responses remain distinguishable from missing work.

SHA-256 is captured on first import. Routine completed-report checks validate log identity and coverage, not the old file contents or all historical SQL rows. This assumes completed imports are immutable outside the pipeline.
