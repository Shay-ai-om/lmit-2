param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Spec = Join-Path $PSScriptRoot "lmit-wiki.spec"
$InnoScript = Join-Path $PSScriptRoot "lmit-2-windows.iss"

Push-Location $Root
try {
    python -m pip install -e ".[packaging]"
    python -m PyInstaller --clean --noconfirm $Spec

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
        & $isccPath $InnoScript
    }
}
finally {
    Pop-Location
}
