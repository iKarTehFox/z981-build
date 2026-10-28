#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
"""Patch vendor/lineage/build/soong/android/variable.go to declare the kernel
variables that BoardConfigSoong.mk exports to Soong.

Without this, `mka bacon` fails with:
    error: vendor/lineage/build/soong/Android.bp:31:8: module
    "generated_kernel_includes": cmd: unknown variable '$(KERNEL_MAKE_FLAGS)'

The lineage_generator's lineageExpandVariables() only resolves variables that
are declared in the Product_variables struct (via the lineageVarsPlugin
VendorConfig). BoardConfigSoong.mk exports KERNEL_ARCH, KERNEL_CROSS_COMPILE,
KERNEL_MAKE_FLAGS, TARGET_KERNEL_CONFIG and TARGET_KERNEL_SOURCE, but the Go
struct never declared them, so IsSet() returns false and the $(...) is left
unexpanded -> soong_build errors out.

NOTE: the file is indented with TABS.
"""
import sys

P = '/src/vendor/lineage/build/soong/android/variable.go'

s = open(P).read()

if 'Kernel_make_flags' in s:
    print('already patched, nothing to do')
    sys.exit(0)

T = '\t'

# --- 1. Product_variables struct (the typed, per-var struct) ----------------
anchor = (T + 'Has_legacy_camera_hal1 struct {\n'
          + T*2 + 'Cflags []string\n'
          + T + '}')
addition = (anchor + '\n'
            + T + 'Kernel_arch struct {\n' + T*2 + 'Cflags []string\n' + T + '}\n'
            + T + 'Kernel_cross_compile struct {\n' + T*2 + 'Cflags []string\n' + T + '}\n'
            + T + 'Kernel_make_flags struct {\n' + T*2 + 'Cflags []string\n' + T + '}')
if anchor not in s:
    print('ERROR: anchor1 not found', file=sys.stderr)
    sys.exit(1)
s = s.replace(anchor, addition, 1)

# --- 2. ProductVariables struct (the JSON-marshalled one) -------------------
anchor2 = T + 'Has_legacy_camera_hal1  *bool `json:",omitempty"`'
addition2 = (anchor2 + '\n'
             + T + 'Kernel_arch  *string `json:",omitempty"`\n'
             + T + 'Kernel_cross_compile  *string `json:",omitempty"`\n'
             + T + 'Kernel_make_flags  *string `json:",omitempty"`\n'
             + T + 'Target_kernel_config  *string `json:",omitempty"`\n'
             + T + 'Target_kernel_source  *string `json:",omitempty"`')
if anchor2 not in s:
    print('ERROR: anchor2 not found', file=sys.stderr)
    sys.exit(1)
s = s.replace(anchor2, addition2, 1)

open(P, 'w').write(s)
print('patched OK')
