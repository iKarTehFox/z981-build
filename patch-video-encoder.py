#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Diego P. (iKarTehFox) and contributors
"""Make video recording work on the Z981 (urd) under Android 9.

SYMPTOM
  Recording video crashes mediaserver:

      Fatal signal 11 (SIGSEGV) in mediaserver (CodecLooper)
      #00 libhwbinder.so  Parcel::writeNativeHandleNoDup(native_handle const*, ...)
      #02 android.hardware.media.omx@1.0.so  writeEmbeddedToParcel(CodecBuffer...)
      #05 libmedia_omx.so  LWOmxNode::emptyBuffer(...)
      #06 libstagefright.so  ACodec::BaseState::onInputBufferFilled(...)

  The camera app then throws:
      java.lang.NullPointerException: camera object is a NULL pointer

ROOT CAUSE
  Android 9's OMX HIDL framework requires the encoder to support the
  OMX.google.android.index.allocateNativeHandle extension.  The old Qualcomm
  Venus hardware encoder rejects it:

      OMX-VENC: Enable/Disable allocate-native-handle allowed only in secure session
      OMXNodeInstance: setParameter(...allocateNativeHandle...): UnsupportedSetting

  The framework then hands the encoder a buffer with a malformed native handle
  and writeNativeHandleNoDup dereferences it -> SIGSEGV.

  Even after switching to the software encoder (OMX.google.h264.encoder) two
  more problems appear:
    * the camera HAL reports video-frame-format=android-opaque, which the
      software encoder cannot consume (it needs raw YUV);
    * the camera's YUV buffers are padded (stride/slice-height larger than
      width/height), so the encoder's buffer was too small (memcpy overflow)
      and it read the chroma plane from the wrong offset (green line).

FIX (five coordinated changes)
  1. CameraSource.h   - storeMetaDataInVideoBuffers default true -> false
                        (use VIDEO_BUFFER_MODE_DATA_CALLBACK_YUV, not gralloc)
  2. CameraSource.cpp - force PIXEL_FORMAT_YUV420SP
  3. MediaCodecSource.cpp - set "max-input-size" from the padded stride x
                        padded height; keep a memcpy clamp as a safety net
  4. SoftAVCEnc.{h,cpp} - add mSliceHeight (padded to 16) and use it for the
                        chroma plane offset (removes the green line)
  5. media_codecs.xml + media_codecs_8952.xml - disable the OMX.qcom.video.encoder.*
                        entries so the framework uses the software encoder

  This script is idempotent.

NOTE: these files live under /src/frameworks/av and /src/device/zte/urd.
"""
import os
import re
import sys

SRC = '/src'

CAMERA_SOURCE_H = SRC + '/frameworks/av/media/libstagefright/include/media/stagefright/CameraSource.h'
CAMERA_SOURCE_CPP = SRC + '/frameworks/av/media/libstagefright/CameraSource.cpp'
MEDIACODEC_SOURCE = SRC + '/frameworks/av/media/libstagefright/MediaCodecSource.cpp'
SOFTAVC_H = SRC + '/frameworks/av/media/libstagefright/codecs/avcenc/SoftAVCEnc.h'
SOFTAVC_CPP = SRC + '/frameworks/av/media/libstagefright/codecs/avcenc/SoftAVCEnc.cpp'
MEDIA_CODECS = [
    SRC + '/device/zte/urd/configs/media/media_codecs.xml',
    SRC + '/device/zte/urd/configs/media/media_codecs_8952.xml',
]

changed = 0


def edit(path, old, new, label):
    """Replace `old` with `new` in `path`. Idempotent: if `new` is already
    present (and `old` is not) it does nothing."""
    global changed
    if not os.path.exists(path):
        print('  SKIP %s (not found: %s)' % (label, path))
        return
    s = open(path).read()
    if new in s:
        print('  %s already patched' % label)
        return
    if old not in s:
        print('  WARNING: %s anchor not found in %s' % (label, path))
        return
    s = s.replace(old, new, 1)
    open(path, 'w').write(s)
    print('  patched %s' % label)
    changed += 1


# ---------------------------------------------------------------------------
# 1. CameraSource.h : storeMetaDataInVideoBuffers default true -> false
# ---------------------------------------------------------------------------
edit(
    CAMERA_SOURCE_H,
    '                                          bool storeMetaDataInVideoBuffers = true);',
    '                                          /*\n'
    '                                           * ZTE Z981 (urd): default to false so\n'
    '                                           * CameraSource stays on\n'
    '                                           * VIDEO_BUFFER_MODE_DATA_CALLBACK_YUV\n'
    '                                           * (raw YUV) instead of BUFFER_QUEUE\n'
    '                                           * (gralloc).  The Qualcomm Venus hw\n'
    '                                           * encoder cannot handle gralloc buffers\n'
    '                                           * (no allocateNativeHandle) and\n'
    '                                           * mediaserver crashes; the sw encoder\n'
    '                                           * needs raw YUV anyway.\n'
    '                                           */\n'
    '                                          bool storeMetaDataInVideoBuffers = false);',
    'CameraSource.h (storeMetaDataInVideoBuffers=false)')

# ---------------------------------------------------------------------------
# 2. CameraSource.cpp : force YUV420SP
# ---------------------------------------------------------------------------
edit(
    CAMERA_SOURCE_CPP,
    'status_t CameraSource::isCameraColorFormatSupported(\n'
    '        const CameraParameters& params) {\n'
    '    mColorFormat = getColorFormat(params.get(\n'
    '            CameraParameters::KEY_VIDEO_FRAME_FORMAT));\n'
    '    if (mColorFormat == -1) {\n'
    '        return BAD_VALUE;\n'
    '    }\n'
    '    return OK;\n'
    '}',
    'status_t CameraSource::isCameraColorFormatSupported(\n'
    '        const CameraParameters& params __unused) {\n'
    '    /*\n'
    '     * ZTE Z981 (urd): the camera HAL reports\n'
    '     * video-frame-format=android-opaque, which the software encoder\n'
    '     * cannot consume.  Force YUV420SP so the camera produces raw YUV.\n'
    '     */\n'
    '    mColorFormat = getColorFormat(CameraParameters::PIXEL_FORMAT_YUV420SP);\n'
    '    if (mColorFormat == -1) {\n'
    '        return BAD_VALUE;\n'
    '    }\n'
    '    return OK;\n'
    '}',
    'CameraSource.cpp (force YUV420SP)')

# ---------------------------------------------------------------------------
# 3a. MediaCodecSource.cpp : set max-input-size from the padded frame size
# ---------------------------------------------------------------------------
edit(
    MEDIACODEC_SOURCE,
    '    AString outputMIME;\n'
    '    CHECK(mOutputFormat->findString("mime", &outputMIME));\n'
    '    mIsVideo = outputMIME.startsWithIgnoreCase("video/");\n',
    '    AString outputMIME;\n'
    '    CHECK(mOutputFormat->findString("mime", &outputMIME));\n'
    '    mIsVideo = outputMIME.startsWithIgnoreCase("video/");\n'
    '\n'
    '    /*\n'
    '     * ZTE Z981 (urd): the camera\'s YUV buffers are padded to a\n'
    '     * stride/slice-height larger than width x height (e.g. 1920x1080 is\n'
    '     * delivered as 2048x1024x1.5 = 3145728 bytes, while the software\n'
    '     * encoder would otherwise allocate only 1920x1080x1.5 = 3110400).\n'
    '     * Feeding the larger buffer into the smaller one overflowed and\n'
    '     * crashed mediaserver.  Tell the encoder the real (padded) input\n'
    '     * size via "max-input-size".\n'
    '     */\n'
    '    if (mIsVideo) {\n'
    '        int32_t width = 0, height = 0;\n'
    '        if (mOutputFormat->findInt32("width", &width)\n'
    '                && mOutputFormat->findInt32("height", &height)\n'
    '                && width > 0 && height > 0) {\n'
    '            int32_t stride = (width + 2047) & ~2047;\n'
    '            if (stride < 2048) {\n'
    '                stride = 2048;\n'
    '            }\n'
    '            int32_t paddedHeight = (height + 31) & ~31;\n'
    '            int32_t maxInputSize = stride * paddedHeight * 3 / 2;\n'
    '            mOutputFormat->setInt32("max-input-size", maxInputSize);\n'
    '            ALOGI("setting max-input-size=%d for %dx%d (stride %d, padded height %d)",\n'
    '                    maxInputSize, width, height, stride, paddedHeight);\n'
    '        }\n'
    '    }\n',
    'MediaCodecSource.cpp (max-input-size)')

# ---------------------------------------------------------------------------
# 3b. MediaCodecSource.cpp : clamp the memcpy (safety net)
# ---------------------------------------------------------------------------
edit(
    MEDIACODEC_SOURCE,
    '            size = mbuf->size();\n'
    '\n'
    '            memcpy(inbuf->data(), mbuf->data(), size);',
    '            size = mbuf->size();\n'
    '\n'
    '            /*\n'
    '             * ZTE Z981 (urd): safety net - never copy more than the\n'
    '             * encoder input buffer can hold.\n'
    '             */\n'
    '            if (size > inbuf->capacity()) {\n'
    '                ALOGW("input buffer too small: mbuf size %zu > inbuf capacity %zu, clamping",\n'
    '                        size, inbuf->capacity());\n'
    '                size = inbuf->capacity();\n'
    '            }\n'
    '\n'
    '            memcpy(inbuf->data(), mbuf->data(), size);',
    'MediaCodecSource.cpp (memcpy clamp)')

# ---------------------------------------------------------------------------
# 4a. SoftAVCEnc.h : add mSliceHeight member
# ---------------------------------------------------------------------------
edit(
    SOFTAVC_H,
    '    int32_t  mStride;\n'
    '\n'
    '    struct timeval mTimeStart;   // Time at the start of decode()',
    '    int32_t  mStride;\n'
    '    // ZTE Z981 (urd): the camera pads the Y plane to a slice-height\n'
    '    // larger than the frame height, so the chroma plane starts later\n'
    '    // than mStride * mHeight.  mSliceHeight carries the padded value\n'
    '    // used for the plane offset; mHeight stays the real frame height.\n'
    '    int32_t  mSliceHeight;\n'
    '\n'
    '    struct timeval mTimeStart;   // Time at the start of decode()',
    'SoftAVCEnc.h (mSliceHeight member)')

# ---------------------------------------------------------------------------
# 4b. SoftAVCEnc.cpp : set mSliceHeight
# ---------------------------------------------------------------------------
edit(
    SOFTAVC_CPP,
    '    mAVCEncLevel = MAX(level, mAVCEncLevel);\n'
    '\n'
    '    mStride = mWidth;\n',
    '    mAVCEncLevel = MAX(level, mAVCEncLevel);\n'
    '\n'
    '    mStride = mWidth;\n'
    '    /*\n'
    '     * ZTE Z981 (urd): the camera pads the Y plane to a slice-height\n'
    '     * that is a multiple of 16 (1080 -> 1088), so the chroma plane\n'
    '     * starts at mStride * paddedHeight, not mStride * mHeight.\n'
    '     * Reading the chroma from the wrong offset produced a green band\n'
    '     * on the right edge of the frame.\n'
    '     */\n'
    '    mSliceHeight = (mHeight + 15) & ~15;\n',
    'SoftAVCEnc.cpp (set mSliceHeight)')

# ---------------------------------------------------------------------------
# 4c. SoftAVCEnc.cpp : use mSliceHeight for the plane offsets
# ---------------------------------------------------------------------------
edit(
    SOFTAVC_CPP,
    '            ps_inp_raw_buf->apv_bufs[0] = pu1_buf;\n'
    '            pu1_buf += (mStride) * mHeight;\n'
    '            ps_inp_raw_buf->apv_bufs[1] = pu1_buf;\n'
    '            pu1_buf += (mStride / 2) * mHeight / 2;\n'
    '            ps_inp_raw_buf->apv_bufs[2] = pu1_buf;',
    '            ps_inp_raw_buf->apv_bufs[0] = pu1_buf;\n'
    '            pu1_buf += (mStride) * mSliceHeight;\n'
    '            ps_inp_raw_buf->apv_bufs[1] = pu1_buf;\n'
    '            pu1_buf += (mStride / 2) * mSliceHeight / 2;\n'
    '            ps_inp_raw_buf->apv_bufs[2] = pu1_buf;',
    'SoftAVCEnc.cpp (YUV420P plane offsets)')

edit(
    SOFTAVC_CPP,
    '            ps_inp_raw_buf->apv_bufs[0] = pu1_buf;\n'
    '            pu1_buf += (mStride) * mHeight;\n'
    '            ps_inp_raw_buf->apv_bufs[1] = pu1_buf;\n'
    '\n'
    '            ps_inp_raw_buf->au4_wd[0] = mWidth;\n'
    '            ps_inp_raw_buf->au4_wd[1] = mWidth;',
    '            ps_inp_raw_buf->apv_bufs[0] = pu1_buf;\n'
    '            pu1_buf += (mStride) * mSliceHeight;\n'
    '            ps_inp_raw_buf->apv_bufs[1] = pu1_buf;\n'
    '\n'
    '            ps_inp_raw_buf->au4_wd[0] = mWidth;\n'
    '            ps_inp_raw_buf->au4_wd[1] = mWidth;',
    'SoftAVCEnc.cpp (YUV420SP plane offset)')

# ---------------------------------------------------------------------------
# 5. media_codecs*.xml : disable the Qualcomm hardware video encoders
# ---------------------------------------------------------------------------
NOTE = (
    '        <!--\n'
    '         * ZTE Z981 (urd): the Qualcomm Venus hardware video encoders do\n'
    '         * NOT support OMX.google.android.index.allocateNativeHandle,\n'
    '         * which Android 9\'s OMX HIDL framework requires -> malformed\n'
    '         * native_handle -> SIGSEGV in Parcel::writeNativeHandleNoDup.\n'
    '         * Disabled so the framework uses the software encoders.\n'
    '         -->\n'
)

for path in MEDIA_CODECS:
    if not os.path.exists(path):
        print('  SKIP %s (not found)' % os.path.basename(path))
        continue
    s = open(path).read()
    if 'allocateNativeHandle' in s:
        print('  %s already patched' % os.path.basename(path))
        continue
    start = s.find('    <Encoders>')
    end = s.find('    </Encoders>')
    if start < 0 or end < 0:
        print('  WARNING: Encoders block not found in %s' % path)
        continue
    block = s[start:end]
    newblock = re.sub(
        r'(<MediaCodec name="OMX\.qcom\.video\.encoder\.[^"]*"[^>]*>.*?</MediaCodec>)',
        lambda m: '<!--\n' + m.group(1) + '\n-->',
        block, flags=re.S)
    newblock = newblock.replace('    <Encoders>\n', '    <Encoders>\n' + NOTE, 1)
    s = s[:start] + newblock + s[end:]
    open(path, 'w').write(s)
    print('  patched %s' % os.path.basename(path))
    changed += 1

print('patch-video-encoder: %d change(s)' % changed)
sys.exit(0)
