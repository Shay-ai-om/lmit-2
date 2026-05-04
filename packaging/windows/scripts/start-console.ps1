param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$ConfigPath
)

$ErrorActionPreference = "Stop"
$ExePath = Join-Path $InstallDir "lmit-wiki.exe"
Add-Type -AssemblyName System.Windows.Forms

function Quote-Argument {
    param([Parameter(Mandatory=$true)][string]$Value)
    '"' + $Value.Replace('"', '\"') + '"'
}

function Show-Error {
    param([Parameter(Mandatory=$true)][string]$Message)
    [System.Windows.Forms.MessageBox]::Show(
        $Message,
        "LMIT-2 Wiki Console",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
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

$server = New-Object System.Diagnostics.ProcessStartInfo
$server.FileName = $ExePath
$server.Arguments = "serve --config $(Quote-Argument $ConfigPath) --host 127.0.0.1 --port 8765"
$server.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$server.UseShellExecute = $true
$serverProcess = [System.Diagnostics.Process]::Start($server)

$deadline = [DateTime]::UtcNow.AddSeconds(20)
while ([DateTime]::UtcNow -lt $deadline) {
    if ($serverProcess.HasExited) {
        $code = $serverProcess.ExitCode
        Show-Error "LMIT-2 Wiki server exited before opening the UI. Exit code: $code. Check that the config file exists and that port 8765 is free.`n`nConfig: $ConfigPath"
        exit 1
    }
    if (Test-TcpPort -HostName "127.0.0.1" -Port 8765) {
        break
    }
    Start-Sleep -Milliseconds 250
}

if ($serverProcess.HasExited -or -not (Test-TcpPort -HostName "127.0.0.1" -Port 8765)) {
    Show-Error "LMIT-2 Wiki server did not start listening on 127.0.0.1:8765. Check the config file and whether another app is already using that port.`n`nConfig: $ConfigPath"
    exit 1
}

$browser = New-Object System.Diagnostics.ProcessStartInfo
$browser.FileName = "http://127.0.0.1:8765"
$browser.UseShellExecute = $true
[System.Diagnostics.Process]::Start($browser) | Out-Null
