#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
#
# Build the LineageOS 16.0 Docker build image for the ZTE Z981 (urd).
#
# This is a thin, self-contained wrapper around `docker build`. It exists so the
# image can be (re)built without remembering the tag or the build context, and
# so the "did the image actually change?" question is easy to answer.
#
# Usage:
#   ./build-image.sh              # build (or rebuild) the image
#   ./build-image.sh --no-cache   # force a clean rebuild (ignore layer cache)
#   ./build-image.sh --check      # show image info + verify baked-in files
#
# Environment overrides:
#   IMAGE=lineage16-urd           # image tag to build
#
# ---------------------------------------------------------------------------
# WHY THIS MATTERS
#
# build.sh, urd_manifest.xml and the patch-*.py scripts are COPY'd into the
# image at /opt/. Editing the working-tree copies does NOT affect a running
# container -- you must rebuild the image for those changes to take effect.
# This script makes that one step.
# ---------------------------------------------------------------------------

set -euo pipefail

IMAGE="${IMAGE:-lineage16-urd}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

step() { echo ""; echo "[*] $*"; }
ok()   { echo "[+] $*"; }
warn() { echo "[!] $*"; }

# --- parse args ------------------------------------------------------------

NO_CACHE=0
CHECK_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --no-cache) NO_CACHE=1 ;;
        --check)    CHECK_ONLY=1 ;;
        -h|--help)
            echo "Usage: $0 [--no-cache] [--check]"
            echo "  --no-cache   force a clean rebuild (ignore Docker layer cache)"
            echo "  --check      show image info + verify baked-in files, then exit"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            echo "Usage: $0 [--no-cache] [--check]" >&2
            exit 1
            ;;
    esac
done

# --- check mode ------------------------------------------------------------

if [ "$CHECK_ONLY" = 1 ]; then
    step "Image '$IMAGE'"
    if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
        warn "Image '$IMAGE' does not exist. Run: ./build-image.sh"
        exit 1
    fi
    docker images "$IMAGE" --format "    {{.Repository}}:{{.Tag}}  {{.Size}}  ({{.CreatedSince}})"

    step "Baked-in files (must match the working tree)"
    for f in build.sh urd_manifest.xml privapp-permissions-com.qualcomm.location.xml; do
        if docker run --rm "$IMAGE" test -f "/opt/$f" 2>/dev/null; then
            local_sum=$(sha256sum "$SCRIPT_DIR/$f" | cut -d' ' -f1)
            image_sum=$(docker run --rm "$IMAGE" sha256sum "/opt/$f" | cut -d' ' -f1)
            if [ "$local_sum" = "$image_sum" ]; then
                ok "/opt/$f matches $f"
            else
                warn "/opt/$f DIFFERS from $f -- rebuild the image"
            fi
        else
            warn "/opt/$f missing from the image"
        fi
    done

    step "Patches baked into the image"
    docker run --rm "$IMAGE" sh -c 'ls -1 /opt/patches/patch-*.py 2>/dev/null | xargs -n1 basename' \
        || warn "no patches found in the image"

    exit 0
fi

# --- build -----------------------------------------------------------------

step "Building Docker image '$IMAGE'"
echo "    context : $SCRIPT_DIR"
if [ "$NO_CACHE" = 1 ]; then
    echo "    cache   : disabled (--no-cache)"
fi

build_args=(build -t "$IMAGE")
if [ "$NO_CACHE" = 1 ]; then
    build_args+=(--no-cache)
fi
build_args+=("$SCRIPT_DIR")

docker "${build_args[@]}"

ok "Image '$IMAGE' built."
echo ""
echo "    Verify with: ./build-image.sh --check"
echo "    Then build:  ./run.sh build"
