#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
"""Fix the clearkey DRM plugin build failure in LineageOS 16.0.

frameworks/av/drm/mediadrm/plugins/clearkey/hidl/include/DrmPlugin.h declares:

    DeviceFiles mFileHandle GUARDED_BY(mFileHandleLock);
    Mutex mFileHandleLock;

but the type `DeviceFiles` is defined NOWHERE in the tree (it is a dangling
reference left over from a bad merge; upstream lineage-16.0 has the same bug).
Neither `mFileHandle` nor `mFileHandleLock` is used anywhere in the plugin, so
both lines are dead code.

With clang-4691093 this fails as:
    error: unknown type name 'DeviceFiles'
    error: attribute guarded_by ignored, because it is not attached to a declaration
    error: private field 'mFileHandle' is not used [-Werror,-Wunused-private-field]

Fix: delete both dead lines.
"""
import sys

P = ('/src/frameworks/av/drm/mediadrm/plugins/clearkey/hidl/include/'
     'DrmPlugin.h')

s = open(P).read()

if 'DeviceFiles' not in s:
    print('already patched, nothing to do')
    sys.exit(0)

dead = ('    DeviceFiles mFileHandle GUARDED_BY(mFileHandleLock);\n'
        '    Mutex mFileHandleLock;\n')

if dead not in s:
    print('ERROR: dead-code block not found verbatim', file=sys.stderr)
    sys.exit(1)

s = s.replace(dead, '', 1)

open(P, 'w').write(s)
print('patched OK')
