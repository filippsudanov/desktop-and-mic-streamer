"""
Two separate system-tray icons:
  1. Tally light  – film-leader style: dotted rings, black fill none/210°/full
  2. Mic status   – mic shape, green (live) or gray + red slash (muted)

Left-click either icon  → toggle mute
Right-click either icon → context menu (status label + Quit)
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

import os
import tempfile
from PIL import Image, ImageDraw

_SIZE     = 64
_ICON_DIR = tempfile.mkdtemp(prefix='ndi_tray_')

# ── Tally labels ──────────────────────────────────────────────────────────────
_TALLY_LABELS = {
    'off':     'Standby',
    'preview': 'In Preview',
    'program': 'ON AIR',
}


# ── Icon generators ───────────────────────────────────────────────────────────

def _tally_icon(state: str) -> str:
    path = os.path.join(_ICON_DIR, f'tally_{state}.png')
    img  = Image.new('RGBA', (_SIZE, _SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx = cy  = _SIZE // 2
    r_outer  = _SIZE // 2 - 3
    r_inner  = _SIZE // 2 - 11
    lw       = max(2, _SIZE // 22)
    bbox_out = [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer]

    # Black background fill — none / 210° wedge / full circle
    if state == 'program':
        draw.ellipse(bbox_out, fill=(0, 0, 0, 255))
    elif state == 'preview':
        # 210° clockwise starting from top (-90° in PIL coords)
        draw.pieslice(bbox_out, start=-90, end=120, fill=(0, 0, 0, 255))

    # Dotted concentric circles: 16 short arcs per ring.
    # Dot is white where it sits over the black fill, black elsewhere.
    n_dots   = 16
    dot_span = 8          # degrees each dot occupies
    step     = 360 / n_dots

    def _dot_color(a0):
        if state == 'program':
            return (255, 255, 255, 255)
        if state == 'preview':
            # Fill covers 270°–360° and 0°–120° (≡ start=-90, end=120 in PIL)
            a = a0 % 360
            if a >= 270 or a <= 120:
                return (255, 255, 255, 255)
        return (0, 0, 0, 255)

    for r in (r_outer, r_inner):
        bbox = [cx - r, cy - r, cx + r, cy + r]
        for i in range(n_dots):
            a0 = -90 + i * step
            draw.arc(bbox, start=a0, end=a0 + dot_span,
                     fill=_dot_color(a0), width=lw)

    img.save(path)
    return path


def _mic_icon(muted: bool) -> str:
    name = 'mic_muted' if muted else 'mic_live'
    path = os.path.join(_ICON_DIR, f'{name}.png')
    img  = Image.new('RGBA', (_SIZE, _SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    body = (150, 150, 150, 255) if muted else (50, 210, 80, 255)
    lw   = max(2, _SIZE // 20)
    cx   = _SIZE // 2

    # Capsule
    cap_w  = _SIZE // 5
    cap_h  = _SIZE * 2 // 5
    cap_x1 = cx - cap_w // 2
    cap_y1 = _SIZE // 10
    cap_x2 = cx + cap_w // 2
    cap_y2 = cap_y1 + cap_h
    draw.rounded_rectangle([cap_x1, cap_y1, cap_x2, cap_y2],
                           radius=cap_w // 2, fill=body)

    # Pickup arc
    arc_cy = cap_y1 + cap_h // 2
    arc_r  = int(cap_h * 0.75)
    draw.arc([cx - arc_r, arc_cy - arc_r, cx + arc_r, arc_cy + arc_r],
             start=0, end=180, fill=body, width=lw)

    # Stem + base
    stem_bot = _SIZE * 83 // 100
    draw.line([cx, arc_cy + arc_r, cx, stem_bot], fill=body, width=lw)
    base_hw  = _SIZE // 8
    draw.line([cx - base_hw, stem_bot, cx + base_hw, stem_bot],
              fill=body, width=lw)

    # Muted slash
    if muted:
        m = _SIZE // 7
        draw.line([m, m, _SIZE - m, _SIZE - m],
                  fill=(220, 50, 50, 255), width=lw + 1)

    img.save(path)
    return path


# Pre-generate all icons
_TALLY_ICONS = {s: _tally_icon(s) for s in _TALLY_LABELS}
_MIC_ICONS   = {muted: _mic_icon(muted) for muted in (False, True)}


# ── TrayIcon ──────────────────────────────────────────────────────────────────

class TrayIcon:
    def __init__(self, on_mute_toggle=None, on_quit=None):
        self._on_mute_toggle = on_mute_toggle
        self._on_quit        = on_quit
        self._tally_state    = 'off'
        self._muted          = True

        self._menu = self._build_menu()

        self._tally_si = self._make_status_icon(_TALLY_ICONS['off'])
        self._mic_si   = self._make_status_icon(_MIC_ICONS[self._muted])

    # ── Public API ────────────────────────────────────────────────────────────

    def set_tally(self, on_program: bool, on_preview: bool):
        if on_program:
            state = 'program'   # red regardless of preview
        elif on_preview:
            state = 'preview'
        else:
            state = 'off'

        if state == self._tally_state:
            return
        self._tally_state = state
        self._tally_si.set_from_file(_TALLY_ICONS[state])
        self._tally_si.set_tooltip_text(f'NDI — {_TALLY_LABELS[state]}')
        self._status_item.set_label(f'NDI Streamer — {_TALLY_LABELS[state]}')

    def set_live(self, active: bool):
        label = f'NDI Streamer — {"Standby" if active else "Stopped"}'
        self._status_item.set_label(label)
        self._tally_si.set_tooltip_text(label)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _make_status_icon(self, icon_path: str) -> Gtk.StatusIcon:
        si = Gtk.StatusIcon()
        si.set_from_file(icon_path)
        si.connect('activate',   self._on_click)
        si.connect('popup-menu', self._on_right_click)
        return si

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        self._status_item = Gtk.MenuItem(label='NDI Streamer — Starting…')
        self._status_item.set_sensitive(False)
        menu.append(self._status_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label='Quit')
        quit_item.connect('activate',
                          lambda _: self._on_quit and self._on_quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _on_click(self, _icon):
        self._muted = not self._muted
        self._mic_si.set_from_file(_MIC_ICONS[self._muted])
        mic_label = 'Muted' if self._muted else 'Live'
        self._mic_si.set_tooltip_text(f'Mic — {mic_label}')
        if self._on_mute_toggle:
            self._on_mute_toggle(self._muted)

    def _on_right_click(self, icon, button, time):
        self._menu.popup(None, None,
                         Gtk.StatusIcon.position_menu, icon, button, time)
