#!/usr/bin/env python3
"""
NDI Desktop Streamer
====================
Streams the desktop (video) + microphone (audio) over NDI.
System-tray icon doubles as a tally light with four states:
  Tally light (left):  gray=standby  green=preview  red=on-air  amber=both
  Mic indicator (right): green=live  gray+slash=muted
  Left-click icon → toggle mute

Run:
    python3 main.py [--name "My Source"]

Requirements: see INSTALL
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

import argparse
import os
import signal
import sys

from streamer import NDIStreamer
from tray     import TrayIcon
from portal   import ScreenCastPortal


def parse_args():
    p = argparse.ArgumentParser(description='NDI Desktop Streamer')
    p.add_argument(
        '--name', default='Desktop Streamer',
        help='NDI source name visible on the network (default: "Desktop Streamer")')
    return p.parse_args()


class App:
    def __init__(self, ndi_name: str):
        self._ndi_name = ndi_name
        self._streamer = NDIStreamer(
            ndi_name=ndi_name,
            on_tally_change=self._on_tally_change,
        )
        self._tray = TrayIcon(
            on_mute_toggle=self._on_mute_toggle,
            on_quit=self._quit,
        )

    # ── Startup ───────────────────────────────────────────────────────────────

    def launch(self):
        """Called from GLib.idle_add — runs after GTK main loop is up."""
        session = os.environ.get('XDG_SESSION_TYPE', '').lower()
        if session == 'wayland':
            self._launch_wayland()
        else:
            self._launch_x11()

    def _launch_x11(self):
        try:
            self._streamer.start()
            self._tray.set_live(True)
        except Exception as e:
            self._fatal(f"Failed to start pipeline: {e}")

    def _launch_wayland(self):
        """Request screen-cast permission via XDG portal, then start."""
        portal = ScreenCastPortal(
            on_ready=self._on_portal_ready,
            on_error=self._on_portal_error,
        )
        portal.request()
        # Portal flow is async; _on_portal_ready / _on_portal_error fire later.

    def _on_portal_ready(self, pw_fd: int, pw_node_id: int):
        try:
            self._streamer.start(pipewire_fd=pw_fd, pipewire_node_id=pw_node_id)
            self._tray.set_live(True)
        except Exception as e:
            self._fatal(f"Failed to start pipeline: {e}")

    def _on_portal_error(self, msg: str):
        self._fatal(f"ScreenCast portal error: {msg}")

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_tally_change(self, on_program: bool, on_preview: bool):
        """Fired on GLib main loop by NDIStreamer._tally_loop."""
        self._tray.set_tally(on_program, on_preview)

    def _on_mute_toggle(self, muted: bool):
        self._streamer.set_mute(muted)

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def _quit(self):
        self._streamer.stop()
        Gtk.main_quit()

    def _fatal(self, msg: str):
        print(f"[ERROR] {msg}", file=sys.stderr)
        self._quit()


def main():
    args = parse_args()

    # Let Ctrl-C propagate to the default SIGINT handler so the process exits
    # cleanly instead of being swallowed by the GLib main loop.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = App(ndi_name=args.name)
    GLib.idle_add(app.launch)
    Gtk.main()


if __name__ == '__main__':
    main()
