"""
XDG ScreenCast portal client for Wayland screen capture.

Drives the org.freedesktop.portal.ScreenCast D-Bus interface and returns a
PipeWire (fd, node_id) pair via callbacks, suitable for use with pipewiresrc.

Usage:
    portal = ScreenCastPortal(on_ready=my_ready_cb, on_error=my_error_cb)
    portal.request()   # call from GTK main loop; callbacks fire on GLib mainloop
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gio, GLib

import random
import string

_BUS_NAME   = 'org.freedesktop.portal.Desktop'
_OBJ_PATH   = '/org/freedesktop/portal/desktop'
_SC_IFACE   = 'org.freedesktop.portal.ScreenCast'
_REQ_IFACE  = 'org.freedesktop.portal.Request'

# SelectSources flags
_TYPE_MONITOR = 1
_CURSOR_EMBEDDED = 2


def _token():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))


class ScreenCastPortal:
    def __init__(self, on_ready, on_error):
        """
        on_ready(fd: int, node_id: int) – PipeWire remote fd + stream node id
        on_error(msg: str)
        """
        self._on_ready = on_ready
        self._on_error = on_error
        self._conn = None
        self._sender = None          # sanitised D-Bus sender name
        self._session_path = None
        self._subs = []              # signal subscription ids (cleanup)

    # ── Entry point ───────────────────────────────────────────────────────────

    def request(self):
        """Start the async portal flow. Safe to call from GLib main loop."""
        try:
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            self._error(f"D-Bus connect failed: {e}")
            return
        # Sender name: ':1.123' → '1_123'  (used in request handle paths)
        self._sender = (
            self._conn.get_unique_name()
            .lstrip(':')
            .replace('.', '_')
        )
        self._create_session()

    # ── Step 1: CreateSession ─────────────────────────────────────────────────

    def _create_session(self):
        tok = _token()
        sess_tok = _token()
        self._watch_request(tok, self._on_create_session)
        self._call('CreateSession', GLib.Variant('(a{sv})', ({
            'handle_token':         GLib.Variant('s', tok),
            'session_handle_token': GLib.Variant('s', sess_tok),
        },)))

    def _on_create_session(self, _conn, _sender, _path, _iface, _sig, params):
        response, results = params.unpack()
        if response != 0:
            self._error(f"CreateSession denied (response={response})")
            return
        self._session_path = results.get('session_handle')
        self._select_sources()

    # ── Step 2: SelectSources ─────────────────────────────────────────────────

    def _select_sources(self):
        tok = _token()
        self._watch_request(tok, self._on_select_sources)
        self._call('SelectSources', GLib.Variant('(oa{sv})', (
            self._session_path,
            {
                'handle_token': GLib.Variant('s', tok),
                'types':        GLib.Variant('u', _TYPE_MONITOR),
                'multiple':     GLib.Variant('b', False),
                'cursor_mode':  GLib.Variant('u', _CURSOR_EMBEDDED),
            },
        )))

    def _on_select_sources(self, _conn, _sender, _path, _iface, _sig, params):
        response, _ = params.unpack()
        if response != 0:
            self._error(f"SelectSources denied (response={response})")
            return
        self._start()

    # ── Step 3: Start ─────────────────────────────────────────────────────────

    def _start(self):
        tok = _token()
        self._watch_request(tok, self._on_start)
        self._call('Start', GLib.Variant('(osa{sv})', (
            self._session_path,
            '',  # parent window handle (empty = no parent)
            {'handle_token': GLib.Variant('s', tok)},
        )))

    def _on_start(self, _conn, _sender, _path, _iface, _sig, params):
        response, results = params.unpack()
        if response != 0:
            self._error(f"Start denied (response={response})")
            return
        streams = results.get('streams', [])
        if not streams:
            self._error("Portal returned no streams")
            return
        node_id, _props = streams[0]
        self._open_pipewire_remote(node_id)

    # ── Step 4: OpenPipeWireRemote ────────────────────────────────────────────

    def _open_pipewire_remote(self, node_id):
        self._node_id = node_id
        self._conn.call_with_unix_fd_list(
            _BUS_NAME, _OBJ_PATH, _SC_IFACE,
            'OpenPipeWireRemote',
            GLib.Variant('(oa{sv})', (self._session_path, {})),
            GLib.VariantType('(h)'),
            Gio.DBusCallFlags.NONE,
            -1, None, None,
            self._on_pipewire_remote,
        )

    def _on_pipewire_remote(self, conn, result):
        try:
            ret, fd_list = conn.call_with_unix_fd_list_finish(result)
            fd_index = ret.get_child_value(0).get_handle()
            fd = fd_list.get(fd_index)
            self._cleanup_subs()
            self._on_ready(fd, self._node_id)
        except Exception as e:
            self._error(f"OpenPipeWireRemote failed: {e}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _call(self, method, params):
        self._conn.call(
            _BUS_NAME, _OBJ_PATH, _SC_IFACE, method,
            params, None,
            Gio.DBusCallFlags.NONE, -1, None,
            lambda *_: None,  # fire-and-forget; results come via Response signal
        )

    def _watch_request(self, token, callback):
        handle = (
            f'/org/freedesktop/portal/desktop/request'
            f'/{self._sender}/{token}'
        )
        sub = self._conn.signal_subscribe(
            _BUS_NAME, _REQ_IFACE, 'Response', handle,
            None, Gio.DBusSignalFlags.NONE, callback,
        )
        self._subs.append(sub)

    def _cleanup_subs(self):
        for sub in self._subs:
            self._conn.signal_unsubscribe(sub)
        self._subs.clear()

    def _error(self, msg):
        self._cleanup_subs()
        self._on_error(msg)
