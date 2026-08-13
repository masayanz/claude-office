<#
Compatibility launcher for AI Office Viewer backend, frontend, readiness checks, and browser.
The legacy filename and CLAUDE_OFFICE_* variables remain stable for existing installations.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $PSScriptRoot).Path
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$settingsPath = Join-Path $root "config\app-settings.json"

function Get-AppSettings {
    $defaults = [ordered]@{
        backend_host = "127.0.0.1"; backend_port = 8000
        frontend_host = "127.0.0.1"; frontend_port = 3000
        open_browser_on_start = $true; browser_mode = "normal"
    }
    try {
        $loaded = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
        foreach ($property in $defaults.Keys) {
            if ($null -ne $loaded.$property) { $defaults[$property] = $loaded.$property }
        }
        if ([int]$defaults.backend_port -lt 1024 -or [int]$defaults.backend_port -gt 65535 -or
            [int]$defaults.frontend_port -lt 1024 -or [int]$defaults.frontend_port -gt 65535 -or
            [int]$defaults.backend_port -eq [int]$defaults.frontend_port) {
            throw "backend/frontend port configuration is invalid"
        }
        if ($defaults.browser_mode -notin @("normal", "app")) { throw "browser_mode is invalid" }
    } catch {
        Write-Warning "共通設定を読み込めないため既定値を使用します: $($_.Exception.Message)"
    }
    return [pscustomobject]$defaults
}

$appSettings = Get-AppSettings
$backendHost = [string]$appSettings.backend_host
$backendPort = [int]$appSettings.backend_port
$frontendHost = [string]$appSettings.frontend_host
$frontendPort = [int]$appSettings.frontend_port
$backendHealthUrl = "http://${backendHost}:${backendPort}/health"
$frontendUrl = "http://${frontendHost}:${frontendPort}"
$apiUrl = "http://${backendHost}:${backendPort}"
$wsUrl = "ws://${backendHost}:${backendPort}"

function Test-EndpointReady([ValidateSet("Backend", "Frontend")][string]$kind, [string]$url) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -ne 200) { return $false }
        if ($kind -eq "Backend") {
            $health = $response.Content | ConvertFrom-Json
            return [string]$health.status -eq "ok"
        }
        # A different service can also answer on the configured port. Require the
        # AI Office Viewer page marker before calling it already running.
        return $response.Content -match "AI Office Viewer"
    } catch {
        return $false
    }
}

function Test-PortInUse([string]$hostName, [int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync($hostName, $port)
        if (-not $task.Wait(500)) { return $false }
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Find-AppBrowser {
    $roots = @(
        [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFilesX86),
        [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles),
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)},
        $env:LOCALAPPDATA
    ) | Where-Object { $_ }
    $installations = @(
        @{ Name = "Microsoft Edge"; RelativePath = "Microsoft\Edge\Application\msedge.exe" },
        @{ Name = "Google Chrome"; RelativePath = "Google\Chrome\Application\chrome.exe" }
    )
    foreach ($installation in $installations) {
        foreach ($rootPath in $roots) {
            $candidate = Join-Path $rootPath $installation.RelativePath
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
        $command = Get-Command ([IO.Path]::GetFileName($installation.RelativePath)) -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
    }
    return $null
}

function Wait-EndpointReady([ValidateSet("Backend", "Frontend")][string]$kind, [string]$name, [string]$url, [int]$maxSeconds = 30) {
    for ($second = 0; $second -lt $maxSeconds; $second++) {
        if (Test-EndpointReady $kind $url) { return $true }
        Start-Sleep -Seconds 1
    }
    Write-Warning "$name の起動確認に失敗しました: $url (最大${maxSeconds}秒)"
    return $false
}

function Start-ClaudeOfficeProcess([string]$title, [string]$workingDirectory, [string]$command) {
    try {
        Start-Process -FilePath "powershell.exe" -WorkingDirectory $workingDirectory -ArgumentList @(
            "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command
        ) | Out-Null
        return $true
    } catch {
        Write-Error "$title を起動できませんでした。PowerShellウィンドウを確認してください。詳細: $($_.Exception.Message)"
        return $false
    }
}

Write-Host "AI Office Viewerを起動します。root: $root"

$backendReady = Test-EndpointReady "Backend" $backendHealthUrl
$backendNeedsCheck = $false
if ($backendReady) {
    Write-Host "Backend: already running"
} elseif (Test-PortInUse $backendHost $backendPort) {
    Write-Warning "port $backendPort は使用中ですがAI Office Viewerのhealth応答ではありません。既存プロセスは終了せず、Backendは起動しません。"
} else {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Error "uv が見つかりません。uvをインストールしてPATHを更新してから、もう一度実行してください。"
    } elseif (-not (Test-Path -LiteralPath $backendDir)) {
        Write-Error "Backendフォルダが見つかりません: $backendDir"
    } else {
        $backendCommand = "`$env:CLAUDE_OFFICE_ROOT = '$root'; `$Host.UI.RawUI.WindowTitle = 'AI Office Viewer - Backend'; uv run uvicorn app.main:app --host $backendHost --port $backendPort"
        if (Start-ClaudeOfficeProcess "Backend" $backendDir $backendCommand) {
            $backendNeedsCheck = $true
        }
    }
}

$frontendReady = Test-EndpointReady "Frontend" $frontendUrl
$frontendNeedsCheck = $false
if ($frontendReady) {
    Write-Host "Frontend: already running"
} elseif (Test-PortInUse $frontendHost $frontendPort) {
    Write-Warning "port $frontendPort は使用中ですがAI Office Viewerの応答ではありません。既存プロセスは終了せず、Frontendは起動しません。"
} else {
    if (-not (Get-Command bun -ErrorAction SilentlyContinue)) {
        Write-Error "bun が見つかりません。bunをインストールしてPATHを更新してから、もう一度実行してください。"
    } elseif (-not (Test-Path -LiteralPath $frontendDir)) {
        Write-Error "Frontendフォルダが見つかりません: $frontendDir"
    } else {
        $frontendCommand = "`$env:NEXT_PUBLIC_API_URL = '$apiUrl'; `$env:NEXT_PUBLIC_WS_URL = '$wsUrl'; `$env:CLAUDE_OFFICE_ROOT = '$root'; `$Host.UI.RawUI.WindowTitle = 'AI Office Viewer - Frontend'; bun run dev -- --hostname $frontendHost --port $frontendPort"
        if (Start-ClaudeOfficeProcess "Frontend" $frontendDir $frontendCommand) {
            $frontendNeedsCheck = $true
        }
    }
}

# Both processes are launched before waiting so a slow backend cannot delay
# frontend startup or make the one-click command appear frozen.
if ($backendNeedsCheck) {
    $backendReady = Wait-EndpointReady "Backend" "Backend" $backendHealthUrl
}
if ($frontendNeedsCheck) {
    $frontendReady = Wait-EndpointReady "Frontend" "Frontend" $frontendUrl
}

if ($backendReady -and $frontendReady) {
    try {
        if ([bool]$appSettings.open_browser_on_start -and [string]$appSettings.browser_mode -eq "app") {
            $browser = Find-AppBrowser
            if (-not $browser) {
                throw "専用画面用のMicrosoft EdgeまたはGoogle Chromeが見つかりません。"
            }
            Start-Process -FilePath $browser -ArgumentList @(
                "--app=$frontendUrl",
                "--new-window",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized"
            ) | Out-Null
        } elseif ([bool]$appSettings.open_browser_on_start) {
            Start-Process $frontendUrl | Out-Null
        }
        if ([bool]$appSettings.open_browser_on_start) {
            Write-Host "ブラウザを開きました: $frontendUrl"
        }
    } catch {
        Write-Warning "ブラウザを起動できませんでした。手動で $frontendUrl を開いてください。"
    }
    Write-Host "AI Office Viewerの起動が完了しました。"
} else {
    Write-Warning "AI Office Viewerは完全には起動できませんでした。Backend ready=$backendReady, Frontend ready=$frontendReady"
    Write-Host "起動したPowerShellウィンドウのログを確認し、依存関係とport使用状況を確認してください。"
}
