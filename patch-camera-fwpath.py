#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
"""Install the CPP camera firmware to the directory ueventd actually searches.

SYMPTOM
  Camera preview is black on both cameras.  The kernel log shows an endless
  loop and the HAL never gets a firmware:

      MSM-CPP cpp_init_hw:1045 CPP HW Version: 0x40030000
      MSM-CPP cpp_load_fw:1189 MC Bootloader Version: 0x312e3176
      MSM-CPP cpp_load_fw:1202 CPP FW Version: 0xdeadbeef   <-- failure
      MSM-CPP msm_cpp_do_timeout_work:1560 Reloading firmware

  and logcat shows:

      ueventd: firmware: could not find firmware for cpp_firmware_v1_5_0.fw
      msm_cpp: Fail to loc blob cpp_firmware_v1_5_0.fw, rc:-12

ROOT CAUSE
  ueventd's firmware search path is:

      firmware_directories /etc/firmware/ /odm/firmware/ /vendor/firmware/ /firmware/image/

  The CPP firmware was being installed to /vendor/etc/firmware/, which is NOT
  in that list.  So request_firmware() fails, the kernel loads nothing, and
  the timeout work retries forever with a NULL firmware name -> 0xdeadbeef.

  The HAL itself is fine: it maps the true hardware version 0x40030000 (4.3.0)
  to firmware v1.5.0, exactly like stock.  No kernel patch is needed.

FIX
  Change the install destination of the cpp_firmware_*.fw entries from
  vendor/etc/firmware/ to vendor/firmware/ in BOTH:

    * device/zte/urd/proprietary-files.txt   (source list)
    * vendor/zte/urd/urd-vendor.mk           (the generated PRODUCT_COPY_FILES
                                              that actually installs them)

  This script is idempotent.

NOTE: run this AFTER the vendor tree has been synced (vendor/zte/urd must
      exist).  It edits files under /src.
"""
import os
import sys

DEVICE_PF = '/src/device/zte/urd/proprietary-files.txt'
VENDOR_MK = '/src/vendor/zte/urd/urd-vendor.mk'

changed = 0


def patch_file(path, old, new, label):
    global changed
    if not os.path.exists(path):
        print('  SKIP %s (not found: %s)' % (label, path))
        return
    s = open(path).read()
    if new in s and old not in s:
        print('  %s already patched' % label)
        return
    if old not in s:
        print('  WARNING: %s anchor not found in %s' % (label, path))
        return
    s = s.replace(old, new)
    open(path, 'w').write(s)
    print('  patched %s' % label)
    changed += 1


# --- device/zte/urd/proprietary-files.txt -----------------------------------
#   etc/firmware/cpp_firmware_vX_Y_Z.fw:vendor/etc/firmware/cpp_firmware_vX_Y_Z.fw
# -> ...:vendor/firmware/cpp_firmware_vX_Y_Z.fw
if os.path.exists(DEVICE_PF):
    s = open(DEVICE_PF).read()
    out = []
    n = 0
    for line in s.splitlines(True):
        if 'cpp_firmware_' in line and ':vendor/etc/firmware/' in line:
            line = line.replace(':vendor/etc/firmware/', ':vendor/firmware/')
            n += 1
        out.append(line)
    if n:
        open(DEVICE_PF, 'w').write(''.join(out))
        print('  patched proprietary-files.txt (%d entries)' % n)
        changed += 1
    else:
        print('  proprietary-files.txt already patched (or no cpp entries)')
else:
    print('  SKIP proprietary-files.txt (not found)')

# --- vendor/zte/urd/urd-vendor.mk -------------------------------------------
#   .../proprietary/vendor/etc/firmware/cpp_firmware_vX_Y_Z.fw:system/vendor/etc/firmware/cpp_firmware_vX_Y_Z.fw
# -> ...:system/vendor/firmware/cpp_firmware_vX_Y_Z.fw
if os.path.exists(VENDOR_MK):
    s = open(VENDOR_MK).read()
    out = []
    n = 0
    for line in s.splitlines(True):
        if 'cpp_firmware_' in line and ':system/vendor/etc/firmware/' in line:
            line = line.replace(':system/vendor/etc/firmware/',
                                ':system/vendor/firmware/')
            n += 1
        out.append(line)
    if n:
        open(VENDOR_MK, 'w').write(''.join(out))
        print('  patched urd-vendor.mk (%d entries)' % n)
        changed += 1
    else:
        print('  urd-vendor.mk already patched (or no cpp entries)')
else:
    print('  SKIP urd-vendor.mk (not found)')

print('patch-camera-fwpath: %d file(s) changed' % changed)
sys.exit(0)
