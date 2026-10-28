#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
"""Add the getrandom syscall (384) to the 32-bit ARM compat syscall table.

WHY:
  The Z981 kernel (3.10.84) implements getrandom() for ARM64 (syscall 278,
  see include/uapi/asm-generic/unistd.h and drivers/char/random.c), but the
  32-bit ARM compat table (arch/arm64/include/asm/unistd32.h) stops at 383
  (seccomp). Android 9's bionic calls getrandom() as syscall 384 on 32-bit
  ARM, so every 32-bit process (app_process32, dex2oat, patchoat,
  audioserver, mediaserver, cameraserver) dies with SIGSYS:

      dex2oat[3921]: syscall 384

  system_server can never finish booting, so Android's Rescue Party fires
  after 3 attempts and reboots to recovery with:
      --prompt_and_wipe_data --reason=RescueParty

FIX:
  Append syscall 384 -> sys_getrandom to unistd32.h. sys_getrandom already
  exists and is declared in include/linux/syscalls.h, so no other change is
  needed.
"""
import sys

P = '/src/kernel/zte/urd/arch/arm64/include/asm/unistd32.h'

s = open(P).read()

if '__NR_getrandom' in s:
    print('already patched, nothing to do')
    sys.exit(0)

anchor = ('#define __NR_seccomp 383\n'
          '__SYSCALL(__NR_seccomp, sys_seccomp)\n')

if anchor not in s:
    print('ERROR: anchor (seccomp 383) not found', file=sys.stderr)
    sys.exit(1)

addition = anchor + ('#define __NR_getrandom 384\n'
                     '__SYSCALL(__NR_getrandom, sys_getrandom)\n')

s = s.replace(anchor, addition, 1)

open(P, 'w').write(s)
print('patched OK')
