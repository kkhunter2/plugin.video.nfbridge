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


def _argb_to_hex(argb):
    """Kodi colour 'AARRGGBB' -> CSS '#RRGGBB' (alpha dropped)."""
    if isinstance(argb, str) and len(argb) >= 8:
        return '#' + argb[2:8]
    return None


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


def style():
    """CSS-ready subtitle style derived from Kodi settings."""
    return {
        'color': _argb_to_hex(_get('subtitles.colorpick')) or '#FFFFFF',
        'outlineColor': _argb_to_hex(_get('subtitles.bordercolorpick')) or '#000000',
        'outlinePx': 2,
    }
