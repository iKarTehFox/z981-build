#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
#
# LineageOS 16.0 build driver for the ZTE Z981 (urd) - HOST side.
#
# Builds the Docker image, then runs the build inside a memory-limited
# container. This is the portable Linux driver; run.ps1 is the Windows port.
#
# Usage:
#   ./run.sh build-image     # build the Docker image (once)
#   ./run.sh init            # repo init + install the local manifest
#   ./run.sh sync            # repo sync (~50 GB download)
#   ./run.sh build           # apply patches + compile
#   ./run.sh all             # init + sync + build
#   ./run.sh shell           # interactive shell in the container
#   ./run.sh status          # show image + directory sizes
#   ./run.sh clean           # DESTROY the source + out + ccache directories
#
# Settings:
#   Copy settings.conf.example to settings.conf and edit it. It is read
#   automatically. Precedence: command-line flag > environment variable >
#   settings.conf > built-in default. See `./run.sh --help`.
#
# ---------------------------------------------------------------------------
# WHERE THE SOURCE TREE LIVES
#
# The source tree and ccache are HOST directories bind-mounted into the
# container. The defaults are relative to this script's parent directory:
#
#   <repo>/src      -> /src        (the Android source tree, ~50 GB)
#   <repo>/out      -> /src/out    (build output, ~40 GB)
#   <repo>/ccache   -> /ccache     (compiler cache, up to 30 GB)
#
# Override any of them with environment variables:
#
#   SRC_DIR=/data/android/src OUT_DIR=/data/android/out ./run.sh build
#
# ---------------------------------------------------------------------------
# OPTIONAL: PUT out/ ON A FASTER DISK
#
# Android builds are extremely I/O-heavy, and the bulk of that I/O is in out/
# (thousands of object files, ninja's dependency database, intermediate
# images). If you have a fast SSD/NVMe alongside a slower HDD, put the source
# tree on the big/slow disk and out/ on the fast one:
#
#   SRC_DIR=/mnt/hdd/android/src OUT_DIR=/mnt/nvme/android-out ./run.sh build
#
# This is purely an optimization - the defaults work fine on a single disk.
#
# ---------------------------------------------------------------------------
# WHY BIND MOUNTS (not named volumes)?
#
# The source tree must live on a filesystem that supports directory renames,
# because `repo init` does os.rename() on .repo/repo.tmp. A Windows bind mount
# is exposed as 9p/drvfs, which does NOT support renames, so `repo init` fails
# with "PermissionError: [Errno 13] Permission denied". On native Linux a bind
# mount is a real ext4 filesystem, so renames work and bind mounts are the
# simplest option. (On Windows, use run.ps1, which uses named volumes.)
#
# OWNERSHIP: the container runs as root by default, so files it creates in
# /src/out become root-owned on the host. Set RUN_AS_USER=1 to run as your own
# uid/gid instead (some Android build steps expect root, so this may not always
# work - fall back to the default if it fails).
# ---------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

step() { echo ""; echo "[*] $*"; }
ok()   { echo "[+] $*"; }
warn() { echo "[!] $*"; }

# --- settings file ----------------------------------------------------------
# A `settings.conf` next to this script is read automatically if present.
# Format is KEY=value (see settings.conf.example). Values from the file are
# only defaults: an environment variable or a command-line flag overrides them.
#
# Precedence (highest wins):
#   1. command-line flag   (--src, --out, --ccache, --image, --mem, --jobs)
#   2. environment variable (SRC_DIR, OUT_DIR, ...)
#   3. settings.conf
#   4. built-in default
CONFIG_FILE="$SCRIPT_DIR/settings.conf"

# Parse args first so --config can point at a different file.
ACTION=""
ARG_SRC="" ARG_OUT="" ARG_CCACHE="" ARG_IMAGE="" ARG_MEM="" ARG_JOBS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --config)  CONFIG_FILE="$2"; shift 2 ;;
        --src)     ARG_SRC="$2";     shift 2 ;;
        --out)     ARG_OUT="$2";     shift 2 ;;
        --ccache)  ARG_CCACHE="$2";  shift 2 ;;
        --image)   ARG_IMAGE="$2";   shift 2 ;;
        --mem)     ARG_MEM="$2";     shift 2 ;;
        --jobs)    ARG_JOBS="$2";    shift 2 ;;
        -h|--help)
            echo "Usage: $0 [options] [action]"
            echo ""
            echo "Actions: build-image | init | sync | build | all | shell | status | clean"
            echo ""
            echo "Options:"
            echo "  --config FILE   read settings from FILE (default: settings.conf)"
            echo "  --src DIR       source tree (overrides SRC_DIR)"
            echo "  --out DIR       build output (overrides OUT_DIR)"
            echo "  --ccache DIR    ccache dir (overrides CCACHE_DIR)"
            echo "  --image TAG     docker image tag (overrides IMAGE)"
            echo "  --mem SIZE      container memory limit (overrides MEM)"
            echo "  --jobs N        parallel jobs (overrides JOBS)"
            exit 0
            ;;
        -*) echo "Unknown option: $1 (try --help)" >&2; exit 1 ;;
        *)  ACTION="$1"; shift ;;
    esac
done

# Load the settings file. Only KEY=value lines are honoured; anything else is
# ignored, so a stray line cannot execute code.
if [ -f "$CONFIG_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        # strip comments and surrounding whitespace
        line="${line%%#*}"
        line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        [ -z "$line" ] && continue
        case "$line" in
            *=*) ;;
            *) continue ;;
        esac
        key="${line%%=*}"
        val="${line#*=}"
        # strip optional surrounding quotes
        val="${val%\"}"; val="${val#\"}"
        val="${val%\'}"; val="${val#\'}"
        # only accept known keys, and only if not already set in the env
        case "$key" in
            SRC_DIR|OUT_DIR|CCACHE_DIR|IMAGE|MEM|JOBS|RUN_AS_USER)
                if [ -z "${!key:-}" ]; then
                    export "$key=$val"
                fi
                ;;
        esac
    done < "$CONFIG_FILE"
fi

# Command-line flags beat everything.
[ -n "$ARG_SRC" ]    && SRC_DIR="$ARG_SRC"
[ -n "$ARG_OUT" ]    && OUT_DIR="$ARG_OUT"
[ -n "$ARG_CCACHE" ] && CCACHE_DIR="$ARG_CCACHE"
[ -n "$ARG_IMAGE" ]  && IMAGE="$ARG_IMAGE"
[ -n "$ARG_MEM" ]    && MEM="$ARG_MEM"
[ -n "$ARG_JOBS" ]   && JOBS="$ARG_JOBS"

IMAGE="${IMAGE:-lineage16-urd}"
MEM="${MEM:-8g}"
JOBS="${JOBS:-6}"
RUN_AS_USER="${RUN_AS_USER:-0}"

# Portable defaults: everything lives next to this repo unless overridden.
SRC_DIR="${SRC_DIR:-$REPO_DIR/src}"
OUT_DIR="${OUT_DIR:-$REPO_DIR/out}"
CCACHE_DIR="${CCACHE_DIR:-$REPO_DIR/ccache}"

# build.sh is baked into the image at /opt/build.sh, so the source tree stays clean.
BUILD_SH_IN_IMAGE=/opt/build.sh

ACTION="${ACTION:-shell}"

# --- helpers ---------------------------------------------------------------

image_exists() {
    docker image inspect "$IMAGE" >/dev/null 2>&1
}

dir_size() {
    du -sh "$1" 2>/dev/null | cut -f1 || echo "(missing)"
}

# --- actions ---------------------------------------------------------------

do_build_image() {
    step "Building Docker image '$IMAGE'"
    docker build -t "$IMAGE" "$SCRIPT_DIR"
    ok "Image built."
}

do_status() {
    step "Build environment status"
    echo "Image '$IMAGE':"
    if image_exists; then
        docker images "$IMAGE" --format "    {{.Repository}}:{{.Tag}}  {{.Size}}  ({{.CreatedSince}})"
    else
        echo "    (does not exist - run: ./run.sh build-image)"
    fi
    echo ""
    echo "Host directories:"
    echo "    $SRC_DIR    : $(dir_size "$SRC_DIR")"
    echo "    $OUT_DIR    : $(dir_size "$OUT_DIR")"
    echo "    $CCACHE_DIR : $(dir_size "$CCACHE_DIR")"
    echo ""
    echo "Containers:"
    docker ps -a --filter "ancestor=$IMAGE" --format "    {{.ID}}  {{.Status}}  {{.Names}}"
}

do_clean() {
    warn "This will DELETE the source tree, out and ccache directories:"
    warn "    $SRC_DIR"
    warn "    $OUT_DIR"
    warn "    $CCACHE_DIR"
    read -rp "Type 'yes' to confirm: " ans
    [ "$ans" = yes ] || { echo "Aborted."; exit 0; }
    rm -rf "$SRC_DIR" "$OUT_DIR" "$CCACHE_DIR"
    ok "Removed $SRC_DIR, $OUT_DIR and $CCACHE_DIR"
}

do_run() {
    local action="$1"

    if ! image_exists; then
        warn "Image '$IMAGE' not found. Run: ./run.sh build-image"
        exit 1
    fi

    if [ ! -d "$SRC_DIR" ]; then
        warn "Source directory '$SRC_DIR' does not exist."
        warn "Create it and run './run.sh init' first, or set SRC_DIR to an"
        warn "existing tree, e.g.:  SRC_DIR=/path/to/src ./run.sh $action"
        exit 1
    fi
    mkdir -p "$OUT_DIR" "$CCACHE_DIR"

    step "Running '$action' in container (mem=$MEM, jobs=$JOBS)"
    echo "    Source dir : $SRC_DIR  -> /src"
    echo "    out dir    : $OUT_DIR -> /src/out"
    echo "    ccache dir : $CCACHE_DIR -> /ccache"

    # -m limits RAM; --cpus left unset = unlimited
    # -it for interactive; for 'all'/'sync'/'build' we still want live output
    # NOTE: bash, not sh. Android's envsetup.sh is bash-only and fails
    # under dash with: Syntax error: "(" unexpected
    #
    # out/ is bind-mounted separately so it can live on a different disk than
    # the source tree (see the header).
    local args=(
        run --rm -it
        -m "$MEM"
        -v "$SRC_DIR:/src"
        -v "$OUT_DIR:/src/out"
        -v "$CCACHE_DIR:/ccache"
        -e "JOBS=$JOBS"
        -w /src
    )

    if [ "$RUN_AS_USER" = 1 ]; then
        args+=(--user "$(id -u):$(id -g)")
    fi

    args+=("$IMAGE" bash "$BUILD_SH_IN_IMAGE" "$action")

    docker "${args[@]}"
}

# --- dispatch --------------------------------------------------------------

case "$ACTION" in
    build-image) do_build_image ;;
    status)      do_status ;;
    clean)       do_clean ;;
    init|sync|build|all|shell) do_run "$ACTION" ;;
    *)
        echo "Usage: $0 {build-image|init|sync|build|all|shell|status|clean}" >&2
        exit 1
        ;;
esac
