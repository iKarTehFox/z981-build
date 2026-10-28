# ZTE Z981 (urd) — LineageOS 16.0 build environment

A self-contained, portable Docker build environment for LineageOS 16.0 on the
ZTE Z981 (`urd`, MSM8952 / Snapdragon 617). It builds a ROM with a **working
camera and working video recording** — the first custom ROM for this device to
do so.

Everything needed to reproduce the build lives in this directory. The Docker
image knows nothing about your host filesystem; the source tree and ccache are
supplied at run time as mounts, so the same image works on any machine.

## Disclaimer
This ROM is provided as-is. It is not officially supported by ZTE, Qualcomm, or LineageOS. It is a custom ROM built by the community, for the community.

As always, no one, other than yourself, can truly be responsible for the choices **you** make, including the choice to flash this on your device. By installing this ROM, you fully accept the responsibility for any issues you may encounter while using this custom ROM, including but not limited to:
  - The custom ROM completely corrupting itself for no reason at all,
  - you losing all 32 GB of your data because of it,
  - you failing to wake up for school or work because the alarm app didn't work properly,
  - you bricking your device and having to buy a new one,
  - and you losing your mind because you can't figure out how to fix the issues you might run into.

So yeah...

## Quick start
See `etc/notes.md` before considering.

```bash
# 1. Build the Docker image (once)
./run.sh build-image

# 2. Fetch the source (~50 GB) and build
./run.sh all
```

The finished ROM lands in `out/target/product/urd/lineage-16.0-*-urd.zip`.

If you already have a synced source tree, point the scripts at it:

```bash
SRC_DIR=/path/to/android/src ./run.sh build
```

---

## Requirements

- **Linux** host with Docker (the scripts use bind mounts; see *Windows* below)
- **~150 GB free disk** (50 GB source + ~40 GB out + up to 30 GB ccache)
- **8 GB RAM minimum** (the container is capped at 8 GB; 16 GB is comfortable)
- A few hours for the first build (subsequent builds are much faster via ccache)

---

## Layout

```
z981-build/
├── Dockerfile              # Ubuntu 18.04 + build deps + repo + ccache
├── run.sh                  # HOST driver: build image, run container
├── build.sh                # CONTAINER driver: init, sync, patch, build
├── build-image.sh          # thin wrapper around `docker build` (+ --check)
├── run.ps1                 # Windows (PowerShell) driver, uses named volumes
├── urd_manifest.xml        # local manifest (fixes the kernel branch)
├── patch-*.py              # 9 idempotent source patches (see below)
├── privapp-permissions-com.qualcomm.location.xml
└── LICENSE                 # GPL-2.0 (covers this repo's files only)
```

### Where the source tree lives

By default, everything lives next to this directory:

| Host path      | Container path | Contents                    |
|----------------|----------------|-----------------------------|
| `../src`       | `/src`         | Android source tree (~50 GB)|
| `../out`       | `/src/out`     | build output (~40 GB)       |
| `../ccache`    | `/ccache`      | compiler cache (≤30 GB)     |

Override with `SRC_DIR`, `OUT_DIR`, `CCACHE_DIR`.

**Optional speed-up:** Android builds are I/O-heavy, and most of that I/O is in
`out/`. If you have a fast SSD/NVMe alongside a slower HDD, put the source tree
on the big/slow disk and `out/` on the fast one:

```bash
SRC_DIR=/mnt/hdd/android/src OUT_DIR=/mnt/nvme/android-out ./run.sh build
```

This is purely an optimization — the defaults work fine on a single disk.

---

## Commands

| Command                  | What it does                                        |
|--------------------------|-----------------------------------------------------|
| `./run.sh build-image`   | Build (or rebuild) the Docker image                 |
| `./run.sh init`          | `repo init` + install the local manifest            |
| `./run.sh sync`          | `repo sync` (~50 GB download)                       |
| `./run.sh build`         | Apply patches + compile                             |
| `./run.sh all`           | init + sync + build                                 |
| `./run.sh shell`         | Interactive shell inside the container              |
| `./run.sh status`        | Show image + directory sizes                        |
| `./run.sh clean`         | **DESTROY** the source, out and ccache directories  |
| `./build-image.sh --check` | Verify the image's baked-in files and patches     |

Environment overrides: `IMAGE`, `SRC_DIR`, `OUT_DIR`, `CCACHE_DIR`, `MEM`,
`JOBS`, `RUN_AS_USER`.

---

## The patches

`build.sh` applies every `patch-*.py` in `/opt/patches/` before each build.
They are **idempotent** (each checks whether its change is already present) and
edit the source tree in place, so they are re-applied after every `repo sync`.

| Patch | Why it exists |
|-------|---------------|
| `patch-variable.py` | Soong kernel variables — **required to build** |
| `patch-clearkey.py` | Build fix for the ClearKey DRM plugin |
| `patch-getrandom.py` | ARM32 `getrandom` syscall (384) — **required to boot** |
| `patch-compat-syscalls.py` | `__NR_compat_syscalls` 384→385 — **required to boot** |
| `patch-device-mk.py` | privapp whitelist + ADB-during-boot props |
| `patch-usb-rc.py` | ADB during boot (optional) |
| `patch-cpp-framelen.py` | Relaxes the CPP frame-message length check |
| `patch-camera-fwpath.py` | **Camera fix** — installs CPP firmware where `ueventd` looks |
| `patch-video-encoder.py` | **Video fix** — software encoder + correct YUV buffers |

### The privapp-permissions fix

`com.qualcomm.location` is a privileged app that requests
`signature|privileged` permissions. Android 9 enforces the privapp permission
whitelist, so without a whitelist entry `PermissionManagerService` throws
during `systemReady()`, `system_server` crashes, and Rescue Party reboots to
recovery.

The device tree does **not** ship
`privapp-permissions-com.qualcomm.location.xml`, so `patch-device-mk.py`
installs it into `device/zte/urd/` from the copy baked into the image at
`/opt/privapp-permissions-com.qualcomm.location.xml`, then adds the
`PRODUCT_COPY_FILES` rule.

The copy rule must use a **root-relative** source path
(`device/zte/urd/privapp-permissions-com.qualcomm.location.xml`), **not**
`$(LOCAL_PATH)/...`. `device.mk` is a product makefile pulled in via
`inherit-product`, where `LOCAL_PATH` is unset, so `$(LOCAL_PATH)/foo.xml`
expands to a bogus path and the build fails with:

```
ninja: error: 'device/zte/urd/privapp-permissions-com.qualcomm.location.xml',
needed by '.../system/etc/permissions/privapp-permissions-com.qualcomm.location.xml',
missing and no known rule to make it
```

### The camera fix (the important one)

The CPP (camera post-processing) firmware was being installed to
`/vendor/etc/firmware/`, but `ueventd` only searches `/etc/firmware/`,
`/odm/firmware/`, `/vendor/firmware/` and `/firmware/image/`. So the kernel
never loaded the firmware, the CPP timed out, and the camera HAL looped on
`0xdeadbeef` forever — a black preview.

`patch-camera-fwpath.py` changes the install path to `/vendor/firmware/` in
both `proprietary-files.txt` and `urd-vendor.mk`. With the firmware loading
correctly, the HAL selects firmware v1.5.0 and the camera works. **No kernel
patch is needed.**

### The video fix

Android 9's OMX HIDL framework requires
`OMX.google.android.index.allocateNativeHandle`, which the old Qualcomm Venus
hardware encoder rejects. The framework then passes a malformed native handle
and `mediaserver` crashes with SIGSEGV on every recording attempt.

`patch-video-encoder.py` makes five coordinated changes that switch recording
to the software encoder with correctly-sized YUV buffers, and fixes the green
line that appeared on the right of recorded video (the camera pads the Y plane
to a multiple of 16, so chroma starts at `stride × 1088`, not `stride × 1080`;
the encoder now uses `mSliceHeight` for the chroma offset).

---

## Windows

Use `run.ps1` instead of `run.sh`. It uses Docker **named volumes** rather than
bind mounts, because a Windows bind mount is exposed as 9p/drvfs, which does
not support directory renames — and `repo init` renames `.repo/repo.tmp`, so it
fails with `PermissionError: [Errno 13]`. Named volumes are ext4 inside the
WSL2 VM and support renames natively.

```powershell
.\run.ps1 build-image
.\run.ps1 all
```

---

## Flashing

The build produces a flashable zip. To install it:

```bash
adb reboot bootloader          # or: adb reboot recovery
fastboot flash recovery twrp.img
# then in TWRP: wipe data, flash the lineage-16.0-*-urd.zip
```

The device must be on the correct firmware base (stock Android 6.0.1) and have
an unlocked bootloader.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Source directory ... does not exist` | Run `./run.sh init` first, or set `SRC_DIR` |
| `ninja: no work to do` after patching | Stale kernel build — `rm -rf out/target/product/urd/obj/KERNEL_OBJ` |
| Soong lock error | `rm -f out/.lock` |
| Build dies with `SyntaxError` in a `.py` | The image's `python` must be python2.7 (it is, by default) |
| `repo sync` fails on webview APKs | `git-lfs` missing — it is installed in the image |
| Out of disk space | `/tmp` may be a small tmpfs; write logs elsewhere |

---

## Credits

The camera and video fixes were developed for this project. Upstream device
tree, kernel and vendor blobs are credited under
[Upstream attribution](#upstream-attribution).

---

## License

The files **in this repository** — the Docker build environment, the host and
container driver scripts, the `patch-*.py` scripts, the local manifest and the
privapp-permissions whitelist — are licensed under the **GNU General Public
License, version 2** (GPL-2.0-only). See [`LICENSE`](LICENSE) for the full text.

**This license covers only the files in this repository.** It does **not**
cover, and does not attempt to relicense, anything fetched at build time:

- the LineageOS / AOSP source tree (`repo sync`),
- the device tree (`device/zte/urd`),
- the kernel tree (`kernel/zte/urd`),
- the vendor blobs (`vendor/zte/urd`),

all of which remain under their own upstream licenses and copyright holders.

### Upstream attribution

The device tree, kernel and vendor blobs for the ZTE Z981 (`urd`) are the work
of the [ZTE-ZMAX-PRO](https://github.com/ZTE-ZMAX-PRO) organization
([@armandopartida0](https://github.com/armandopartida0)):

| Tree | Repository |
|------|------------|
| Device tree (DT) | [`ZTE-ZMAX-PRO/android_device_zte_urd`](https://github.com/ZTE-ZMAX-PRO/android_device_zte_urd) |
| Kernel (KT) | [`ZTE-ZMAX-PRO/android_kernel_zte_urd`](https://github.com/ZTE-ZMAX-PRO/android_kernel_zte_urd) |
| Vendor blobs (VT) | [`ZTE-ZMAX-PRO/vendor_zte_urd`](https://github.com/ZTE-ZMAX-PRO/vendor_zte_urd) |
| Local manifest | [`ZTE-ZMAX-PRO/local_manifest`](https://github.com/ZTE-ZMAX-PRO/local_manifest) |

