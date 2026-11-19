# Public source and sanitization

This repository publishes the active implementation as a sanitized source snapshot. It is not a rewritten demonstration of similar behavior.

## Retained

- Workflow controller and Windows process-containment code.
- Export discovery, PIM browser submission, collection/extraction, SQL importer and notifications.
- Original regression tests and their synthetic local browser fixture.
- Eight-table DDL, import-log DDL and exact TSV column/type contract.
- Hourly Task Scheduler registration script, with the public configuration filename.

The active deployment directory retained a historical candidate name. Its current modules are published under `src/`; tests are under `tests/`. The HTML fixture remains under `src/tests/` to preserve resource lookup. Historical runner modules are retained because they contain shared helpers and regression coverage. The current entry point is `src/workflow_runner.py`.

## Replaced

Company names and catalog labels become `Example Catalog` and illustrative brands. Internal server/database identifiers, PIM URL and account names, and all recipient addresses become explicit placeholders. `example.invalid` addresses are not working delivery destinations. The configuration filename becomes `config.json`.

The original deployment used explicit target and recipient guards in source as well as configuration. These remain explicit guards with public placeholders. Changing only the JSON does not convert this snapshot into a general-purpose deployment. See [reconstruction](reconstruction.md) for the locations that must agree.

The SQL scripts retain their original implementation comments, including their creation chronology. Targets shown there are substituted placeholders; they are not actual infrastructure. The import log described as a future component in the first DDL was subsequently implemented in the second DDL and importer.

## Excluded

Credentials and DPAPI exports; working company configuration; trackers and backups; original catalog ZIPs, XML, TSV and error files; licensed vehicle reference content; production logs, screenshots, task exports and notification queues; and source-history backups.

Public tests generate synthetic records in temporary directories. No production rows were transformed into fake sample data. The documentation's worked example is labeled illustrative. The workflow graphic is original documentation artwork.

## Publication checks

The preparation process checks the copied implementation against its original files after the declared text substitutions, searches the public tree for internal identifiers and credential patterns, checks links and file inventory, and runs the original tests against the sanitized source. Only the sanitized folder receives a new Git history. The working automation directory and its operational state are not part of that history.

The case study uses aggregate operational evidence and distinguishes it from reproducible tests. It includes no claim that the public package can access the private PIM environment or reproduce the recorded production totals.
