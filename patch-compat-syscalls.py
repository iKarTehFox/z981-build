#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
"""Bump __NR_compat_syscalls so the new getrandom (384) entry is reachable.

WHY (this is the second half of the getrandom fix):
  arch/arm64/kernel/entry.S dispatches 32-bit syscalls like this:

      el0_svc_compat:
          adr     stbl, compat_sys_call_table
          uxtw    scno, w7
          mov     sc_nr, #__NR_compat_syscalls     <-- bounds limit
          b       el0_svc_naked
      ...
          cmp     scno, sc_nr                      <-- reject if scno >= sc_nr

  __NR_compat_syscalls was 384, so syscall 384 (getrandom) failed the
  `384 >= 384` check and was rejected with do_ni_syscall BEFORE the table
  was consulted. That is why adding the unistd32.h entry alone did nothing
  and the log still showed "syscall 384" + "do_ni_syscall".

  Bumping the limit to 385 makes syscall 384 reachable.

  (The compat_sys_call_table array itself is built from unistd32.h and has
  no fixed size, so it already contains the 384 entry.)
"""
import sys

P = '/src/kernel/zte/urd/arch/arm64/include/asm/unistd.h'

s = open(P).read()

import re

# Match any amount of whitespace between the name and the value.
pat = re.compile(r'#define\s+__NR_compat_syscalls\s+384\b')
if re.search(r'#define\s+__NR_compat_syscalls\s+385\b', s):
    print('already patched, nothing to do')
    sys.exit(0)

if not pat.search(s):
    print('ERROR: anchor not found', file=sys.stderr)
    sys.exit(1)

s = pat.sub('#define __NR_compat_syscalls            385', s, count=1)
open(P, 'w').write(s)
print('patched OK')
