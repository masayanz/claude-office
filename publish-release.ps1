[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$NotesFile = "",
    [switch]$Publish,
    [switch]$PreRelease,
    [switch]$UpdateAssets,
    [switch]$ReplaceAssets,
    [switch]$Build,
    [switch]$Yes,
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = (Resolve-Path $PSScriptRoot).Path
$releaseDirectory = Join-Path $root "release"
$releaseConfigPath = Join-Path $root "release-config.json"
$defaultNotesPath = Join-Path $root "RELEASE_NOTES.md"
$publishLogPath = Join-Path $releaseDirectory "publish.log"
$script:GhPath = $null

$ProjectVersionPattern = '^version\s*=\s*"([^"]+)"'
$Sha256FilePattern = '^([0-9a-fA-F]{64})\s+\*?(.+?)$'
$ManifestHeaderPattern = '(?m)^AI Office Viewer Portable Package Manifest\s*$'
$ManifestEntryPattern = '(?m)^[0-9a-fA-F]{64}\s{2}.+$'
$BlockedZipEntryPatterns = @(
    '(?i)(^|/)\.env(?:\..*)?$'
    '(?i)\.(?:db|sqlite|sqlite3|jsonl)$'
    '(?i)(^|/)\.codex(?:/|$)'
    '(?i)(^|/)(?:sessions?|rollouts?)(?:/|$)'
    '(?i)(^|/)(?:token|secret|credential|private[-_ ]key)(?:/|$)'
)
$SensitiveTextExtensions = @(
    '.txt', '.json', '.ps1', '.py', '.toml', '.md', '.ini', '.cfg',
    '.yaml', '.yml', '.xml', '.log'
)
$SensitiveContentPatterns = @(
    '(?i)C:\\Users\\'
    '(?i)[A-Z]:\\(?:vswork|webwork)'
    '(?i)authorization\s*[:=]'
    '(?i)\bbearer\s+\S{8,}'
    '(?i)-----BEGIN .* PRIVATE KEY-----'
    '(?i)"?api[_-]?key"?\s*[:=]\s*"[^"]{8,}"'
    '(?i)\bapi[_-]?key\s*=\s*\S{8,}'
    '(?i)"?token"?\s*[:=]\s*"[^"]{8,}"'
    '(?i)\btoken\s*=\s*\S{8,}'
    '(?i)"?password"?\s*[:=]\s*"[^"]{8,}"'
    '(?i)\bpassword\s*=\s*\S{8,}'
)

function Show-Usage {
    Write-Host "AI Office Viewer Portable Release publisher"
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  .\publish-release.ps1 [-Version X.Y.Z] [-NotesFile path] [-Build]"
    Write-Host "                             [-Publish] [-PreRelease] [-Yes]"
    Write-Host "  .\publish-release.ps1 -UpdateAssets [-ReplaceAssets] [-Version X.Y.Z]"
    Write-Host "  .\publish-release.ps1 -Help"
    Write-Host ""
    Write-Host "通常実行はDraft Releaseを作成します。-Publish指定時だけ公開します。"
}

function Write-PublishLog([string]$Message) {
    New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Add-Content -LiteralPath $publishLogPath -Value $line -Encoding UTF8
}

function Invoke-NativeCapture(
    [string]$FilePath,
    [string[]]$Arguments
) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 converts native stderr redirected with 2>&1
        # into ErrorRecord objects. Capture them and judge only by LASTEXITCODE.
        $ErrorActionPreference = "Continue"
        $lines = @(& $FilePath @Arguments 2>&1 | ForEach-Object { $_.ToString() })
        $nativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    [pscustomobject]@{
        ExitCode = $nativeExitCode
        Output = ($lines -join "`n").Trim()
    }
}

function Test-NetworkFailure([string]$Detail) {
    return $Detail -match '(?i)network|connection|connect|timeout|timed out|could not resolve|TLS|dial tcp|unexpected EOF|HTTP 5\d\d'
}

function Invoke-Gh([string[]]$Arguments, [string]$FailureMessage) {
    $result = Invoke-NativeCapture $script:GhPath $Arguments
    if ($result.ExitCode -ne 0) {
        $detail = $result.Output
        if (Test-NetworkFailure $detail) {
            throw "GitHubとの通信に失敗しました。`n$detail"
        }
        throw "$FailureMessage`n$detail"
    }
    return $result.Output
}

function Get-ProjectVersion {
    $pyproject = Join-Path $root "pyproject.toml"
    if (-not (Test-Path -LiteralPath $pyproject -PathType Leaf)) {
        throw "VERSION sourceのpyproject.tomlが見つかりません。"
    }
    $match = Select-String -LiteralPath $pyproject -Pattern $ProjectVersionPattern |
        Select-Object -First 1
    if (-not $match) {
        throw "pyproject.tomlからversionを取得できません。"
    }
    return $match.Matches[0].Groups[1].Value
}

function Normalize-GitHubRepository([string]$RemoteUrl) {
    $value = $RemoteUrl.Trim()
    $value = $value -replace '^https://github\.com/', ''
    $value = $value -replace '^git@github\.com:', ''
    $value = $value -replace '^ssh://git@github\.com/', ''
    $value = $value.TrimEnd('/') -replace '\.git$', ''
    if ($value -match '^[^/\s]+/[^/\s]+$') {
        return $value
    }
    return $null
}

function Get-RemoteRepositories {
    $remoteResult = Invoke-NativeCapture "git" @(
        "-c", "safe.directory=$root", "remote"
    )
    if ($remoteResult.ExitCode -ne 0) {
        throw "git remoteを確認できません。`n$($remoteResult.Output)"
    }
    $repositories = [System.Collections.Generic.List[string]]::new()
    foreach ($remote in @($remoteResult.Output -split "`r?`n" | Where-Object { $_ })) {
        $urlResult = Invoke-NativeCapture "git" @(
            "-c", "safe.directory=$root", "remote", "get-url", $remote
        )
        if ($urlResult.ExitCode -ne 0) { continue }
        $repository = Normalize-GitHubRepository $urlResult.Output
        if ($repository -and -not $repositories.Contains($repository)) {
            $repositories.Add($repository)
        }
    }
    return @($repositories)
}

function Get-ReleaseRepository {
    if (-not (Test-Path -LiteralPath $releaseConfigPath -PathType Leaf)) {
        throw "公開repository設定がありません: release-config.json"
    }
    try {
        $config = Get-Content -LiteralPath $releaseConfigPath -Raw | ConvertFrom-Json
    } catch {
        throw "release-config.jsonを読み取れません: $($_.Exception.Message)"
    }
    $repository = [string]$config.release_repository
    if ($repository -notmatch '^[^/\s]+/[^/\s]+$') {
        throw "release_repositoryはowner/repository形式で指定してください。"
    }
    $remoteRepositories = @(Get-RemoteRepositories)
    if ($remoteRepositories -notcontains $repository) {
        throw "公開repositoryが現在のgit remoteと一致しません: $repository"
    }
    return $repository
}

function Initialize-GitHubCli {
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($gh) {
        $script:GhPath = $gh.Source
    } else {
        $ghCandidates = @()
        if ($env:ProgramFiles) {
            $ghCandidates += (Join-Path $env:ProgramFiles "GitHub CLI\gh.exe")
        }
        if ($env:LOCALAPPDATA) {
            $ghCandidates += (Join-Path $env:LOCALAPPDATA "Programs\GitHub CLI\gh.exe")
            $ghCandidates += (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\gh.exe")
        }
        $script:GhPath = $ghCandidates |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
            Select-Object -First 1
    }
    if (-not $script:GhPath) {
        throw "GitHub CLI (gh) が見つかりません。公式手順でghをインストールし、gh auth loginを実行してください。"
    }
    $versionResult = Invoke-NativeCapture $script:GhPath @("--version")
    if ($versionResult.ExitCode -ne 0) {
        throw "GitHub CLI (gh) を実行できません。"
    }
    $authResult = Invoke-NativeCapture $script:GhPath @("auth", "status", "--hostname", "github.com")
    if ($authResult.ExitCode -ne 0) {
        if (Test-NetworkFailure $authResult.Output) {
            throw "GitHubとの通信に失敗したため、認証状態を確認できません。`n$($authResult.Output)"
        }
        throw "GitHub CLIへログインしてください。`ngh auth login"
    }
}

function Assert-GitHubReady([string]$Repository) {
    $repoJson = Invoke-Gh @(
        "repo", "view", $Repository, "--json", "nameWithOwner,isPrivate,url"
    ) "公開repositoryを確認できません。"
    try {
        $repoInfo = $repoJson | ConvertFrom-Json
    } catch {
        throw "GitHub repository情報を解析できません。"
    }
    if ($repoInfo.nameWithOwner -ne $Repository) {
        throw "GitHubが返したrepositoryが設定と一致しません。"
    }
    if ([bool]$repoInfo.isPrivate) {
        throw "Release対象がPrivate repositoryです。処理を中断します: $Repository"
    }
    return [string]$repoInfo.url
}

function Invoke-PortableBuild([string]$SelectedVersion) {
    $buildScript = Join-Path $root "build-portable.ps1"
    if (-not (Test-Path -LiteralPath $buildScript -PathType Leaf)) {
        throw "build-portable.ps1が見つかりません。"
    }
    $hostExecutable = (Get-Process -Id $PID).Path
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $buildScript)
    if ($SelectedVersion) {
        $arguments += @("-Version", $SelectedVersion)
    }
    Write-Host "Portable ZIPをビルドします。" -ForegroundColor Cyan
    $buildResult = Invoke-NativeCapture $hostExecutable $arguments
    if ($buildResult.Output) {
        Write-Host $buildResult.Output
    }
    if ($buildResult.ExitCode -ne 0) {
        throw "build-portable.ps1が失敗したため、Release作成を中断します。`n$($buildResult.Output)"
    }
}

function Get-PortableArtifact([string]$SelectedVersion) {
    if (-not (Test-Path -LiteralPath $releaseDirectory -PathType Container)) {
        throw "releaseフォルダがありません。先にbuild-portable.ps1を実行してください。"
    }
    $pattern = "AI-Office-Viewer-Portable-$SelectedVersion-*.zip"
    $zip = Get-ChildItem -LiteralPath $releaseDirectory -Filter $pattern -File |
        Where-Object { $_.Name -ne "AI-Office-Viewer-Portable-Latest.zip" } |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if (-not $zip) {
        throw "Version $SelectedVersion の正式Portable ZIPが見つかりません。"
    }
    $shaPath = "$($zip.FullName).sha256"
    if (-not (Test-Path -LiteralPath $shaPath -PathType Leaf)) {
        throw "対応するSHA256ファイルが見つかりません: $shaPath"
    }
    [pscustomobject]@{
        Zip = $zip
        ShaPath = $shaPath
    }
}

function Test-Sha256([IO.FileInfo]$Zip, [string]$ShaPath) {
    $line = (Get-Content -LiteralPath $ShaPath -Raw).Trim()
    if ($line -notmatch $Sha256FilePattern) {
        throw "SHA256ファイルの形式が不正です: $ShaPath"
    }
    $expectedHash = $Matches[1].ToLowerInvariant()
    $expectedName = $Matches[2].Trim()
    if ($expectedName -ne $Zip.Name) {
        throw "SHA256ファイルの対象名がZIPと一致しません。"
    }
    $actualHash = (Get-FileHash -LiteralPath $Zip.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "ZIPのSHA256が一致しません。Releaseを中断します。"
    }
    return $actualHash
}

function Read-ZipEntryText($Entry) {
    $reader = [IO.StreamReader]::new($Entry.Open())
    try {
        return $reader.ReadToEnd()
    } finally {
        $reader.Dispose()
    }
}

function Test-PortableZip([IO.FileInfo]$Zip, [string]$SelectedVersion) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($Zip.FullName)
    try {
        $required = @(
            "AI-Office-Viewer/AI-Office-Viewer-Manager.exe",
            "AI-Office-Viewer/VERSION.txt",
            "AI-Office-Viewer/PACKAGE_MANIFEST.txt",
            "AI-Office-Viewer/portable.flag"
        )
        $names = @($archive.Entries | ForEach-Object { $_.FullName })
        foreach ($requiredName in $required) {
            if ($names -notcontains $requiredName) {
                throw "正式Portable ZIPではありません。必要ファイルがありません: $requiredName"
            }
        }
        $blocked = @($archive.Entries | Where-Object {
            if ([string]::IsNullOrEmpty($_.Name)) { return $false }
            foreach ($pattern in $BlockedZipEntryPatterns) {
                if ($_.FullName -match $pattern) { return $true }
            }
            return $false
        })
        if ($blocked.Count -gt 0) {
            throw "ZIPに公開禁止ファイル候補があります: $($blocked[0].FullName)"
        }

        foreach ($entry in $archive.Entries) {
            if ([string]::IsNullOrEmpty($entry.Name)) { continue }
            if ($entry.FullName -match '(?i)^AI-Office-Viewer/runtime/frontend/_next/') { continue }
            if ($entry.Length -gt 5MB) { continue }
            $extension = [IO.Path]::GetExtension($entry.Name).ToLowerInvariant()
            if ($SensitiveTextExtensions -notcontains $extension) { continue }
            $content = Read-ZipEntryText $entry
            foreach ($pattern in $SensitiveContentPatterns) {
                if ($content -match $pattern) {
                    throw "ZIP内に機密情報またはローカル情報の候補があります: $($entry.FullName)"
                }
            }
        }

        $manifestEntry = $archive.GetEntry("AI-Office-Viewer/PACKAGE_MANIFEST.txt")
        $manifestText = Read-ZipEntryText $manifestEntry
        if ($manifestText -notmatch $ManifestHeaderPattern -or
            $manifestText -notmatch $ManifestEntryPattern) {
            throw "ZIP内PACKAGE_MANIFEST.txtの形式が不正です。"
        }

        $versionEntry = $archive.GetEntry("AI-Office-Viewer/VERSION.txt")
        $versionText = Read-ZipEntryText $versionEntry
        if ($versionText -notmatch "(?m)^Version:\s+$([regex]::Escape($SelectedVersion))\s*$") {
            throw "ZIP内VERSION.txtとRelease versionが一致しません。"
        }
    } finally {
        $archive.Dispose()
    }
}

function Get-ReleaseState([string]$Repository, [string]$Tag) {
    $result = Invoke-NativeCapture $script:GhPath @(
        "release", "view", $Tag, "--repo", $Repository,
        "--json", "url,isDraft,isPrerelease,tagName"
    )
    if ($result.ExitCode -eq 0) {
        return ($result.Output | ConvertFrom-Json)
    }
    if ($result.Output -match '(?i)not found|release not found|HTTP 404') {
        return $null
    }
    if (Test-NetworkFailure $result.Output) {
        throw "GitHubとの通信に失敗したため、既存Releaseを確認できません。`n$($result.Output)"
    }
    throw "既存Releaseを確認できません。`n$($result.Output)"
}

function Test-TagExists([string]$Repository, [string]$Tag) {
    $result = Invoke-NativeCapture $script:GhPath @(
        "api", "repos/$Repository/git/ref/tags/$Tag", "--silent"
    )
    if ($result.ExitCode -eq 0) { return $true }
    if ($result.Output -match '(?i)not found|HTTP 404') { return $false }
    if (Test-NetworkFailure $result.Output) {
        throw "GitHubとの通信に失敗したため、既存tagを確認できません。`n$($result.Output)"
    }
    throw "既存tagを確認できません。`n$($result.Output)"
}

function Get-ReleaseUrl([string]$Repository, [string]$Tag) {
    $json = Invoke-Gh @(
        "release", "view", $Tag, "--repo", $Repository, "--json", "url"
    ) "Release URLを取得できません。"
    return [string](($json | ConvertFrom-Json).url)
}

function Confirm-Release {
    if ($Yes) { return $true }
    $answer = Read-Host "この内容でGitHub Releaseを作成または更新しますか？ [Y/N]"
    return $answer -match '^(?i)y(?:es)?$'
}

function Main {
    if ($Help) {
        Show-Usage
        return
    }

    if ($ReplaceAssets) { $UpdateAssets = $true }
    if ($UpdateAssets -and ($Publish -or $PreRelease)) {
        throw "-UpdateAssets/-ReplaceAssetsと-Publish/-PreReleaseは同時指定できません。"
    }

    Initialize-GitHubCli

    $selectedVersion = if ($Version) { $Version.TrimStart("v") } else { Get-ProjectVersion }
    if ($selectedVersion -notmatch '^[0-9]+(?:\.[0-9]+){2}(?:[-+][0-9A-Za-z.-]+)?$') {
        throw "Version形式が不正です: $selectedVersion"
    }
    $repository = Get-ReleaseRepository
    $repositoryUrl = Assert-GitHubReady $repository
    if ($Build) {
        Invoke-PortableBuild $selectedVersion
    }
    $artifact = Get-PortableArtifact $selectedVersion
    $sha256 = Test-Sha256 $artifact.Zip $artifact.ShaPath
    Test-PortableZip $artifact.Zip $selectedVersion

    $tag = "v$selectedVersion"
    $title = "AI Office Viewer $tag"
    $assetZipName = "AI-Office-Viewer-Portable-$tag.zip"
    $assetShaName = "$assetZipName.sha256"
    $existingRelease = Get-ReleaseState $repository $tag
    if ($existingRelease -and -not $UpdateAssets) {
        throw "Release $tag はすでに存在します。通常実行では変更しません。"
    }
    if (-not $existingRelease -and $UpdateAssets) {
        throw "更新対象のRelease $tag が存在しません。"
    }
    if (-not $existingRelease -and (Test-TagExists $repository $tag)) {
        throw "tag $tag はすでに存在します。Releaseは作成しません。"
    }

    $dirtyResult = Invoke-NativeCapture "git" @(
        "-c", "safe.directory=$root", "status", "--porcelain"
    )
    if ($dirtyResult.ExitCode -ne 0) {
        throw "git statusを確認できません。"
    }
    $isDirty = -not [string]::IsNullOrWhiteSpace($dirtyResult.Output)
    if ($isDirty) {
        if ($Publish) {
            Write-Host "警告: 未コミット変更があります。公開Release作成前に内容を確認してください。" -ForegroundColor Red
        } else {
            Write-Warning "未コミット変更があります。Draft Releaseとして確認してください。"
        }
    }

    $notesSource = $null
    if ($NotesFile) {
        $notesSource = if ([IO.Path]::IsPathRooted($NotesFile)) {
            [IO.Path]::GetFullPath($NotesFile)
        } else {
            [IO.Path]::GetFullPath((Join-Path $root $NotesFile))
        }
        if (-not (Test-Path -LiteralPath $notesSource -PathType Leaf)) {
            throw "Release notesファイルが見つかりません: $notesSource"
        }
    } elseif (Test-Path -LiteralPath $defaultNotesPath -PathType Leaf) {
        $notesSource = $defaultNotesPath
    }

    Write-Host ""
    Write-Host "GitHub Release確認" -ForegroundColor Cyan
    Write-Host "Repository:  $repository ($repositoryUrl)"
    Write-Host "Version:     $selectedVersion"
    Write-Host "Tag:         $tag"
    Write-Host "ZIP:         $($artifact.Zip.FullName)"
    Write-Host "ZIP size:    $($artifact.Zip.Length) bytes"
    Write-Host "SHA256:      $sha256"
    Write-Host "Asset ZIP:   $assetZipName"
    Write-Host "Asset SHA:   $assetShaName"
    Write-Host "Draft:       $(-not $Publish)"
    Write-Host "PreRelease:  $([bool]$PreRelease)"
    Write-Host "Mode:        $(if ($UpdateAssets) { 'Asset update' } else { 'New release' })"
    if (-not (Confirm-Release)) {
        Write-Host "Release操作をキャンセルしました。"
        return
    }

    $tempDirectory = Join-Path $releaseDirectory (".publish-temp-" + [guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Path $tempDirectory -Force | Out-Null
        $assetZipPath = Join-Path $tempDirectory $assetZipName
        $assetShaPath = Join-Path $tempDirectory $assetShaName
        Copy-Item -LiteralPath $artifact.Zip.FullName -Destination $assetZipPath -Force
        "$sha256  $assetZipName" | Set-Content -LiteralPath $assetShaPath -Encoding ASCII

        $notesPath = $notesSource
        if (-not $notesPath) {
            $notesPath = Join-Path $tempDirectory "release-notes.md"
            @"
# AI Office Viewer $tag

- Windows向けPortable版
- ZIPを展開してManagerから起動
"@ | Set-Content -LiteralPath $notesPath -Encoding UTF8
        }

        if (-not $existingRelease) {
            $createArgs = @(
                "release", "create", $tag, "--repo", $repository,
                "--title", $title, "--notes-file", $notesPath, "--draft"
            )
            if ($PreRelease) { $createArgs += "--prerelease" }
            Invoke-Gh $createArgs "Release作成に失敗しました。" | Out-Null
            Write-PublishLog "repository=$repository tag=$tag result=draft-created"
        }

        $uploadArgs = @(
            "release", "upload", $tag, $assetZipPath, $assetShaPath,
            "--repo", $repository
        )
        if ($ReplaceAssets) { $uploadArgs += "--clobber" }
        $uploadResult = Invoke-NativeCapture $script:GhPath $uploadArgs
        if ($uploadResult.ExitCode -ne 0) {
            $failedUrl = try { Get-ReleaseUrl $repository $tag } catch { "$repositoryUrl/releases" }
            Write-PublishLog "repository=$repository tag=$tag assets=$assetZipName,$assetShaName result=upload-failed"
            if (Test-NetworkFailure $uploadResult.Output) {
                throw "GitHubとの通信に失敗したため、Release assetをアップロードできませんでした。Releaseは削除していません。`n$failedUrl"
            }
            throw "Release assetのアップロードに失敗しました。Releaseは削除していません。`n$failedUrl`n$($uploadResult.Output)"
        }

        if ($Publish -and -not $existingRelease) {
            Invoke-Gh @(
                "release", "edit", $tag, "--repo", $repository, "--draft=false"
            ) "Draft Releaseの公開に失敗しました。" | Out-Null
        }

        $releaseUrl = Get-ReleaseUrl $repository $tag
        Write-PublishLog "repository=$repository tag=$tag assets=$assetZipName,$assetShaName result=success url=$releaseUrl"
        Write-Host ""
        Write-Host "Release assetのアップロードが完了しました。" -ForegroundColor Green
        Write-Host $releaseUrl
        if ($Publish) {
            Write-Host "GitHub Releaseを公開しました。" -ForegroundColor Green
        } elseif ($existingRelease) {
            Write-Host "既存Releaseのassetを更新しました。" -ForegroundColor Green
        } else {
            Write-Host "GitHub上で内容を確認して公開してください。" -ForegroundColor Yellow
        }
    } finally {
        if (Test-Path -LiteralPath $tempDirectory) {
            Remove-Item -LiteralPath $tempDirectory -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
    Main
    exit 0
} catch {
    $message = $_.Exception.Message
    Write-Host "エラー: $message" -ForegroundColor Red
    try { Write-PublishLog "result=failed" } catch {}
    exit 1
}
