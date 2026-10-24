# -*- coding: utf-8 -*-
"""Read Kodi's subtitle settings and resolve the configured subtitle font.

Pulls the font the user set for subtitles (Settings > Player > Language > Fonts)
from Kodi's font folders (special://home/media/Fonts, special://xbmc/media/Fonts),
plus the colour/outline so the embedded player can match Kodi's subtitle look.
"""
import os
import json

import xbmc
import xbmcvfs


def _get(setting_id):
    try:
        q = {'jsonrpc': '2.0', 'id': 1, 'method': 'Settings.GetSettingValue',
             'params': {'setting': setting_id}}
        resp = json.loads(xbmc.executeJSONRPC(json.dumps(q)))
        return resp.get('result', {}).get('value')
    except Exception:
        return None


def _argb_split(argb):
    """Kodi colour 'AARRGGBB' -> (css '#RRGGBB', alpha 0..1); (None, None) if unparseable."""
    if isinstance(argb, str) and len(argb) >= 8:
        hexv = argb[-8:]
        try:
            alpha = int(hexv[0:2], 16) / 255.0
        except ValueError:
            alpha = 1.0
        return '#' + hexv[2:8], alpha
    return None, None


def _argb_to_hex(argb):
    """Kodi colour 'AARRGGBB' -> CSS '#RRGGBB' (alpha dropped)."""
    return _argb_split(argb)[0]


def _get_num(setting_id, default):
    """Read a numeric Kodi setting, falling back to Kodi's own default if absent/invalid."""
    v = _get(setting_id)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _font_dirs():
    return [xbmcvfs.translatePath('special://home/media/Fonts/'),
            xbmcvfs.translatePath('special://xbmc/media/Fonts/')]


def _font_family(path):
    """Read a TTF/OTF font's family/full name from its 'name' table (pure-Python, so it
    works in Kodi's interpreter which has no fontTools). Kodi stores this name (not the
    filename) in subtitles.fontname."""
    import struct
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
        if data[:4] == b'ttcf':            # font collection -> skip
            return None
        num = struct.unpack('>H', data[4:6])[0]
        name_off = None
        for i in range(num):
            rec = 12 + i * 16
            if data[rec:rec + 4] == b'name':
                name_off = struct.unpack('>I', data[rec + 8:rec + 12])[0]
                break
        if name_off is None:
            return None
        count = struct.unpack('>H', data[name_off + 2:name_off + 4])[0]
        str_off = struct.unpack('>H', data[name_off + 4:name_off + 6])[0]
        names = {}
        for i in range(count):
            rec = name_off + 6 + i * 12
            pid, eid, lid, nid, length, off = struct.unpack('>HHHHHH', data[rec:rec + 12])
            raw = data[name_off + str_off + off:name_off + str_off + off + length]
            try:
                txt = raw.decode('utf-16-be') if pid in (0, 3) else raw.decode('latin-1')
            except Exception:
                continue
            if nid in (1, 4, 6) and nid not in names and txt:
                names[nid] = txt
        return names.get(1) or names.get(4) or names.get(6)
    except Exception:
        return None


def resolve_font_path():
    """Return the path to the EXPLICITLY configured subtitle font (TTF/OTF), or None.

    Honours `subtitles.fontname` (DEFAULT -> None -> Netflix's own font). Kodi stores the
    font's internal NAME, so match by filename first, then by each font's family name.
    """
    name = _get('subtitles.fontname')
    if not name or name in ('DEFAULT', 'Default'):
        return None
    name = name.strip()
    dirs = [d for d in _font_dirs() if os.path.isdir(d)]
    # 1) by filename
    for d in dirs:
        for c in (name, name + '.ttf', name + '.otf', name + '.TTF'):
            p = os.path.join(d, c)
            if os.path.isfile(p):
                return p
    # 2) by the font's internal family/full name
    for d in dirs:
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith(('.ttf', '.otf')):
                continue
            p = os.path.join(d, f)
            fam = _font_family(p)
            if fam and fam.strip() == name:
                return p
    return None


# Map Kodi's subtitles.fontsize to a % of video height, matching Kodi's own renderer.
# Kodi (v20+, libass; DVDSubtitlesLibass.cpp) renders the glyph at fontsize*playResY/720
# in a playResY-tall frame, i.e. fontsize/720 of the video height. Rendering our overlay
# at the same fraction reproduces Kodi's exact size (verified against Kodi: 42 -> ~5.83vh).
_FONT_REF_H = 720.0
# Kodi's border/shadow sizes are 0..100 (%); map to em of the rendered glyph so the edge
# scales with the font at any resolution. 0.18em at 100% looks ~right vs Kodi's renderer.
_EDGE_EM = 0.0018


def style():
    """EVERY Kodi subtitle appearance setting, resolved to one CSS-ready dict.

    Reads the full set the user configured under Settings > Player > Subtitles — font
    size, style (bold/italic), colour + opacity, border/outline, background box, drop
    shadow, line spacing, blur and vertical position — not just the colours, so the
    embedded player matches Kodi's subtitle look exactly. Missing settings fall back to
    Kodi's own defaults, so this degrades gracefully across Kodi versions.
    """
    color, _ = _argb_split(_get('subtitles.colorpick'))
    border, _ = _argb_split(_get('subtitles.bordercolorpick'))
    bg, _ = _argb_split(_get('subtitles.bgcolorpick'))
    shadow, _ = _argb_split(_get('subtitles.shadowcolor'))

    fontsize = _get_num('subtitles.fontsize', 42.0)            # px @ ~1080 canvas
    fstyle = int(_get_num('subtitles.style', 0))               # 0 N, 1 B, 2 I, 3 BI
    text_op = _get_num('subtitles.opacity', 100.0) / 100.0
    border_sz = _get_num('subtitles.bordersize', 25.0)         # 0..100 %
    line_sp = _get_num('subtitles.linespacing', 12.0)          # 0..100 %
    blur = _get_num('subtitles.blur', 0.0)                     # 0..100 %
    bg_type = int(_get_num('subtitles.backgroundtype', 0))     # 0 none,1 shadow,2 box,3 square
    bg_op = _get_num('subtitles.bgopacity', 80.0) / 100.0
    shadow_op = _get_num('subtitles.shadowopacity', 100.0) / 100.0
    shadow_sz = _get_num('subtitles.shadowsize', 15.0)         # 0..100 %
    margin_v = _get_num('subtitles.marginvertical', 4.95)      # % of screen
    align = int(_get_num('subtitles.align', 1))                # 1/2 bottom, 3/4 top

    return {
        # --- text ---
        'color': color or '#FFFFFF',
        'opacity': round(text_op, 3),
        'fontPct': round(fontsize / _FONT_REF_H * 100.0, 3),
        'bold': fstyle in (1, 3),
        'italic': fstyle in (2, 3),
        'lineHeight': round(1.0 + line_sp / 100.0, 3),
        'blurEm': round(blur * _EDGE_EM, 4),
        # --- outline / border ---
        'outlineColor': border or '#000000',
        'outlineEm': round(border_sz * _EDGE_EM, 4),
        # --- background box ---
        'bgType': {1: 'shadow', 2: 'box', 3: 'square'}.get(bg_type, 'none'),
        'bgColor': bg or '#000000',
        'bgOpacity': round(bg_op, 3),
        # --- drop shadow ---
        'shadowColor': shadow or '#000000',
        'shadowOpacity': round(shadow_op, 3),
        'shadowEm': round(shadow_sz * _EDGE_EM, 4),
        # --- position ---
        'align': 'top' if align in (3, 4) else 'bottom',
        'marginPct': round(margin_v, 3),
        # --- back-compat with older webview_host builds (fixed px outline @ ~1080) ---
        'outlinePx': max(1, int(round(fontsize * border_sz / 100.0 * 0.18))),
    }
