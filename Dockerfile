# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
#
# LineageOS 16.0 build environment for the ZTE Z981 (urd)
#
# Ubuntu 18.04 is the officially supported host for LineageOS 16.0, so we pin
# it. The image contains every build dependency plus ccache, the `repo` tool,
# and the build driver + patches baked in at /opt.
#
# This image is fully self-contained and portable: it knows nothing about the
# host filesystem. The source tree and ccache are supplied at run time as
# mounts (see run.sh), so the same image works on any machine.
#
# Build:
#   docker build -t lineage16-urd .
#   (or: ./run.sh build-image)

FROM ubuntu:18.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# --- Base tooling -----------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash bc bison build-essential ccache curl flex g++-multilib gcc-multilib \
    git git-lfs gnupg gperf imagemagick lib32ncurses5-dev lib32readline-dev \
    lib32z1-dev liblz4-tool libncurses5 libncurses5-dev libsdl1.2-dev \
    libssl-dev libwxgtk3.0-dev libxml2 libxml2-utils lzop pngcrush \
    rsync schedtool squashfs-tools xsltproc zip zlib1g-dev \
    python python3 openjdk-8-jdk \
    android-tools-adb android-tools-fastboot \
    file wget unzip nano vim less procps \
    && rm -rf /var/lib/apt/lists/*

# git-lfs is required: external/chromium-webview/prebuilt/* stores its APKs via
# Git LFS (*.apk filter=lfs). Without it, `repo sync` fails with
# "Cannot initialize work tree" for those four projects.
RUN git lfs install --system

# --- repo tool --------------------------------------------------------------
RUN curl -o /usr/local/bin/repo https://storage.googleapis.com/git-repo-downloads/repo \
    && chmod a+x /usr/local/bin/repo

# --- python: Android 9 (LineageOS 16.0) build scripts are Python 2 -----------
# Many build/make/tools/*.py and libcore/annotations/*.py use Python 2 syntax
# (e.g. `except X, e:` and `print 'x'`). With /usr/bin/python -> python3 the
# build dies with SyntaxError. Point `python` at python2.7 instead.
# `repo` itself is invoked as `python3 /usr/local/bin/repo` by its shebang, so
# it is unaffected.
RUN ln -sf /usr/bin/python2.7 /usr/bin/python

# --- git identity (repo requires it) ----------------------------------------
RUN git config --global user.name "Lineage Builder" \
    && git config --global user.email "builder@localhost" \
    && git config --global color.ui false

# --- ccache tuning for a memory-constrained build ---------------------------
ENV USE_CCACHE=1
ENV CCACHE_DIR=/ccache
ENV CCACHE_EXEC=/usr/bin/ccache
# 30 GB ccache is plenty for one device and saves rebuild time
RUN ccache -M 30G

# --- jackd / java heap: keep it modest for 8 GB RAM -------------------------
ENV JACK_SERVER_VM_ARGUMENTS="-Dfile.encoding=UTF-8 -XX:+TieredCompilation -Xmx4g"

# --- build driver baked into the image --------------------------------------
# Kept at /opt so the /src volume stays clean (no build.sh inside the tree).
COPY build.sh /opt/build.sh
COPY urd_manifest.xml /opt/urd_manifest.xml
RUN chmod +x /opt/build.sh

# --- patch scripts baked into the image -------------------------------------
# build.sh applies these before every build (see do_patch). They are idempotent
# and edit the source tree in place, so they must be re-applied after any
# `repo sync`.
#
# Only patches to repos we do NOT own live here (vendor/lineage, frameworks/av).
# The device/vendor/kernel fixes are committed into my forks and delivered by
# `repo sync` (see urd_manifest.xml), so they need no script.
COPY patch-*.py /opt/patches/
RUN chmod +x /opt/patches/*.py

WORKDIR /src
CMD ["/bin/bash"]
