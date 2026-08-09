<#[CmdletBinding()]#>
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $PSScriptRoot).Path
$icon = Join-Path $root "manager\assets\claude-office-manager.ico"
$entry = Join-Path $root "manager_launcher.py"
$dist = Join-Path $root "dist"
$work = Join-Path $root "build\manager"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if ([string]::IsNullOrWhiteSpace($Python)) {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "Python virtual environment was not found. Run 'uv sync --extra manager' first."
    }
    $Python = "`"$venvPython`""
}

if (-not (Test-Path -LiteralPath $icon)) {
    throw "Manager icon was not found: $icon"
}
if (-not (Test-Path -LiteralPath $entry)) {
    throw "Manager entry point was not found: $entry"
}

Write-Host "PyInstallerでAI-Office-Manager.exeをビルドします。"
$assetData = "$icon;manager\assets"
$command = "& $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name AI-Office-Manager --icon `"$icon`" --add-data `"$assetData`" --distpath `"$dist`" --workpath `"$work`" --specpath `"$work`" `"$entry`""
Invoke-Expression $command
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$exe = Join-Path $dist "AI-Office-Manager.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "PyInstaller did not produce the expected executable: $exe"
}
Write-Host "ビルド完了: $exe"
