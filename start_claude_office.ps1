<#
Start Claude Office's backend, frontend, readiness checks, and browser.
The script is relocatable: its own directory is the Claude Office root.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $PSScriptRoot).Path
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$backendHealthUrl = "http://127.0.0.1:8000/health"
$frontendUrl = "http://localhost:3000"

function Test-EndpointReady([ValidateSet("Backend", "Frontend")][string]$kind, [string]$url) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -ne 200) { return $false }
        if ($kind -eq "Backend") {
            $health = $response.Content | ConvertFrom-Json
            return [string]$health.status -eq "ok"
        }
        # A different service can also answer on port 3000. Require the
        # Claude Office page marker before calling it already running.
        return $response.Content -match "Claude Office"
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

Write-Host "Claude Officeを起動します。root: $root"

$backendReady = Test-EndpointReady "Backend" $backendHealthUrl
$backendNeedsCheck = $false
if ($backendReady) {
    Write-Host "Backend: already running"
} elseif (Test-PortInUse "127.0.0.1" 8000) {
    Write-Warning "port 8000 は使用中ですがClaude Officeのhealth応答ではありません。既存プロセスは終了せず、Backendは起動しません。"
} else {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Error "uv が見つかりません。uvをインストールしてPATHを更新してから、もう一度実行してください。"
    } elseif (-not (Test-Path -LiteralPath $backendDir)) {
        Write-Error "Backendフォルダが見つかりません: $backendDir"
    } else {
        $backendCommand = "`$Host.UI.RawUI.WindowTitle = 'Claude Office - Backend'; uv run uvicorn app.main:app --host 127.0.0.1 --port 8000"
        if (Start-ClaudeOfficeProcess "Backend" $backendDir $backendCommand) {
            $backendNeedsCheck = $true
        }
    }
}

$frontendReady = Test-EndpointReady "Frontend" $frontendUrl
$frontendNeedsCheck = $false
if ($frontendReady) {
    Write-Host "Frontend: already running"
} elseif (Test-PortInUse "127.0.0.1" 3000) {
    Write-Warning "port 3000 は使用中ですがClaude Officeの応答ではありません。既存プロセスは終了せず、Frontendは起動しません。"
} else {
    if (-not (Get-Command bun -ErrorAction SilentlyContinue)) {
        Write-Error "bun が見つかりません。bunをインストールしてPATHを更新してから、もう一度実行してください。"
    } elseif (-not (Test-Path -LiteralPath $frontendDir)) {
        Write-Error "Frontendフォルダが見つかりません: $frontendDir"
    } else {
        $frontendCommand = "`$Host.UI.RawUI.WindowTitle = 'Claude Office - Frontend'; bun run dev"
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
        Start-Process $frontendUrl | Out-Null
        Write-Host "ブラウザを開きました: $frontendUrl"
    } catch {
        Write-Warning "ブラウザを起動できませんでした。手動で $frontendUrl を開いてください。"
    }
    Write-Host "Claude Officeの起動が完了しました。停止は各ウィンドウでCtrl+Cです。"
} else {
    Write-Warning "Claude Officeは完全には起動できませんでした。Backend ready=$backendReady, Frontend ready=$frontendReady"
    Write-Host "起動したPowerShellウィンドウのログを確認し、依存関係とport使用状況を確認してください。"
}
