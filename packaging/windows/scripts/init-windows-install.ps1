param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$KnowledgeBaseRoot,
    [Parameter(Mandatory=$true)][string]$RawSourceDir,
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [bool]$InstallTasks = $true
)

$ErrorActionPreference = "Stop"

$KnowledgeBaseRoot = [System.IO.Path]::GetFullPath($KnowledgeBaseRoot)
$RawSourceDir = [System.IO.Path]::GetFullPath($RawSourceDir)
$ConfigPath = [System.IO.Path]::GetFullPath($ConfigPath)
$ConfigDir = Split-Path -Parent $ConfigPath

New-Item -ItemType Directory -Force -Path $KnowledgeBaseRoot | Out-Null
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null

$kb = $KnowledgeBaseRoot.Replace('\', '/')
$raw = $RawSourceDir.Replace('\', '/')

$config = @"
[wiki]
root_dir = "$kb"
raw_dir = "$kb/raw"
sources_dir = "$kb/wiki/sources"
topics_dir = "$kb/wiki/topics"
entities_dir = "$kb/wiki/entities"
queries_dir = "$kb/wiki/queries"
schema_dir = "$kb/schema"
log_path = "$kb/wiki/log.md"
index_path = "$kb/wiki/index.md"

[wiki_ingest]
source_dirs = [
  "$raw",
]

[wiki_runtime]
settings_path = "$kb/.wiki_runtime.json"
state_path = "$kb/.wiki_state.json"
auto_sync_on_ingest = false
search_limit = 8
serve_host = "127.0.0.1"
serve_port = 8765

[windows.task_schedule]
enabled = $($InstallTasks.ToString().ToLowerInvariant())
ingest_interval_minutes = 60
sync_interval_minutes = 240
lint_interval_minutes = 1440
"@

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ConfigPath, $config, $utf8NoBom)

$ExePath = Join-Path $InstallDir "lmit-wiki.exe"
& $ExePath init --config $ConfigPath
if ($LASTEXITCODE -ne 0) {
    throw "lmit-wiki init failed with exit code $LASTEXITCODE"
}

if ($InstallTasks) {
    & (Join-Path $InstallDir "scripts\install-scheduled-tasks.ps1") `
        -ExePath $ExePath `
        -ConfigPath $ConfigPath `
        -IngestIntervalMinutes 60 `
        -SyncIntervalMinutes 240 `
        -LintIntervalMinutes 1440
}
