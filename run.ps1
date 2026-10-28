# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
#
# LineageOS 16.0 build driver for ZTE Z981 (urd) - HOST side
#
# Builds the Docker image, then runs the build inside a memory-limited container.
#
# Usage:
#   .\run.ps1 build-image     # build the Docker image (once)
#   .\run.ps1 init            # repo init + local manifest
#   .\run.ps1 sync            # repo sync (~50 GB download)
#   .\run.ps1 build           # compile
#   .\run.ps1 all             # init + sync + build
#   .\run.ps1 shell           # interactive shell in the container
#   .\run.ps1 status          # show volume sizes / state
#   .\run.ps1 clean           # DESTROY the source + ccache volumes
#
# Settings:
#   Copy settings.conf.example to settings.conf and edit it. It is read
#   automatically. Precedence: command-line parameter > environment variable >
#   settings.conf > built-in default.
#
# Memory: the container is capped at 7 GB (host has 15.8 GB, Docker sees 8.2 GB).
# CPU: unlimited (all 12 threads available).
#
# ---------------------------------------------------------------------------
# WHY NAMED VOLUMES (not a Windows bind mount)?
#
# The source tree MUST live on a Linux filesystem. A Windows bind mount
# (-v C:\...:/src) is exposed to the container as a 9p/drvfs mount, which
# does NOT support directory renames. The `repo` tool does
#   os.rename('/src/.repo/repo.tmp', '/src/.repo/repo')
# during `repo init`, which fails on 9p with:
#   PermissionError: [Errno 13] Permission denied
# Running as root does NOT help - it is a filesystem limitation, not a
# permission problem. Docker named volumes are ext4 inside the WSL2 VM and
# support renames natively, so `repo init` works.
#
# Trade-off: the source tree is not directly browsable from Windows. Use
#   .\run.ps1 shell
# to get a shell, or `docker run --rm -v lineage-src:/src ...` to inspect it.
# ---------------------------------------------------------------------------

param(
    [Parameter(Position = 0)]
    [ValidateSet('build-image', 'init', 'sync', 'build', 'all', 'shell', 'status', 'clean')]
    [string]$Action = 'shell',

    # Settings file. Defaults to settings.conf next to this script.
    [string]$Config = '',

    [string]$ImageName = '',
    [string]$SrcVolume = '',
    [string]$CcacheVolume = '',
    [string]$MemLimit = '',
    [string]$Jobs = ''
)

$ErrorActionPreference = 'Stop'

function Write-Step { param([string]$m) Write-Host "`n[*] $m" -ForegroundColor Cyan }
function Write-Ok   { param([string]$m) Write-Host "[+] $m" -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host "[!] $m" -ForegroundColor Yellow }

# --- settings file ----------------------------------------------------------
# A `settings.conf` next to this script is read automatically if present.
# Format is KEY=value (see settings.conf.example). Values from the file are
# only defaults: a command-line parameter or an environment variable overrides
# them.
#
# Precedence (highest wins):
#   1. command-line parameter (-SrcVolume, -MemLimit, ...)
#   2. environment variable (SRC_DIR, OUT_DIR, ...)
#   3. settings.conf
#   4. built-in default
if (-not $Config) { $Config = Join-Path $PSScriptRoot 'settings.conf' }

$fileSettings = @{}
if (Test-Path $Config) {
    foreach ($line in Get-Content $Config) {
        $l = $line -replace '#.*$', ''
        $l = $l.Trim()
        if (-not $l) { continue }
        if ($l -notmatch '=') { continue }
        $k, $v = $l -split '=', 2
        $k = $k.Trim()
        $v = $v.Trim().Trim('"').Trim("'")
        $fileSettings[$k] = $v
    }
}

# Resolve a setting: explicit param > env var > file > default.
function Resolve-Setting {
    param([string]$Param, [string]$EnvName, [string]$Default)
    if ($Param) { return $Param }
    $envVal = [Environment]::GetEnvironmentVariable($EnvName)
    if ($envVal) { return $envVal }
    if ($fileSettings.ContainsKey($EnvName)) { return $fileSettings[$EnvName] }
    return $Default
}

$ImageName    = Resolve-Setting $ImageName    'IMAGE'      'lineage16-urd'
$SrcVolume    = Resolve-Setting $SrcVolume    'SRC_DIR'    'lineage-src'
$CcacheVolume = Resolve-Setting $CcacheVolume 'CCACHE_DIR' 'lineage-ccache'
$MemLimit     = Resolve-Setting $MemLimit     'MEM'        '7g'
$Jobs         = Resolve-Setting $Jobs         'JOBS'       '4'

# --- helpers ---------------------------------------------------------------

function Test-Volume {
    param([string]$Name)
    $v = docker volume ls -q --filter "name=^${Name}$" 2>$null
    return [bool]$v
}

function New-VolumeIfMissing {
    param([string]$Name)
    if (-not (Test-Volume $Name)) {
        docker volume create $Name | Out-Null
        Write-Host "    (created volume $Name)"
    }
}

# build.sh is baked into the image at /opt/build.sh, so the source volume
# stays clean.
$BuildShInImage = '/opt/build.sh'

switch ($Action) {

    'build-image' {
        Write-Step "Building Docker image '$ImageName'"
        docker build -t $ImageName "$PSScriptRoot"
        Write-Ok "Image built."
    }

    'status' {
        Write-Step "Build environment status"
        Write-Host "Image '$ImageName':"
        docker images $ImageName --format "    {{.Repository}}:{{.Tag}}  {{.Size}}  ({{.CreatedSince}})"
        Write-Host "`nVolumes:"
        foreach ($v in @($SrcVolume, $CcacheVolume)) {
            if (Test-Volume $v) {
                $size = docker run --rm -v "${v}:/mnt" alpine sh -c "du -sh /mnt 2>/dev/null | cut -f1" 2>$null
                Write-Host "    $v : $size"
            } else {
                Write-Host "    $v : (does not exist)"
            }
        }
        Write-Host "`nContainers:"
        docker ps -a --filter "ancestor=$ImageName" --format "    {{.ID}}  {{.Status}}  {{.Names}}"
    }

    'clean' {
        Write-Warn "This will DELETE the source tree and ccache volumes."
        $ans = Read-Host "Type 'yes' to confirm"
        if ($ans -ne 'yes') { Write-Host "Aborted."; exit 0 }
        foreach ($v in @($SrcVolume, $CcacheVolume)) {
            if (Test-Volume $v) {
                docker volume rm $v | Out-Null
                Write-Ok "Removed volume $v"
            }
        }
    }

    default {
        # Verify the image exists
        $exists = docker images -q $ImageName 2>$null
        if (-not $exists) {
            Write-Warn "Image '$ImageName' not found. Run: .\run.ps1 build-image"
            exit 1
        }

        New-VolumeIfMissing $SrcVolume
        New-VolumeIfMissing $CcacheVolume

        Write-Step "Running '$Action' in container (mem=$MemLimit, jobs=$Jobs)"
        Write-Host "    Source volume : $SrcVolume  -> /src"
        Write-Host "    ccache volume : $CcacheVolume -> /ccache"

        # -m limits RAM; --cpus left unset = unlimited
        # -it for interactive; for 'all'/'sync'/'build' we still want live output
        # NOTE: bash, not sh. Android's envsetup.sh is bash-only and fails
        # under dash with: Syntax error: "(" unexpected
        $dockerArgs = @(
            'run', '--rm', '-it',
            '-m', $MemLimit,
            '-v', "${SrcVolume}:/src",
            '-v', "${CcacheVolume}:/ccache",
            '-e', "JOBS=$Jobs",
            '-w', '/src',
            $ImageName,
            'bash', $BuildShInImage, $Action
        )

        & docker @dockerArgs
    }
}
