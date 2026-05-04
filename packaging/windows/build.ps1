param(
    [switch]$SkipInstaller,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Spec = Join-Path $PSScriptRoot "lmit-wiki.spec"
$InnoScript = Join-Path $PSScriptRoot "lmit-2-windows.iss"

function Invoke-Checked {
    param(
        [Parameter(Mandatory=$true)][scriptblock]$Command,
        [Parameter(Mandatory=$true)][string]$Label
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Push-Location $Root
try {
    if (-not $SkipInstall) {
        Invoke-Checked -Label "pip install" -Command {
            python -m pip install -e ".[packaging]"
        }
    }
    else {
        Write-Host "Skipping dependency installation."
    }
    Invoke-Checked -Label "PyInstaller" -Command {
        python -m PyInstaller --clean --noconfirm $Spec
    }

    if (-not $SkipInstaller) {
        $iscc = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        $isccPath = if ($null -ne $iscc) { $iscc.Source } else { $null }
        if (-not $isccPath) {
            $isccPath = @(
                "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
                "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
            ) | Where-Object { Test-Path $_ } | Select-Object -First 1
        }
        if (-not $isccPath) {
            throw "ISCC.exe was not found. Install Inno Setup or rerun with -SkipInstaller."
        }
        Invoke-Checked -Label "Inno Setup" -Command {
            & $isccPath $InnoScript
        }
    }
}
finally {
    Pop-Location
}
