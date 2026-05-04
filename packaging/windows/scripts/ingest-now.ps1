param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$ConfigPath
)

$ExePath = Join-Path $InstallDir "lmit-wiki.exe"
& $ExePath ingest --config $ConfigPath
if ($LASTEXITCODE -ne 0) {
    Read-Host "LMIT-2 ingest failed. Press Enter to close"
    exit $LASTEXITCODE
}
