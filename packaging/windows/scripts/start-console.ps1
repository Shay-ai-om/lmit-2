param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$ConfigPath
)

$ErrorActionPreference = "Stop"
$ExePath = Join-Path $InstallDir "lmit-wiki.exe"

function Quote-Argument {
    param([Parameter(Mandatory=$true)][string]$Value)
    '"' + $Value.Replace('"', '\"') + '"'
}

$server = New-Object System.Diagnostics.ProcessStartInfo
$server.FileName = $ExePath
$server.Arguments = "serve --config $(Quote-Argument $ConfigPath) --host 127.0.0.1 --port 8765"
$server.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$server.UseShellExecute = $true
[System.Diagnostics.Process]::Start($server) | Out-Null

Start-Sleep -Seconds 2

$browser = New-Object System.Diagnostics.ProcessStartInfo
$browser.FileName = "http://127.0.0.1:8765"
$browser.UseShellExecute = $true
[System.Diagnostics.Process]::Start($browser) | Out-Null
