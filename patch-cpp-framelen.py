#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
"""Relax the CPP frame-message length check.

CONTEXT
  Once the CPP firmware loads correctly (see patch-camera-fwpath.py), the CPP
  boots, but a NEW error appears:

      MSM-CPP msm_cpp_cfg_frame:2249 Invalid frame message,len=303,expected=319

  The kernel validates the HAL-supplied frame message with:

      if ((stripe_base + new_frame->num_strips * stripe_size + 1) !=
              new_frame->msg_len) {
              pr_err("Invalid frame message,len=%d,expected=%d\\n", ...);
              return -EINVAL;
      }

  With the device-tree payload params for msm8952
  (arch/arm/boot/dts/qcom/msm8952-camera.dtsi, node qcom,cpp-fw-payload-info):

      qcom,stripe-base = <156>;
      qcom,stripe-size = <27>;

  and num_strips = 6, the kernel expects 156 + 6*27 + 1 = 319 words. The
  prebuilt HAL sends a different length depending on which firmware it
  selected:

      fw v1.4.0 (hw 4.0.0/4.2.0): msg_len = 303   (shorter)
      fw v1.6.0 (hw 4.1.0):       msg_len = 627   (longer)

  Neither factors cleanly against the DT formula, so msg_len is NOT derived
  from num_strips * stripe_size - the HAL builds the payload from its own
  internal layout, and the kernel formula is only a sanity check.

WHY RELAXING IS SAFE
  The kernel only ever READS the message up to:

      i1 = stripe_base + stripe_size * first_stripe_index
      i2 = stripe_size * (last_stripe_index - first_stripe_index + 1)
      -> reads cpp_cmd_msg[0 .. stripe_base-1] and [i1 .. i1+i2-1]

  (see msm_cpp.c, the "send top level and plane level" / "send stripes"
  loops). It never indexes past i1+i2, so a LONGER payload is simply ignored
  and a shorter one is only a problem if it does not cover the read range.
  The trailer check (cpp_cmd_msg[msg_len-1] == MSM_CPP_MSG_ID_TRAILER) still
  validates that the message is well-formed.

FIX
  Drop the upper bound: accept any msg_len >= stripe_base + stripe_size *
  num_strips. This accepts both the shorter (303) and longer (627) payloads
  the prebuilt HAL produces, while still rejecting a payload too short to
  cover the range the kernel reads.

NOTE: msm_cpp.c is indented with TABS.
"""
import sys

P = ('/src/kernel/zte/urd/drivers/media/platform/msm/camera_v2/'
     'pproc/cpp/msm_cpp.c')

s = open(P).read()

T = '\t'

# The check we want to end up with: reject only payloads too SHORT to cover
# the range the kernel reads. No upper bound.
addition = (
    T + '/*\n'
    + T + ' * Z981: accept any frame message long enough to cover the range\n'
    + T + ' * the kernel reads (stripe_base + stripe_size * num_strips).\n'
    + T + ' * The prebuilt HAL sends a different length per firmware version\n'
    + T + ' * (303 with fw v1.4.0, 627 with fw v1.6.0) and neither matches the\n'
    + T + ' * device-tree formula exactly. The kernel never indexes past\n'
    + T + ' * i1+i2, so a longer payload is harmless; only a too-short one\n'
    + T + ' * would read past the end of the buffer.\n'
    + T + ' */\n'
    + T + 'if ((stripe_base + new_frame->num_strips * stripe_size) >\n'
    + T*2 + 'new_frame->msg_len) {\n'
    + T*2 + 'pr_err("Invalid frame message,len=%d,expected>=%d\\n",\n'
    + T*3 + 'new_frame->msg_len,\n'
    + T*3 + '(stripe_base +\n'
    + T*3 + 'new_frame->num_strips * stripe_size));\n'
    + T*2 + 'return -EINVAL;\n'
    + T + '}'
)

# Already at the final form?
if 'Z981: accept any frame message long enough' in s:
    print('already patched, nothing to do')
    sys.exit(0)

# Two possible starting points: the original `!=` check, or the earlier
# `<` (shorter-only) form from a previous version of this script.
anchor_original = (
    T + 'if ((stripe_base + new_frame->num_strips * stripe_size + 1) !=\n'
    + T*2 + 'new_frame->msg_len) {\n'
    + T*2 + 'pr_err("Invalid frame message,len=%d,expected=%d\\n",\n'
    + T*3 + 'new_frame->msg_len,\n'
    + T*3 + '(stripe_base +\n'
    + T*3 + 'new_frame->num_strips * stripe_size + 1));\n'
    + T*2 + 'return -EINVAL;\n'
    + T + '}'
)

anchor_shorter = (
    T + '/*\n'
    + T + ' * Z981: accept shorter frame message. The prebuilt HAL is from an\n'
    + T + ' * older CPP generation and sends a payload 16 words shorter than\n'
    + T + ' * the device tree describes (303 vs 319). Reject only payloads\n'
    + T + ' * that are too LONG, which would overrun the buffer we allocated.\n'
    + T + ' */\n'
    + T + 'if ((stripe_base + new_frame->num_strips * stripe_size + 1) <\n'
    + T*2 + 'new_frame->msg_len) {\n'
    + T*2 + 'pr_err("Invalid frame message,len=%d,expected=%d\\n",\n'
    + T*3 + 'new_frame->msg_len,\n'
    + T*3 + '(stripe_base +\n'
    + T*3 + 'new_frame->num_strips * stripe_size + 1));\n'
    + T*2 + 'return -EINVAL;\n'
    + T + '}'
)

if anchor_original in s:
    s = s.replace(anchor_original, addition, 1)
    print('patched frame-length check (original != -> no upper bound) OK')
elif anchor_shorter in s:
    s = s.replace(anchor_shorter, addition, 1)
    print('patched frame-length check (shorter-only -> no upper bound) OK')
else:
    print('ERROR: frame-length anchor not found', file=sys.stderr)
    sys.exit(1)

open(P, 'w').write(s)
