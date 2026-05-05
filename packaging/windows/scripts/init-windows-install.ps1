param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$KnowledgeBaseRoot,
    [Parameter(Mandatory=$true)][string]$RawSourceDir,
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [bool]$InstallTasks = $false
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "config-common.ps1")

$ExePath = Join-Path $InstallDir "lmit-wiki.exe"

if ($InstallTasks) {
    Write-LmitWikiConfig `
        -ConfigPath $ConfigPath `
        -KnowledgeBaseRoot $KnowledgeBaseRoot `
        -RawSourceDir $RawSourceDir `
        -InstallTasks:$InstallTasks

    Invoke-LmitWikiInit -ExePath $ExePath -ConfigPath $ConfigPath

    & (Join-Path $InstallDir "scripts\install-scheduled-tasks.ps1") `
        -ExePath $ExePath `
        -ConfigPath $ConfigPath `
        -IngestIntervalMinutes 60 `
        -SyncIntervalMinutes 240 `
        -LintIntervalMinutes 1440
}
