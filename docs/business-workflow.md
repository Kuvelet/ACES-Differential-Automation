# Business workflow

## What is being tracked?

The unit of work is one comparison between two full catalog exports for the same configured keyword. A keyword identifies an export stream; it is not a part number. Different streams need their own predecessor history even if their names overlap.

An application record links a part to vehicle attributes and conditions. A part can have many applications, and several reports can contain records about the same part. The pipeline therefore stores report history rather than treating every imported row as a new unique part or a change to a master catalog.

The approved application-table headers include make, model, year range, engine and transmission attributes, qualifiers, notes, quantity, part type, position, and part. Part and note outputs have their own smaller schemas. These fields explain why copying one summary number would lose useful context: the source records and the comparison identity both need to survive the transfer.

ACES supplies a standard for communicating product application information. Its supporting vehicle reference data is not a catalog of which products fit those vehicles. [Auto Care Association FAQ](https://www.autocare.org/about-us/frequently-asked-questions)

## The recurring operational job

The original workflow spans several handoffs: locate exports, select the correct dates, upload both files into PIM's comparison tool, monitor for completed output, copy the four output files, extract the ZIPs, and append the eight TSVs to SQL with source identifiers. Each handoff needs evidence of completion before the next one can rely on it.

The deployed configuration contains 16 keyword streams. Monthly arrivals motivate retaining multiple comparison periods. The hourly task handles readiness and retries; it does not schedule PIM's upstream exports or force one report per stream per month. Missing or delayed upstream files do not become fabricated comparisons.

The workflow owner receives exceptions and recovery notices. The configured stakeholder list receives confirmed submission and SQL-completion emails. A submission email means PIM accepted a job; the SQL email means the import log records completion of all eight files. Those are intentionally different milestones.

## One illustrative comparison

This example uses invented names and UUIDs. It is an explanation of the implemented rules, not a production record or a bundled sample catalog.

| Step | Example and resulting behavior |
| --- | --- |
| Previous snapshot | `11111111-1111-4111-8111-111111111111~Example Catalog_BrandAlpha-ACES_2026-10-01_FULL.zip` is already known. |
| New snapshot | `22222222-2222-4222-8222-222222222222~Example Catalog_BrandAlpha-ACES_2026-11-01_FULL.zip` arrives and passes the readiness checks. |
| Saved pair | If these are consecutive eligible exports, the tracker saves the October file as the November file's predecessor. The report name is `BrandAlpha-ACES_Diff_2026-10-01_to_2026-11-01`. |
| PIM processing | The browser submits the pair. Only the matching fresh acceptance confirmation marks the submission successful. Outputs can arrive on a later hourly run. |
| Retained files | Two ZIPs and two `.error` files are copied into `BrandAlpha-ACES/2026-10-01_to_2026-11-01/`. Both ZIPs receive their own extraction folder, retaining the original ZIP basename. |
| SQL import | Eight TSVs from the `ACESDifferential` extraction folder append to their corresponding tables. Each row identifies the November export UUID, both dates, keyword, exact TSV, source row and import timestamp. |
| Next run | Eight consistent committed log entries prove completion. The importer skips the TSVs; a failed notification can retry independently. |
| Next period | A later export gets its own saved pair and period folder. Earlier report rows remain available under their own metadata. |

An empty AddedApps file can be a valid result: it contributes zero data rows but still receives a successful import-log entry. A `.error` file is also an expected output artifact; the extension by itself does not mean the automation failed. This pipeline retains these artifacts without deciding whether their contents represent a business issue.

## Why the design took this shape

| Requirement | Implementation choice | Tradeoff |
| --- | --- | --- |
| Keep routine operation understandable | One sequential hourly controller | Lower orchestration complexity; no concurrent stage throughput |
| Know where a SQL record came from | `Meta_` columns in all eight tables | Repeated metadata uses space, but each table is understandable on its own |
| Preserve monthly results | Keyword and comparison-period folders, unchanged extraction names | Longer paths; Windows long-path handling is needed |
| Recover without duplicate imports | Report-level transaction plus committed import log | Additional database bookkeeping, including zero-row files |
| Avoid duplicate external submissions | Hold uncertain PIM acceptance for review | Some interruptions require an owner decision |
| Keep repeated checks economical | Reuse unchanged export metadata and skip completed imports | Historical file edits are deliberately outside routine verification |
| Keep stakeholders informed | Distinct success events; owner-only exceptions | Durable email state must be retained alongside workflow state |

The system began with separate submission and collection runners. The deployed controller combines them because immediate processing was less important than a simple, reliable setup. The original stage modules remain separate, making their rules testable without operating the external systems.

## Scope of the result

The delivered outcome is an automated path from recurring exports to organized source artifacts and traceable SQL history. The recorded checkpoint demonstrates that the pipeline imported real reports; the tests demonstrate specific failure and recovery behaviors. There is no claimed measurement of labor savings, revenue improvement, catalog accuracy, or downstream adoption.

Catalog review, business interpretation of differences, fitment approval, and changes to the master catalog are separate work. This project does not automatically approve or apply the changes described in a differential report.
