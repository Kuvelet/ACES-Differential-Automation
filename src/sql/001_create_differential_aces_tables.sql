/*
ACES differential report tables -- target: CATALOGSQL\CATALOG / CatalogWarehouse.

Before executing manually:
  1. Connect to CATALOGSQL\CATALOG using Windows authentication.
  2. Select CatalogWarehouse in your SQL client.
  3. This script refuses to overwrite existing objects.

This script creates eight empty dbo tables in one transaction. It does not
connect, import data, start an automation, or change existing tables.
If ANY target object already exists, it stops without making changes.

All tables are self-contained: metadata precedes unchanged TSV column names.
Meta_ReportID is the existing tracker file UUID, supplied by the importer.
Meta_ImportedAt is Eastern local time with its UTC offset, supplied by the
importer and identical across a report's successful import transaction. Use
America/New_York timezone rules (EST -05:00 / EDT -04:00), never a fixed offset.
DATETIMEOFFSET(3) preserves the offset but does not enforce a named timezone;
the importer must apply these rules. There is no per-row timestamp default.
Meta_SourceRowNumber starts at 1 after the TSV header.
Original text retains blanks and whitespace; blank numeric fields become NULL.
The importer must reject invalid numbers and overlength bounded text.
Headers-only TSVs insert no rows; import logging is a separate future component.

AppID is INT in both application tables.
Bounded original text: Part / Part Number NVARCHAR(100).
All other original text: NVARCHAR(MAX), including BodyNumDoors and ValvesPerEngine.
The bounded columns permit future search indexes; none are added speculatively.
Each table gets a clustered row-ID primary key and a unique nonclustered
(ReportID, SourceRowNumber) constraint, which also supports report-ID lookups.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;

DECLARE @ExpectedDatabase sysname = N'CatalogWarehouse';
IF @ExpectedDatabase = N'REPLACE_WITH_TARGET_DATABASE'
    THROW 50001, 'Set @ExpectedDatabase before executing this script.', 1;
IF CONVERT(nvarchar(128), SERVERPROPERTY('MachineName')) <> N'CATALOGSQL'
   OR COALESCE(CONVERT(nvarchar(128), SERVERPROPERTY('InstanceName')), N'') <> N'CATALOG'
    THROW 50005, 'Selected SQL Server is not CATALOGSQL\CATALOG.', 1;
IF DB_NAME() <> @ExpectedDatabase
    THROW 50002, 'Selected database does not match @ExpectedDatabase.', 1;
IF DB_NAME() IN (N'master', N'model', N'msdb', N'tempdb')
    THROW 50003, 'Do not create project tables in a system database.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'dbo.Differential_ACES_AddedApps') IS NOT NULL
        THROW 50004, 'Target object dbo.Differential_ACES_AddedApps already exists. No changes made.', 1;
    IF OBJECT_ID(N'dbo.Differential_ACES_DeletedApps') IS NOT NULL
        THROW 50004, 'Target object dbo.Differential_ACES_DeletedApps already exists. No changes made.', 1;
    IF OBJECT_ID(N'dbo.Differential_ACES_AddedParts') IS NOT NULL
        THROW 50004, 'Target object dbo.Differential_ACES_AddedParts already exists. No changes made.', 1;
    IF OBJECT_ID(N'dbo.Differential_ACES_DeletedParts') IS NOT NULL
        THROW 50004, 'Target object dbo.Differential_ACES_DeletedParts already exists. No changes made.', 1;
    IF OBJECT_ID(N'dbo.Differential_ACES_AllParts') IS NOT NULL
        THROW 50004, 'Target object dbo.Differential_ACES_AllParts already exists. No changes made.', 1;
    IF OBJECT_ID(N'dbo.Differential_ACES_AnalysisParts') IS NOT NULL
        THROW 50004, 'Target object dbo.Differential_ACES_AnalysisParts already exists. No changes made.', 1;
    IF OBJECT_ID(N'dbo.Differential_ACES_UniqueNotes') IS NOT NULL
        THROW 50004, 'Target object dbo.Differential_ACES_UniqueNotes already exists. No changes made.', 1;
    IF OBJECT_ID(N'dbo.Differential_ACES_VCDBNotes') IS NOT NULL
        THROW 50004, 'Target object dbo.Differential_ACES_VCDBNotes already exists. No changes made.', 1;

    CREATE TABLE [dbo].[Differential_ACES_AddedApps]
    (
        [Meta_RowID] BIGINT IDENTITY(1,1) NOT NULL,
        [Meta_ReportID] UNIQUEIDENTIFIER NOT NULL,
        [Meta_Keyword] NVARCHAR(200) NOT NULL,
        [Meta_PreviousExportDate] DATE NOT NULL,
        [Meta_CurrentExportDate] DATE NOT NULL,
        [Meta_SourceFileName] NVARCHAR(255) NOT NULL,
        [Meta_SourceRowNumber] BIGINT NOT NULL,
        [Meta_ImportedAt] DATETIMEOFFSET(3) NOT NULL,
        [AppID] INT NULL,
        [RecordType] NVARCHAR(MAX) NULL,
        [VehicleType] NVARCHAR(MAX) NULL,
        [Make] NVARCHAR(MAX) NULL,
        [Model] NVARCHAR(MAX) NULL,
        [From Year] SMALLINT NULL,
        [To Year] SMALLINT NULL,
        [Submodel] NVARCHAR(MAX) NULL,
        [MfrBodyCode] NVARCHAR(MAX) NULL,
        [BodyNumDoors] NVARCHAR(MAX) NULL,
        [BodyType] NVARCHAR(MAX) NULL,
        [DriveType] NVARCHAR(MAX) NULL,
        [EngineBase] NVARCHAR(MAX) NULL,
        [EngineDesignation] NVARCHAR(MAX) NULL,
        [EngineVin] NVARCHAR(MAX) NULL,
        [EngineVersion] NVARCHAR(MAX) NULL,
        [EngineMfr] NVARCHAR(MAX) NULL,
        [PowerOutput] NVARCHAR(MAX) NULL,
        [ValvesPerEngine] NVARCHAR(MAX) NULL,
        [FuelDeliveryType] NVARCHAR(MAX) NULL,
        [FuelDeliverySubType] NVARCHAR(MAX) NULL,
        [FuelSystemControlType] NVARCHAR(MAX) NULL,
        [FuelSystemDesign] NVARCHAR(MAX) NULL,
        [Aspiration] NVARCHAR(MAX) NULL,
        [CylinderHeadType] NVARCHAR(MAX) NULL,
        [FuelType] NVARCHAR(MAX) NULL,
        [IgnitionSystemType] NVARCHAR(MAX) NULL,
        [TransmissionMfrCode] NVARCHAR(MAX) NULL,
        [TransmissionBase] NVARCHAR(MAX) NULL,
        [TransmissionType] NVARCHAR(MAX) NULL,
        [TransmissionControlType] NVARCHAR(MAX) NULL,
        [TransmissionNumSpeeds] NVARCHAR(MAX) NULL,
        [TransElecControlled] NVARCHAR(MAX) NULL,
        [TransmissionMfr] NVARCHAR(MAX) NULL,
        [TransferCaseBase] NVARCHAR(MAX) NULL,
        [TransferCase] NVARCHAR(MAX) NULL,
        [TransferCaseMfr] NVARCHAR(MAX) NULL,
        [BedLength] NVARCHAR(MAX) NULL,
        [BedType] NVARCHAR(MAX) NULL,
        [WheelBase] NVARCHAR(MAX) NULL,
        [BrakeSystem] NVARCHAR(MAX) NULL,
        [FrontBrakeType] NVARCHAR(MAX) NULL,
        [RearBrakeType] NVARCHAR(MAX) NULL,
        [BrakeABS] NVARCHAR(MAX) NULL,
        [FrontSpringType] NVARCHAR(MAX) NULL,
        [RearSpringType] NVARCHAR(MAX) NULL,
        [SteeringSystem] NVARCHAR(MAX) NULL,
        [SteeringType] NVARCHAR(MAX) NULL,
        [RestraintType] NVARCHAR(MAX) NULL,
        [Region] NVARCHAR(MAX) NULL,
        [Qualifiers] NVARCHAR(MAX) NULL,
        [Notes] NVARCHAR(MAX) NULL,
        [Quantity] INT NULL,
        [PartType] NVARCHAR(MAX) NULL,
        [MfrLabel] NVARCHAR(MAX) NULL,
        [Position] NVARCHAR(MAX) NULL,
        [Part] NVARCHAR(100) NULL,
        [DisplayOrder] NVARCHAR(MAX) NULL,
        [AssetName] NVARCHAR(MAX) NULL,
        [AssetItemOrder] NVARCHAR(MAX) NULL,
        [AssetItemRef] NVARCHAR(MAX) NULL,
        CONSTRAINT [PK_Differential_ACES_AddedApps] PRIMARY KEY CLUSTERED ([Meta_RowID]),
        CONSTRAINT [UQ_Differential_ACES_AddedApps_ReportRow] UNIQUE NONCLUSTERED ([Meta_ReportID], [Meta_SourceRowNumber])
    );

    CREATE TABLE [dbo].[Differential_ACES_DeletedApps]
    (
        [Meta_RowID] BIGINT IDENTITY(1,1) NOT NULL,
        [Meta_ReportID] UNIQUEIDENTIFIER NOT NULL,
        [Meta_Keyword] NVARCHAR(200) NOT NULL,
        [Meta_PreviousExportDate] DATE NOT NULL,
        [Meta_CurrentExportDate] DATE NOT NULL,
        [Meta_SourceFileName] NVARCHAR(255) NOT NULL,
        [Meta_SourceRowNumber] BIGINT NOT NULL,
        [Meta_ImportedAt] DATETIMEOFFSET(3) NOT NULL,
        [AppID] INT NULL,
        [RecordType] NVARCHAR(MAX) NULL,
        [VehicleType] NVARCHAR(MAX) NULL,
        [Make] NVARCHAR(MAX) NULL,
        [Model] NVARCHAR(MAX) NULL,
        [From Year] SMALLINT NULL,
        [To Year] SMALLINT NULL,
        [Submodel] NVARCHAR(MAX) NULL,
        [MfrBodyCode] NVARCHAR(MAX) NULL,
        [BodyNumDoors] NVARCHAR(MAX) NULL,
        [BodyType] NVARCHAR(MAX) NULL,
        [DriveType] NVARCHAR(MAX) NULL,
        [EngineBase] NVARCHAR(MAX) NULL,
        [EngineDesignation] NVARCHAR(MAX) NULL,
        [EngineVin] NVARCHAR(MAX) NULL,
        [EngineVersion] NVARCHAR(MAX) NULL,
        [EngineMfr] NVARCHAR(MAX) NULL,
        [PowerOutput] NVARCHAR(MAX) NULL,
        [ValvesPerEngine] NVARCHAR(MAX) NULL,
        [FuelDeliveryType] NVARCHAR(MAX) NULL,
        [FuelDeliverySubType] NVARCHAR(MAX) NULL,
        [FuelSystemControlType] NVARCHAR(MAX) NULL,
        [FuelSystemDesign] NVARCHAR(MAX) NULL,
        [Aspiration] NVARCHAR(MAX) NULL,
        [CylinderHeadType] NVARCHAR(MAX) NULL,
        [FuelType] NVARCHAR(MAX) NULL,
        [IgnitionSystemType] NVARCHAR(MAX) NULL,
        [TransmissionMfrCode] NVARCHAR(MAX) NULL,
        [TransmissionBase] NVARCHAR(MAX) NULL,
        [TransmissionType] NVARCHAR(MAX) NULL,
        [TransmissionControlType] NVARCHAR(MAX) NULL,
        [TransmissionNumSpeeds] NVARCHAR(MAX) NULL,
        [TransElecControlled] NVARCHAR(MAX) NULL,
        [TransmissionMfr] NVARCHAR(MAX) NULL,
        [TransferCaseBase] NVARCHAR(MAX) NULL,
        [TransferCase] NVARCHAR(MAX) NULL,
        [TransferCaseMfr] NVARCHAR(MAX) NULL,
        [BedLength] NVARCHAR(MAX) NULL,
        [BedType] NVARCHAR(MAX) NULL,
        [WheelBase] NVARCHAR(MAX) NULL,
        [BrakeSystem] NVARCHAR(MAX) NULL,
        [FrontBrakeType] NVARCHAR(MAX) NULL,
        [RearBrakeType] NVARCHAR(MAX) NULL,
        [BrakeABS] NVARCHAR(MAX) NULL,
        [FrontSpringType] NVARCHAR(MAX) NULL,
        [RearSpringType] NVARCHAR(MAX) NULL,
        [SteeringSystem] NVARCHAR(MAX) NULL,
        [SteeringType] NVARCHAR(MAX) NULL,
        [RestraintType] NVARCHAR(MAX) NULL,
        [Region] NVARCHAR(MAX) NULL,
        [Qualifiers] NVARCHAR(MAX) NULL,
        [Notes] NVARCHAR(MAX) NULL,
        [Quantity] INT NULL,
        [PartType] NVARCHAR(MAX) NULL,
        [MfrLabel] NVARCHAR(MAX) NULL,
        [Position] NVARCHAR(MAX) NULL,
        [Part] NVARCHAR(100) NULL,
        [DisplayOrder] NVARCHAR(MAX) NULL,
        [AssetName] NVARCHAR(MAX) NULL,
        [AssetItemOrder] NVARCHAR(MAX) NULL,
        [AssetItemRef] NVARCHAR(MAX) NULL,
        CONSTRAINT [PK_Differential_ACES_DeletedApps] PRIMARY KEY CLUSTERED ([Meta_RowID]),
        CONSTRAINT [UQ_Differential_ACES_DeletedApps_ReportRow] UNIQUE NONCLUSTERED ([Meta_ReportID], [Meta_SourceRowNumber])
    );

    CREATE TABLE [dbo].[Differential_ACES_AddedParts]
    (
        [Meta_RowID] BIGINT IDENTITY(1,1) NOT NULL,
        [Meta_ReportID] UNIQUEIDENTIFIER NOT NULL,
        [Meta_Keyword] NVARCHAR(200) NOT NULL,
        [Meta_PreviousExportDate] DATE NOT NULL,
        [Meta_CurrentExportDate] DATE NOT NULL,
        [Meta_SourceFileName] NVARCHAR(255) NOT NULL,
        [Meta_SourceRowNumber] BIGINT NOT NULL,
        [Meta_ImportedAt] DATETIMEOFFSET(3) NOT NULL,
        [Part Number] NVARCHAR(100) NULL,
        [Part Description] NVARCHAR(MAX) NULL,
        CONSTRAINT [PK_Differential_ACES_AddedParts] PRIMARY KEY CLUSTERED ([Meta_RowID]),
        CONSTRAINT [UQ_Differential_ACES_AddedParts_ReportRow] UNIQUE NONCLUSTERED ([Meta_ReportID], [Meta_SourceRowNumber])
    );

    CREATE TABLE [dbo].[Differential_ACES_DeletedParts]
    (
        [Meta_RowID] BIGINT IDENTITY(1,1) NOT NULL,
        [Meta_ReportID] UNIQUEIDENTIFIER NOT NULL,
        [Meta_Keyword] NVARCHAR(200) NOT NULL,
        [Meta_PreviousExportDate] DATE NOT NULL,
        [Meta_CurrentExportDate] DATE NOT NULL,
        [Meta_SourceFileName] NVARCHAR(255) NOT NULL,
        [Meta_SourceRowNumber] BIGINT NOT NULL,
        [Meta_ImportedAt] DATETIMEOFFSET(3) NOT NULL,
        [Part Number] NVARCHAR(100) NULL,
        [Part Description] NVARCHAR(MAX) NULL,
        CONSTRAINT [PK_Differential_ACES_DeletedParts] PRIMARY KEY CLUSTERED ([Meta_RowID]),
        CONSTRAINT [UQ_Differential_ACES_DeletedParts_ReportRow] UNIQUE NONCLUSTERED ([Meta_ReportID], [Meta_SourceRowNumber])
    );

    CREATE TABLE [dbo].[Differential_ACES_AllParts]
    (
        [Meta_RowID] BIGINT IDENTITY(1,1) NOT NULL,
        [Meta_ReportID] UNIQUEIDENTIFIER NOT NULL,
        [Meta_Keyword] NVARCHAR(200) NOT NULL,
        [Meta_PreviousExportDate] DATE NOT NULL,
        [Meta_CurrentExportDate] DATE NOT NULL,
        [Meta_SourceFileName] NVARCHAR(255) NOT NULL,
        [Meta_SourceRowNumber] BIGINT NOT NULL,
        [Meta_ImportedAt] DATETIMEOFFSET(3) NOT NULL,
        [Part Number] NVARCHAR(100) NULL,
        [Part Description] NVARCHAR(MAX) NULL,
        CONSTRAINT [PK_Differential_ACES_AllParts] PRIMARY KEY CLUSTERED ([Meta_RowID]),
        CONSTRAINT [UQ_Differential_ACES_AllParts_ReportRow] UNIQUE NONCLUSTERED ([Meta_ReportID], [Meta_SourceRowNumber])
    );

    CREATE TABLE [dbo].[Differential_ACES_AnalysisParts]
    (
        [Meta_RowID] BIGINT IDENTITY(1,1) NOT NULL,
        [Meta_ReportID] UNIQUEIDENTIFIER NOT NULL,
        [Meta_Keyword] NVARCHAR(200) NOT NULL,
        [Meta_PreviousExportDate] DATE NOT NULL,
        [Meta_CurrentExportDate] DATE NOT NULL,
        [Meta_SourceFileName] NVARCHAR(255) NOT NULL,
        [Meta_SourceRowNumber] BIGINT NOT NULL,
        [Meta_ImportedAt] DATETIMEOFFSET(3) NOT NULL,
        [Expected] NVARCHAR(MAX) NULL,
        [Part Number] NVARCHAR(100) NULL,
        [Part Description] NVARCHAR(MAX) NULL,
        [Previous Application Count] INT NULL,
        [Current Application Count] INT NULL,
        [Application Differential] NVARCHAR(MAX) NULL,
        CONSTRAINT [PK_Differential_ACES_AnalysisParts] PRIMARY KEY CLUSTERED ([Meta_RowID]),
        CONSTRAINT [UQ_Differential_ACES_AnalysisParts_ReportRow] UNIQUE NONCLUSTERED ([Meta_ReportID], [Meta_SourceRowNumber])
    );

    CREATE TABLE [dbo].[Differential_ACES_UniqueNotes]
    (
        [Meta_RowID] BIGINT IDENTITY(1,1) NOT NULL,
        [Meta_ReportID] UNIQUEIDENTIFIER NOT NULL,
        [Meta_Keyword] NVARCHAR(200) NOT NULL,
        [Meta_PreviousExportDate] DATE NOT NULL,
        [Meta_CurrentExportDate] DATE NOT NULL,
        [Meta_SourceFileName] NVARCHAR(255) NOT NULL,
        [Meta_SourceRowNumber] BIGINT NOT NULL,
        [Meta_ImportedAt] DATETIMEOFFSET(3) NOT NULL,
        [Note] NVARCHAR(MAX) NULL,
        CONSTRAINT [PK_Differential_ACES_UniqueNotes] PRIMARY KEY CLUSTERED ([Meta_RowID]),
        CONSTRAINT [UQ_Differential_ACES_UniqueNotes_ReportRow] UNIQUE NONCLUSTERED ([Meta_ReportID], [Meta_SourceRowNumber])
    );

    CREATE TABLE [dbo].[Differential_ACES_VCDBNotes]
    (
        [Meta_RowID] BIGINT IDENTITY(1,1) NOT NULL,
        [Meta_ReportID] UNIQUEIDENTIFIER NOT NULL,
        [Meta_Keyword] NVARCHAR(200) NOT NULL,
        [Meta_PreviousExportDate] DATE NOT NULL,
        [Meta_CurrentExportDate] DATE NOT NULL,
        [Meta_SourceFileName] NVARCHAR(255) NOT NULL,
        [Meta_SourceRowNumber] BIGINT NOT NULL,
        [Meta_ImportedAt] DATETIMEOFFSET(3) NOT NULL,
        [Note] NVARCHAR(MAX) NULL,
        [VCDB] NVARCHAR(MAX) NULL,
        CONSTRAINT [PK_Differential_ACES_VCDBNotes] PRIMARY KEY CLUSTERED ([Meta_RowID]),
        CONSTRAINT [UQ_Differential_ACES_VCDBNotes_ReportRow] UNIQUE NONCLUSTERED ([Meta_ReportID], [Meta_SourceRowNumber])
    );

    COMMIT TRANSACTION;
    PRINT 'Created all eight empty ACES differential tables. No report data imported.';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
