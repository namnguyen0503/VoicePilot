<#
.SYNOPSIS
    Builds the portable VoicePilot folder and ZIP for Windows x64.

.DESCRIPTION
    Creates .venv with Python 3.12, installs requirements.lock and the dev and build extras,
    runs the unit tests, downloads the models, renders the icon, runs PyInstaller, copies the
    models and documents next to VoicePilot.exe, writes SOURCE.txt, runs the --selftest smoke
    check and creates dist\VoicePilot-<version>-win64.zip.

    Windows PowerShell 5.1 compatible. Keep this file ASCII-only: 5.1 reads scripts without a
    BOM as ANSI, which would corrupt non-ASCII text. Paths are passed as arguments, never
    concatenated into command strings, so the repository path may contain spaces and
    Vietnamese characters.

.PARAMETER SkipTests
    Skip pytest. Do not use for a release build.

.PARAMETER SkipModels
    Skip tools\download_models.py. The models must already be in models\.

.PARAMETER SkipSmoke
    Skip the VoicePilot.exe --selftest check.

.PARAMETER RepoUrl
    Repository URL written to SOURCE.txt. Replace the placeholder before publishing.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\build.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipModels,
    [switch]$SkipSmoke,
    [string]$RepoUrl = 'REPO_URL_PLACEHOLDER'
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$Root = [System.IO.Path]::GetFullPath((Join-Path -Path $PSScriptRoot -ChildPath '..'))
$VenvDir = Join-Path -Path $Root -ChildPath '.venv'
$VenvPy = Join-Path -Path $VenvDir -ChildPath 'Scripts\python.exe'
$Lock = Join-Path -Path $Root -ChildPath 'requirements.lock'
$Spec = Join-Path -Path $Root -ChildPath 'packaging\voicepilot.spec'
$BuildDir = Join-Path -Path $Root -ChildPath 'build'
$Icon = Join-Path -Path $BuildDir -ChildPath 'icon.ico'
$DistDir = Join-Path -Path $Root -ChildPath 'dist'
$AppDir = Join-Path -Path $DistDir -ChildPath 'VoicePilot'
$ModelsSrc = Join-Path -Path $Root -ChildPath 'models'
$env:QT_QPA_PLATFORM = 'offscreen'

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Text)
    Write-Host ''
    Write-Host ('== ' + $Text) -ForegroundColor Cyan
}

function Invoke-Native {
    # Runs a native command with an argument array and stops on a non-zero exit code.
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @()
    )
    Write-Host ('> ' + $Exe + ' ' + ($Arguments -join ' '))
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ('Command failed with exit code ' + $LASTEXITCODE + ': ' + $Exe)
    }
}

function Get-AppVersion {
    $text = [System.IO.File]::ReadAllText((Join-Path -Path $Root -ChildPath 'pyproject.toml'), $Utf8NoBom)
    $match = [regex]::Match($text, '(?m)^version\s*=\s*"([^"]+)"')
    if (-not $match.Success) {
        throw 'Cannot read the version from pyproject.toml'
    }
    return $match.Groups[1].Value
}

function Get-GitState {
    # Returns the commit hash and whether the working tree has uncommitted changes.
    $result = @{ Commit = 'unknown'; Dirty = 'unknown' }
    if (-not (Get-Command -Name 'git' -ErrorAction SilentlyContinue)) {
        return $result
    }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $commit = (& git -C $Root rev-parse HEAD 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and $commit) {
            $result.Commit = [string]$commit
            $status = (& git -C $Root status --porcelain 2>$null)
            if ($LASTEXITCODE -eq 0) {
                if ($status) { $result.Dirty = 'yes' } else { $result.Dirty = 'no' }
            }
        }
    }
    finally {
        $ErrorActionPreference = $previous
    }
    return $result
}

Push-Location -LiteralPath $Root
try {
    $Version = Get-AppVersion
    Write-Host ('VoicePilot ' + $Version + ' build in ' + $Root)

    Write-Step 'Python environment (.venv, Python 3.12)'
    if (-not (Test-Path -LiteralPath $VenvPy)) {
        Invoke-Native -Exe 'py' -Arguments @('-3.12', '-m', 'venv', $VenvDir)
    }
    Invoke-Native -Exe $VenvPy -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip')
    Invoke-Native -Exe $VenvPy -Arguments @('-m', 'pip', 'install', '-r', $Lock)
    Invoke-Native -Exe $VenvPy -Arguments @('-m', 'pip', 'install', '-e', ($Root + '[dev,build]'))

    if (-not $SkipTests) {
        Write-Step 'Unit tests'
        Invoke-Native -Exe $VenvPy -Arguments @('-m', 'pytest', '-q')
    }

    if (-not $SkipModels) {
        Write-Step 'Models (tools\download_models.py)'
        Invoke-Native -Exe $VenvPy -Arguments @((Join-Path -Path $Root -ChildPath 'tools\download_models.py'), '--models-dir', $ModelsSrc)
    }
    if (-not (Test-Path -LiteralPath $ModelsSrc)) {
        throw 'models\ does not exist; run without -SkipModels or copy the models first'
    }
    $partial = @(Get-ChildItem -LiteralPath $ModelsSrc -Recurse -Filter '*.part' -File)
    if ($partial.Count -gt 0) {
        throw ('Incomplete download(s) in models\: ' + $partial[0].FullName)
    }

    Write-Step 'Icon (SVG to ICO)'
    New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
    $svg = Join-Path -Path $Root -ChildPath 'src\voicepilot\resources\icon.svg'
    Invoke-Native -Exe $VenvPy -Arguments @((Join-Path -Path $Root -ChildPath 'tools\make_icon.py'), $svg, $Icon)

    Write-Step 'PyInstaller (onedir, windowed)'
    if (Test-Path -LiteralPath $AppDir) {
        Remove-Item -LiteralPath $AppDir -Recurse -Force
    }
    $pyiWork = Join-Path -Path $BuildDir -ChildPath 'pyinstaller'
    Invoke-Native -Exe $VenvPy -Arguments @('-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', $DistDir, '--workpath', $pyiWork, $Spec)
    if (-not (Test-Path -LiteralPath (Join-Path -Path $AppDir -ChildPath 'VoicePilot.exe'))) {
        throw 'PyInstaller did not produce dist\VoicePilot\VoicePilot.exe'
    }

    Write-Step 'Copy models and documents'
    Copy-Item -LiteralPath $ModelsSrc -Destination (Join-Path -Path $AppDir -ChildPath 'models') -Recurse -Force
    $appDocs = Join-Path -Path $AppDir -ChildPath 'docs'
    New-Item -ItemType Directory -Force -Path $appDocs | Out-Null
    foreach ($doc in @('docs\COMMANDS.vi.md', 'docs\DEMO.vi.md')) {
        Copy-Item -LiteralPath (Join-Path -Path $Root -ChildPath $doc) -Destination $appDocs -Force
    }
    foreach ($file in @('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md')) {
        Copy-Item -LiteralPath (Join-Path -Path $Root -ChildPath $file) -Destination $AppDir -Force
    }

    Write-Step 'SOURCE.txt'
    $git = Get-GitState
    $built = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $sourceLines = @(
        ('VoicePilot ' + $Version + ' (Windows x64, portable)'),
        ('Built (UTC): ' + $built),
        ('Source repository: ' + $RepoUrl),
        ('Source commit: ' + $git.Commit),
        ('Uncommitted changes at build time: ' + $git.Dirty),
        'License: GPL-3.0-or-later. See LICENSE. The corresponding source is the repository and commit above.',
        'Third-party licenses and model sources: THIRD_PARTY_NOTICES.md'
    )
    [System.IO.File]::WriteAllText((Join-Path -Path $AppDir -ChildPath 'SOURCE.txt'), (($sourceLines -join "`r`n") + "`r`n"), $Utf8NoBom)

    if (-not $SkipSmoke) {
        Write-Step 'Smoke check (VoicePilot.exe --selftest)'
        $exe = Join-Path -Path $AppDir -ChildPath 'VoicePilot.exe'
        # A fresh config folder keeps the check away from the user's settings and logs. Without
        # VOICEPILOT_MODELS_DIR the app looks for models\ next to VoicePilot.exe, as it does when installed.
        $smokeHome = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath ('voicepilot-selftest-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $smokeHome | Out-Null
        $savedConfigDir = $env:VOICEPILOT_CONFIG_DIR
        $savedModelsDir = $env:VOICEPILOT_MODELS_DIR
        try {
            $env:VOICEPILOT_CONFIG_DIR = $smokeHome
            Remove-Item -LiteralPath 'Env:VOICEPILOT_MODELS_DIR' -ErrorAction SilentlyContinue
            # The app is a windowed (GUI) executable, so use Start-Process to get the exit code.
            $proc = Start-Process -FilePath $exe -ArgumentList '--selftest' -Wait -PassThru -WindowStyle Hidden
            $reportPath = Join-Path -Path $smokeHome -ChildPath 'logs\selftest.txt'
            if (-not (Test-Path -LiteralPath $reportPath)) {
                throw ('Smoke check: no selftest report at ' + $reportPath + ' (exit code ' + $proc.ExitCode + ')')
            }
            $report = [System.IO.File]::ReadAllText($reportPath, $Utf8NoBom)
            Write-Host $report
            if ($proc.ExitCode -ne 0) {
                throw ('Smoke check failed with exit code ' + $proc.ExitCode + ':' + "`r`n" + $report)
            }
            Write-Host 'selftest passed'
        }
        finally {
            $env:VOICEPILOT_CONFIG_DIR = $savedConfigDir
            $env:VOICEPILOT_MODELS_DIR = $savedModelsDir
            Remove-Item -LiteralPath $smokeHome -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Step 'ZIP'
    $zipPath = Join-Path -Path $DistDir -ChildPath ('VoicePilot-' + $Version + '-win64.zip')
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -LiteralPath $AppDir -DestinationPath $zipPath -CompressionLevel Optimal
    $sizeMb = [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 1)
    Write-Host ('Done: ' + $zipPath + ' (' + $sizeMb + ' MB)') -ForegroundColor Green
}
finally {
    Pop-Location
}
