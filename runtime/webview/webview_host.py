# -*- coding: utf-8 -*-
"""Embedded Netflix player for Windows: Microsoft Edge WebView2 (system Edge engine,
Netflix-trusted -> plays incl. PlayReady SL3000 / 4K) hosting netflix.com, with the
Kodi subtitle font + style injected.

Reads from the environment:
  NF_URL    the netflix.com watch URL
  NF_FONT   path to the Kodi subtitle TTF/OTF (optional)
  NF_STYLE  JSON: {"color":"#RRGGBB","outlineColor":"#RRGGBB","outlinePx":2} (optional)

Font is OTS-fixed (fontfix) and loaded via the FontFace binary API (bypasses Netflix
CSP font-src); Netflix's own subtitle text/timing/position/DRM are untouched — we only
override the font (and optionally color/outline) with an !important CSS rule.
"""
import os
import sys
import json
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fontfix  # noqa: E402
import webview  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')

URL = os.environ.get('NF_URL', 'https://www.netflix.com/')
FONT = os.environ.get('NF_FONT', '')
try:
    STYLE = json.loads(os.environ.get('NF_STYLE', '{}'))
except Exception:
    STYLE = {}

_SEL = ('.player-timedtext,.player-timedtext-text-container,'
        '.player-timedtext-text-container span,.player-timedtext span')

# ---- build the font loader (FontFace binary, CSP-safe) ----
# Prefer a pre-fixed WOFF2 (the addon validates/repairs the font before launch and
# passes NF_FONT_WOFF2); otherwise OTS-fix a raw NF_FONT here.
FONT_WOFF2 = os.environ.get('NF_FONT_WOFF2', '')
LOAD_JS = ''
_family = 'inherit'
_woff2 = None
if FONT_WOFF2 and os.path.exists(FONT_WOFF2):
    _woff2 = FONT_WOFF2
elif FONT and os.path.exists(FONT):
    try:
        _woff2 = fontfix.to_web_woff2(FONT, CACHE)
    except Exception as exc:
        sys.stderr.write('fontfix failed: %s\n' % exc)
if _woff2:
    b64 = fontfix.woff2_base64(_woff2)
    _family = 'KodiSubFont'
    LOAD_JS = ('if(!window.__KSF){window.__KSF=1;try{'
               'var b=atob("' + b64 + '");var a=new Uint8Array(b.length);'
               'for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);'
               'new FontFace("KodiSubFont",a.buffer).load().then(function(f){document.fonts.add(f);});'
               '}catch(e){console.log("KSF "+e);}}')

# ---- build the CSS override ----
_rules = []
if _family != 'inherit':
    _rules.append('font-family:"%s",sans-serif !important' % _family)
if STYLE.get('color'):
    _rules.append('color:%s !important' % STYLE['color'])
if STYLE.get('outlineColor'):
    o = int(STYLE.get('outlinePx', 2))
    oc = STYLE['outlineColor']
    shadow = ','.join('%dpx %dpx 0 %s' % (dx, dy, oc)
                      for dx in (-o, 0, o) for dy in (-o, 0, o) if dx or dy)
    _rules.append('text-shadow:%s !important' % shadow)
_CSS = '%s{%s}' % (_SEL, ';'.join(_rules)) if _rules else ''
STYLE_JS = ('if(!document.getElementById("nf-subfont")){var s=document.createElement("style");'
            's.id="nf-subfont";s.textContent=' + json.dumps(_CSS) + ';'
            '(document.head||document.documentElement).appendChild(s);}') if _CSS else ''


def injector(window):
    while True:
        try:
            if LOAD_JS:
                window.evaluate_js(LOAD_JS)
            if STYLE_JS:
                window.evaluate_js(STYLE_JS)
        except Exception:
            pass
        time.sleep(3)


def on_start(window):
    threading.Thread(target=injector, args=(window,), daemon=True).start()


if not os.path.isdir(CACHE):
    os.makedirs(CACHE)
_profile = os.environ.get('NF_PROFILE') or os.path.join(CACHE, 'profile')
_win = webview.create_window('Netflix', URL, width=1600, height=900,
                             fullscreen=(os.environ.get('NF_FULLSCREEN') == '1'))
webview.start(on_start, _win, private_mode=False, storage_path=_profile)
