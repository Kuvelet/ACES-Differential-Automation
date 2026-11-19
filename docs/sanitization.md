# Public source

This repository contains the deployed automation's source with private identifiers replaced. It includes the workflow controller, Windows process management, export detection, browser submission, collection and extraction, SQL imports, notifications, and scheduling script. The regression tests, local browser fixture, SQL table definitions, and TSV schema are included as well.

The current entry point is `src/workflow_runner.py`. Earlier runner modules remain because they contain shared helpers used by the workflow and its tests. Tests are under `tests/`; the browser fixture stays under `src/tests/` so its resource path continues to work.

## Private information

Company names and catalog labels have been replaced with `Example Catalog` and illustrative brands. Internal server and database names, the PIM URL, accounts, and email recipients use placeholders. Addresses under `example.invalid` cannot receive email. The working configuration is represented by a disabled example using the filename `config.json`.

SQL destination and email-recipient checks exist in both the source and configuration. Those checks are retained with matching placeholders. Adapting the project requires updating each location listed in the [setup documentation](reconstruction.md).

The SQL scripts retain their original comments and creation order. The first script refers to the import log as a future addition; the second script and importer implement it. All database targets in the public scripts are placeholders.

The repository excludes:

- Credentials, encrypted DPAPI exports, and working company configuration.
- Trackers, backups, notification queues, and other operational state.
- Original catalog ZIPs, XML, TSVs, error files, and licensed vehicle reference data.
- Production logs, screenshots, task exports, and source-history backups.

Tests create synthetic records in temporary directories. They do not use altered production rows. The worked example uses invented names and UUIDs, and the workflow image is a documentation illustration.

## Publication checks

Before publication, the copied source was compared with the original after the intended text substitutions. The public files were checked for internal identifiers and credential patterns, documentation links were checked, and the tests were run against the sanitized implementation.

The repository began with a new Git history containing only the sanitized files. The production directory and its operational history were excluded. The [verification notes](verification.md) retain aggregate results from the internal deployment; reproducing those results would require access to that private environment.
