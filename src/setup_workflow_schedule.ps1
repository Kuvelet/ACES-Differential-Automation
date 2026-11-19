$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$taskName = 'PIM ACES Differential Workflow'
$oldNames = @('PIM ACES Differential Automation', 'PIM ACES Differential Collection')
foreach ($oldName in $oldNames) {
    $old = Get-ScheduledTask -TaskName $oldName -ErrorAction SilentlyContinue
    if ($old -and ($old.Settings.Enabled -or $old.State -eq 'Running')) {
        throw "Old schedule must be disabled and idle before registration: $oldName"
    }
}
$configPath = Join-Path $PSScriptRoot 'config.json'
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if (-not $config.workflow.enabled) { throw 'Workflow config is not enabled.' }
$arguments = '"{0}" --config "{1}"' -f (Join-Path $PSScriptRoot 'workflow_runner.py'), $configPath
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Actions.Arguments -notcontains $arguments) {
    throw 'A different task already uses the workflow name.'
}
$action = New-ScheduledTaskAction -Execute (Join-Path $projectDir '.venv\Scripts\python.exe') -Argument $arguments -WorkingDirectory $projectDir
$now = Get-Date
$nextStart = $now.Date.AddHours($now.Hour)
while ($nextStart -le $now) { $nextStart = $nextStart.AddHours(1) }
$trigger = New-ScheduledTaskTrigger -Once -At $nextStart -RepetitionInterval (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
# No network prerequisite: an offline run should record the failure and queue alerts.
# Six hours exceeds the combined stage timeouts, allowing normal child cleanup first.
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'Sequential ACES submission, collection/extraction, SQL import and owner failure alerts. Hourly on the hour; overlapping starts ignored.' -Force | Out-Null
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
