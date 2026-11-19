/* Created on CATALOGSQL\CATALOG / CatalogWarehouse on November 16, 2026.
   Retained for setup documentation; do not rerun against the existing table.
   Eight successful file records per report, including zero-row TSVs.
   The importer inserts these in the same transaction as the eight data tables.
   Failed imports leave no committed log entries; their error goes in the CSV.
   This script never alters the existing eight report tables. */
SET XACT_ABORT ON;
SET NOCOUNT ON;
IF CONVERT(nvarchar(128), SERVERPROPERTY('MachineName')) <> N'CATALOGSQL'
   OR COALESCE(CONVERT(nvarchar(128), SERVERPROPERTY('InstanceName')), N'') <> N'CATALOG'
   OR DB_NAME() <> N'CatalogWarehouse'
    THROW 50001, 'Wrong server or database for the ACES import log.', 1;
IF OBJECT_ID(N'dbo.Differential_ACES_ImportLog') IS NOT NULL
    THROW 50002, 'Import log already exists; no changes made.', 1;
BEGIN TRY
    BEGIN TRANSACTION;
    CREATE TABLE dbo.Differential_ACES_ImportLog
    (
        Meta_ReportID UNIQUEIDENTIFIER NOT NULL,
        TableName NVARCHAR(128) NOT NULL,
        ReportName NVARCHAR(255) NOT NULL,
        Meta_Keyword NVARCHAR(200) NOT NULL,
        Meta_PreviousExportDate DATE NOT NULL,
        Meta_CurrentExportDate DATE NOT NULL,
        SourceFileName NVARCHAR(255) NOT NULL,
        SourceSHA256 CHAR(64) NOT NULL,
        ImportedRowCount BIGINT NOT NULL,
        ImportedAt DATETIMEOFFSET(3) NOT NULL,
        CONSTRAINT PK_Differential_ACES_ImportLog
            PRIMARY KEY (Meta_ReportID, TableName),
        CONSTRAINT UQ_Differential_ACES_ImportLog_ReportName
            UNIQUE (ReportName, TableName),
        CONSTRAINT CK_Differential_ACES_ImportLog_RowCount
            CHECK (ImportedRowCount >= 0)
    );
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
