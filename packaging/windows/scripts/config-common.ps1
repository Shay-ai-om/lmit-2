$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms

function Show-LmitError {
    param([Parameter(Mandatory=$true)][string]$Message)
    [System.Windows.Forms.MessageBox]::Show(
        $Message,
        "LMIT-2 Wiki",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}

function Show-LmitInfo {
    param([Parameter(Mandatory=$true)][string]$Message)
    [System.Windows.Forms.MessageBox]::Show(
        $Message,
        "LMIT-2 Wiki",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
}

function Get-LmitDocumentsPath {
    $documents = [Environment]::GetFolderPath("MyDocuments")
    if ([string]::IsNullOrWhiteSpace($documents)) {
        return (Join-Path $env:USERPROFILE "Documents")
    }
    return $documents
}

function Select-LmitFolder {
    param(
        [Parameter(Mandatory=$true)][string]$Description,
        [Parameter(Mandatory=$true)][string]$DefaultPath
    )

    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = $Description
    $dialog.ShowNewFolderButton = $true
    if (Test-Path -LiteralPath $DefaultPath) {
        $dialog.SelectedPath = $DefaultPath
    }
    else {
        $parent = Split-Path -Parent $DefaultPath
        if ($parent -and (Test-Path -LiteralPath $parent)) {
            $dialog.SelectedPath = $parent
        }
    }

    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        throw "Folder selection cancelled."
    }
    return $dialog.SelectedPath
}

function Write-LmitWikiConfig {
    param(
        [Parameter(Mandatory=$true)][string]$ConfigPath,
        [Parameter(Mandatory=$true)][string]$KnowledgeBaseRoot,
        [Parameter(Mandatory=$true)][string]$RawSourceDir,
        [bool]$InstallTasks = $false
    )

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
}

function Invoke-LmitWikiInit {
    param(
        [Parameter(Mandatory=$true)][string]$ExePath,
        [Parameter(Mandatory=$true)][string]$ConfigPath
    )

    & $ExePath init --config $ConfigPath
    if ($LASTEXITCODE -ne 0) {
        throw "lmit-wiki init failed with exit code $LASTEXITCODE"
    }
}

function Ensure-LmitWikiConfig {
    param(
        [Parameter(Mandatory=$true)][string]$InstallDir,
        [Parameter(Mandatory=$true)][string]$ConfigPath
    )

    if (Test-Path -LiteralPath $ConfigPath) {
        return
    }

    $documents = Get-LmitDocumentsPath
    $defaultKb = Join-Path $documents "LMIT-2\knowledge_base"
    $defaultRaw = Join-Path $documents "LMIT\output\raw"

    Show-LmitInfo "LMIT-2 needs to create its local config before continuing. Select the knowledge base folder, then select the LMIT-1 raw Markdown folder."
    $kb = Select-LmitFolder -Description "Choose the LMIT-2 knowledge base folder." -DefaultPath $defaultKb
    $raw = Select-LmitFolder -Description "Choose the LMIT-1 raw Markdown source folder." -DefaultPath $defaultRaw

    Write-LmitWikiConfig `
        -ConfigPath $ConfigPath `
        -KnowledgeBaseRoot $kb `
        -RawSourceDir $raw `
        -InstallTasks:$false

    $exe = Join-Path $InstallDir "lmit-wiki.exe"
    Invoke-LmitWikiInit -ExePath $exe -ConfigPath $ConfigPath
}
