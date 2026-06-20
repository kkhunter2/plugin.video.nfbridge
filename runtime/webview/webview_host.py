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

# Dual-purpose binary: `<exe> fontfix <src> <cache>` repairs a font (OTS) and exits;
# otherwise it runs the WebView2 Netflix player. Lets the addon do the synchronous
# font pre-check (and notify on a corrupt font) without a separate Python.
if len(sys.argv) >= 2 and sys.argv[1] == 'fontfix':
    sys.exit(fontfix.main(['fontfix'] + sys.argv[2:]))

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

# Hide the scrollbar: when subtitles render the page overflows a hair and the vertical
# scrollbar's up/down arrows show as white dots top-right/bottom-right of the screen.
# Hide the scrollbar AND most Netflix player controls, keeping only the seek bar
# (timeline*) and the play/pause button. Each control has a stable data-uia, so showing/
# hiding any of them is just editing this list.
_CONTROL_HIDE = (
    "[data-uia='control-nav-back'],[data-uia='control-flag'],[data-uia='control-back10'],"
    "[data-uia='control-forward10'],[data-uia^='control-volume'],[data-uia='control-next'],"
    "[data-uia='control-episodes'],[data-uia='control-audio-subtitle'],[data-uia='control-speed'],"
    "[data-uia^='control-fullscreen'],[data-uia='video-title']"
    "{display:none !important}")
HIDE_JS = ('if(!document.getElementById("nf-noscroll")){var s=document.createElement("style");'
           's.id="nf-noscroll";s.textContent="::-webkit-scrollbar{display:none !important}'
           'html,body{overflow:hidden !important}' + _CONTROL_HIDE + '";'
           '(document.head||document.documentElement).appendChild(s);}')

# During playback the fullscreen WebView has focus; Netflix doesn't treat Back/Esc as
# "return to Kodi", so we capture those keys and flag window.__TOKODI for the monitor.
BACK_KEY_JS = ('if(!window.__BKL){window.__BKL=1;document.addEventListener("keydown",function(e){'
               'if(e.key==="Escape"||e.key==="Backspace"||e.key==="BrowserBack"||e.key==="GoBack"'
               '||e.keyCode===8||e.keyCode===166){window.__TOKODI=1;}},true);}')


SESSION_OUT = os.environ.get('NF_SESSION_OUT')
# Netflix exposes the shakti session in the page's react context once signed in.
EXTRACT_JS = ('(function(){try{var m=window.netflix&&netflix.reactContext&&netflix.reactContext.models;'
              'if(!m)return null;var ui=(m.userInfo&&m.userInfo.data)||{};'
              'var sd=(m.serverDefs&&m.serverDefs.data)||{};'
              'if(!ui.authURL||!sd.BUILD_IDENTIFIER)return null;'
              'return JSON.stringify({authURL:ui.authURL,build:sd.BUILD_IDENTIFIER,'
              'guid:ui.guid,country:ui.countryOfSignup,membership:ui.membershipStatus,'
              'gpsModel:sd.gpsModel});}catch(e){return null;}})()')


def _capture_session(window):
    try:
        raw = window.evaluate_js(EXTRACT_JS)
        if not raw or raw == 'null':
            return False
        data = json.loads(raw)
        # live-session cookies via pywebview (not a credential-store read)
        cookies = []
        try:
            for c in (window.get_cookies() or []):
                try:
                    for name, morsel in c.items():
                        cookies.append({'name': name, 'value': morsel.value,
                                        'domain': morsel.get('domain', '.netflix.com'),
                                        'path': morsel.get('path', '/')})
                except Exception:
                    pass
        except Exception:
            pass
        data['cookies'] = cookies
        with open(SESSION_OUT, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False)
        return True
    except Exception:
        return False


# --------------------------------------------------------------- RPC daemon ---
# When NF_RPC_PORT is set, run a localhost HTTP server that proxies falcor queries
# THROUGH the page's own data client (netflix.appContext.state.pathEvaluator.get) —
# so all X-Netflix headers / auth / CSRF are Netflix's own (no 412, future-proof).
RPC_PORT = os.environ.get('NF_RPC_PORT')
# ONE lock serialises every access to the WebView from any thread (RPC handlers, the
# window monitor, the injector). Concurrent load_url/evaluate_js from two threads
# crashes WebView2 (the daemon dies during rapid menu navigation). RLock so a handler
# that already holds it can call helpers that also take it.
_rpc_lock = threading.RLock()
_rpc_counter = [0]
_ready = [False]   # last-known readiness; /health returns it without blocking
_BACK_FLAG = [False]    # low-level keyboard hook sets this when Back is pressed in playback
_SKIPNEXT_FLAG = [False]  # ... and this when OK/Enter is pressed in playback
_HOOK_REFS = []

# Click the visible Skip-Intro / Skip-Recap / seamless Next-Episode overlay button (these
# are separate from the hidden control bar). Used when OK/Enter is pressed during playback.
_SKIP_SELS_JS = ("[\"[data-uia='player-skip-intro']\",\"[data-uia='player-skip-recap']\","
                 "\"[data-uia*='skip']\",\"[data-uia*='next-episode-seamless']\"]")
CLICK_SKIP_JS = ("(function(){var S=" + _SKIP_SELS_JS + ";for(var i=0;i<S.length;i++){"
                 "var b=document.querySelector(S[i]);if(b){var r=b.getBoundingClientRect();"
                 "if(r.width>0&&r.height>0){b.click();return 1;}}}return 0;})()")
try:
    # restart the daemon on code change OR subtitle-font change (the font is applied at
    # launch, so a running daemon must relaunch to pick up a newly chosen font).
    _CODE_VER = (str(int(os.path.getmtime(os.path.abspath(__file__))))
                 + '|' + os.environ.get('NF_FONT_NAME', ''))
except Exception:
    _CODE_VER = '0'

READY_JS = ('!!(window.netflix&&netflix.appContext&&netflix.appContext.state&&'
            'netflix.appContext.state.pathEvaluator&&netflix.reactContext&&'
            'netflix.reactContext.models.userInfo&&netflix.reactContext.models.userInfo.data.authURL)')

# fresh page context: shakti session bits + the current LoCo (home) root id.
CONTEXT_JS = ('(function(){try{var m=netflix.reactContext.models,ui=m.userInfo.data,sd=m.serverDefs.data;'
              'var root=null;try{var lc=netflix.falcorCache&&netflix.falcorCache.locos;'
              'if(lc){root=Object.keys(lc).filter(function(k){return k.indexOf("$")<0;})[0];}}catch(e){}'
              'return JSON.stringify({authURL:ui.authURL,build:sd.BUILD_IDENTIFIER,guid:ui.guid,'
              'country:ui.countryOfSignup,locoRoot:root});}catch(e){return JSON.stringify({err:String(e)});}})()')


def _falcor_get(window, paths, timeout_s=20):
    with _rpc_lock:
        _rpc_counter[0] += 1
        rid = 'r%d' % _rpc_counter[0]
        start = ('window.__RPC=window.__RPC||{};window.__RPC["%s"]=null;try{'
                 'var pe=netflix.appContext.state.pathEvaluator;'
                 'pe.get.apply(pe,%s).then(function(r){window.__RPC["%s"]=JSON.stringify({ok:1,json:(r&&r.json)||null});},'
                 'function(e){window.__RPC["%s"]=JSON.stringify({err:String((e&&e.message)||e)});});'
                 '}catch(e){window.__RPC["%s"]=JSON.stringify({err:"throw:"+String(e)});}'
                 % (rid, json.dumps(paths), rid, rid, rid))
        try:
            window.evaluate_js(start)
        except Exception as exc:
            return {'err': 'evaluate failed: %s' % exc}
        for _ in range(int(timeout_s * 5)):
            try:
                v = window.evaluate_js('window.__RPC["%s"]' % rid)
            except Exception:
                v = None
            if v:
                try:
                    window.evaluate_js('delete window.__RPC["%s"]' % rid)
                except Exception:
                    pass
                try:
                    return json.loads(v)
                except Exception:
                    return {'err': 'bad json'}
            time.sleep(0.2)
        return {'err': 'timeout'}


def _art_get(window, ids):
    """Box art for video ids, read from the page's Apollo (GraphQL) cache — that is
    where Netflix keeps artwork (falcor has none). Cache read only (no fetch), so it
    covers videos the home has already rendered."""
    js = ('(function(ids){try{var c=netflix.appContext.state.graphqlClient.cache.extract();var out={};'
          'ids.forEach(function(id){var ek=null;["Movie","Show","Episode","Supplemental"].forEach(function(t){'
          'var k=t+\':{"videoId":\'+id+\'}\';if(c[k]&&!ek)ek=k;});if(!ek)return;var e=c[ek],url=null;'
          'Object.keys(e).forEach(function(f){if(url)return;'
          'if(f.indexOf("artwork")>=0&&f.indexOf(\'"SDP"\')>=0){try{var v=e[f];if(v&&v.url)url=v.url;}catch(_){}}});'
          'if(url)out[String(id)]=url;});return JSON.stringify(out);}catch(e){return "{}";}})(%s)'
          % json.dumps([str(i) for i in ids]))
    try:
        raw = window.evaluate_js(js)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


SEARCH_EXTRACT_JS = (
    '(function(){try{var c=netflix.appContext.state.graphqlClient.cache.extract();'
    'var gk=Object.keys(c).filter(function(x){return /^PinotGallerySection/.test(x);})[0];'
    'if(!gk)return "[]";var g=c[gk];'
    'var ek=Object.keys(g).filter(function(x){return x.indexOf("entities")>=0;})[0];'
    'var edges=((g[ek]||{}).edges)||[];var out=[];'
    'edges.forEach(function(ed){var n=ed.node||{};var ue=((n.unifiedEntity||{}).__ref)||"";'
    'var m=ue.match(/^(Movie|Show|Episode):\\{"videoId":(\\d+)\\}/);if(!m)return;'
    'var art=null;var ar=(n.contextualArtwork||{}).__ref;if(ar&&c[ar]){var ae=c[ar];'
    'Object.keys(ae).forEach(function(f){if(art)return;try{if(ae[f]&&ae[f].url)art=ae[f].url;}catch(_){}});}'
    'out.push({id:m[2],type:m[1].toLowerCase(),title:n.displayString,art:art});});'
    'return JSON.stringify(out);}catch(e){return "[]";}})()')


def _search_get(window, q, timeout=15):
    try:
        window.evaluate_js("location.assign('https://www.netflix.com/search?q='+encodeURIComponent(%s))"
                           % json.dumps(q))
    except Exception:
        pass
    for _ in range(timeout * 2):
        try:
            arr = json.loads(window.evaluate_js(SEARCH_EXTRACT_JS) or '[]')
            if arr:
                return arr
        except Exception:
            pass
        time.sleep(0.5)
    return []


def _season_list_js(show_id):
    """Ordered season ids/titles from the Show's seasons connection (Apollo)."""
    return (
        '(function(){try{var c=netflix.appContext.state.graphqlClient.cache.extract();'
        'var e=c[\'Show:{"videoId":%s}\'];if(!e||!e.seasons)return "[]";var out=[];'
        '((e.seasons.edges)||[]).forEach(function(ed){var ref=((ed.node)||{}).__ref||"";'
        'var m=ref.match(/Season:\\{"videoId":(\\d+)\\}/);if(m){var s=c[ref]||{};'
        'out.push({id:m[1],title:s.title,number:s.number});}});'
        'return JSON.stringify(out);}catch(e){return "[]";}})()' % str(show_id))


def _episodes_get(window, vid, timeout=15):
    """Seasons + episodes WITHOUT rendering the episode UI: the season list comes from
    the Apollo Show.seasons connection; each season's episodes come from falcor
    (["seasons",id,"episodes",range,"itemSummary"]) — both work off-screen, no flash."""
    _set_fullscreen(window, False)
    _render_offscreen(window)
    try:
        window.evaluate_js("location.assign('https://www.netflix.com/title/%s')" % str(vid))
    except Exception:
        pass
    meta = []
    sjs = _season_list_js(vid)
    title_js = "location.assign('https://www.netflix.com/title/%s')" % str(vid)
    for i in range(timeout * 2):
        try:
            meta = json.loads(window.evaluate_js(sjs) or '[]')
        except Exception:
            meta = []
        if meta:
            break
        # /title can bounce to the "Who's watching" gate; pick the profile and reload it
        try:
            if i % 4 == 3 and window.evaluate_js('document.querySelectorAll(".profile-link").length'):
                window.evaluate_js('window.__PSEL=0')
                window.evaluate_js(SELECT_PROFILE_JS)
                time.sleep(1.5)
                window.evaluate_js(title_js)
        except Exception:
            pass
        time.sleep(0.5)
    if not meta:
        return []

    rng = {'from': 0, 'to': 60}
    result = []
    for sm in meta:
        sid = sm['id']
        eps = []
        try:
            fj = _falcor_get(window, [['seasons', sid, 'episodes', rng, 'itemSummary'],
                                      ['seasons', sid, 'episodes', rng, 'synopsis']], 15)
            node = (fj.get('json') or {}).get('seasons', {}).get(sid, {}).get('episodes', {})
            for k, cell in node.items():
                if not (isinstance(k, str) and k.isdigit()) or not isinstance(cell, dict):
                    continue
                it = cell.get('itemSummary') or {}
                path = cell.get('$__path') or []
                epid = str(path[1]) if len(path) > 1 else (str(it.get('id')) if it.get('id') else None)
                if not epid:
                    continue
                syn = cell.get('synopsis')
                if isinstance(syn, dict):
                    syn = syn.get('text')
                try:
                    art = (it.get('boxArt') or {}).get('url')
                except Exception:
                    art = None
                try:
                    rating = (((it.get('maturity') or {}).get('rating')) or {}).get('value')
                except Exception:
                    rating = None
                eps.append({'id': epid, 'title': it.get('title'), 'number': it.get('episode'),
                            'season': it.get('season'), 'year': it.get('releaseYear'),
                            'runtime': it.get('infoDensityRuntime'), 'art': art,
                            'synopsis': syn, 'rating': rating})
            eps.sort(key=lambda e: e.get('number') or 0)
            if eps:
                bj = _falcor_get(window, [['videos', e['id'], 'bookmarkPosition'] for e in eps], 15)
                bv = (bj.get('json') or {}).get('videos', {})
                for e in eps:
                    e['bookmark'] = (bv.get(e['id'], {}) or {}).get('bookmarkPosition')
        except Exception:
            pass
        result.append({'id': sid, 'title': sm.get('title'), 'number': sm.get('number'), 'eps': eps})
    return result


def _switch_profile(window, index=0):
    """Open the 'Who's watching' gate in the foreground for the user to pick a profile.

    Netflix blocks programmatic profile switching (synthetic clicks don't trigger the
    React handler and /SwitchProfile?tkn= does nothing on a direct navigation), so the
    only reliable switch is a real user click. We surface the gate; the user clicks.
    """
    try:
        window.evaluate_js("location.assign('https://www.netflix.com/ProfilesGate')")
    except Exception:
        pass
    time.sleep(1)
    AUTH_SHOWN[0] = True   # auto-minimize once the user picks a profile (home reached)
    _restore(window)       # bring the gate forward (ctypes SetForegroundWindow)
    return {'shown': True}


# Scroll ONE row (matched by its title) fully right so Netflix lazy-loads box art for all
# of its tiles (only ~8 load initially). %s is filled with the JSON-quoted row title.
WARMROW_JS = (
    "(function(t){"
    "var rows=document.querySelectorAll(\".lolomoRow,[class*='lolomoRow']\");"
    "for(var i=0;i<rows.length;i++){var r=rows[i],"
    "h=r.querySelector(\".rowTitle,[class*='rowTitle'],h2\");"
    "if(h&&(h.textContent||'').trim()===t){"
    "var sl=r.querySelector(\".sliderContent,[class*='sliderContent']\");"
    "if(sl){try{sl.scrollLeft=sl.scrollWidth;}catch(e){}}"
    "r.querySelectorAll(\".handleNext,[class*='handleNext']\").forEach("
    "function(b){try{b.click();}catch(e){}});return 'ok';}}return 'nf';})(%s)")


def _warm_row(window, title):
    """Scroll just the named row a few times so the rest of its tiles load box art."""
    if not title:
        return
    js = WARMROW_JS % json.dumps(title)
    for _ in range(4):
        try:
            window.evaluate_js(js)
        except Exception:
            pass
        time.sleep(0.9)


def _genre(window, gid, timeout=25):
    """Navigate to a genre/category page and return its LoCo root (rows are then read
    by falcor like the home). Renders off-screen so the page prefetches, no flash."""
    _set_fullscreen(window, False)
    _render_offscreen(window)
    try:
        window.evaluate_js("location.assign('https://www.netflix.com/browse/genre/%s')" % str(gid))
    except Exception:
        pass
    result = {}
    for _ in range(timeout):
        try:
            ctx = json.loads(window.evaluate_js(CONTEXT_JS) or '{}')
            if ctx.get('locoRoot'):
                result = ctx
                break
            url = window.evaluate_js('location.href') or ''
            if '/account' in url:
                window.evaluate_js("location.assign('https://www.netflix.com/browse/genre/%s')" % str(gid))
        except Exception:
            pass
        time.sleep(1)
    _focus_kodi()
    return result   # window stays off-screen (rendered there); no flash


def _go_home(window, timeout=30):
    """Navigate back to /browse (from search/play) and re-resolve the home root. The
    window must render for akira to prefetch the home LoCo, so un-minimize first, then
    tuck it away again once home is ready."""
    _set_fullscreen(window, False)
    _render_offscreen(window)   # render off-screen (no visible flash) so home prefetches
    result = {}
    for _t in range(timeout):
        try:
            ctx = json.loads(window.evaluate_js(CONTEXT_JS) or '{}')
            if ctx.get('locoRoot'):
                result = ctx
                break
            url = window.evaluate_js('location.href') or ''
            on_browse = '/browse' in url
            if not on_browse or _t % 6 == 0:
                # (re)navigate; Netflix sometimes bounces /browse -> /account mid-session
                window.evaluate_js("window.__PSEL=0;location.assign('https://www.netflix.com/browse');")
            else:
                window.evaluate_js('(function(){try{var e=document.querySelectorAll(".profile-link");'
                                   'if(e.length)e[0].click();}catch(x){}})()')
        except Exception:
            pass
        time.sleep(1)
    _focus_kodi()
    return result   # window stays off-screen (rendered there); no flash


_FS = [False]
_PREV_STYLE = [0]


def _set_fullscreen(window, on):
    """Borderless fullscreen via Win32: a WS_POPUP window covering the whole screen
    (with a few px overscan so no edge line shows). pywebview's toggle_fullscreen left
    gaps at the bottom/right."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.FindWindowW.restype = wintypes.HWND
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        hwnd = _own_hwnd()   # by process, NOT title (the title changes to the video name)
        if not hwnd:
            return
        GWL_STYLE = -16
        if on and not _FS[0]:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor, real px
            except Exception:
                pass
            _PREV_STYLE[0] = u.GetWindowLongW(hwnd, GWL_STYLE)
            sw = u.GetSystemMetrics(0)
            sh = u.GetSystemMetrics(1)
            u.SetWindowLongW(hwnd, GWL_STYLE, -2147483648 | 0x10000000)  # WS_POPUP|WS_VISIBLE
            # HWND_TOP(0) not TOPMOST: cover the screen but still let Alt+Tab bring another
            # window in front. 2px overscan each side, FRAMECHANGED|SHOWWINDOW.
            u.SetWindowPos(hwnd, 0, -2, -2, sw + 4, sh + 4, 0x0020 | 0x0040)
            _FS[0] = True
        elif not on and _FS[0]:
            u.SetWindowLongW(hwnd, GWL_STYLE, _PREV_STYLE[0] or 0x00CF0000)
            u.SetWindowPos(hwnd, 0, -4000, -4000, 1600, 900, 0x0020 | 0x0010)  # FRAMECHANGED|NOACTIVATE
            _FS[0] = False
    except Exception:
        pass


def _own_hwnd():
    """The daemon's own main window: the LARGEST visible top-level of this process."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        mypid = k.GetCurrentProcessId()
        best = [0, -1]   # hwnd, area

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _cb(hwnd, _lp):
            if not u.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == mypid and u.GetWindow(hwnd, 4) == 0:  # GW_OWNER==4 -> top-level
                r = wintypes.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                area = abs((r.right - r.left) * (r.bottom - r.top))
                if area > best[1]:
                    best[0], best[1] = hwnd, area
            return True
        u.EnumWindows(_cb, 0)
        return best[0]
    except Exception:
        return 0


def _set_window_icon():
    """Replace python's default window/taskbar icon with the add-on (Netflix) icon."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
        hwnd = _own_hwnd()
        if not (os.path.exists(ico) and hwnd):
            return
        u.LoadImageW.restype = ctypes.c_void_p
        u.LoadImageW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_uint,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        hicon = u.LoadImageW(None, ico, 1, 0, 0, 0x00000010 | 0x00000040)  # ICON, LOADFROMFILE|DEFAULTSIZE
        if hicon:
            u.SendMessageW(hwnd, 0x0080, 0, hicon)   # WM_SETICON, ICON_SMALL
            u.SendMessageW(hwnd, 0x0080, 1, hicon)   # WM_SETICON, ICON_BIG
    except Exception:
        pass


def _render_offscreen(window):
    """Show the window OFF-SCREEN so it keeps rendering, WITHOUT activating it (so Kodi
    keeps keyboard focus). SW_SHOWNOACTIVATE + a no-activate move; falls back to pywebview."""
    try:
        import ctypes
        u = ctypes.windll.user32
        hwnd = _own_hwnd()
        if hwnd:
            u.ShowWindow(hwnd, 4)   # SW_SHOWNOACTIVATE (un-minimize / show, no focus steal)
            u.SetWindowPos(hwnd, 0, -4000, -4000, 1600, 900, 0x0014)  # NOACTIVATE|NOZORDER (wide -> more art tiles render)
            return
    except Exception:
        pass
    try:
        window.move(-4000, -4000)
    except Exception:
        pass


def _hide_to_kodi(window):
    """Send the daemon window off-screen (keeps rendering, no focus steal) and hand the
    keyboard/remote focus to Kodi."""
    _render_offscreen(window)
    _focus_kodi()


def _force_foreground(hwnd):
    """Reliably bring hwnd to the foreground WITH keyboard focus. Windows blocks
    SetForegroundWindow from a background process, so attach to the current foreground
    thread + tap Alt to unlock it. Used both for the play window (so Back / player keys
    work) and for Kodi (so the remote works after the window goes away)."""
    try:
        import ctypes
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        if not hwnd:
            return
        fg = u.GetForegroundWindow()
        tgt = u.GetWindowThreadProcessId(hwnd, None)
        cur = k.GetCurrentThreadId()
        fgt = u.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = []
        for a in (cur, fgt):
            if a and a != tgt and u.AttachThreadInput(a, tgt, True):
                attached.append(a)
        try:
            u.keybd_event(0x12, 0, 0, 0)       # ALT down
            u.keybd_event(0x12, 0, 0x0002, 0)  # ALT up
            u.ShowWindow(hwnd, 9)              # SW_RESTORE
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
            u.SetActiveWindow(hwnd)
            u.SetFocus(hwnd)
        finally:
            for a in attached:
                u.AttachThreadInput(a, tgt, False)
    except Exception:
        pass


def _kodi_hwnd():
    """Kodi's main window, found by its process image name (kodi.exe)."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        found = [0]

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _cb(hwnd, _lp):
            if not u.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h = k.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
            if h:
                buf = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(260)
                try:
                    if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) \
                            and buf.value.lower().endswith('kodi.exe'):
                        found[0] = hwnd
                finally:
                    k.CloseHandle(h)
            return found[0] == 0
        u.EnumWindows(_cb, 0)
        return found[0]
    except Exception:
        return 0


def _focus_kodi():
    """Give keyboard/remote focus back to Kodi (after login / Back / returning to browse)."""
    _force_foreground(_kodi_hwnd())


def _restore(window):
    """Un-minimize and bring the daemon's own window forward WITH keyboard focus (so the
    Back key and Netflix player keys work during playback)."""
    for method in ('restore', 'show'):
        try:
            getattr(window, method)()
        except Exception:
            pass
    _force_foreground(_own_hwnd())


def _start_rpc(window, port):
    import http.server

    def send(h, code, obj):
        try:
            body = json.dumps(obj).encode('utf-8')
            h.send_response(code)
            h.send_header('Content-Type', 'application/json')
            h.send_header('Content-Length', str(len(body)))
            h.end_headers()
            h.wfile.write(body)
        except Exception:
            pass   # client disconnected (rapid navigation) -> ignore

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == '/health':
                # never blocks: refresh readiness only if the window is free this instant
                if _rpc_lock.acquire(blocking=False):
                    try:
                        _ready[0] = bool(window.evaluate_js(READY_JS))
                    except Exception:
                        pass
                    finally:
                        _rpc_lock.release()
                return send(self, 200, {'ready': _ready[0], 'ver': _CODE_VER, 'playing': _FS[0]})
            if self.path == '/quit':
                send(self, 200, {'ok': True})
                threading.Timer(0.3, lambda: os._exit(0)).start()
                return
            with _rpc_lock:
              if self.path == '/context':
                try:
                    raw = window.evaluate_js(CONTEXT_JS)
                    send(self, 200, json.loads(raw) if raw else {'err': 'no context'})
                except Exception as exc:
                    send(self, 200, {'err': str(exc)})
              elif self.path == '/show':
                # bring the window forward for sign-in; auto-minimize once home is reached
                try:
                    window.load_url('https://www.netflix.com/login')
                    AUTH_SHOWN[0] = True
                    _restore(window)
                    send(self, 200, {'ok': True})
                except Exception as exc:
                    send(self, 200, {'err': str(exc)})
              else:
                send(self, 404, {'err': 'not found'})

        def do_POST(self):
          with _rpc_lock:
            try:
                ln = int(self.headers.get('Content-Length', 0))
                req = json.loads(self.rfile.read(ln) or b'{}')
            except Exception as exc:
                return send(self, 400, {'err': 'bad request: %s' % exc})
            if self.path == '/falcor':
                send(self, 200, _falcor_get(window, req.get('paths', []), req.get('timeout', 20)))
            elif self.path == '/art':
                send(self, 200, _art_get(window, req.get('ids', [])))
            elif self.path == '/search':
                send(self, 200, {'results': _search_get(window, req.get('q', ''))})
            elif self.path == '/episodes':
                send(self, 200, {'seasons': _episodes_get(window, req.get('videoId', ''))})
            elif self.path == '/home':
                send(self, 200, _go_home(window))
            elif self.path == '/genre':
                send(self, 200, _genre(window, req.get('id', '')))
            elif self.path == '/switch':
                send(self, 200, _switch_profile(window, req.get('index', 0)))
            elif self.path == '/eval':
                try:
                    send(self, 200, {'result': window.evaluate_js(req.get('js', ''))})
                except Exception as exc:
                    send(self, 200, {'err': str(exc)})
            elif self.path == '/play':
                vid = req.get('videoId')
                try:
                    # Navigate to the new watch page FIRST: this unloads any previous
                    # (paused) video so bringing the window forward can't resume the old one.
                    if vid:
                        window.load_url('https://www.netflix.com/watch/%s' % vid)
                    _restore(window)
                    _set_fullscreen(window, True)   # fill the screen for playback
                    _force_foreground(_own_hwnd())  # focus so Back / player keys work
                    send(self, 200, {'ok': True})
                except Exception as exc:
                    send(self, 200, {'err': str(exc)})
            elif self.path == '/stop':
                # leave the watch page so the WebView frees the audio device for Kodi
                try:
                    window.evaluate_js('window.__TOKODI=0')
                    window.load_url('https://www.netflix.com/browse')
                    _set_fullscreen(window, False)
                    _render_offscreen(window)
                    _focus_kodi()
                    send(self, 200, {'ok': True})
                except Exception as exc:
                    send(self, 200, {'err': str(exc)})
            elif self.path == '/warmrow':
                # scroll one row so all its tiles load box art (the caller caches the result)
                try:
                    _warm_row(window, req.get('title', ''))
                    send(self, 200, {'ok': True})
                except Exception as exc:
                    send(self, 200, {'err': str(exc)})
            else:
                send(self, 404, {'err': 'not found'})

    # Threaded so a long op (episodes/search navigation) never blocks /health or other
    # requests; the _rpc_lock still serialises actual window access.
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', int(port)), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def injector(window):
    captured = False
    while True:
        try:
            with _rpc_lock:
                window.evaluate_js(HIDE_JS)
                window.evaluate_js(BACK_KEY_JS)
                if LOAD_JS:
                    window.evaluate_js(LOAD_JS)
                if STYLE_JS:
                    window.evaluate_js(STYLE_JS)
                if SESSION_OUT and not captured:
                    captured = _capture_session(window)
        except Exception:
            pass
        time.sleep(3)


# If Netflix shows the "Who's watching?" profile gate there is no home LoCo; pick a
# profile (NF_PROFILE_INDEX, default the first) so the home loads.
PROFILE_INDEX = int(os.environ.get('NF_PROFILE_INDEX', '0') or '0')
SELECT_PROFILE_JS = ('(function(idx){if(window.__PSEL)return "waiting";'
                     'var els=document.querySelectorAll(".profile-link");'
                     'if(els&&els.length){window.__PSEL=1;(els[idx]||els[0]).click();return "clicked";}'
                     'return "none";})(%d)' % PROFILE_INDEX)


def _auto_minimize(window):
    # The akira UI only prefetches the home LoCo while the window renders, so we load
    # it visible, (auto-pick a profile if gated,) wait until the home root is cached,
    # then tuck the window away. falcor RPC keeps working minimized; /play, /show restore.
    for _ in range(90):
        try:
            if window.evaluate_js(READY_JS):
                ctx = json.loads(window.evaluate_js(CONTEXT_JS) or '{}')
                if ctx.get('locoRoot'):
                    time.sleep(1)
                    _render_offscreen(window)
                    return
                window.evaluate_js(SELECT_PROFILE_JS)  # likely on the profile gate
        except Exception:
            pass
        time.sleep(1)


AUTH_SHOWN = [True]    # release focus to Kodi once home is reached (first load too)


def window_monitor(window):
    """Single window serves browse + every play. Two behaviours:
      - while surfaced for auth (login/profile/first load): once the home is reached
        (locoRoot) the user is done -> minimize so the window is out of sight;
      - while playing fullscreen: when the user leaves the player (Back -> not /watch)
        -> drop fullscreen and minimize so Kodi returns to the front."""
    was_watching = False
    reload_count = 0
    stuck = 0
    while True:
        # non-blocking: only touch the window when no RPC handler holds the lock,
        # so the monitor never races a navigation (which crashes WebView2).
        if _rpc_lock.acquire(blocking=False):
            try:
                if _FS[0]:
                    st = json.loads(window.evaluate_js(
                        'JSON.stringify({u:location.href,v:!!document.querySelector("video"),'
                        'e:/D7\\d{3}/.test(document.body.innerText),b:!!window.__TOKODI})') or '{}')
                    url = st.get('u', '')
                    on_watch = '/watch/' in url
                    has_video = bool(st.get('v'))
                    if _SKIPNEXT_FLAG[0]:
                        _SKIPNEXT_FLAG[0] = False
                        try:
                            window.evaluate_js(CLICK_SKIP_JS)   # OK -> Skip Intro / Next if shown
                        except Exception:
                            pass
                    if on_watch:
                        was_watching = True
                    if has_video:
                        reload_count = 0
                        stuck = 0
                    elif on_watch:
                        stuck += 1
                        if st.get('e') and reload_count < 3:
                            window.load_url(url)           # transient error -> re-navigate fresh
                            reload_count += 1
                    # Back pressed (keyboard hook or in-page listener), stalled too long
                    # (any stuck/error screen), or the player was exited -> return to Kodi.
                    if _BACK_FLAG[0] or st.get('b') or stuck > 50 or (was_watching and not on_watch):
                        _BACK_FLAG[0] = False
                        try:
                            # leave the watch page entirely (not just pause) so the WebView
                            # releases the audio device for Kodi's own local playback.
                            window.evaluate_js('window.__TOKODI=0')
                            window.load_url('https://www.netflix.com/browse')
                        except Exception:
                            pass
                        _set_fullscreen(window, False)
                        _hide_to_kodi(window)
                        was_watching = False
                        reload_count = 0
                        stuck = 0
                elif AUTH_SHOWN[0]:
                    ctx = json.loads(window.evaluate_js(CONTEXT_JS) or '{}')
                    if ctx.get('locoRoot'):
                        _hide_to_kodi(window)
                        AUTH_SHOWN[0] = False
                else:
                    # Not playing and not surfaced for auth: the browse window must not hold
                    # focus. If it grabbed the foreground (e.g. just after login), push it
                    # off-screen and hand focus back to Kodi.
                    try:
                        import ctypes
                        u = ctypes.windll.user32
                        u.GetForegroundWindow.restype = ctypes.c_void_p
                        fg = u.GetForegroundWindow()
                        own = _own_hwnd()
                        if own and fg and int(fg) == int(own):
                            _render_offscreen(window)
                            _focus_kodi()
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                _rpc_lock.release()
        time.sleep(0.5)


def _back_hook_thread():
    """A low-level keyboard hook so Back works during playback even when the WebView does
    NOT hold keyboard focus (Kodi often keeps the foreground). While playing, Esc /
    Backspace / browser-Back set a flag (the monitor then returns to Kodi) and are
    swallowed so Kodi doesn't also act. When not playing, all keys pass through."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        WH_KEYBOARD_LL = 13
        WM_KEYDOWN = 0x0100
        WM_SYSKEYDOWN = 0x0104

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [('vkCode', wintypes.DWORD), ('scanCode', wintypes.DWORD),
                        ('flags', wintypes.DWORD), ('time', wintypes.DWORD),
                        ('dwExtraInfo', ctypes.c_size_t)]

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM,
                                    ctypes.POINTER(KBDLLHOOKSTRUCT))

        def _proc(nCode, wParam, lParam):
            try:
                if nCode == 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    vk = lParam[0].vkCode
                    if _FS[0] and vk in (0x1B, 0x08, 0xA6):   # ESC, BACKSPACE, BROWSER_BACK
                        _BACK_FLAG[0] = True
                        return 1                              # swallow (Kodi shouldn't also act)
                    if _FS[0] and vk == 0x0D:                 # Enter/OK -> Skip Intro / Next if shown
                        _SKIPNEXT_FLAG[0] = True
                        return 1
            except Exception:
                pass
            return u.CallNextHookEx(None, nCode, wParam, lParam)

        cb = HOOKPROC(_proc)
        u.SetWindowsHookExW.restype = ctypes.c_void_p
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
        u.CallNextHookEx.restype = LRESULT
        _HOOK_REFS.append(cb)
        _HOOK_REFS.append(u.SetWindowsHookExW(WH_KEYBOARD_LL, cb, None, 0))
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
    except Exception:
        pass


def on_start(window):
    threading.Thread(target=injector, args=(window,), daemon=True).start()
    threading.Thread(target=_back_hook_thread, daemon=True).start()

    def _icon_later():
        time.sleep(1.5)            # let the native window exist before stamping the icon
        _set_window_icon()
    threading.Thread(target=_icon_later, daemon=True).start()

    if RPC_PORT:
        try:
            _start_rpc(window, RPC_PORT)
        except Exception as exc:
            # port already bound -> another daemon owns it; don't leave a stray window
            sys.stderr.write('rpc start failed (duplicate?); exiting: %s\n' % exc)
            os._exit(1)
    # The window starts off-screen and renders there; nothing to minimize. AUTH_SHOWN is
    # only set when /show or /switch surface it for sign-in/profile (then it returns
    # off-screen once home is reached).
    threading.Thread(target=window_monitor, args=(window,), daemon=True).start()


if not os.path.isdir(CACHE):
    os.makedirs(CACHE)
_profile = os.environ.get('NF_PROFILE') or os.path.join(CACHE, 'profile')


def _cleanup_stale(profile, port):
    """A force-killed/crashed daemon leaves orphaned msedgewebview2 children holding the
    profile lock, so the next launch fails with 'Main window failed to start'. If no
    live daemon owns the RPC port, kill our orphaned WebView2 children and clear the
    lock so this fresh start succeeds."""
    import socket
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(('127.0.0.1', int(port)))
        s.close()
        return   # a live daemon already owns the port -> those children are ITS, leave them
    except Exception:
        try:
            s.close()
        except Exception:
            pass
    tag = 'plugin.video.nfbridge'
    if tag in profile:
        try:
            import subprocess
            ps = ("Get-CimInstance Win32_Process -Filter \"name='msedgewebview2.exe'\" | "
                  "Where-Object { $_.CommandLine -like '*%s*' } | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" % tag)
            subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                           capture_output=True, timeout=15, creationflags=0x08000000)
        except Exception:
            pass
    for lf in (os.path.join(profile, 'EBWebView', 'Default', 'LOCK'),
               os.path.join(profile, 'EBWebView', 'lockfile')):
        try:
            os.remove(lf)
        except OSError:
            pass


if RPC_PORT:
    _cleanup_stale(_profile, RPC_PORT)

# Give the process its own taskbar identity so it doesn't group under python's icon
# (then WM_SETICON's icon is what the taskbar shows).
try:
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('nfbridge.netflix.webview')
except Exception:
    pass

# The browse daemon lives OFF-SCREEN: a hidden/minimized window does NOT render (akira
# never prefetches), but a window positioned off-screen DOES render while staying
# invisible -> data loads with no flash. /play and /show move it on-screen.
_offscreen = bool(RPC_PORT) and os.environ.get('NF_FULLSCREEN') != '1'
_win = webview.create_window('Nflix', URL, width=1600, height=900,
                             x=(-4000 if _offscreen else None),
                             y=(-4000 if _offscreen else None),
                             fullscreen=(os.environ.get('NF_FULLSCREEN') == '1'))
webview.start(on_start, _win, private_mode=False, storage_path=_profile)
