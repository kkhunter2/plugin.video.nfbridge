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
_rpc_lock = threading.Lock()
_rpc_counter = [0]

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


EPISODES_EXTRACT_JS = (
    '(function(){try{var c=netflix.appContext.state.graphqlClient.cache.extract();var out=[];'
    'Object.keys(c).forEach(function(k){var m=k.match(/^Episode:\\{"videoId":(\\d+)\\}/);if(!m)return;'
    'var e=c[k];var snum=null;var ps=(e.parentSeason||{}).__ref;if(ps&&c[ps])snum=c[ps].number;'
    'var art=null;Object.keys(e).forEach(function(f){if(art)return;'
    'if(f.indexOf("artwork")>=0&&f.indexOf("342")>=0){try{if(e[f]&&e[f].url)art=e[f].url;}catch(_){}}});'
    'out.push({id:m[1],title:e.title,number:e.number,season:snum,art:art,'
    'runtime:e.runtimeSec||e.displayRuntimeSec});});'
    'out.sort(function(a,b){return ((a.season||0)-(b.season||0))||((a.number||0)-(b.number||0));});'
    'return JSON.stringify(out);}catch(e){return "[]";}})()')


def _episodes_get(window, vid, timeout=20):
    try:
        window.restore()   # render so the episode list loads (minimized loads only 1)
    except Exception:
        pass
    try:
        window.evaluate_js("location.assign('https://www.netflix.com/title/%s')" % str(vid))
    except Exception:
        pass
    best, stable = [], 0
    for _ in range(timeout * 2):
        try:
            arr = json.loads(window.evaluate_js(EPISODES_EXTRACT_JS) or '[]')
            if len(arr) > len(best):
                best, stable = arr, 0
            elif arr:
                stable += 1
                if stable >= 6:   # ~3s with no new episodes -> settled
                    break
        except Exception:
            pass
        time.sleep(0.5)
    try:
        window.minimize()
    except Exception:
        pass
    return best


def _go_home(window, timeout=30):
    """Navigate back to /browse (from search/play) and re-resolve the home root. The
    window must render for akira to prefetch the home LoCo, so un-minimize first, then
    tuck it away again once home is ready."""
    try:
        window.restore()   # un-minimize (no foreground steal) so akira renders
    except Exception:
        pass
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
    try:
        window.minimize()
    except Exception:
        pass
    return result


def _restore(window):
    """Un-minimize / un-hide and bring the window forward (pywebview-version safe)."""
    for method in ('restore', 'show'):
        try:
            getattr(window, method)()
        except Exception:
            pass
    # Windows: force the Netflix window to the foreground (pywebview restore alone may
    # leave it minimized/behind when called from the RPC thread).
    try:
        import ctypes
        u = ctypes.windll.user32
        hwnd = u.FindWindowW(None, u'Netflix')
        if hwnd:
            u.ShowWindow(hwnd, 9)          # SW_RESTORE
            u.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _start_rpc(window, port):
    import http.server

    def send(h, code, obj):
        body = json.dumps(obj).encode('utf-8')
        h.send_response(code)
        h.send_header('Content-Type', 'application/json')
        h.send_header('Content-Length', str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == '/health':
                try:
                    ready = bool(window.evaluate_js(READY_JS))
                except Exception:
                    ready = False
                send(self, 200, {'ready': ready})
            elif self.path == '/context':
                try:
                    raw = window.evaluate_js(CONTEXT_JS)
                    send(self, 200, json.loads(raw) if raw else {'err': 'no context'})
                except Exception as exc:
                    send(self, 200, {'err': str(exc)})
            elif self.path == '/show':
                # bring the minimized/hidden browse window forward, e.g. for sign-in
                try:
                    window.load_url('https://www.netflix.com/login')
                    _restore(window)
                    send(self, 200, {'ok': True})
                except Exception as exc:
                    send(self, 200, {'err': str(exc)})
            elif self.path == '/quit':
                # used to relaunch with a different profile index
                send(self, 200, {'ok': True})
                threading.Timer(0.3, lambda: os._exit(0)).start()
            else:
                send(self, 404, {'err': 'not found'})

        def do_POST(self):
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
                send(self, 200, {'episodes': _episodes_get(window, req.get('videoId', ''))})
            elif self.path == '/home':
                send(self, 200, _go_home(window))
            elif self.path == '/eval':
                try:
                    send(self, 200, {'result': window.evaluate_js(req.get('js', ''))})
                except Exception as exc:
                    send(self, 200, {'err': str(exc)})
            elif self.path == '/play':
                vid = req.get('videoId')
                try:
                    if vid:
                        window.load_url('https://www.netflix.com/watch/%s' % vid)
                    _restore(window)
                    send(self, 200, {'ok': True})
                except Exception as exc:
                    send(self, 200, {'err': str(exc)})
            else:
                send(self, 404, {'err': 'not found'})

    srv = http.server.HTTPServer(('127.0.0.1', int(port)), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def injector(window):
    captured = False
    while True:
        try:
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
                    window.minimize()
                    return
                window.evaluate_js(SELECT_PROFILE_JS)  # likely on the profile gate
        except Exception:
            pass
        time.sleep(1)


def on_start(window):
    threading.Thread(target=injector, args=(window,), daemon=True).start()
    if RPC_PORT:
        try:
            _start_rpc(window, RPC_PORT)
        except Exception as exc:
            sys.stderr.write('rpc start failed: %s\n' % exc)
    if os.environ.get('NF_MINIMIZE_AFTER_LOAD') == '1':
        threading.Thread(target=_auto_minimize, args=(window,), daemon=True).start()


if not os.path.isdir(CACHE):
    os.makedirs(CACHE)
_profile = os.environ.get('NF_PROFILE') or os.path.join(CACHE, 'profile')
# NOTE: a fully hidden window does not render, so the akira UI never prefetches the
# home LoCo (no locoRoot). A *minimized* window renders (prefetch happens) but stays
# out of the way; /play and /show restore it. Prefer minimized for the browse daemon.
_win = webview.create_window('Netflix', URL, width=1600, height=900,
                             hidden=(os.environ.get('NF_HIDDEN') == '1'),
                             minimized=(os.environ.get('NF_MINIMIZED') == '1'),
                             fullscreen=(os.environ.get('NF_FULLSCREEN') == '1'))
webview.start(on_start, _win, private_mode=False, storage_path=_profile)
