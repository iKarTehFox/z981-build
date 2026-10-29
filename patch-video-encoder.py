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

FIXES
  1. CameraSource.h   - storeMetaDataInVideoBuffers default true -> false
                        (use VIDEO_BUFFER_MODE_DATA_CALLBACK_YUV, not gralloc)
  2. CameraSource.cpp - force PIXEL_FORMAT_YUV420SP
  2b. CameraSource.cpp - reset the frame-tracking state in start() so a second
                        recording does not inherit the previous recording's
                        mFirstFrameTimeUs (which produced ~10-hour files that
                        would not play back)
  3. MediaCodecSource.cpp - set "max-input-size" from the padded stride x
                        padded height; keep a memcpy clamp as a safety net
    4. SoftAVCEnc.{h,cpp} - use Qualcomm YUV layout: Y stride aligned to 128,
                                                Y scanlines to 32, UV scanlines to 16. The fixed-1920
                                                and 2048 guesses both disagreed with callback size.
  4b. SoftAVCEnc.cpp  - size the conversion buffer from the padded geometry
    4c. SoftVideoEncoderOMXComponent.cpp - give the input port the same aligned
                                                Y/UV geometry and a 16 KiB-rounded capacity.
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
SOFTVIDEOENC_CPP = (SRC + '/frameworks/av/media/libstagefright/omx/'
                    'SoftVideoEncoderOMXComponent.cpp')
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


def edit_any(path, old_versions, new, label):
    """Replace the first matching prior version, or leave the final form alone."""
    global changed
    if not os.path.exists(path):
        print('  SKIP %s (not found: %s)' % (label, path))
        return
    s = open(path).read()
    if new in s:
        print('  %s already patched' % label)
        return
    for old in old_versions:
        if old in s:
            s = s.replace(old, new, 1)
            open(path, 'w').write(s)
            print('  patched %s' % label)
            changed += 1
            return
    print('  WARNING: %s anchor not found in %s' % (label, path))


def remove_once(path, old, label):
    """Remove a stale block once, if it is present."""
    global changed
    if not os.path.exists(path):
        print('  SKIP %s (not found: %s)' % (label, path))
        return
    s = open(path).read()
    if old not in s:
        print('  %s already absent' % label)
        return
    s = s.replace(old, '', 1)
    open(path, 'w').write(s)
    print('  removed %s' % label)
    changed += 1


def remove_regex(path, pattern, label):
    """Remove one narrowly anchored stale block matched by regex."""
    global changed
    if not os.path.exists(path):
        print('  SKIP %s (not found: %s)' % (label, path))
        return
    s = open(path).read()
    updated, count = re.subn(pattern, '\n', s, count=1, flags=re.S)
    if not count:
        print('  %s already absent' % label)
        return
    open(path, 'w').write(updated)
    print('  removed %s' % label)
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

_camera_meta_old = (
    '    mMeta->setInt32(kKeyColorFormat, mColorFormat);\n'
    '    mMeta->setInt32(kKeyWidth,       mVideoSize.width);\n'
    '    mMeta->setInt32(kKeyHeight,      mVideoSize.height);\n'
    '    mMeta->setInt32(kKeyStride,      mVideoSize.width);\n'
    '    mMeta->setInt32(kKeySliceHeight, mVideoSize.height);')
_camera_meta_1920 = (
    '    mMeta->setInt32(kKeyColorFormat, mColorFormat);\n'
    '    mMeta->setInt32(kKeyWidth,       mVideoSize.width);\n'
    '    mMeta->setInt32(kKeyHeight,      mVideoSize.height);\n'
    '    mMeta->setInt32(kKeyStride,      mVideoSize.width < 1920 ? 1920 : mVideoSize.width);\n'
    '    mMeta->setInt32(kKeySliceHeight, (mVideoSize.height + 15) & ~15);')
_camera_meta_new = (
    '    mMeta->setInt32(kKeyColorFormat, mColorFormat);\n'
    '    mMeta->setInt32(kKeyWidth,       mVideoSize.width);\n'
    '    mMeta->setInt32(kKeyHeight,      mVideoSize.height);\n'
    '    mMeta->setInt32(kKeyStride,      (mVideoSize.width + 127) & ~127);\n'
    '    mMeta->setInt32(kKeySliceHeight, (mVideoSize.height + 31) & ~31);')
edit_any(
    CAMERA_SOURCE_CPP,
    [_camera_meta_old, _camera_meta_1920],
    _camera_meta_new,
    'CameraSource.cpp (Qualcomm Y/UV alignment metadata)')

_callback_log_new = (
    '    CHECK(data != NULL && data->size() > 0);\n'
    '    if (mNumFramesReceived == 1) {\n'
    '        ALOGI("Z981 video callback: %dx%d memorySize=%zu stride=%d yScanlines=%d uvScanlines=%d",\n'
    '                mVideoSize.width, mVideoSize.height, data->size(),\n'
    '                (mVideoSize.width + 127) & ~127,\n'
    '                (mVideoSize.height + 31) & ~31,\n'
    '                (mVideoSize.height / 2 + 15) & ~15);\n'
    '    }\n'
    '    mFramesReceived.push_back(data);')
edit_any(
    CAMERA_SOURCE_CPP,
    [
        '    CHECK(data != NULL && data->size() > 0);\n'
        '    mFramesReceived.push_back(data);',
        '    CHECK(data != NULL && data->size() > 0);\n'
        '    if (mNumFramesReceived == 1) {\n'
        '        ALOGI("Z981 video callback: %dx%d memorySize=%zu strideCandidate=%d sliceCandidate=%d",\n'
        '                mVideoSize.width, mVideoSize.height, data->size(),\n'
        '                mVideoSize.width < 1920 ? 1920 : mVideoSize.width,\n'
        '                (mVideoSize.height + 15) & ~15);\n'
        '    }\n'
        '    mFramesReceived.push_back(data);'
    ],
    _callback_log_new,
    'CameraSource.cpp (log aligned callback layout)')

# ---------------------------------------------------------------------------
# 2b. CameraSource.cpp : reset the frame-tracking state in start()
# ---------------------------------------------------------------------------
# These members are only initialised in the constructor, so a second recording
# on the same CameraSource instance inherited the previous recording's
# mFirstFrameTimeUs.  dataCallbackTimestamp() normalises timestamps as
#
#     timeUs = mStartTimeUs + (timestampUs - mFirstFrameTimeUs);
#
# so a stale mFirstFrameTimeUs shifted every frame of the new recording by a
# huge offset.  MPEG4Writer::writeSttsBox() then inflated the first sample's
# duration to cover the gap between frame 1 (at 0) and frame 2 (at the
# offset), producing files that report ~10 hours and do not play back.
#
# Only visible after switching to VIDEO_BUFFER_MODE_DATA_CALLBACK_YUV
# (storeMetaDataInVideoBuffers=false), which uses this code path.
edit(
    CAMERA_SOURCE_CPP,
    '    mStartTimeUs = 0;\n'
    '    mNumInputBuffers = 0;\n'
    '    mEncoderFormat = HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED;\n'
    '    mEncoderDataSpace = HAL_DATASPACE_V0_BT709;\n',
    '    mStartTimeUs = 0;\n'
    '    mNumInputBuffers = 0;\n'
    '    mEncoderFormat = HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED;\n'
    '    mEncoderDataSpace = HAL_DATASPACE_V0_BT709;\n'
    '\n'
    '    /*\n'
    '     * ZTE Z981 (urd): reset the frame-tracking state for this recording.\n'
    '     *\n'
    '     * These are only initialised in the constructor, so a second recording\n'
    '     * on the same CameraSource instance inherited the previous recording\'s\n'
    '     * mFirstFrameTimeUs.  The timestamp normalisation in\n'
    '     * dataCallbackTimestamp() is\n'
    '     *\n'
    '     *     timeUs = mStartTimeUs + (timestampUs - mFirstFrameTimeUs);\n'
    '     *\n'
    '     * so a stale mFirstFrameTimeUs shifted every frame of the new recording\n'
    '     * by a huge offset.  The MPEG4 writer then saw the first sample at 0 and\n'
    '     * the second at that offset, and inflated the first sample\'s duration to\n'
    '     * match -- producing files that report ~10 hours and do not play back.\n'
    '     *\n'
    '     * This only became visible after switching to\n'
    '     * VIDEO_BUFFER_MODE_DATA_CALLBACK_YUV (storeMetaDataInVideoBuffers=false),\n'
    '     * which uses this code path.\n'
    '     */\n'
    '    mNumFramesReceived = 0;\n'
    '    mNumFramesEncoded = 0;\n'
    '    mNumFramesDropped = 0;\n'
    '    mFirstFrameTimeUs = 0;\n'
    '    mLastFrameTimestampUs = 0;\n'
    '    mNumGlitches = 0;\n',
    'CameraSource.cpp (reset frame state in start())')

# ---------------------------------------------------------------------------
# 3a. MediaCodecSource.cpp : size input capacity for the camera callback layout
# ---------------------------------------------------------------------------
_max_input_new = (
    '    AString outputMIME;\n'
    '    CHECK(mOutputFormat->findString("mime", &outputMIME));\n'
    '    mIsVideo = outputMIME.startsWithIgnoreCase("video/");\n'
    '\n'
    '    /* ZTE Z981: reserve capacity for aligned Qualcomm YUV callback planes. */\n'
    '    if (mIsVideo) {\n'
    '        int32_t width = 0, height = 0;\n'
    '        if (mOutputFormat->findInt32("width", &width)\n'
    '                && mOutputFormat->findInt32("height", &height)\n'
    '                && width > 0 && height > 0) {\n'
    '            int32_t stride = (width + 127) & ~127;\n'
    '            int32_t yScanlines = (height + 31) & ~31;\n'
    '            int32_t uvScanlines = (height / 2 + 15) & ~15;\n'
    '            int32_t maxInputSize = stride * (yScanlines + uvScanlines);\n'
    '            maxInputSize = (maxInputSize + 16383) & ~16383;\n'
    '            mOutputFormat->setInt32("max-input-size", maxInputSize);\n'
    '            ALOGI("Z981 max-input-size=%d for %dx%d (stride %d, Y %d, UV %d)",\n'
    '                    maxInputSize, width, height, stride, yScanlines, uvScanlines);\n'
    '        }\n'
    '    }\n')

_max_input_2048_block = (
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
    '    }\n')
remove_once(MEDIACODEC_SOURCE, _max_input_2048_block,
            'obsolete duplicate 2048 max-input-size block')

remove_regex(
    MEDIACODEC_SOURCE,
    r'\n    /\* ZTE Z981: reserve capacity for its fixed 1920-pixel callback stride\. \*/\n'
    r'    if \(mIsVideo\) \{.*?\n    \}\n',
    'obsolete duplicate fixed-1920 max-input-size block')

edit_any(
    MEDIACODEC_SOURCE,
    [
        '    AString outputMIME;\n'
        '    CHECK(mOutputFormat->findString("mime", &outputMIME));\n'
        '    mIsVideo = outputMIME.startsWithIgnoreCase("video/");\n'
        '\n'
        '    /*\n'
        '     * ZTE Z981 (urd): reserve input capacity for the callback layout,\n'
        '     * which uses a fixed 1920-pixel stride and 16-line slice alignment.\n'
        '     */\n'
        '    if (mIsVideo) {\n'
        '        int32_t width = 0, height = 0;\n'
        '        if (mOutputFormat->findInt32("width", &width)\n'
        '                && mOutputFormat->findInt32("height", &height)\n'
        '                && width > 0 && height > 0) {\n'
        '            int32_t stride = width < 1920 ? 1920 : width;\n'
        '            int32_t paddedHeight = (height + 15) & ~15;\n'
        '            int32_t maxInputSize = stride * paddedHeight * 3 / 2;\n'
        '            mOutputFormat->setInt32("max-input-size", maxInputSize);\n'
        '            ALOGI("Z981 max-input-size=%d for %dx%d (camera stride %d, slice %d)",\n'
        '                    maxInputSize, width, height, stride, paddedHeight);\n'
        '        }\n'
        '    }\n',
        '    AString outputMIME;\n'
        '    CHECK(mOutputFormat->findString("mime", &outputMIME));\n'
        '    mIsVideo = outputMIME.startsWithIgnoreCase("video/");\n',
    ],
    _max_input_new,
    'MediaCodecSource (1920 callback stride capacity)')

edit(
    SOFTAVC_CPP,
    '        case OMX_COLOR_FormatYUV420SemiPlanar:\n'
    '            mIvVideoColorFormat = IV_YUV_420SP_UV;\n'
    '            ALOGV("colorFormat YUV_420SP");\n'
    '            break;',
    '        case OMX_COLOR_FormatYUV420SemiPlanar:\n'
    '            /* ZTE camera PIXEL_FORMAT_YUV420SP is NV21 (VU order). */\n'
    '            mIvVideoColorFormat = IV_YUV_420SP_VU;\n'
    '            ALOGV("colorFormat YUV_420SP (NV21/VU)");\n'
    '            break;',
    'SoftAVCEnc.cpp (NV21/VU color order)')

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
# 4b. SoftAVCEnc.cpp : use the Z981 callback stride/slice-height
# ---------------------------------------------------------------------------
# The device's 1080p callback is the known-good layout. Smaller frames show
# progressive horizontal shear when the encoder ignores Qualcomm's per-plane
# alignment. The measured 1280x720 callback size matches Y stride ALIGN(w,128),
# Y scanlines ALIGN(h,32), UV scanlines ALIGN(h/2,16), total rounded to 16 KiB.
#
#     stride      = (width + 127) & ~127
#     sliceHeight = (height + 31) & ~31
#
_avc_geometry_old = (
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
    '    mSliceHeight = (mHeight + 15) & ~15;\n')
_avc_geometry_2048 = (
    '    mAVCEncLevel = MAX(level, mAVCEncLevel);\n'
    '\n'
    '    /*\n'
    '     * ZTE Z981 (urd): the camera delivers padded YUV buffers, and\n'
    '     * MediaCodecSource advertises the padded geometry to the encoder via\n'
    '     * "max-input-size" using\n'
    '     *\n'
    '     *     stride      = (width  + 2047) & ~2047, minimum 2048\n'
    '     *     sliceHeight = (height +   31) & ~31\n'
    '     *\n'
    '     * The encoder MUST use the same geometry, otherwise it reads the\n'
    '     * chroma plane from the wrong offset and the frame is sheared /\n'
    '     * shows a green band.  Previously mStride was just mWidth, which\n'
    '     * happened to match only for 1080p (1920 -> 2048 is the same buffer\n'
    '     * layout the camera uses there), so every lower resolution was\n'
    '     * corrupted.\n'
    '     */\n'
    '    mStride = (mWidth + 2047) & ~2047;\n'
    '    if (mStride < 2048) {\n'
    '        mStride = 2048;\n'
    '    }\n'
    '    mSliceHeight = (mHeight + 31) & ~31;\n')
_avc_geometry_new = (
    '    mAVCEncLevel = MAX(level, mAVCEncLevel);\n'
    '\n'
    '    /* ZTE Z981: match Qualcomm callback Y stride and scanline alignment. */\n'
    '    mStride = (mWidth + 127) & ~127;\n'
    '    mSliceHeight = (mHeight + 31) & ~31;\n')
_avc_geometry_1920 = (
    '    mAVCEncLevel = MAX(level, mAVCEncLevel);\n'
    '\n'
    '    /* ZTE Z981: camera YUV callback uses a fixed 1920-pixel stride. */\n'
    '    mStride = mWidth < 1920 ? 1920 : mWidth;\n'
    '    mSliceHeight = (mHeight + 15) & ~15;\n')
# Pristine upstream text, for a freshly synced tree. This MUST stay last in the
# list: it is a prefix of _avc_geometry_old, so matching it earlier would leave
# the stale comment and the mSliceHeight assignment behind.
_avc_geometry_pristine = (
    '    mAVCEncLevel = MAX(level, mAVCEncLevel);\n'
    '\n'
    '    mStride = mWidth;\n')
edit_any(
    SOFTAVC_CPP,
    [_avc_geometry_old, _avc_geometry_2048, _avc_geometry_1920,
     _avc_geometry_pristine],
    _avc_geometry_new,
    'SoftAVCEnc.cpp (fixed callback stride/slice height)')

# The 2048-stride experiment changed conversion allocation too. Keep this
# consistent with the corrected plane geometry and handle pristine sources.
edit_any(
    SOFTAVC_CPP,
    [
        '            if (((uint64_t)mStride * mHeight) > ((uint64_t)INT32_MAX / 3)) {\n'
        '                ALOGE("Buffer size is too big.");\n'
        '                return OMX_ErrorUndefined;\n'
        '            }\n'
        '            mConversionBuffers[i] = (uint8_t *)malloc(mStride * mHeight * 3 / 2);',
        '            /*\n'
        '             * ZTE Z981 (urd): size the conversion buffer from the padded\n'
        '             * geometry (mStride x mSliceHeight), matching what the camera\n'
        '             * sends and what the plane offsets below assume.\n'
        '             */\n'
        '            if (((uint64_t)mStride * mSliceHeight) > ((uint64_t)INT32_MAX / 3)) {\n'
        '                ALOGE("Buffer size is too big.");\n'
        '                return OMX_ErrorUndefined;\n'
        '            }\n'
        '            mConversionBuffers[i] = (uint8_t *)malloc(mStride * mSliceHeight * 3 / 2);'
    ],
    '            /* ZTE Z981: conversion buffer uses callback stride/slice. */\n'
    '            if (((uint64_t)mStride * mSliceHeight) > ((uint64_t)INT32_MAX / 3)) {\n'
    '                ALOGE("Buffer size is too big.");\n'
    '                return OMX_ErrorUndefined;\n'
    '            }\n'
    '            mConversionBuffers[i] = (uint8_t *)malloc(mStride * mSliceHeight * 3 / 2);',
    'SoftAVCEnc.cpp (conversion buffer uses callback geometry)')

# ---------------------------------------------------------------------------
# 4b3. SoftVideoEncoderOMXComponent.cpp : callback geometry in input port
# ---------------------------------------------------------------------------
_input_port_old = (
    '    OMX_PARAM_PORTDEFINITIONTYPE *inDef = &editPortInfo(kInputPortIndex)->mDef;\n'
    '    inDef->format.video.nFrameWidth = mWidth;\n'
    '    inDef->format.video.nFrameHeight = mHeight;\n'
    '    inDef->format.video.nStride = inDef->format.video.nFrameWidth;\n'
    '    inDef->format.video.nSliceHeight = inDef->format.video.nFrameHeight;\n'
    '    inDef->format.video.xFramerate = mFramerate;\n'
    '    inDef->format.video.eColorFormat = mColorFormat;\n'
    '    uint32_t rawBufferSize =\n'
    '        inDef->format.video.nStride * inDef->format.video.nSliceHeight * 3 / 2;')
_input_port_2048 = (
    '    OMX_PARAM_PORTDEFINITIONTYPE *inDef = &editPortInfo(kInputPortIndex)->mDef;\n'
    '    inDef->format.video.nFrameWidth = mWidth;\n'
    '    inDef->format.video.nFrameHeight = mHeight;\n'
    '    /*\n'
    '     * ZTE Z981 (urd): the camera delivers padded YUV buffers, and\n'
    '     * MediaCodecSource advertises the padded geometry via "max-input-size"\n'
    '     * using\n'
    '     *\n'
    '     *     stride      = (width  + 2047) & ~2047, minimum 2048\n'
    '     *     sliceHeight = (height +   31) & ~31\n'
    '     *\n'
    '     * The input port must be at least that large, otherwise the padded\n'
    '     * buffer the camera sends does not fit and the encoder reads the\n'
    '     * chroma plane from the wrong offset (sheared frames, green band).\n'
    '     * Use the same formula here so the port and the encoder agree.\n'
    '     */\n'
    '    uint32_t paddedStride = (mWidth + 2047) & ~2047;\n'
    '    if (paddedStride < 2048) {\n'
    '        paddedStride = 2048;\n'
    '    }\n'
    '    uint32_t paddedSliceHeight = (mHeight + 31) & ~31;\n'
    '    inDef->format.video.nStride = paddedStride;\n'
    '    inDef->format.video.nSliceHeight = paddedSliceHeight;\n'
    '    inDef->format.video.xFramerate = mFramerate;\n'
    '    inDef->format.video.eColorFormat = mColorFormat;\n'
    '    uint32_t rawBufferSize =\n'
    '        inDef->format.video.nStride * inDef->format.video.nSliceHeight * 3 / 2;')
_input_port_1920 = (
    '    OMX_PARAM_PORTDEFINITIONTYPE *inDef = &editPortInfo(kInputPortIndex)->mDef;\n'
    '    inDef->format.video.nFrameWidth = mWidth;\n'
    '    inDef->format.video.nFrameHeight = mHeight;\n'
    '    /* ZTE Z981: legacy camera callback uses max(width, 1920), slice aligned 16. */\n'
    '    uint32_t callbackStride = mWidth < 1920 ? 1920 : mWidth;\n'
    '    uint32_t callbackSliceHeight = (mHeight + 15) & ~15;\n'
    '    inDef->format.video.nStride = callbackStride;\n'
    '    inDef->format.video.nSliceHeight = callbackSliceHeight;\n'
    '    inDef->format.video.xFramerate = mFramerate;\n'
    '    inDef->format.video.eColorFormat = mColorFormat;\n'
    '    uint32_t rawBufferSize =\n'
    '        inDef->format.video.nStride * inDef->format.video.nSliceHeight * 3 / 2;')
_input_port_new = (
    '    OMX_PARAM_PORTDEFINITIONTYPE *inDef = &editPortInfo(kInputPortIndex)->mDef;\n'
    '    inDef->format.video.nFrameWidth = mWidth;\n'
    '    inDef->format.video.nFrameHeight = mHeight;\n'
    '    /* ZTE Z981: Qualcomm YUV callback plane alignment. */\n'
    '    uint32_t callbackStride = (mWidth + 127) & ~127;\n'
    '    uint32_t callbackSliceHeight = (mHeight + 31) & ~31;\n'
    '    uint32_t callbackUvSliceHeight = (mHeight / 2 + 15) & ~15;\n'
    '    inDef->format.video.nStride = callbackStride;\n'
    '    inDef->format.video.nSliceHeight = callbackSliceHeight;\n'
    '    inDef->format.video.xFramerate = mFramerate;\n'
    '    inDef->format.video.eColorFormat = mColorFormat;\n'
    '    uint32_t rawBufferSize = callbackStride * (callbackSliceHeight + callbackUvSliceHeight);\n'
    '    rawBufferSize = (rawBufferSize + 16383) & ~16383;')
edit_any(
    SOFTVIDEOENC_CPP,
    [_input_port_old, _input_port_2048, _input_port_1920],
    _input_port_new,
    'SoftVideoEncoderOMXComponent.cpp (callback input geometry)')

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
