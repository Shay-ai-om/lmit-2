param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$ConfigPath
)

$ErrorActionPreference = "Stop"
$ExePath = Join-Path $InstallDir "lmit-wiki.exe"

Start-Process -FilePath $ExePath -ArgumentList @("serve", "--config", $ConfigPath) -WindowStyle Hidden
Start-Sleep -Seconds 2
Start-Process "http://127.0.0.1:8765"
