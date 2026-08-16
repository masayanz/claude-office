[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$NoZip,
    [switch]$Clean,
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $PSScriptRoot).Path
$releaseDir = Join-Path $root "release"
$stagingDir = Join-Path $releaseDir "staging"
$packageDir = Join-Path $stagingDir "AI-Office-Viewer"
$frontendOut = Join-Path $root "frontend\out"
$managerExe = Join-Path $root "dist\AI-Office-Viewer-Manager.exe"
$managerBuildDir = Join-Path $root "build\portable-manager-dist"
$managerBuildExe = Join-Path $managerBuildDir "AI-Office-Viewer-Manager.exe"
$backendDir = Join-Path $root "dist\AI-Office-Viewer-Backend"
$backendExe = Join-Path $backendDir "AI-Office-Viewer-Backend.exe"
$adapterDir = Join-Path $root "dist\AI-Office-Viewer-Codex-Adapter"
$adapterExe = Join-Path $adapterDir "AI-Office-Viewer-Codex-Adapter.exe"
$python = Join-Path $root ".venv\Scripts\python.exe"
$backendSitePackages = Join-Path $root "backend\.venv\Lib\site-packages"
$buildStage = "初期化"
$zipPath = $null
$latestPath = Join-Path $releaseDir "AI-Office-Viewer-Portable-Latest.zip"
$latestHashPath = "$latestPath.sha256"

function Write-Step([int]$Number, [string]$Title) {
    Write-Host "[$Number/10] $Title" -ForegroundColor Cyan
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments, [string]$Label, [string]$WorkingDirectory = $root) {
    Write-Host "  $Label" -ForegroundColor DarkGray
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Label が終了コード $LASTEXITCODE で失敗しました。"
        }
    } finally {
        Pop-Location
    }
}

function Copy-DirectoryContents([string]$Source, [string]$Destination) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($item in @(Get-ChildItem -LiteralPath $Source -Force)) {
        Copy-Item -LiteralPath $item.FullName -Destination $Destination -Recurse -Force
    }
}

function Get-ProjectVersion {
    if (-not [string]::IsNullOrWhiteSpace($Version)) {
        if ($Version -notmatch '^[0-9A-Za-z][0-9A-Za-z.+-]*$') {
            throw "Version overrideが不正です: $Version"
        }
        return $Version
    }
    $pyproject = Join-Path $root "pyproject.toml"
    $match = Select-String -LiteralPath $pyproject -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($null -eq $match) {
        throw "pyproject.tomlからVersionを取得できません。-Versionを指定してください。"
    }
    return $match.Matches[0].Groups[1].Value
}

function Get-GitValue([string[]]$Arguments, [string]$Fallback) {
    try {
        $output = & git -c safe.directory=$root @Arguments 2>$null
        $exitCode = $LASTEXITCODE
        $first = $output | Select-Object -First 1
        if ($null -eq $first) {
            $value = ""
        } else {
            $value = $first.ToString().Trim()
        }
        if ($exitCode -eq 0 -and $value) { return $value }
    } catch {
        # A source archive or a machine without Git is supported.
    }
    return $Fallback
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Write-InitialSettings([string]$Path) {
    $settings = [ordered]@{
        language = "ja"
        backend_host = "127.0.0.1"
        backend_port = 8000
        frontend_host = "127.0.0.1"
        frontend_port = 3000
        open_browser_on_start = $true
        browser_mode = "normal"
        company_name = ""
        owner_name = "Owner"
        owner_title = ""
        owner_message = ""
        owner_image_filename = $null
        board_mode = "todo"
        daily_goals = @()
        weekly_goals = @()
        board_memo = ""
        custom_board_title = ""
        custom_board_message = ""
        board_auto_rotate = $false
        board_rotate_seconds = 10
        stop_servers_on_manager_exit = $true
        restore_codex_sessions = $true
        restore_window_minutes = 30
        clock_timezone_mode = "local"
        clock_timezone = ""
        main_agent_name_mode = "auto"
        main_agent_custom_name = ""
        replay_history_enabled = $true
        replay_retention_days = 30
        replay_compress_idle = $false
        replay_default_speed = 1
        replay_clock_mode = "recorded"
    }
    Write-Utf8NoBom $Path ($settings | ConvertTo-Json -Depth 8)
}

function Write-PortableReadme([string]$Path, [string]$ProductVersion, [string]$BuildStamp) {
    @"
AI Office Viewer Portable
=========================

これはAI Office ViewerのPortable配布版です。Python、uv、Node.js、Bunは配布先で不要です。

初回起動
--------
1. このZIPを任意のフォルダへ展開します。
2. AI-Office-Viewer-Manager.exeを起動します。
3. Managerの「Codex連携を診断」を確認し、必要なら「Global Hooksを修復」を実行します。
4. VS CodeでCodexを使うと、ViewerにAIエージェントの活動が表示されます。
5. ViewerはManagerの「ブラウザで開く」または「専用画面で開く」から開きます。
6. 終了時はManagerの「停止」を押してからManagerを終了します。

詳しい操作方法
--------------
Managerの「ヘルプ」→「利用者マニュアル」、または help\index.html を開いてください。

サーバーが残った場合
----------------------
tools\emergency-stop.ps1 -Check で対象を確認できます。
停止する場合は tools\emergency-stop.ps1 -Force を実行します。
このスクリプトはPortableフォルダ内の設定と実行状態を基準に確認します。

Codex連携の修復・解除
----------------------
Managerの「Global Hooksを修復」で、既存の他のHooksを保持したままAI Office Viewer用entryだけを追加します。
連携を解除する場合は runtime\codex-adapter\uninstall-global-hooks.ps1 を実行します。
Codexが未導入でもManagerやViewerはクラッシュせず、診断に「Codexが見つかりません」と表示します。

データ保存先
------------
runtime\ は配布ランタイム、config\ は初期設定、data\ はSQLite DB・ユーザー設定、logs\ は実行ログです。
更新時もruntimeとdataを分離しているため、旧フォルダをバックアップしてから新ZIPを展開し、必要ならconfig\とdata\を引き継いでください。

更新手順
--------
1. AI Office Viewerを終了します。
2. 旧フォルダをバックアップします。
3. 新しいZIPを展開します。
4. 必要なら旧フォルダのconfig\data\logsを新フォルダへ引き継ぎます。

削除方法
--------
Viewerを終了し、このPortableフォルダを削除します。Codex連携を設定した場合は、先にGlobal Hooksを解除してください。

構成
----
Manager.exeがBackend.exeを起動し、Backend.exeがruntime\frontendの静的production buildをlocalhostだけで配信します。
外部LAN公開やFirewall変更は行いません。ポートが使用中の場合はManagerが空きポートへ切り替えます。

Version: $ProductVersion
Build: $BuildStamp
"@ | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Invoke-DistributionScan([string]$Directory) {
    $blockedNamePattern = '(?i)(^|[\\/])(?:\.env(?:\..*)?|visualizer\.db(?:[-.].*)?|.*\.sqlite(?:[-.].*)?|token|secret|credential|private[-_ ]key)(?:$|[\\/])'
    $textExtensions = @('.txt', '.json', '.ps1', '.py', '.toml', '.md', '.ini', '.cfg', '.yaml', '.yml', '.js', '.ts', '.tsx', '.css', '.html', '.xml', '.log')
    $contentPatterns = @(
        '(?i)C:\\Users\\admin',
        '(?i)D:\\vswork2',
        '(?i)api[_-]?key\s*[:=]',
        '(?i)authorization\s*[:=]',
        '(?i)\bbearer\b',
        '(?i)PRIVATE KEY',
        '(?i)BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY',
        '(?i)password\s*=',
        '(?i)token\s*='
    )
    $findings = [System.Collections.Generic.List[string]]::new()
    foreach ($file in @(Get-ChildItem -LiteralPath $Directory -File -Recurse -Force)) {
        $relative = $file.FullName.Substring($Directory.Length).TrimStart('\', '/')
        # Next.js production chunks contain generic words such as "password"
        # in browser polyfills. They are generated third-party runtime assets,
        # so scan the application/configuration text but skip this bundle.
        if ($relative -match '^runtime[\\/]frontend[\\/]_next[\\/]') {
            continue
        }
        if ($relative -match $blockedNamePattern) {
            $findings.Add("禁止ファイル名: $relative")
            continue
        }
        if ($textExtensions -contains $file.Extension.ToLowerInvariant()) {
            try {
                $content = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop
                foreach ($pattern in $contentPatterns) {
                    if ($content -match $pattern) {
                        $findings.Add("疑わしい文字列 ($pattern): $relative")
                        break
                    }
                }
            } catch {
                $findings.Add("読み取り失敗: $relative")
            }
        }
    }
    if ($findings.Count -gt 0) {
        throw "配布内容の機密情報検査に失敗しました。`n$($findings -join "`n")"
    }
}

function Assert-PortableSmokeTest([string]$Directory) {
    $required = @(
        "AI-Office-Viewer-Manager.exe",
        "runtime\backend\AI-Office-Viewer-Backend.exe",
        "runtime\frontend\index.html",
        "runtime\codex-adapter\AI-Office-Viewer-Codex-Adapter.exe",
        "tools\emergency-stop.ps1",
        "help\index.html",
        "README.txt",
        "VERSION.txt",
        "portable.flag"
    )
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $Directory $relative) -PathType Leaf)) {
            throw "staging smoke test失敗: $relative がありません。"
        }
    }
    if (Test-Path -LiteralPath (Join-Path $Directory "data\visualizer.db")) {
        throw "staging smoke test失敗: 初期SQLite DBが含まれています。"
    }
}

function Assert-HelpKit([string]$Directory) {
    $index = Join-Path $Directory "index.html"
    if (-not (Test-Path -LiteralPath $index -PathType Leaf)) {
        throw "help検査失敗: help/index.html がありません。"
    }
    foreach ($html in @(Get-ChildItem -LiteralPath $Directory -Filter "*.html" -File -Recurse)) {
        $content = Get-Content -LiteralPath $html.FullName -Raw
        if ($content -match '(?i)https?://') {
            throw "help検査失敗: 外部依存URLがあります: $($html.FullName)"
        }
        $ids = [regex]::Matches($content, '(?i)\bid\s*=\s*["'']([^"'']+)["'']') |
            ForEach-Object { $_.Groups[1].Value }
        $duplicateIds = @($ids | Group-Object | Where-Object Count -gt 1)
        if ($duplicateIds.Count -gt 0) {
            throw "help検査失敗: 重複idがあります ($($duplicateIds[0].Name)): $($html.FullName)"
        }
        foreach ($match in [regex]::Matches($content, '(?i)(?:href|src)\s*=\s*["'']([^"'']+)["'']')) {
            $reference = $match.Groups[1].Value
            if ($reference -match '^(?:#|mailto:|tel:|data:|javascript:)' -or $reference -match '^[a-z]+:') {
                continue
            }
            $relative = ($reference -split '[?#]', 2)[0]
            if (-not $relative) { continue }
            $target = [IO.Path]::GetFullPath((Join-Path $html.DirectoryName $relative))
            $helpRoot = [IO.Path]::GetFullPath($Directory).TrimEnd('\') + '\'
            if (-not $target.StartsWith($helpRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw "help検査失敗: help外への参照があります ($reference): $($html.FullName)"
            }
            if (-not (Test-Path -LiteralPath $target)) {
                throw "help検査失敗: 参照先がありません ($reference): $($html.FullName)"
            }
        }
    }
    foreach ($css in @(Get-ChildItem -LiteralPath $Directory -Filter "*.css" -File -Recurse)) {
        $content = Get-Content -LiteralPath $css.FullName -Raw
        if ($content -match '(?i)https?://|@import\s+url|@import\s+["'']') {
            throw "help検査失敗: CSSに外部依存があります: $($css.FullName)"
        }
    }
}

function Write-PackageManifest([string]$Directory, [string]$Path) {
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add("AI Office Viewer Portable Package Manifest")
    $lines.Add("Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
    $lines.Add("SHA256: PACKAGE_MANIFEST.txt自身は自己参照を避けるため除外")
    $lines.Add("")
    foreach ($file in @(Get-ChildItem -LiteralPath $Directory -File -Recurse -Force | Sort-Object FullName)) {
        $relative = $file.FullName.Substring($Directory.Length).TrimStart('\', '/')
        if ($relative -eq "PACKAGE_MANIFEST.txt") { continue }
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $lines.Add("$hash  $relative")
    }
    $lines | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Test-RelocatedKit([string]$Directory) {
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("ai-office-portable-path-test-" + [guid]::NewGuid().ToString("N"))
    $relocated = Join-Path $tempRoot "AI Office Viewer_日本語"
    try {
        New-Item -ItemType Directory -Path $relocated -Force | Out-Null
        Copy-DirectoryContents $Directory $relocated
        Assert-PortableSmokeTest $relocated
        Assert-HelpKit (Join-Path $relocated "help")
        $manualPath = (Resolve-Path -LiteralPath (Join-Path $relocated "help\index.html")).Path
        $manualUri = [Uri]::new($manualPath)
        if (-not $manualUri.IsFile -or -not $manualUri.AbsoluteUri.StartsWith("file:")) {
            throw "移設path test失敗: help/index.htmlをfile URLへ変換できません。"
        }
        $settings = Get-Content -LiteralPath (Join-Path $relocated "config\app-settings.json") -Raw | ConvertFrom-Json
        if ($settings.backend_host -ne "127.0.0.1") {
            throw "移設path test失敗: localhost設定が変更されています。"
        }
        Write-Host "  日本語・空白を含む移設先で相対構成とhelp URLを確認しました。" -ForegroundColor DarkGray
    } finally {
        if (Test-Path -LiteralPath $tempRoot) {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
    $projectVersion = Get-ProjectVersion
    $buildStamp = Get-Date -Format "yyyy-MM-dd HH:mm"
    $commit = Get-GitValue @("rev-parse", "--short", "HEAD") "unknown"
    $gitStatus = @()
    $gitStatusExit = 1
    $gitErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $gitStatus = & git -c safe.directory=$root status --porcelain 2>$null
        $gitStatusExit = $LASTEXITCODE
    } catch {
        # A locked test directory should not make an otherwise valid package fail.
    } finally {
        $ErrorActionPreference = $gitErrorAction
    }
    $dirty = $gitStatusExit -eq 0 -and @($gitStatus).Count -gt 0
    if ($dirty) {
        Write-Warning "Working tree contains uncommitted changes"
    }

    $buildStage = "配布環境を初期化"
    Write-Step 1 $buildStage
    New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
    if ($Clean) {
        foreach ($path in @((Join-Path $root "build"), (Join-Path $root "dist"))) {
            if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
        }
    }
    if (Test-Path -LiteralPath $stagingDir) { Remove-Item -LiteralPath $stagingDir -Recurse -Force }
    New-Item -ItemType Directory -Path $packageDir -Force | Out-Null

    if (-not $SkipBuild) {
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            throw ".venvのPythonが見つかりません。uv sync --extra manager を実行してください。"
        }
        if (-not (Test-Path -LiteralPath $backendSitePackages -PathType Container)) {
            throw "Backendの依存環境が見つかりません: $backendSitePackages"
        }
        $powerShell = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
        if (-not $powerShell) { $powerShell = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source }
        if (-not $powerShell) { throw "PowerShell実行ファイルが見つかりません。" }
    }

    $buildStage = "Manager EXEをビルド"
    Write-Step 2 $buildStage
    if (-not $SkipBuild) {
        Invoke-Checked $powerShell @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $root "build_manager.ps1"), "-Python", $python, "-OutputDirectory", $managerBuildDir) "Manager PyInstaller build"
        $managerSourceExe = $managerBuildExe
    } else {
        $managerSourceExe = $managerExe
    }
    if (-not (Test-Path -LiteralPath $managerSourceExe -PathType Leaf)) { throw "Manager EXEが生成されませんでした。" }

    $buildStage = "Backend EXEをビルド"
    Write-Step 3 $buildStage
    if (-not $SkipBuild) {
        $backendArgs = @(
            "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
            "--name", "AI-Office-Viewer-Backend", "--paths", (Join-Path $root "backend"),
            "--paths", $backendSitePackages,
            "--collect-submodules", "app", "--collect-submodules", "uvicorn",
            "--collect-submodules", "aiosqlite",
            "--collect-all", "aiosqlite", "--hidden-import", "aiosqlite",
            "--distpath", (Join-Path $root "dist"), "--workpath", (Join-Path $root "build\backend"),
            "--specpath", (Join-Path $root "build\backend"), (Join-Path $root "backend_launcher.py")
        )
        Invoke-Checked $python $backendArgs "Backend PyInstaller build"
    }
    if (-not (Test-Path -LiteralPath $backendExe -PathType Leaf)) { throw "Backend EXEが生成されませんでした。" }

    $buildStage = "Frontend production build"
    Write-Step 4 $buildStage
    if (-not $SkipBuild) {
        $npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
        if ($npm) {
            Invoke-Checked $npm @("run", "build", "--", "--webpack") "Frontend npm production build" (Join-Path $root "frontend")
        } else {
            $bun = (Get-Command bun.exe -ErrorAction SilentlyContinue).Source
            if (-not $bun) { throw "npm.cmdまたはbun.exeが見つかりません。Frontend buildが必要です。" }
            Invoke-Checked $bun @("run", "build", "--", "--webpack") "Frontend Bun production build" (Join-Path $root "frontend")
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frontendOut "index.html") -PathType Leaf)) { throw "Frontend production buildのindex.htmlが見つかりません。" }

    $buildStage = "Codex Adapter EXEをビルド"
    Write-Step 5 $buildStage
    if (-not $SkipBuild) {
        $adapterArgs = @(
            "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--noconsole",
            "--name", "AI-Office-Viewer-Codex-Adapter", "--paths", (Join-Path $root "codex-adapter\src"),
            "--distpath", (Join-Path $root "dist"), "--workpath", (Join-Path $root "build\codex-adapter"),
            "--specpath", (Join-Path $root "build\codex-adapter"), (Join-Path $root "codex_adapter_launcher.py")
        )
        Invoke-Checked $python $adapterArgs "Codex Adapter PyInstaller build"
    }
    if (-not (Test-Path -LiteralPath $adapterExe -PathType Leaf)) { throw "Codex Adapter EXEが生成されませんでした。" }

    $buildStage = "Runtimeを配置"
    Write-Step 6 $buildStage
    Copy-Item -LiteralPath $managerSourceExe -Destination (Join-Path $packageDir "AI-Office-Viewer-Manager.exe") -Force
    New-Item -ItemType Directory -Path (Join-Path $packageDir "runtime\backend") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $packageDir "runtime\frontend") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $packageDir "runtime\codex-adapter") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $packageDir "tools") -Force | Out-Null
    Copy-DirectoryContents $backendDir (Join-Path $packageDir "runtime\backend")
    Copy-Item -LiteralPath (Join-Path $root "backend\floors.toml") -Destination (Join-Path $packageDir "runtime\backend\floors.toml") -Force
    Copy-DirectoryContents $frontendOut (Join-Path $packageDir "runtime\frontend")
    Copy-DirectoryContents $adapterDir (Join-Path $packageDir "runtime\codex-adapter")
    Copy-Item -LiteralPath (Join-Path $root "codex-adapter\install-global-hooks.ps1") -Destination (Join-Path $packageDir "runtime\codex-adapter\install-global-hooks.ps1") -Force
    Copy-Item -LiteralPath (Join-Path $root "codex-adapter\uninstall-global-hooks.ps1") -Destination (Join-Path $packageDir "runtime\codex-adapter\uninstall-global-hooks.ps1") -Force
    Copy-Item -LiteralPath (Join-Path $root "tools\emergency-stop.ps1") -Destination (Join-Path $packageDir "tools\emergency-stop.ps1") -Force
    Copy-Item -LiteralPath (Join-Path $root "LICENSE") -Destination (Join-Path $packageDir "LICENSE") -Force
    $helpSource = Join-Path $root "help"
    Assert-HelpKit $helpSource
    New-Item -ItemType Directory -Path (Join-Path $packageDir "help") -Force | Out-Null
    Copy-DirectoryContents $helpSource (Join-Path $packageDir "help")

    $buildStage = "初期設定を生成"
    Write-Step 7 $buildStage
    New-Item -ItemType Directory -Path (Join-Path $packageDir "config\owner-image") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $packageDir "data") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $packageDir "logs") -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $packageDir "config\owner-image\.gitkeep") -Value "" -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $packageDir "data\.gitkeep") -Value "" -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $packageDir "logs\.gitkeep") -Value "" -Encoding UTF8
    Write-InitialSettings (Join-Path $packageDir "config\app-settings.json")
    Set-Content -LiteralPath (Join-Path $packageDir "portable.flag") -Value "AI Office Viewer Portable`n" -Encoding UTF8
    Write-PortableReadme (Join-Path $packageDir "README.txt") $projectVersion $buildStamp
    $versionLines = @(
        "AI Office Viewer",
        "Version: $projectVersion",
        "Build: $buildStamp",
        "Commit: $commit",
        "WorkingTree: $(if ($dirty) { 'dirty' } else { 'clean' })"
    )
    $versionLines | Set-Content -LiteralPath (Join-Path $packageDir "VERSION.txt") -Encoding UTF8
    @(
        "AI Office Viewer Portable third-party notice",
        "===============================================",
        "",
        "この配布キットには、AI Office Viewerの実行に必要なPySide6、FastAPI、Uvicorn、SQLAlchemy等のランタイムがバンドルされています。",
        "各依存ライブラリのライセンス条件は、依存ライブラリの配布元およびプロジェクトの依存関係定義を確認してください。",
        "AI Office Viewer本体のライセンスは同梱のLICENSEに記載しています。",
        ""
    ) | Set-Content -LiteralPath (Join-Path $packageDir "THIRD_PARTY_LICENSES.txt") -Encoding UTF8

    $buildStage = "配布内容検査"
    Write-Step 8 $buildStage
    Assert-PortableSmokeTest $packageDir
    Assert-HelpKit (Join-Path $packageDir "help")
    Test-RelocatedKit $packageDir
    Invoke-DistributionScan $packageDir

    $buildStage = "Manifest生成"
    Write-Step 9 $buildStage
    Write-PackageManifest $packageDir (Join-Path $packageDir "PACKAGE_MANIFEST.txt")
    Assert-PortableSmokeTest $packageDir

    $buildStage = "ZIP作成"
    Write-Step 10 $buildStage
    if (-not $NoZip) {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $zipPath = Join-Path $releaseDir ("AI-Office-Viewer-Portable-{0}-{1}.zip" -f $projectVersion, $timestamp)
        while (Test-Path -LiteralPath $zipPath) {
            Start-Sleep -Milliseconds 1000
            $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
            $zipPath = Join-Path $releaseDir ("AI-Office-Viewer-Portable-{0}-{1}.zip" -f $projectVersion, $timestamp)
        }
        [IO.Compression.ZipFile]::CreateFromDirectory($stagingDir, $zipPath, [IO.Compression.CompressionLevel]::Optimal, $false)
        $zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
        "$zipHash  $([IO.Path]::GetFileName($zipPath))" | Set-Content -LiteralPath "$zipPath.sha256" -Encoding ASCII
        Copy-Item -LiteralPath $zipPath -Destination $latestPath -Force
        $latestHash = (Get-FileHash -LiteralPath $latestPath -Algorithm SHA256).Hash.ToLowerInvariant()
        "$latestHash  $([IO.Path]::GetFileName($latestPath))" | Set-Content -LiteralPath $latestHashPath -Encoding ASCII
    }

    Write-Host ""
    Write-Host "================================" -ForegroundColor Green
    Write-Host "AI Office Viewer Portable Build" -ForegroundColor Green
    Write-Host "================================" -ForegroundColor Green
    Write-Host "Version:  $projectVersion"
    Write-Host "Build:    $buildStamp"
    Write-Host "Manager:  AI-Office-Viewer-Manager.exe"
    Write-Host "Backend:  AI-Office-Viewer-Backend.exe"
    Write-Host "Frontend: static production build"
    Write-Host "Output:   $(if ($NoZip) { $stagingDir } else { $zipPath })"
    if (-not $NoZip) {
        $zipInfo = Get-Item -LiteralPath $zipPath
        Write-Host "ZIP size: $($zipInfo.Length) bytes"
        Write-Host "SHA256:   $zipHash"
    }
    Write-Host ""
    Write-Host "SUCCESS" -ForegroundColor Green
    exit 0
} catch {
    if ($zipPath -and (Test-Path -LiteralPath $zipPath)) { Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue }
    if ($zipPath -and (Test-Path -LiteralPath "$zipPath.sha256")) { Remove-Item -LiteralPath "$zipPath.sha256" -Force -ErrorAction SilentlyContinue }
    Write-Error "Portable build失敗（stage: $buildStage）: $($_.Exception.Message)"
    exit 1
}
