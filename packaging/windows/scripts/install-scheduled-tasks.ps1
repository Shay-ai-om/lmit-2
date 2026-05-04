param(
    [Parameter(Mandatory=$true)][string]$ExePath,
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [int]$IngestIntervalMinutes = 60,
    [int]$SyncIntervalMinutes = 240,
    [int]$LintIntervalMinutes = 1440
)

$ErrorActionPreference = "Stop"

function Register-LmitTask {
    param(
        [string]$Name,
        [string]$Command,
        [int]$IntervalMinutes,
        [int]$StartOffsetMinutes
    )

    $action = New-ScheduledTaskAction -Execute $ExePath -Argument "$Command --config `"$ConfigPath`""
    $trigger = New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date).AddMinutes($StartOffsetMinutes) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable

    Register-ScheduledTask `
        -TaskName $Name `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "LMIT-2 Wiki $Command task" `
        -Force | Out-Null
}

Register-LmitTask -Name "LMIT-2 Wiki Ingest" -Command "ingest" -IntervalMinutes $IngestIntervalMinutes -StartOffsetMinutes 5
Register-LmitTask -Name "LMIT-2 Wiki Sync" -Command "sync" -IntervalMinutes $SyncIntervalMinutes -StartOffsetMinutes 10
Register-LmitTask -Name "LMIT-2 Wiki Lint" -Command "lint" -IntervalMinutes $LintIntervalMinutes -StartOffsetMinutes 15
