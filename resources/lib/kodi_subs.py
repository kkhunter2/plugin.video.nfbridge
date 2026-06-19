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


def resolve_font_path():
    """Return the path to the EXPLICITLY configured subtitle font (TTF/OTF), or None.

    Only honours `subtitles.fontname`. If it is unset (DEFAULT) we return None so the
    embedded player keeps Netflix's own subtitle font — we do NOT guess a font from
    media/Fonts (that surprised users by applying a leftover font they never chose).
    """
    name = _get('subtitles.fontname')
    if not name or name in ('DEFAULT', 'Default'):
        return None
    for d in _font_dirs():
        if not os.path.isdir(d):
            continue
        for c in (name, name + '.ttf', name + '.otf', name + '.TTF'):
            p = os.path.join(d, c)
            if os.path.isfile(p):
                return p
    return None


def style():
    """CSS-ready subtitle style derived from Kodi settings."""
    return {
        'color': _argb_to_hex(_get('subtitles.colorpick')) or '#FFFFFF',
        'outlineColor': _argb_to_hex(_get('subtitles.bordercolorpick')) or '#000000',
        'outlinePx': 2,
    }
