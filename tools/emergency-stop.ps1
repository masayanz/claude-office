[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if ($Check -and $Force) {
    throw "-Check と -Force は同時に指定できません。"
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$configPath = Join-Path $root "config\app-settings.json"
$runtimePath = Join-Path $root "runtime\processes.json"
$logPath = Join-Path $root "runtime\logs\manager.log"

try {
    $settings = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
} catch {
    throw "共有設定を読み込めません: $configPath"
}

$servicePorts = [ordered]@{
    backend = [int]$settings.backend_port
    frontend = [int]$settings.frontend_port
}
foreach ($entry in $servicePorts.GetEnumerator()) {
    if ($entry.Value -lt 1 -or $entry.Value -gt 65535) {
        throw "共有設定の $($entry.Key) ポートが不正です。"
    }
}

$records = @{}
if (Test-Path -LiteralPath $runtimePath) {
    try {
        $runtime = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
        foreach ($service in @("backend", "frontend")) {
            $record = $runtime.$service
            if ($null -ne $record) {
                $records[$service] = $record
            }
        }
    } catch {
        Write-Warning "Managerの実行時記録を読めないため、PIDの所有確認は未確認になります。"
    }
}

function Write-SafeEvent([string]$Message) {
    $line = "$(Get-Date -Format o) emergency-stop: $Message"
    Write-Output $line
    try {
        $logDirectory = Split-Path -Parent $logPath
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
    } catch {
        # Reporting to the console remains available when the log is locked.
    }
}

function Get-Listeners([int]$Port) {
    @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object LocalPort, OwningProcess)
}

function Get-ProcessSummary([int]$ProcessId, [string]$Service) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    $name = if ($null -ne $process -and $process.Name) { [IO.Path]::GetFileName($process.Name) } else { "-" }
    $cwdName = "-"
    $verified = $false
    if ($records.ContainsKey($Service)) {
        $record = $records[$Service]
        $expectedCwd = Join-Path $root $Service
        if ([int]$record.pid -eq $ProcessId -and $record.cwd) {
            try {
                $actual = [IO.Path]::GetFullPath($record.cwd).TrimEnd('\')
                $expected = [IO.Path]::GetFullPath($expectedCwd).TrimEnd('\')
                $verified = $actual.Equals($expected, [StringComparison]::OrdinalIgnoreCase) -or
                    $actual.StartsWith($expected + '\', [StringComparison]::OrdinalIgnoreCase)
                $cwdName = [IO.Path]::GetFileName($actual)
            } catch {
                $verified = $false
            }
        }
    }
    [pscustomobject]@{
        Service = $Service
        Port = $servicePorts[$Service]
        Pid = $ProcessId
        ProcessName = $name
        CwdName = $cwdName
        Identity = if ($verified) { "AI Office Viewer" } else { "AI Office Viewerと確認できません" }
        Verified = $verified
    }
}

function Get-CurrentTargets {
    $items = @()
    foreach ($entry in $servicePorts.GetEnumerator()) {
        $listeners = @(Get-Listeners $entry.Value)
        foreach ($listener in $listeners) {
            $targetPid = [int]$listener.OwningProcess
            if ($targetPid -gt 0) {
                $items += Get-ProcessSummary $targetPid $entry.Key
            }
        }
    }
    $items
}

function Get-DescendantPids([int]$RootPid) {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Select-Object ProcessId, ParentProcessId)
    $pending = [Collections.Generic.List[int]]::new()
    $pending.Add($RootPid)
    $descendants = [Collections.Generic.List[int]]::new()
    while ($pending.Count -gt 0) {
        $parent = $pending[0]
        $pending.RemoveAt(0)
        foreach ($process in $processes) {
            if ([int]$process.ParentProcessId -eq $parent -and
                -not $descendants.Contains([int]$process.ProcessId)) {
                $descendants.Add([int]$process.ProcessId)
                $pending.Add([int]$process.ProcessId)
            }
        }
    }
    @($descendants)
}

$targets = @(Get-CurrentTargets)
if ($targets.Count -eq 0) {
    Write-SafeEvent "対象ポートにLISTENINGプロセスはありません。"
    exit 0
}

foreach ($target in $targets) {
    Write-SafeEvent (
        "inspect service={0} port={1} pid={2} process={3} cwd={4} identity={5}" -f
        $target.Service, $target.Port, $target.Pid, $target.ProcessName,
        $target.CwdName, $target.Identity
    )
}

if ($Check) {
    Write-SafeEvent "Check mode: 終了処理は実行していません。"
    exit 0
}

if (-not $Force) {
    $answer = Read-Host "表示されたPIDを強制停止します。続行する場合は YES と入力してください"
    if ($answer -cne "YES") {
        Write-SafeEvent "キャンセルされました。"
        exit 0
    }
}

# Recheck immediately before action.  The current PID is used, never a stale
# PID from the first inspection.  -Force itself is the explicit confirmation
# for this standalone emergency command.
$targets = @(Get-CurrentTargets)
foreach ($target in $targets) {
    Write-SafeEvent "emergency stop requested service=$($target.Service) port=$($target.Port) pid=$($target.Pid) identity=$($target.Identity)"
    $pids = [Collections.Generic.List[int]]::new()
    $pids.Add($target.Pid)
    if ($target.Verified) {
        foreach ($childPid in @(Get-DescendantPids $target.Pid)) {
            if (-not $pids.Contains([int]$childPid)) {
                $pids.Add([int]$childPid)
            }
        }
    }

    foreach ($targetPid in ($pids | Sort-Object -Descending)) {
        try {
            Stop-Process -Id $targetPid -ErrorAction SilentlyContinue
            Write-SafeEvent "terminate pid=$targetPid tree=$($target.Verified)"
        } catch {
            Write-SafeEvent "terminate failed pid=$targetPid"
        }
    }
    Start-Sleep -Seconds 2
    foreach ($targetPid in ($pids | Sort-Object -Descending)) {
        if (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) {
            try {
                Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
                Write-SafeEvent "kill pid=$targetPid tree=$($target.Verified)"
            } catch {
                Write-SafeEvent "kill failed pid=$targetPid"
            }
        }
    }
}

$remaining = @(Get-CurrentTargets)
if ($remaining.Count -eq 0) {
    Write-SafeEvent "port released"
    exit 0
}
foreach ($target in $remaining) {
    Write-SafeEvent "port remains in use service=$($target.Service) port=$($target.Port) pid=$($target.Pid) identity=$($target.Identity)"
}
exit 2
