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

    [string]$ImageName = 'lineage16-urd',
    [string]$SrcVolume = 'lineage-src',
    [string]$CcacheVolume = 'lineage-ccache',
    [string]$MemLimit = '7g',
    [int]$Jobs = 4
)

$ErrorActionPreference = 'Stop'

function Write-Step { param([string]$m) Write-Host "`n[*] $m" -ForegroundColor Cyan }
function Write-Ok   { param([string]$m) Write-Host "[+] $m" -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host "[!] $m" -ForegroundColor Yellow }

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
