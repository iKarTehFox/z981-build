#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
"""Add an adb-only handler to init.qcom.usb.rc.

The file has handlers for mtp, mtp,adb, rndis, rndis,adb, ptp, ptp,adb,
mass_storage, mass_storage,adb, midi, midi,adb -- but NOT for a bare "adb".
Since we set persist.sys.usb.config=adb (to get adb during boot), init needs
a matching `on property:sys.usb.config=adb` action to actually start adbd.
"""
import sys

P = '/src/device/zte/urd/rootdir/etc/init.qcom.usb.rc'
s = open(P).read()

if 'sys.usb.config=adb\n' in s:
    print('already patched, nothing to do')
    sys.exit(0)

anchor = """# MTP
on property:sys.usb.config=mtp
"""

addition = """# ADB only (used for debugging during boot)
on property:sys.usb.config=adb
    stop adbd
    write /sys/class/android_usb/android0/enable 0
    write /sys/class/android_usb/android0/idVendor 05C6
    write /sys/class/android_usb/android0/idProduct 901d
    write /sys/class/android_usb/android0/functions adb
    write /sys/class/android_usb/android0/enable 1
    start adbd
    setprop sys.usb.state ${sys.usb.config}

# MTP
on property:sys.usb.config=mtp
"""

if anchor not in s:
    print('ERROR: anchor not found', file=sys.stderr)
    sys.exit(1)

s = s.replace(anchor, addition, 1)
open(P, 'w').write(s)
print('patched OK')
