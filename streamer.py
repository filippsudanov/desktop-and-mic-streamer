"""
GStreamer capture pipeline + NDI SDK sender.

Video path: ximagesrc / pipewiresrc → UYVY → appsink → NDIlib_send_send_video_v2
Audio path: pulsesrc → F32LE interleaved → deinterleave in Python → NDIlib_send_send_audio_v2

Muting is handled by zeroing audio in the GStreamer `volume` element so that
NDI receivers always get a continuous (possibly silent) audio stream.
"""

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

import ctypes
import os
import sys
import threading

import numpy as np

import ndi

Gst.init(None)

# ── Constants ─────────────────────────────────────────────────────────────────
SAMPLE_RATE = 48000
CHANNELS    = 2

# GStreamer format string → (NDI FourCC, bytes-per-pixel)
_VIDEO_FORMAT_MAP = {
    'BGRx': (ndi.FOURCC_BGRX, 4),
    'BGRA': (ndi.FOURCC_BGRA, 4),
    'RGBx': (ndi.FOURCC_RGBX, 4),
    'RGBA': (ndi.FOURCC_RGBA, 4),
    'UYVY': (ndi.FOURCC_UYVY, 2),
}


class NDIStreamer:
    def __init__(self, ndi_name: str = 'Desktop Streamer', on_tally_change=None):
        """
        ndi_name:         NDI source name visible on the network
        on_tally_change:  callable(on_program: bool, on_preview: bool)
                          called on GLib main loop when tally state changes
        """
        self._ndi_name         = ndi_name
        self._on_tally_change  = on_tally_change
        self._ndi_instance     = None
        self._pipeline         = None
        self._running          = False
        self._last_tally       = (False, False)
        self._tally_thread     = None

    # ── Public interface ──────────────────────────────────────────────────────

    def start(self, pipewire_fd: int = None, pipewire_node_id: int = None):
        """
        Start streaming.

        For Wayland, pass pipewire_fd and pipewire_node_id obtained from
        portal.ScreenCastPortal.  For X11 leave both as None.
        """
        ndi.initialize()
        self._ndi_instance = ndi.send_create(self._ndi_name)

        pipeline_str = self._build_pipeline_str(pipewire_fd, pipewire_node_id)
        self._pipeline = Gst.parse_launch(pipeline_str)

        self._pipeline.get_by_name('video_sink').connect(
            'new-sample', self._on_video_sample)
        self._pipeline.get_by_name('audio_sink').connect(
            'new-sample', self._on_audio_sample)

        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::error', self._on_gst_error)
        bus.connect('message::warning', self._on_gst_warning)

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            # Poll the bus briefly to get the actual error before tearing down.
            msg = bus.timed_pop_filtered(
                500 * Gst.MSECOND,
                Gst.MessageType.ERROR,
            )
            detail = ''
            if msg:
                err, dbg = msg.parse_error()
                detail = f': {err.message}'
                if dbg:
                    print(f'[GStreamer debug] {dbg}', file=sys.stderr)
            raise RuntimeError(f"GStreamer pipeline failed to start{detail}")

        self._running = True
        self._tally_thread = threading.Thread(
            target=self._tally_loop, daemon=True, name='ndi-tally')
        self._tally_thread.start()

    def stop(self):
        self._running = False
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        if self._ndi_instance:
            ndi.send_destroy(self._ndi_instance)
            self._ndi_instance = None
        ndi.destroy()

    def set_mute(self, muted: bool):
        """Mute / unmute microphone.  Thread-safe."""
        vol = self._pipeline and self._pipeline.get_by_name('mic_volume')
        if vol:
            vol.set_property('volume', 0.0 if muted else 1.0)

    # ── Pipeline construction ─────────────────────────────────────────────────

    def _build_pipeline_str(self, pw_fd, pw_node_id) -> str:
        session_type = os.environ.get('XDG_SESSION_TYPE', '').lower()

        if session_type == 'wayland' and pw_fd is not None:
            # videoconvert + explicit capsfilter normalises whatever raw format
            # pipewiresrc negotiates (RGBA, BGRx, NV12, …) to BGRx so the
            # downstream appsink always gets a known format.
            video_src = (
                f'pipewiresrc fd={pw_fd} path={pw_node_id} '
                f'do-timestamp=true ! '
                f'videoconvert ! video/x-raw,format=BGRx'
            )
        else:
            display = os.environ.get('DISPLAY', ':0')
            # use-damage=false: capture full frame every tick (lower CPU than
            # tracking damage regions, which requires extra compositor round-trips)
            video_src = (
                f'ximagesrc display-name={display} '
                f'use-damage=false do-timestamp=true'
            )

        return f"""
            {video_src} !
            queue max-size-buffers=2 leaky=downstream !
            appsink name=video_sink emit-signals=true sync=false
                    max-buffers=2 drop=true

            pulsesrc do-timestamp=true !
            volume name=mic_volume volume=0.0 !
            audioconvert !
            audio/x-raw,format=F32LE,channels={CHANNELS},
                rate={SAMPLE_RATE},layout=interleaved !
            queue max-size-buffers=4 leaky=downstream !
            appsink name=audio_sink emit-signals=true sync=false
                    max-buffers=4 drop=false
        """

    # ── GStreamer callbacks (called from GStreamer streaming threads) ──────────

    def _on_video_sample(self, sink) -> Gst.FlowReturn:
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.ERROR

        buf    = sample.get_buffer()
        struct = sample.get_caps().get_structure(0)
        width   = struct.get_int('width').value
        height  = struct.get_int('height').value
        fmt     = struct.get_string('format')
        fps     = struct.get_fraction('framerate')
        fps_n   = fps.value_numerator
        fps_d   = fps.value_denominator

        fourcc, bpp = _VIDEO_FORMAT_MAP.get(fmt, (ndi.FOURCC_BGRX, 4))

        ok, minfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            frame_np = np.frombuffer(minfo.data, dtype=np.uint8)
            frame = ndi.VideoFrameV2(
                xres=width,
                yres=height,
                FourCC=fourcc,
                frame_rate_N=fps_n,
                frame_rate_D=fps_d,
                picture_aspect_ratio=float(width) / float(height),
                frame_format_type=ndi.FRAME_FORMAT_PROGRESSIVE,
                timecode=ndi.TIMECODE_SYNTHESIZE,
                p_data=ctypes.cast(frame_np.ctypes.data, ctypes.c_void_p),
                line_stride_in_bytes=width * bpp,
                p_metadata=None,
                timestamp=0,
            )
            ndi.send_video(self._ndi_instance, frame)
        finally:
            buf.unmap(minfo)

        return Gst.FlowReturn.OK

    def _on_audio_sample(self, sink) -> Gst.FlowReturn:
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.ERROR

        buf    = sample.get_buffer()
        struct = sample.get_caps().get_structure(0)
        rate   = struct.get_int('rate').value
        chans  = struct.get_int('channels').value

        ok, minfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            # GStreamer gives us interleaved float32: [L,R,L,R,...]
            # NDI send_audio_v2 wants planar float32:  [L,L,...,R,R,...]
            interleaved = np.frombuffer(minfo.data, dtype=np.float32)
            no_samples  = len(interleaved) // chans
            planar      = np.ascontiguousarray(
                interleaved.reshape(no_samples, chans).T
            )

            frame = ndi.AudioFrameV2(
                sample_rate=rate,
                no_channels=chans,
                no_samples=no_samples,
                timecode=ndi.TIMECODE_SYNTHESIZE,
                p_data=ctypes.cast(planar.ctypes.data, ctypes.c_void_p),
                channel_stride_in_bytes=no_samples * 4,  # float32 = 4 bytes
            )
            ndi.send_audio(self._ndi_instance, frame)
        finally:
            buf.unmap(minfo)

        return Gst.FlowReturn.OK

    def _on_gst_error(self, _bus, message):
        err, dbg = message.parse_error()
        print(f"[GStreamer ERROR] {err.message}  ({dbg})")

    def _on_gst_warning(self, _bus, message):
        warn, dbg = message.parse_warning()
        print(f"[GStreamer WARNING] {warn.message}  ({dbg})")

    # ── Tally polling (background thread) ─────────────────────────────────────

    def _tally_loop(self):
        """
        Poll tally state from NDI send instance.
        The SDK blocks for up to `timeout_ms` waiting for a tally change,
        so this loop is cheap — it only wakes when state actually changes
        or times out.
        """
        while self._running:
            if self._ndi_instance is None:
                continue
            tally = ndi.get_tally(self._ndi_instance, timeout_ms=500)
            state = (tally.on_program, tally.on_preview)
            if state != self._last_tally:
                self._last_tally = state
                if self._on_tally_change:
                    GLib.idle_add(
                        self._on_tally_change,
                        tally.on_program,
                        tally.on_preview,
                    )
