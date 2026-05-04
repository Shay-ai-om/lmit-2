param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$ConfigPath
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "config-common.ps1")

$ExePath = Join-Path $InstallDir "lmit-wiki.exe"
try {
    Ensure-LmitWikiConfig -InstallDir $InstallDir -ConfigPath $ConfigPath
}
catch {
    Show-LmitError "LMIT-2 could not create or load its local config.`n`n$($_.Exception.Message)`n`nConfig: $ConfigPath"
    exit 1
}

& $ExePath lint --config $ConfigPath
if ($LASTEXITCODE -ne 0) {
    Read-Host "LMIT-2 lint found issues. Press Enter to close"
    exit $LASTEXITCODE
}
