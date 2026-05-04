param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$ConfigPath
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "config-common.ps1")
$ExePath = Join-Path $InstallDir "lmit-wiki.exe"

function Quote-Argument {
    param([Parameter(Mandatory=$true)][string]$Value)
    '"' + $Value.Replace('"', '\"') + '"'
}

function Test-TcpPort {
    param(
        [Parameter(Mandatory=$true)][string]$HostName,
        [Parameter(Mandatory=$true)][int]$Port
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync($HostName, $Port)
        if (-not $task.Wait(250)) {
            return $false
        }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

try {
    Ensure-LmitWikiConfig -InstallDir $InstallDir -ConfigPath $ConfigPath
}
catch {
    Show-LmitError "LMIT-2 could not create or load its local config.`n`n$($_.Exception.Message)`n`nConfig: $ConfigPath"
    exit 1
}

$configDir = Split-Path -Parent ([System.IO.Path]::GetFullPath($ConfigPath))
$logDir = Join-Path $configDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdoutPath = Join-Path $logDir "serve.stdout.log"
$stderrPath = Join-Path $logDir "serve.stderr.log"

$server = New-Object System.Diagnostics.ProcessStartInfo
$server.FileName = $ExePath
$server.Arguments = "serve --config $(Quote-Argument $ConfigPath) --host 127.0.0.1 --port 8765"
$server.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$server.UseShellExecute = $false
$server.RedirectStandardOutput = $true
$server.RedirectStandardError = $true
$server.CreateNoWindow = $true
$serverProcess = [System.Diagnostics.Process]::Start($server)

$deadline = [DateTime]::UtcNow.AddSeconds(20)
while ([DateTime]::UtcNow -lt $deadline) {
    if ($serverProcess.HasExited) {
        $code = $serverProcess.ExitCode
        $stdout = $serverProcess.StandardOutput.ReadToEnd()
        $stderr = $serverProcess.StandardError.ReadToEnd()
        [System.IO.File]::WriteAllText($stdoutPath, $stdout, [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::WriteAllText($stderrPath, $stderr, [System.Text.UTF8Encoding]::new($false))
        Show-LmitError "LMIT-2 Wiki server exited before opening the UI. Exit code: $code.`n`n$stderr`n`nConfig: $ConfigPath`nLog: $stderrPath"
        exit 1
    }
    if (Test-TcpPort -HostName "127.0.0.1" -Port 8765) {
        break
    }
    Start-Sleep -Milliseconds 250
}

if ($serverProcess.HasExited -or -not (Test-TcpPort -HostName "127.0.0.1" -Port 8765)) {
    if (-not $serverProcess.HasExited) {
        $serverProcess.Kill()
    }
    Show-LmitError "LMIT-2 Wiki server did not start listening on 127.0.0.1:8765. Check whether another app is already using that port.`n`nConfig: $ConfigPath"
    exit 1
}

$browser = New-Object System.Diagnostics.ProcessStartInfo
$browser.FileName = "http://127.0.0.1:8765"
$browser.UseShellExecute = $true
[System.Diagnostics.Process]::Start($browser) | Out-Null
