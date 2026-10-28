#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
"""Patch device/zte/urd/device.mk to:

1. Install the privapp-permissions whitelist for com.qualcomm.location
   (fixes the system_server crash -> Rescue Party -> reboot-to-recovery loop).

2. Enable ADB during boot so the device can be debugged over adb while it is
   booting (previously there was no adb until the ROM finished booting, which
   it never did).

NOTE on the privapp-permissions file:
  The device tree does NOT ship privapp-permissions-com.qualcomm.location.xml,
  so this script installs it into device/zte/urd/ from the copy baked into the
  image at /opt/privapp-permissions-com.qualcomm.location.xml.

  The PRODUCT_COPY_FILES source must be a path relative to the build root
  (e.g. device/zte/urd/foo.xml). It must NOT use $(LOCAL_PATH): device.mk is a
  product makefile pulled in via inherit-product, where LOCAL_PATH is unset, so
  $(LOCAL_PATH)/foo.xml expands to a bogus path and ninja fails with:

      ninja: error: 'device/zte/urd/privapp-permissions-com.qualcomm.location.xml',
      needed by '.../system/etc/permissions/privapp-permissions-com.qualcomm.location.xml',
      missing and no known rule to make it
"""
import os
import shutil
import sys

P = '/src/device/zte/urd/device.mk'
XML_SRC = '/opt/privapp-permissions-com.qualcomm.location.xml'
XML_DST = '/src/device/zte/urd/privapp-permissions-com.qualcomm.location.xml'
# Path as it must appear in PRODUCT_COPY_FILES (relative to the build root).
XML_REL = 'device/zte/urd/privapp-permissions-com.qualcomm.location.xml'

s = open(P).read()

changed = False

# --- 0. install the whitelist XML into the device tree ----------------------
# The device tree does not ship this file; without it the PRODUCT_COPY_FILES
# rule below has no source and ninja aborts. Copy it in from the image.
if not os.path.exists(XML_DST):
    if not os.path.exists(XML_SRC):
        print('ERROR: %s not found in the image' % XML_SRC, file=sys.stderr)
        sys.exit(1)
    shutil.copyfile(XML_SRC, XML_DST)
    print('  + installed privapp-permissions-com.qualcomm.location.xml')
else:
    print('  = privapp-permissions XML already present')

# --- 1. privapp-permissions whitelist ---------------------------------------
# Two cases:
#   a) not present at all            -> insert the (correct) rule
#   b) present but using $(LOCAL_PATH) -> rewrite to the root-relative path
#      (an earlier version of this script emitted $(LOCAL_PATH), which does not
#      resolve in a product makefile and broke the build)
if 'privapp-permissions-com.qualcomm.location.xml' not in s:
    anchor = """# Permissions
PRODUCT_COPY_FILES += \\
    frameworks/native/data/etc/android.hardware.bluetooth_le.xml:$(TARGET_COPY_OUT_VENDOR)/etc/permissions/android.hardware.bluetooth_le.xml \\"""
    addition = """# Permissions
PRODUCT_COPY_FILES += \\
    %s:$(TARGET_COPY_OUT_SYSTEM)/etc/permissions/privapp-permissions-com.qualcomm.location.xml \\
    frameworks/native/data/etc/android.hardware.bluetooth_le.xml:$(TARGET_COPY_OUT_VENDOR)/etc/permissions/android.hardware.bluetooth_le.xml \\""" % XML_REL
    if anchor not in s:
        print('ERROR: permissions anchor not found', file=sys.stderr)
        sys.exit(1)
    s = s.replace(anchor, addition, 1)
    changed = True
    print('  + privapp-permissions whitelist')
elif '$(LOCAL_PATH)/privapp-permissions-com.qualcomm.location.xml' in s:
    # Fix the broken $(LOCAL_PATH) reference emitted by older versions.
    s = s.replace('$(LOCAL_PATH)/privapp-permissions-com.qualcomm.location.xml',
                  XML_REL, 1)
    changed = True
    print('  ~ privapp-permissions whitelist ($(LOCAL_PATH) -> root-relative)')
else:
    print('  = privapp-permissions already present')

# --- 2. ADB during boot -----------------------------------------------------
if 'persist.sys.usb.config=adb' not in s:
    anchor2 = """# Dalvik
PRODUCT_PROPERTY_OVERRIDES += \\
    dalvik.vm.heapstartsize=16m \\"""
    addition2 = """# ADB during boot
# Make adbd come up early and stay up so the device can be debugged over adb
# while it is booting (and if it bootloops). ro.adb.secure=0 disables the
# host-key authorization prompt; persist.sys.usb.config=adb forces the adb
# function on; persist.adb.notify=0 hides the "USB debugging connected" toast.
PRODUCT_PROPERTY_OVERRIDES += \\
    persist.sys.usb.config=adb \\
    persist.adb.notify=0 \\
    ro.adb.secure=0 \\
    ro.debuggable=1

# Dalvik
PRODUCT_PROPERTY_OVERRIDES += \\
    dalvik.vm.heapstartsize=16m \\"""
    if anchor2 not in s:
        print('ERROR: dalvik anchor not found', file=sys.stderr)
        sys.exit(1)
    s = s.replace(anchor2, addition2, 1)
    changed = True
    print('  + ADB-during-boot properties')
else:
    print('  = ADB properties already present')

if changed:
    open(P, 'w').write(s)
    print('patched OK')
else:
    print('nothing to do')
