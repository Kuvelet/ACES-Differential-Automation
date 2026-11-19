# Business workflow

Each differential report compares two full catalog exports for the same keyword. The keyword identifies a catalog stream, with its own sequence of exports and comparison history. It is separate from the part numbers inside those files.

An application record describes a part's relationship to a vehicle, including conditions such as engine, transmission, position, quantity, and notes. One part can have many applications and appear in several monthly reports. Keeping both the original records and their comparison dates makes that history useful after it reaches SQL Server.

The files use ACES, the Aftermarket Catalog Exchange Standard, to communicate product application information. The supporting vehicle reference data describes vehicles; product-to-vehicle applications come from the catalog. [Auto Care Association FAQ](https://www.autocare.org/about-us/frequently-asked-questions)

## From monthly exports to SQL history

The deployed workflow follows 16 catalog keywords. It checks hourly for new exports, chooses the previous and current versions, submits them to PIM, collects the completed files, and imports the results. Export arrivals follow a monthly business cadence. If an upstream export is late or missing, the workflow waits for eligible files; it does not create a comparison just because another month has passed.

PIM performs the comparison and generates two ZIPs and two `.error` files. The pipeline archives all four files by keyword and comparison period, extracts both ZIPs, and appends the eight report TSVs to SQL Server. Added metadata connects every imported row to its source file and comparison.

Submission and import emails mark different points in the process. The first confirms that PIM accepted the job. The second confirms that all eight files have committed to SQL and includes their row counts. Both go to the configured success list. Failures, review requests, reminders, and recovery notices go to the workflow owner.

## Example comparison

The following example uses invented catalog names and UUIDs.

| Step | Files and behavior |
| --- | --- |
| Previous export | `11111111-1111-4111-8111-111111111111~Example Catalog_BrandAlpha-ACES_2026-10-01_FULL.zip` is already known. |
| New export | `22222222-2222-4222-8222-222222222222~Example Catalog_BrandAlpha-ACES_2026-11-01_FULL.zip` arrives and passes the readiness checks. |
| Saved comparison | If these are consecutive eligible exports, the tracker saves the October file as the November file's predecessor. The report name is `BrandAlpha-ACES_Diff_2026-10-01_to_2026-11-01`. |
| PIM submission | The browser submits both files and checks for a fresh acceptance message matching the report. Results may arrive on a later hourly run. |
| Archive | The four output files are copied into `BrandAlpha-ACES/2026-10-01_to_2026-11-01/`. Each ZIP is extracted into its own folder using the original ZIP basename. |
| SQL import | Eight TSVs from the `ACESDifferential` folder append to their corresponding tables. Every row includes the November export UUID, both export dates, keyword, source filename, row number, and import time. |
| Later runs | Eight consistent, committed import-log entries identify the report as complete. The importer skips its TSVs. Any unsent email can retry separately. |
| Next month | The next eligible export receives its own saved comparison and period folder. Earlier SQL records and archived files remain available. |

A header-only AddedApps file is a valid result. It adds no data rows but still receives a successful import-log entry. The `.error` files are also expected PIM outputs; their extension alone does not indicate an automation failure. They are retained with the report for later review.

## Design choices

A single sequential controller keeps the scheduled workflow straightforward. It replaces the earlier separate submission and collection schedules, while the Python modules remain separate for maintenance and testing. Immediate processing was less important than being able to understand and recover the workflow.

The archive retains the original output files and extraction names. Each SQL table includes its own `Meta_` columns so records can be traced without first joining a report register. This uses more storage for repeated metadata and creates longer archive paths, which the Windows file handling must accommodate.

The import log and report-level SQL transaction prevent repeated imports. Completed reports are treated as final, so routine checks skip their files. Uncertain PIM submissions require an owner decision because resubmitting could create a duplicate job.

The pipeline ends with organized reports and analysis-ready SQL history. Reviewing the catalog changes, approving fitment, and updating the master catalog remain separate work.
