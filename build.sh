#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
#
# LineageOS 16.0 build driver for the ZTE Z981 (urd)
#
# Runs INSIDE the container. Handles: repo init, local manifest, sync, build.
#
# Usage (from the host, via run.sh):
#   ./run.sh [init|sync|build|all|shell]
#
# Memory notes: this is tuned for ~8 GB RAM. Key knobs:
#   -j N        : parallel jobs (default: nproc, but capped for RAM)
#   -Xmx4g      : Java heap for jack (set in Dockerfile)
#   ccache      : 30 GB, avoids recompiling unchanged files
#
# IMPORTANT: this script MUST run under bash, not dash. Android's
# build/envsetup.sh uses bash-only syntax (arrays, `function`, etc.) and
# fails under dash with: Syntax error: "(" unexpected
#
# IMPORTANT: /src must be a Linux filesystem that supports directory renames
# (repo init renames .repo/repo.tmp). A host bind mount on Linux is fine; a
# Windows 9p/drvfs mount is not. See run.sh for the full explanation.

set -e

SRC=/src
# Corrected local manifest is baked into the image at /opt/urd_manifest.xml.
# It fixes the kernel revision (lineage-16.0 -> lineage-16.0-new), which the
# upstream manifest gets wrong. Fall back to the upstream URL if missing.
MANIFEST_LOCAL="/opt/urd_manifest.xml"
MANIFEST_URL="https://raw.githubusercontent.com/ZTE-ZMAX-PRO/local_manifest/lineage-16.0/urd_manifest.xml"
BRANCH="lineage-16.0"
DEVICE="urd"

# --- filesystem sanity check ------------------------------------------------
# `repo` renames directories during init. 9p/drvfs (Windows bind mounts) do
# not support that and fail with "PermissionError: [Errno 13]". Fail fast with
# a clear message instead of a confusing traceback.
check_fs() {
    fstype=$(stat -f -c %T "$SRC" 2>/dev/null || echo unknown)
    case "$fstype" in
        9p|drvfs|v9fs|cifs|smb*|nfs*)
            echo "ERROR: /src is on a '$fstype' filesystem, which does not support" >&2
            echo "       directory renames. 'repo init' will fail." >&2
            echo "       Use a Linux filesystem (host bind mount or named volume)." >&2
            exit 1
            ;;
    esac
}

# --- memory-aware job count -------------------------------------------------
# Rule of thumb: ~1.5 GB RAM per job for Android builds.
#
# NOTE: we deliberately do NOT clamp JOBS to nproc. The build is I/O-bound
# (source tree on an HDD), so running more jobs than cores overlaps the I/O
# waits and is a net win. The caller sets JOBS (run.sh defaults to 6); we only
# fall back to nproc if it is unset.
NPROC=$(nproc)
JOBS=${JOBS:-$NPROC}

log() { echo ""; echo "=== $* ==="; echo ""; }

# --- 1. repo init -----------------------------------------------------------
do_init() {
    check_fs
    log "repo init ($BRANCH)"
    cd "$SRC"
    if [ ! -d .repo ]; then
        repo init --depth=1 -u https://github.com/LineageOS/android.git -b "$BRANCH"
    else
        echo ".repo already exists, skipping init"
    fi

    log "installing local manifest"
    mkdir -p .repo/local_manifests
    if [ -f "$MANIFEST_LOCAL" ]; then
        cp "$MANIFEST_LOCAL" .repo/local_manifests/urd_manifest.xml
        echo "(using baked-in corrected manifest: $MANIFEST_LOCAL)"
    else
        curl -fsSL -o .repo/local_manifests/urd_manifest.xml "$MANIFEST_URL"
        echo "(using upstream manifest: $MANIFEST_URL)"
    fi
    cat .repo/local_manifests/urd_manifest.xml
}

# --- 2. repo sync -----------------------------------------------------------
do_sync() {
    check_fs
    log "repo sync (this takes a long time - ~50 GB)"
    cd "$SRC"
    # -j4 for network parallelism; --force-sync to recover from interruptions
    repo sync -c -j4 --force-sync --no-clone-bundle --no-tags
}

# --- 2b. apply upstream patches ---------------------------------------------
# The patch-*.py scripts are baked into the image at /opt/patches/. They are
# idempotent: each checks whether its change is already present and exits 0 if
# so. Run them before every build so the source tree always reflects the
# intended configuration.
#
# NOTE: these edit the source tree in place. `repo sync` would revert them, so
# they must be re-applied after any sync (which is why this runs on every
# build, not just once).
#
# SCOPE: only patches to repos we do NOT own live here (vendor/lineage,
# frameworks/av). The device/vendor/kernel fixes are committed directly into
# my forks (see urd_manifest.xml), so `repo sync` delivers them and no patch
# script is needed.
do_patch() {
    log "applying patches"
    local dir=/opt/patches
    if [ ! -d "$dir" ]; then
        echo "WARNING: $dir not found in the image -- no patches applied" >&2
        return 0
    fi
    local f
    for f in "$dir"/patch-*.py; do
        [ -e "$f" ] || continue
        echo "--- $(basename "$f")"
        python3 "$f" || {
            echo "ERROR: $(basename "$f") failed" >&2
            exit 1
        }
    done
}

# --- 3. build ---------------------------------------------------------------
do_build() {
    check_fs
    log "build ($DEVICE), jobs=$JOBS"
    cd "$SRC"

    do_patch

    # Android's envsetup.sh and its functions are NOT `set -e`-safe, so disable
    # strict mode for the build steps and check the final `mka` status instead.
    #
    # Concretely: `breakfast urd` runs `echo "zurd" | grep -q "-"` to test
    # whether the argument looks like a full device name. `zurd` has no hyphen,
    # so `grep -q` returns 1 -- and under `set -e` that aborts the script
    # immediately, before breakfast's own `if [ $? -eq 0 ]` check can run.
    # (The upstream code assumes `set -e` is off; it inspects `$?` by hand.)
    #
    # `lunch lineage_<device>-userdebug` selects the same config and avoids the
    # `breakfast` wrapper entirely.
    set +e

    # shellcheck disable=SC1091
    . build/envsetup.sh

    lunch "lineage_${DEVICE}-userdebug"

    # jack needs a bigger heap than default; keep it at 4g for 8 GB RAM
    export JACK_SERVER_VM_ARGUMENTS="-Dfile.encoding=UTF-8 -XX:+TieredCompilation -Xmx4g"
    prebuilts/sdk/tools/jack-admin kill-server 2>/dev/null || true
    prebuilts/sdk/tools/jack-admin start-server 2>/dev/null || true

    # ccache
    export USE_CCACHE=1
    export CCACHE_DIR=/ccache
    ccache -M 30G >/dev/null

    # Build. -j$JOBS keeps memory in check.
    mka bacon -j"$JOBS"
    local rc=$?

    # Restore strict mode for the rest of the script.
    set -e

    if [ "$rc" -ne 0 ]; then
        echo "ERROR: build failed (mka bacon exited $rc)" >&2
        exit "$rc"
    fi
}

# --- dispatch ---------------------------------------------------------------
case "${1:-all}" in
    init)  do_init ;;
    sync)  do_sync ;;
    build) do_build ;;
    all)   do_init; do_sync; do_build ;;
    shell) exec /bin/bash ;;
    *)     echo "usage: $0 [init|sync|build|all|shell]"; exit 1 ;;
esac
