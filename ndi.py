"""
NDI SDK ctypes bindings.

Requires NDI SDK installed: https://www.ndi.tv/sdk/
Library search order: libndi.so.6, libndi.so.5, libndi.so
"""

import ctypes
import os

_LIB_NAMES = ['libndi.so.6', 'libndi.so.5', 'libndi.so']
_LIB_PATHS = [
    # portable: lib/ next to this file (set LD_LIBRARY_PATH via run.sh too)
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'),
    '/usr/lib', '/usr/local/lib', '/opt/ndi/lib',
]

# ── FourCC constants ──────────────────────────────────────────────────────────
FOURCC_UYVY = 0x59565955   # video: UYVY 4:2:2
FOURCC_BGRA = 0x41524742   # video: BGRA 8:8:8:8
FOURCC_BGRX = 0x58524742   # video: BGRx 8:8:8:x
FOURCC_RGBA = 0x41424752   # video: RGBA 8:8:8:8
FOURCC_RGBX = 0x58424752   # video: RGBx 8:8:8:x
FOURCC_FLTP = 0x50544C46   # audio: 32-bit float planar

# ── Frame-format constants ────────────────────────────────────────────────────
FRAME_FORMAT_PROGRESSIVE = 1

# ── Timecode: let SDK synthesize ──────────────────────────────────────────────
TIMECODE_SYNTHESIZE = ctypes.c_int64(-1).value


# ── Structures ────────────────────────────────────────────────────────────────

class Tally(ctypes.Structure):
    _fields_ = [
        ('on_program', ctypes.c_bool),
        ('on_preview', ctypes.c_bool),
    ]


class SendCreate(ctypes.Structure):
    _fields_ = [
        ('p_ndi_name',   ctypes.c_char_p),
        ('p_groups',     ctypes.c_char_p),
        ('clock_video',  ctypes.c_bool),
        ('clock_audio',  ctypes.c_bool),
    ]


class VideoFrameV2(ctypes.Structure):
    _fields_ = [
        ('xres',                 ctypes.c_int),
        ('yres',                 ctypes.c_int),
        ('FourCC',               ctypes.c_uint),
        ('frame_rate_N',         ctypes.c_int),
        ('frame_rate_D',         ctypes.c_int),
        ('picture_aspect_ratio', ctypes.c_float),
        ('frame_format_type',    ctypes.c_int),
        ('timecode',             ctypes.c_int64),
        ('p_data',               ctypes.c_void_p),
        ('line_stride_in_bytes', ctypes.c_int),
        ('p_metadata',           ctypes.c_char_p),
        ('timestamp',            ctypes.c_int64),
    ]


class AudioFrameV2(ctypes.Structure):
    """Float-planar audio frame (channels stored consecutively, not interleaved)."""
    _fields_ = [
        ('sample_rate',            ctypes.c_int),
        ('no_channels',            ctypes.c_int),
        ('no_samples',              ctypes.c_int),
        ('timecode',                ctypes.c_int64),
        ('p_data',                  ctypes.c_void_p),
        ('channel_stride_in_bytes', ctypes.c_int),
        ('p_metadata',              ctypes.c_char_p),
        ('timestamp',               ctypes.c_int64),
    ]


# ── Library loading ───────────────────────────────────────────────────────────

def _load():
    for name in _LIB_NAMES:
        try:
            return ctypes.CDLL(name)
        except OSError:
            pass
    for base in _LIB_PATHS:
        for name in _LIB_NAMES:
            path = os.path.join(base, name)
            if os.path.exists(path):
                return ctypes.CDLL(path)
    raise RuntimeError(
        "NDI SDK library not found.\n"
        "Download and install from https://www.ndi.tv/sdk/\n"
        "Then run: sudo ldconfig"
    )


_lib = None


def _get():
    global _lib
    if _lib is None:
        _lib = _load()
        _configure(_lib)
    return _lib


def _configure(lib):
    """Set argtypes/restype once after loading."""
    lib.NDIlib_initialize.restype = ctypes.c_bool
    lib.NDIlib_initialize.argtypes = []

    lib.NDIlib_destroy.restype = None
    lib.NDIlib_destroy.argtypes = []

    lib.NDIlib_send_create.restype = ctypes.c_void_p
    lib.NDIlib_send_create.argtypes = [ctypes.POINTER(SendCreate)]

    lib.NDIlib_send_destroy.restype = None
    lib.NDIlib_send_destroy.argtypes = [ctypes.c_void_p]

    lib.NDIlib_send_send_video_v2.restype = None
    lib.NDIlib_send_send_video_v2.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(VideoFrameV2)]

    lib.NDIlib_send_send_audio_v2.restype = None
    lib.NDIlib_send_send_audio_v2.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(AudioFrameV2)]

    lib.NDIlib_send_get_tally.restype = ctypes.c_bool
    lib.NDIlib_send_get_tally.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(Tally), ctypes.c_uint]


# ── Public API ────────────────────────────────────────────────────────────────

def initialize():
    if not _get().NDIlib_initialize():
        raise RuntimeError("NDIlib_initialize() failed")


def destroy():
    if _lib:
        _lib.NDIlib_destroy()


def send_create(name: str, clock_video=True, clock_audio=True) -> int:
    """Returns opaque NDI send instance pointer (int)."""
    desc = SendCreate(
        p_ndi_name=name.encode(),
        p_groups=None,
        clock_video=clock_video,
        clock_audio=clock_audio,
    )
    instance = _get().NDIlib_send_create(ctypes.byref(desc))
    if not instance:
        raise RuntimeError(f"NDIlib_send_create('{name}') failed")
    return instance


def send_destroy(instance: int):
    _get().NDIlib_send_destroy(instance)


def send_video(instance: int, frame: VideoFrameV2):
    _get().NDIlib_send_send_video_v2(instance, ctypes.byref(frame))


def send_audio(instance: int, frame: AudioFrameV2):
    _get().NDIlib_send_send_audio_v2(instance, ctypes.byref(frame))


def get_tally(instance: int, timeout_ms: int = 0) -> Tally:
    tally = Tally()
    _get().NDIlib_send_get_tally(instance, ctypes.byref(tally), timeout_ms)
    return tally
