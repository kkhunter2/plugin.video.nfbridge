# -*- coding: utf-8 -*-
"""Embedded Netflix player for Windows: Microsoft Edge WebView2 (system Edge engine,
Netflix-trusted -> plays incl. PlayReady SL3000 / 4K) hosting netflix.com, with the
Kodi subtitle font + style injected.

Reads from the environment:
  NF_URL            the netflix.com URL to open (browse)
  NF_RPC_PORT       localhost port for the browse/play RPC server (daemon mode)
  NF_PROFILE        WebView2 user-data dir (login cookies, DRM provisioning)
  NF_PROFILE_INDEX  profile to auto-select at the "Who's watching?" gate (0 = first)
  NF_FONT_WOFF2     pre-repaired (OTS-valid) Kodi subtitle font, see fontfix.py (optional)
  NF_FONT_NAME      the Kodi subtitle font NAME; part of the daemon version key (optional)
  NF_STYLE          JSON of Kodi's full subtitle appearance (see kodi_subs.style()): colour +
                    opacity, bold/italic, line height, outline (outlineColor/outlineEm),
                    background box (bgType/bgColor/bgOpacity), drop shadow (shadow*), blur.
                    Optional; any subset is honoured.
  NF_OSD_LABELS     JSON of localized OSD labels {play,pause,subtitles,next,back} (optional)

The font is loaded via the FontFace binary API (bypasses Netflix CSP font-src); Netflix's
own subtitle text/timing/position/DRM are untouched — we only override the font (and
optionally colour/outline) with an !important CSS rule.
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

# DPI awareness must be set BEFORE any window exists (a later call is ignored), so the
# borderless-fullscreen math (_set_fullscreen) sees real pixels on scaled displays.
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
except Exception:
    pass

import webview  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')

URL = os.environ.get('NF_URL', 'https://www.netflix.com/')
try:
    STYLE = json.loads(os.environ.get('NF_STYLE', '{}'))
except Exception:
    STYLE = {}
_OSD_DEFAULT = {'play': 'Play', 'pause': 'Pause', 'subtitles': 'Subtitles',
                'next': 'Next episode', 'back': 'Back'}
try:
    OSD_LABELS = dict(_OSD_DEFAULT, **json.loads(os.environ.get('NF_OSD_LABELS', '{}')))
except Exception:
    OSD_LABELS = dict(_OSD_DEFAULT)

_SEL = ('.player-timedtext,.player-timedtext-text-container,'
        '.player-timedtext-text-container span,.player-timedtext span')
# size + vertical position belong on the container (Netflix sets those inline there)
_CONT_SEL = '.player-timedtext-text-container,.player-timedtext'


def _rgba(hex_color, alpha):
    """('#RRGGBB', 0..1) -> 'rgba(r,g,b,a)'; passes the colour through on any parse error."""
    try:
        h = hex_color.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return 'rgba(%d,%d,%d,%.3g)' % (r, g, b, max(0.0, min(1.0, float(alpha))))
    except Exception:
        return hex_color

# ---- build the font loader (FontFace binary, CSP-safe) ----
# The addon validates/repairs the font before launch (fontfix) and passes the WOFF2.
FONT_WOFF2 = os.environ.get('NF_FONT_WOFF2', '')
LOAD_JS = ''
_family = 'inherit'
_woff2 = FONT_WOFF2 if FONT_WOFF2 and os.path.exists(FONT_WOFF2) else None
if _woff2:
    b64 = fontfix.woff2_base64(_woff2)
    _family = 'KodiSubFont'
    LOAD_JS = ('if(!window.__KSF){window.__KSF=1;try{'
               'var b=atob("' + b64 + '");var a=new Uint8Array(b.length);'
               'for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);'
               'new FontFace("KodiSubFont",a.buffer).load().then(function(f){document.fonts.add(f);});'
               '}catch(e){console.log("KSF "+e);}}')

# ---- restyle Netflix's OWN subtitle nodes with Kodi's font attributes ----
# DRM-safe: pure CSS on Netflix's `.player-timedtext` elements — we add NO overlay element
# over the hardware-protected <video> and never touch the video or its parents (doing so
# breaks the protected compositing path -> Netflix decode error D7361). The trade-off: we
# do NOT override subtitle SIZE or POSITION (Netflix scales each cue to fit the width, and
# forcing a size just fights that scaler), so font size/placement stay Netflix-native; every
# other Kodi attribute (font, colour, opacity, weight, slant, line spacing, outline, box,
# shadow, blur) IS applied. (An overlay can pin the exact Kodi size but risks the DRM error.)
def _build_sub_css():
    text = []   # applied to Netflix's subtitle text nodes
    if _family != 'inherit':
        text.append('font-family:"%s",sans-serif !important' % _family)
    if STYLE.get('color'):
        text.append('color:%s !important' % STYLE['color'])
    if STYLE.get('bold'):
        text.append('font-weight:700 !important')
    if STYLE.get('italic'):
        text.append('font-style:italic !important')
    if STYLE.get('lineHeight'):
        text.append('line-height:%s !important' % STYLE['lineHeight'])

    # outline (+ drop shadow when Kodi's background type is "shadow") via text-shadow; em
    # units track Netflix's chosen font size so the edge stays proportional.
    shadows = []
    oe = float(STYLE.get('outlineEm', 0) or 0)
    if oe > 0 and STYLE.get('outlineColor'):
        oc = STYLE['outlineColor']
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    shadows.append('%.4gem %.4gem 0 %s' % (dx * oe, dy * oe, oc))
    if STYLE.get('bgType') == 'shadow':
        se = float(STYLE.get('shadowEm', 0) or 0)
        if se > 0:
            sc = _rgba(STYLE.get('shadowColor', '#000000'), STYLE.get('shadowOpacity', 1.0))
            shadows.append('%.4gem %.4gem %.4gem %s' % (se, se, se * 0.5, sc))
    if shadows:
        text.append('text-shadow:%s !important' % ','.join(shadows))

    be = float(STYLE.get('blurEm', 0) or 0)
    if be > 0:
        text.append('filter:blur(%.4gem) !important' % be)

    if STYLE.get('bgType') in ('box', 'square'):
        text.append('background-color:%s !important'
                    % _rgba(STYLE.get('bgColor', '#000000'), STYLE.get('bgOpacity', 0.8)))
        text.append('-webkit-box-decoration-break:clone !important')
        text.append('box-decoration-break:clone !important')
        text.append('padding:%s !important'
                    % ('0.1em 1em' if STYLE.get('bgType') == 'square' else '0.1em 0.35em'))

    # text opacity -> fade the whole subtitle (text + outline + box) together
    cont = []
    op = float(STYLE.get('opacity', 1.0) or 1.0)
    if op < 1.0:
        cont.append('opacity:%.3g !important' % max(0.0, op))

    out = ''
    if text:
        out += '%s{%s}' % (_SEL, ';'.join(text))
    if cont:
        out += '%s{%s}' % (_CONT_SEL, ';'.join(cont))
    return out

_CSS = _build_sub_css()
# inject (or refresh) the stylesheet on Netflix's own subtitle nodes — no overlay element
STYLE_JS = ('var s=document.getElementById("nf-subfont");'
            'if(!s){s=document.createElement("style");s.id="nf-subfont";'
            '(document.head||document.documentElement).appendChild(s);}'
            'var c=' + json.dumps(_CSS) + ';if(s.textContent!==c)s.textContent=c;'
            'var ov=document.getElementById("nfsubs-overlay");if(ov)ov.remove();'
            'var oh=document.getElementById("nfov-hide");if(oh)oh.remove();') if _CSS else ''

# Hide the scrollbar: when subtitles render the page overflows a hair and the vertical
# scrollbar's up/down arrows show as white dots top-right/bottom-right of the screen.
# Hide the scrollbar AND most Netflix player controls, keeping only the seek bar
# (timeline*) and the play/pause button. Each control has a stable data-uia, so showing/
# hiding any of them is just editing this list.
# Keep ONLY the seek bar (timeline) and the time; everything else (play/pause, audio/
# subtitle, etc.) is handled by our own OSD (OK during playback), so hide it.
_CONTROL_HIDE = (
    "[data-uia='control-nav-back'],[data-uia='control-flag'],[data-uia='control-back10'],"
    "[data-uia='control-forward10'],[data-uia^='control-volume'],[data-uia='control-next'],"
    "[data-uia='control-episodes'],[data-uia='control-speed'],[data-uia^='control-play-pause'],"
    "[data-uia='control-audio-subtitle'],[data-uia^='control-fullscreen'],[data-uia='video-title']"
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
_login = [False]   # last-known "page is the sign-in form" (so callers needn't wait)
_BACK_FLAG = [False]    # low-level keyboard hook sets this when Back is pressed in playback
_HOOK_REFS = []

# Kodi-style OSD navigation of the player controls during playback. The low-level keyboard
# hook can't read the DOM, so it just queues actions; _nav_handler runs them via __NFNAV
# (injected page controller). _NAV mirrors __NFNAV's active state so the hook knows whether
# arrow keys should drive the OSD (active) or pass through to Netflix seek/volume (inactive).
_NAV = [False]
_NAV_Q = []
_NAV_EV = threading.Event()
_NAV_LAST = [0.0]


def _nav_queue(act):
    _NAV_Q.append(act)
    _NAV_EV.set()


_NAV_FN = {'ok': 'ok()', 'left': 'move(-1)', 'right': 'move(1)', 'exit': 'exit()'}

_OSD_LOG = os.path.join(os.environ.get('TEMP') or os.environ.get('TMP') or '.', 'nfbridge_osd.log')


def _osd_log(msg):
    try:
        with open(_OSD_LOG, 'a', encoding='utf-8') as fh:
            fh.write(msg + '\n')
    except Exception:
        pass

# Injected once. A self-contained OSD overlay built from OUR OWN DOM — immune to Netflix's
# "minimized" player view (its controls aren't even mounted) and its hardening against
# synthetic events. Navigation drives the Netflix INTERNAL player API directly (play/pause,
# subtitle tracks incl. off, next episode), so nothing depends on Netflix's own UI.
# ok()/move()/exit() return "1:.."/"0:.." = OSD active or not.
NAV_JS = r'''if(!window.__NFNAV){window.__NFNAV=(function(){
var L=__OSD_LABELS__;
function P(){try{var vp=netflix.appContext.state.playerApp.getAPI().videoPlayer;
var ids=vp.getAllPlayerSessionIds();var sid=ids.filter(function(s){return /watch/.test(s);})[0]||ids[0];
return vp.getVideoPlayerBySessionId(sid);}catch(e){return null;}}
function nextId(){try{var api=netflix.appContext.state.playerApp.getAPI();
var vp=api.videoPlayer;var ids=vp.getAllPlayerSessionIds();
var sid=ids.filter(function(s){return /watch/.test(s);})[0]||ids[0];
var cur=api.getVideoIdBySessionId?api.getVideoIdBySessionId(sid):null;
if(!cur){var m=location.href.match(/watch\/(\d+)/);cur=m?+m[1]:null;}
var md=api.getVideoMetadataByVideoId(cur);
var cv=md&&md.getCurrentVideo&&md.getCurrentVideo();
var ne=cv&&cv.getNextEpisode&&cv.getNextEpisode();
return (ne&&ne.getId&&ne.getId())||0;}catch(e){return 0;}}
var active=false,level=0,idx=0,cache=[],EL=null;
function el(){if(EL&&document.body.contains(EL))return EL;
EL=document.createElement("div");EL.id="__nfosd";
EL.style.cssText="position:fixed;left:50%;bottom:9%;transform:translateX(-50%);z-index:2147483647;background:rgba(0,0,0,0.88);border-radius:12px;padding:10px 0;min-width:380px;max-height:64%;overflow:auto;font-family:Arial,sans-serif;font-size:23px;color:#fff;box-shadow:0 4px 40px rgba(0,0,0,.9);";
(document.fullscreenElement||document.body).appendChild(EL);return EL;}
function build(){var p=P();cache=[];
if(level===0){var paused=false;try{paused=p&&p.getPaused();}catch(e){}
cache.push({t:(paused?"▶  "+L.play:"❙❙  "+L.pause),f:function(){var q=P();try{if(q.getPaused())q.play();else q.pause();}catch(e){}build();draw();}});
cache.push({t:L.subtitles+"  ›",f:function(){level=1;idx=0;build();draw();}});
if(nextId())cache.push({t:L.next,next:true});}
else{cache.push({t:"‹  "+L.back,f:function(){level=0;idx=1;build();draw();}});
try{var list=p.getTimedTextTrackList();var cur=null;try{cur=p.getTimedTextTrack();}catch(e){}
var cid=cur&&(cur.trackId||cur.id);
list.forEach(function(tr){var tid=tr.trackId||tr.id;var on=(tid===cid);
cache.push({t:(on?"● ":"  ")+(tr.displayName||tr.bcp47||tr.language||"?"),f:function(){var q=P();try{q.setTimedTextTrack(tr);}catch(e){}level=0;idx=1;build();draw();}});});}catch(e){}}}
function draw(){var d=el();if(idx>=cache.length)idx=cache.length-1;if(idx<0)idx=0;
var h="";cache.forEach(function(it,i){h+="<div style='padding:9px 28px;white-space:nowrap;"+(i===idx?"background:#e50914;color:#fff;":"color:#dcdcdc;")+"'>"+it.t+"</div>";});
d.innerHTML=h;d.style.display="block";var sel=d.children[idx];if(sel){try{sel.scrollIntoView({block:"nearest"});}catch(e){}}}
function hide(){active=false;if(EL)EL.style.display="none";}
function open(){active=true;level=0;idx=0;build();draw();}
return{
ok:function(){if(!active){
var ss=["player-skip-intro","player-skip-recap","watch-video-skip-content","next-episode-seamless-button-draining","next-episode-seamless-button"];
for(var i=0;i<ss.length;i++){var e=document.querySelector('[data-uia="'+ss[i]+'"]');if(e&&e.offsetParent!==null){try{e.click();return "1:skip";}catch(x){}}}
open();return "1:open";}
var it=cache[idx];
if(it&&it.next){var id=nextId();hide();return id?("2:next:"+id):"1:nonext";}
if(it&&it.f)it.f();return active?"1:ok":"0:ok";},
move:function(d){if(!active){open();return "1:open";}idx=(idx+d+cache.length)%cache.length;draw();return "1:m"+idx;},
exit:function(){if(level===1){level=0;idx=1;build();draw();return "1:back";}hide();return "0:exit";}
};})();}'''.replace('__OSD_LABELS__', json.dumps(OSD_LABELS, ensure_ascii=False))


def _runtime_file():
    """The file whose mtime identifies this build: the exe when frozen (PyInstaller runs
    the entry script from its archive, so __file__ does NOT exist on disk), else this
    script. player._code_ver() applies the same rule."""
    if getattr(sys, 'frozen', False):
        return sys.executable
    return os.path.abspath(__file__)


try:
    # restart the daemon on code change OR subtitle-font change (the font is applied at
    # launch, so a running daemon must relaunch to pick up a newly chosen font).
    _CODE_VER = (str(int(os.path.getmtime(_runtime_file())))
                 + '|' + os.environ.get('NF_FONT_NAME', ''))
except Exception:
    _CODE_VER = '0'

READY_JS = ('!!(window.netflix&&netflix.appContext&&netflix.appContext.state&&'
            'netflix.appContext.state.pathEvaluator&&netflix.reactContext&&'
            'netflix.reactContext.models.userInfo&&netflix.reactContext.models.userInfo.data.authURL)')

# True only on the real home page (/browse), NOT /browse/genre/<id>, /search, /watch …
# (a genre page also has a LoCo root in falcorCache, so locoRoot alone can't tell them apart)
_ON_HOME_JS = '/^https?:\\/\\/[^\\/]+\\/browse\\/?(\\?.*)?$/.test(location.href)'

# fresh page context: shakti session bits + the current LoCo root id + where the page is.
CONTEXT_JS = ('(function(){try{var m=netflix.reactContext.models,ui=m.userInfo.data,sd=m.serverDefs.data;'
              'var root=null;try{var lc=netflix.falcorCache&&netflix.falcorCache.locos;'
              'if(lc){root=Object.keys(lc).filter(function(k){return k.indexOf("$")<0;})[0];}}catch(e){}'
              'return JSON.stringify({authURL:ui.authURL,build:sd.BUILD_IDENTIFIER,guid:ui.guid,'
              'country:ui.countryOfSignup,locoRoot:root,url:location.href,home:' + _ON_HOME_JS + ','
              # the ACTIVE profile: reactContext's userInfo.guid is the account owner's and
              # does not change on a profile switch; Apollo's ROOT_QUERY.currentProfile does
              'profile:(function(){try{var c=netflix.appContext.state.graphqlClient.cache.extract();'
              'var r=(c.ROOT_QUERY||{}).currentProfile;var m=r&&r.__ref&&r.__ref.match(/"guid":"([^"]+)"/);'
              'return m?m[1]:null;}catch(e){return null;}})()});'
              # while the page is still loading (no netflix.* yet) report the URL-derived
              # `home` flag too, so _go_home doesn't re-navigate a /browse load every tick
              '}catch(e){return JSON.stringify({err:String(e),url:location.href,home:' + _ON_HOME_JS + '});}})()')


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
    'var syn=null;var ent=c[ue]||{};Object.keys(ent).forEach(function(f){if(syn)return;'
    'if(/ynopsis/.test(f)){var v=ent[f];if(typeof v==="string")syn=v;'
    'else if(v&&typeof v.text==="string")syn=v.text;}});'
    'out.push({id:m[2],type:m[1].toLowerCase(),title:n.displayString,art:art,synopsis:syn});});'
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
            if i % 4 == 3 and _on_profile_gate(window):
                window.evaluate_js('window.__PSEL=0')
                window.evaluate_js(_select_profile_js())
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


# Header account menu: one <button data-uia="navigation+profile-menu+switch-profile-<GUID>">
# (or "...switch-profile-kids-<GUID>") per OTHER profile. Clicking it switches in place.
# (/ProfilesGate now just redirects to /browse, so the gate can't be summoned by URL.)
_SWITCH_BTN_JS = ('(function(g){var b=document.querySelector(\'[data-uia^="navigation+profile-menu+switch-profile"]'
                  '[data-uia$="-\'+g+\'"]\');if(!b)return "nobtn";b.click();return "clicked";})(%s)')


def _active_profile(window):
    try:
        return (json.loads(window.evaluate_js(CONTEXT_JS) or '{}') or {}).get('profile') or ''
    except Exception:
        return ''


def _switch_profile(window, index=0, guid='', timeout=25):
    """Switch to a profile — by guid via the header account menu's switch button (works
    from any page with the nav header), else at the 'Who's watching' gate by guid/index.
    Success is judged on the ACTIVE profile (Apollo currentProfile). Returns {ok, guid}.
    If nothing works in time, surface the window so the user can click."""
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 0
    PROFILE_INDEX[0] = index        # the monitor / episodes gate-handling now use it too
    PROFILE_GUID[0] = str(guid or '')
    _set_fullscreen(window, False)
    _render_offscreen(window)
    if guid and _active_profile(window) == guid:
        return {'ok': True, 'guid': guid, 'changed': False}
    # make sure we're on a page with the nav header (the home)
    try:
        if not (json.loads(window.evaluate_js(CONTEXT_JS) or '{}') or {}).get('home'):
            window.evaluate_js("window.__PSEL=0;location.assign('https://www.netflix.com/browse')")
    except Exception:
        pass
    clicked = False
    for _ in range(timeout * 2):
        time.sleep(0.5)
        try:
            if not clicked:
                if _on_profile_gate(window):
                    clicked = window.evaluate_js(_select_profile_js()) == 'clicked'
                elif guid:
                    clicked = window.evaluate_js(_SWITCH_BTN_JS % json.dumps(guid)) == 'clicked'
                continue
            cur = _active_profile(window)
            if cur and (not guid or cur == guid) and not _on_profile_gate(window):
                return {'ok': True, 'guid': cur, 'changed': True}
        except Exception:
            pass
    # fallback: let the user pick in the visible window (the monitor hides it once home)
    AUTH_SHOWN[0] = True
    _restore(window)
    return {'ok': False, 'shown': True}


def _on_profile_gate(window):
    """True while the 'Who's watching?' gate is up (profile tiles present; see GATE_SEL)."""
    try:
        return (window.evaluate_js('document.querySelectorAll(%s).length' % json.dumps(GATE_SEL)) or 0) >= 1
    except Exception:
        return False


# ---------------------------------------------------------------- Pinot pages ---
# Since the 2026-10 Netflix build the browse pages (/browse, /browse/genre/<id>,
# /browse/my-list) are no longer falcor LoCo ("locos") but GraphQL "Pinot" pages, kept
# in the page's Apollo cache:  Pinot*Page -> sections(...).edges -> PinotCarouselSection
# {displayString, entities.edges -> Pinot*EntityTreatment {displayString, unifiedEntity
# -> Movie|Show:{videoId}, contextualArtwork.artwork.url}}. Sections and each row's
# entities are paginated by the page itself (infinite scroll / carousel arrows), so we
# scroll/click the off-screen page to load more, then read the cache. Falcor still
# serves per-video data (videos[id].synopsis/bookmark/episodes), so that stays.
PINOT_ROWS_JS = r'''(function(opt){try{
var c=netflix.appContext.state.graphqlClient.cache.extract();
var best=null,bestN=-1;
Object.keys(c).forEach(function(k){if(!/^Pinot\w*Page:/.test(k))return;var p=c[k];var n=0;
 Object.keys(p).forEach(function(f){if(f.indexOf("sections")===0)n+=((p[f]||{}).edges||[]).length;});
 if(n>bestN){bestN=n;best=p;}});
if(!best)return JSON.stringify({rows:[],err:"no pinot page"});
var seen={},rows=[],idx=0;
Object.keys(best).forEach(function(f){if(f.indexOf("sections")!==0)return;
 ((best[f]||{}).edges||[]).forEach(function(ed){var ref=(ed.node||{}).__ref;if(!ref||seen[ref])return;seen[ref]=1;
  var s=c[ref];if(!s||!/CarouselSection/.test(s.__typename||""))return;
  var row={index:idx++,title:s.displayString||"",id:s.id||"",total:((s.entities||{}).totalCount)||0,videos:[]};
  if(opt.videos||opt.row===row.index||(opt.title&&opt.title===row.title)){
   var vseen={};
   Object.keys(s).forEach(function(g){if(g.indexOf("entities")!==0)return;
    ((s[g]||{}).edges||[]).forEach(function(e){var tr=c[(e.node||{}).__ref];if(!tr)return;
     var ue=(tr.unifiedEntity||{}).__ref||"";var m=ue.match(/^(Movie|Show|Episode|Supplemental):\{"videoId":(\d+)\}/);
     if(!m||vseen[m[2]])return;vseen[m[2]]=1;
     var art=null,ca=tr.contextualArtwork;if(ca){Object.keys(ca).forEach(function(h){if(art)return;var v=ca[h];if(v&&v.url)art=v.url;});}
     var ent=c[ue]||{},syn=null;
     Object.keys(ent).forEach(function(h){if(syn||h.indexOf("contextualSynopsis")!==0)return;var v=ent[h];
      if(v&&v.__ref)v=c[v.__ref];if(v&&typeof v.text==="string")syn=v.text;});
     row.videos.push({id:m[2],type:m[1].toLowerCase(),title:tr.displayString||ent.title||m[2],art:art,
      synopsis:syn,bookmark:(ent.bookmark&&ent.bookmark.position)||null,runtime:ent.runtimeSec||ent.displayRuntimeSec||null,
      isOriginal:ent.hasOriginalTreatment||false});
    });});
  }
  rows.push(row);
 });});
return JSON.stringify({rows:rows});
}catch(e){return JSON.stringify({rows:[],err:String(e)});}})(%s)'''

# Bring the page's "browse-page+sentinel" INTO the viewport: its IntersectionObserver then
# loads the next batch of sections (~16). Scrolling straight to the bottom flies past it
# (no intersection -> nothing loads). Returns "nosent" once every section is loaded.
SCROLL_SENTINEL_JS = ('(function(){var s=document.querySelector("[data-uia=\'browse-page+sentinel\']");'
                      'if(!s)return "nosent";var r=s.getBoundingClientRect();'
                      'window.scrollBy(0,r.top-innerHeight/2);return "ok";})()')
# click the right arrow of the carousel whose heading text equals the title (loads the
# row's next entity page); returns "clicked" / "nf"
ROW_NEXT_JS = r'''(function(t,i){var rows=document.querySelectorAll('[data-uia^="carousel-row-section-"]');var r=null;
for(var k=0;k<rows.length;k++){var h=rows[k].querySelector("h2,h3,[data-uia*=title],[data-uia*=heading]");
 if(h&&(h.innerText||"").trim()===t){r=rows[k];break;}}
if(!r&&rows[i])r=rows[i];if(!r)return "nf";
try{r.scrollIntoView({block:"center"});}catch(e){}
var b=r.querySelector('[data-uia="carousel-hawkins-right-button"]');if(!b)return "nobtn";b.click();return "clicked";})(%s,%d)'''


def _pinot_rows(window, **opt):
    try:
        return json.loads(window.evaluate_js(PINOT_ROWS_JS % json.dumps(opt)) or '{}') or {}
    except Exception as exc:
        return {'rows': [], 'err': str(exc)}


def _pinot_page(window, path='/browse', rows=40, row=None, title=None, n=40, videos=False, timeout=25):
    """Serve a browse page's rows from the Pinot cache.
      path   : /browse | /browse/genre/<id> | /browse/my-list (navigated to if not there)
      rows   : load at least this many sections (scrolls the page)
      row    : with title: return THAT row's videos, after paging its carousel to >= n
      videos : include each loaded row's (currently loaded) videos
    Returns {path, rows:[{index,title,id,total,videos}]} (videos empty unless asked)."""
    _set_fullscreen(window, False)
    _render_offscreen(window)
    url = 'https://www.netflix.com' + path
    try:
        cur = window.evaluate_js('location.pathname') or ''
    except Exception:
        cur = ''
    if cur.rstrip('/') != path.rstrip('/'):
        try:
            window.evaluate_js("window.__PSEL=0;location.assign(%s)" % json.dumps(url))
        except Exception:
            pass
    # wait for the page (handle the profile gate and the /account bounce on the way)
    data = {}
    for _t in range(timeout * 2):
        data = _pinot_rows(window)
        if data.get('rows'):
            break
        try:
            if _on_profile_gate(window):
                window.evaluate_js(_select_profile_js())
            elif '/account' in (window.evaluate_js('location.pathname') or '') or (_t and _t % 20 == 0):
                window.evaluate_js("window.__PSEL=0;location.assign(%s)" % json.dumps(url))
        except Exception:
            pass
        time.sleep(0.5)
    want = max(rows, (row or 0) + 1)
    stale = 0
    while len(data.get('rows') or []) < want and stale < 3:
        before = len(data.get('rows') or [])
        try:
            if window.evaluate_js(SCROLL_SENTINEL_JS) == 'nosent':
                break                      # everything the page has is loaded
        except Exception:
            pass
        time.sleep(2.0)
        data = _pinot_rows(window)
        stale = stale + 1 if len(data.get('rows') or []) <= before else 0
    if row is None and not title:
        if videos:
            data = _pinot_rows(window, videos=True)
        _focus_kodi()
        return {'path': path, 'rows': data.get('rows') or [], 'err': data.get('err')}
    # one row: page its carousel until it holds >= n entities (or stops growing)
    sel = _pinot_rows(window, row=row, title=title or '')
    pick = _pick_row(sel, row, title)
    stale, clicks = 0, 0
    # each arrow click advances one viewport of tiles; the carousel fetches its next
    # entity page (~16) as it nears the loaded end, so a few clicks are needed per page
    while pick and len(pick['videos']) < min(n, pick.get('total') or n) and stale < 3 and clicks < 10:
        before = len(pick['videos'])
        try:
            window.evaluate_js(ROW_NEXT_JS % (json.dumps(pick['title']), int(pick['index'])))
        except Exception:
            pass
        clicks += 1
        time.sleep(1.2)
        sel = _pinot_rows(window, row=row, title=title or '')
        pick = _pick_row(sel, row, title) or pick
        stale = stale + 1 if len(pick['videos']) <= before else 0
    _focus_kodi()
    return {'path': path, 'rows': sel.get('rows') or [], 'row': pick, 'err': sel.get('err')}


def _pick_row(data, row, title):
    rows = data.get('rows') or []
    if title:
        for r in rows:
            if r.get('title') == title:
                return r
    if row is not None and 0 <= int(row) < len(rows):
        return rows[int(row)]
    return None


def _go_home(window, timeout=25):
    """Navigate back to /browse (from search/play/title) and wait for the home rows."""
    return _pinot_page(window, '/browse', rows=1, timeout=timeout)


_FS = [False]
_PREV_STYLE = [0]


def _set_square_corners(hwnd, on):
    """Windows 11 rounds the corners of any window that doesn't exactly cover a monitor
    and draws a 1px light border around it. Our overscanned fullscreen window counts, so
    the rounded border showed as small light dots in all four screen corners during
    playback. While fullscreen: no rounding + black border (the window size is kept);
    afterwards: system defaults. No-op before Windows 11 (the attributes don't exist)."""
    try:
        import ctypes
        DWMWA_WINDOW_CORNER_PREFERENCE, DWMWA_BORDER_COLOR = 33, 34
        DWMWCP_DEFAULT, DWMWCP_DONOTROUND = 0, 1
        DWMWA_COLOR_DEFAULT, BLACK = 0xFFFFFFFF, 0x00000000
        corner = ctypes.c_int(DWMWCP_DONOTROUND if on else DWMWCP_DEFAULT)
        border = ctypes.c_uint(BLACK if on else DWMWA_COLOR_DEFAULT)
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
                                  ctypes.byref(corner), ctypes.sizeof(corner))
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_BORDER_COLOR,
                                  ctypes.byref(border), ctypes.sizeof(border))
    except Exception:
        pass


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
        sw = u.GetSystemMetrics(0)
        sh = u.GetSystemMetrics(1)
        if on and not _FS[0]:
            # (DPI awareness is set at import, before the window exists; see top of file)
            _PREV_STYLE[0] = u.GetWindowLongW(hwnd, GWL_STYLE)
            _set_taskbar(hwnd, True)
            u.SetWindowLongW(hwnd, GWL_STYLE, -2147483648 | 0x10000000)  # WS_POPUP|WS_VISIBLE
            # HWND_TOP(0) not TOPMOST: cover the screen but still let Alt+Tab bring another
            # window in front. 2px overscan each side, FRAMECHANGED|SHOWWINDOW.
            u.SetWindowPos(hwnd, 0, -2, -2, sw + 4, sh + 4, 0x0020 | 0x0040)
            _set_square_corners(hwnd, True)
            _FS[0] = True
        elif not on and _FS[0]:
            _set_square_corners(hwnd, False)
            u.SetWindowLongW(hwnd, GWL_STYLE, _PREV_STYLE[0] or 0x00CF0000)
            u.SetWindowPos(hwnd, 0, -sw - 200, -sh - 200, sw, sh, 0x0020 | 0x0010)  # FRAMECHANGED|NOACTIVATE
            _FS[0] = False
            _NAV[0] = False          # leaving playback -> drop any OSD-nav state
            del _NAV_Q[:]
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


def _is_foreground():
    """True only when the daemon's own window is the foreground window. The playback key
    hook is global (system-wide), so without this an Alt+Tab away would still let our hook
    swallow Enter/arrows and pop the OSD over another app."""
    try:
        import ctypes
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = ctypes.c_void_p
        fg = u.GetForegroundWindow()
        own = _own_hwnd()
        return bool(own and fg and int(fg) == int(own))
    except Exception:
        return False


def _foreground_is_daemon():
    """True if the foreground window belongs to OUR daemon UI — our own process OR a
    WebView2 child (msedgewebview2.exe). WebView2 renders in separate processes, so right
    after login the focused window is an Edge child with a DIFFERENT pid; _own_hwnd()/
    _is_foreground() miss it (they only match our own pid's window), which is why focus
    never returned to Kodi until the user clicked. This catches the Edge-child case so the
    monitor can hand focus back."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        u.GetForegroundWindow.restype = ctypes.c_void_p
        fg = u.GetForegroundWindow()
        if not fg:
            return False
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(ctypes.c_void_p(fg), ctypes.byref(pid))
        if pid.value == k.GetCurrentProcessId():
            return True
        h = k.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value.lower().endswith('msedgewebview2.exe')
            return False
        finally:
            k.CloseHandle(h)
    except Exception:
        return False


def _set_window_icon():
    """Replace python's default window/taskbar icon with the add-on (Netflix) icon."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        # frozen: PyInstaller unpacks --add-data files next to sys._MEIPASS, not __file__
        ico = os.path.join(getattr(sys, '_MEIPASS', HERE), 'icon.ico')
        if not os.path.exists(ico):
            ico = os.path.join(HERE, 'icon.ico')
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


def _set_taskbar(hwnd, visible):
    """Show/hide the window's taskbar button. While the window lives off-screen a taskbar
    button is a trap: clicking it 'activates' a window nobody can see. Toggle
    WS_EX_TOOLWINDOW (no taskbar button) <-> WS_EX_APPWINDOW; the ex-style only takes
    effect while the window is hidden, so hide/re-show around the change."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        if not hwnd:
            return
        GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x00000080, 0x00040000
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        want = (ex & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW if visible else (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        if want == ex:
            return
        u.ShowWindow(hwnd, 0)                  # SW_HIDE
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, want)
        u.ShowWindow(hwnd, 4)                  # SW_SHOWNOACTIVATE
    except Exception:
        pass


def _render_offscreen(window):
    """Show the window OFF-SCREEN so it keeps rendering, WITHOUT activating it (so Kodi
    keeps keyboard focus) and without a taskbar button. SW_SHOWNOACTIVATE + a no-activate
    move; falls back to pywebview."""
    try:
        import ctypes
        u = ctypes.windll.user32
        hwnd = _own_hwnd()
        if hwnd:
            u.ShowWindow(hwnd, 4)   # SW_SHOWNOACTIVATE (un-minimize / show, no focus steal)
            # screen-sized (physical px) so Netflix lays the page out at full width: more
            # tiles per row / sections per viewport are loaded. Placed entirely beyond the
            # top-left of the virtual screen so nothing shows.
            sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
            u.SetWindowPos(hwnd, 0, -sw - 200, -sh - 200, sw, sh, 0x0014)  # NOACTIVATE|NOZORDER
            _set_taskbar(hwnd, False)
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
        # drop the foreground-lock timeout so SetForegroundWindow actually takes (Windows
        # otherwise silently refuses a foreground change from the background) — and put the
        # user's value back afterwards: it is a SYSTEM-WIDE setting, not ours to keep.
        prev_timeout = None
        try:
            val = ctypes.c_uint(0)
            if u.SystemParametersInfoW(0x2000, 0, ctypes.byref(val), 0):   # SPI_GETFOREGROUNDLOCKTIMEOUT
                prev_timeout = val.value
            u.SystemParametersInfoW(0x2001, 0, 0, 0)                        # SPI_SETFOREGROUNDLOCKTIMEOUT = 0
        except Exception:
            pass
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
            if prev_timeout:
                try:
                    u.SystemParametersInfoW(0x2001, 0, ctypes.c_void_p(prev_timeout), 0)
                except Exception:
                    pass
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


def _move_onscreen(hwnd):
    """The window normally lives OFF-SCREEN (-4000,-4000; see _render_offscreen). Before
    showing it to the user (sign-in / profile gate / play) put it back on the primary
    screen, centred, else 'show' just shows an invisible window."""
    try:
        import ctypes
        u = ctypes.windll.user32
        if not hwnd:
            return
        sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        # physical px (the process is per-monitor DPI aware): size the window to ~5/6 of
        # the screen so it is comfortable on a 4K display too, then centre it
        w, h = int(sw * 5 / 6), int(sh * 5 / 6)
        _set_taskbar(hwnd, True)
        u.SetWindowPos(hwnd, 0, (sw - w) // 2, (sh - h) // 2, w, h, 0x0040)  # SWP_SHOWWINDOW
    except Exception:
        pass


def _restore(window):
    """Bring the daemon's own window on-screen and forward WITH keyboard focus (so the
    user can sign in / pick a profile, and Back / player keys work during playback)."""
    for method in ('restore', 'show'):
        try:
            getattr(window, method)()
        except Exception:
            pass
    hwnd = _own_hwnd()
    if not _FS[0]:
        _move_onscreen(hwnd)
    _force_foreground(hwnd)


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
                # never blocks: refresh readiness only if the window is free this instant.
                # 'login' = the page sits on the sign-in form, so a caller waiting for
                # 'ready' can give up at once instead of waiting out its timeout.
                if _rpc_lock.acquire(blocking=False):
                    try:
                        _ready[0] = bool(window.evaluate_js(READY_JS))
                        _login[0] = '/login' in (window.evaluate_js('location.href') or '')
                    except Exception:
                        pass
                    finally:
                        _rpc_lock.release()
                return send(self, 200, {'ready': _ready[0], 'login': _login[0],
                                        'ver': _CODE_VER, 'playing': _FS[0]})
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
            elif self.path == '/pinot':
                send(self, 200, _pinot_page(
                    window, req.get('path') or '/browse', rows=int(req.get('rows') or 40),
                    row=req.get('row'), title=req.get('title'), n=int(req.get('n') or 40),
                    videos=bool(req.get('videos'))))
            elif self.path == '/switch':
                send(self, 200, _switch_profile(window, req.get('index', 0), req.get('guid', '')))
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
            else:
                send(self, 404, {'err': 'not found'})

    # Threaded so a long op (episodes/search navigation) never blocks /health or other
    # requests; the _rpc_lock still serialises actual window access.
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', int(port)), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def injector(window):
    while True:
        try:
            with _rpc_lock:
                window.evaluate_js(HIDE_JS)
                window.evaluate_js(BACK_KEY_JS)
                window.evaluate_js(NAV_JS)
                if LOAD_JS:
                    window.evaluate_js(LOAD_JS)
                if STYLE_JS:
                    window.evaluate_js(STYLE_JS)
        except Exception:
            pass
        time.sleep(3)


def _nav_handler(window):
    """Run OSD-navigation actions queued by the keyboard hook (which can't touch the DOM).
    Event-driven so each arrow/OK is near-instant; auto-closes the OSD after a few idle
    seconds so the arrow keys revert to Netflix seek/volume."""
    while True:
        _NAV_EV.wait(timeout=1.0)
        _NAV_EV.clear()
        while _NAV_Q:
            act = _NAV_Q.pop(0)
            res = None
            if _rpc_lock.acquire(timeout=2):
                try:
                    res = window.evaluate_js('window.__NFNAV?__NFNAV.%s:-1' % _NAV_FN[act])
                except Exception:
                    res = None
                finally:
                    _rpc_lock.release()
            _osd_log('%s -> %r  (_FS=%s)' % (act, res, _FS[0]))
            r = str(res)
            if r.startswith('2:next:'):
                # OSD "Next episode": navigate natively (window.load_url, under the lock) so
                # the monitor never sees a transient non-/watch state and bounce to browse.
                nid = r.split(':', 2)[2]
                if nid.isdigit() and _rpc_lock.acquire(timeout=3):
                    try:
                        window.load_url('https://www.netflix.com/watch/%s' % nid)
                        _force_foreground(_own_hwnd())
                    except Exception:
                        pass
                    finally:
                        _rpc_lock.release()
                _NAV[0] = False
                _NAV_LAST[0] = time.time()
                continue
            if r.startswith('1:'):
                _NAV[0] = True
            elif r.startswith('0:'):
                _NAV[0] = False
            _NAV_LAST[0] = time.time()
        if _NAV[0] and time.time() - _NAV_LAST[0] > 5:
            if _rpc_lock.acquire(timeout=2):
                try:
                    window.evaluate_js('window.__NFNAV&&__NFNAV.exit()')
                except Exception:
                    pass
                finally:
                    _rpc_lock.release()
            _NAV[0] = False


# If Netflix shows the "Who's watching?" profile gate there is no home LoCo; pick a
# profile so the home loads: by guid (NF_PROFILE_GUID, exact) else by position
# (NF_PROFILE_INDEX, default the first). /switch updates both.
try:
    PROFILE_INDEX = [int(os.environ.get('NF_PROFILE_INDEX', '0') or '0')]
except ValueError:
    PROFILE_INDEX = [0]
PROFILE_GUID = [os.environ.get('NF_PROFILE_GUID', '') or '']

# The gate's profile tiles. Netflix rebuilt the gate in 2026-10: the old `.profile-link`
# anchors became <button data-uia="profile-selector+tile-<GUID>">; match both. The
# home page's header account menu has no such tile, so this also tells gate from home.
GATE_SEL = '.profile-link,[data-uia^="profile-selector+tile-"]'


def _select_profile_js():
    return ('(function(idx,guid){if(window.__PSEL)return "waiting";'
            'var els=document.querySelectorAll(%s);if(!els||!els.length)return "none";'
            'var el=null;if(guid){el=document.querySelector(\'[data-uia="profile-selector+tile-\'+guid+\'"]\');}'
            'if(!el)el=els[idx]||els[0];window.__PSEL=1;el.click();return "clicked";'
            '})(%d,%s)' % (json.dumps(GATE_SEL), PROFILE_INDEX[0], json.dumps(PROFILE_GUID[0])))


# While browsing (window off-screen, NOT playing) Netflix auto-plays billboard/hover
# preview trailers WITH audio -> the user hears sound with no picture. Mute (and pause)
# every media element on the browse page. Never runs while playing (guarded by _FS).
MUTE_PREVIEWS_JS = (
    "(function(){try{"
    "function brs(){return location.href.indexOf('/watch/')<0;}"
    "function kill(m){try{if(brs()){m.muted=true;m.volume=0;if(!m.paused)m.pause();}}catch(e){}}"
    "function sweep(){try{if(brs())document.querySelectorAll('video,audio').forEach(kill);}catch(e){}}"
    "if(!window.__NFMUTE){window.__NFMUTE=1;"
    # capture media the INSTANT it loads/plays so it never opens (and holds) the HDMI audio
    # endpoint -> no contention with Kodi's own audio device while browsing.
    "['loadstart','loadedmetadata','loadeddata','canplay','play','playing','volumechange'].forEach("
    "function(ev){document.addEventListener(ev,function(e){var t=e&&e.target;"
    "if(t&&t.tagName&&(t.tagName=='VIDEO'||t.tagName=='AUDIO'))kill(t);},true);});"
    "try{new MutationObserver(sweep).observe(document.documentElement,{childList:true,subtree:true});}catch(e){}"
    "}"
    "sweep();}catch(e){}})()")


# True only while the window has been SURFACED for the user (/show sign-in, /switch
# fallback): then the monitor neither auto-picks a profile nor reclaims the foreground
# until the user is signed in and past the gate. At launch the window is off-screen, so
# this starts False (the first load's gate is auto-entered and focus stays with Kodi).
AUTH_SHOWN = [False]


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
                else:
                    # Not in real playback -> kill any auto-playing preview/trailer audio on
                    # the (off-screen) browse page so it never leaks sound with no picture.
                    try:
                        window.evaluate_js(MUTE_PREVIEWS_JS)
                    except Exception:
                        pass
                    # Browse is direct-HTTP now, so the daemon window NEVER needs to reach
                    # home — after login it only has to get out of the way. So focus handoff
                    # is keyed on SIGN-IN (guid), NOT on locoRoot. (The old locoRoot gate is
                    # why a stuck "Who's watching?" screen — or a Netflix build that moved the
                    # loco cache — left the window holding the foreground: home never loaded,
                    # so the hide+focus-Kodi step never fired and the remote was dead until a
                    # mouse click.) We still auto-pick a profile off-screen so later playback
                    # has one, but we don't BLOCK the focus handoff on it.
                    try:
                        ctx = json.loads(window.evaluate_js(CONTEXT_JS) or '{}')
                    except Exception:
                        ctx = {}
                    try:
                        href = window.evaluate_js('location.href') or ''
                    except Exception:
                        href = ''
                    on_login = '/login' in href
                    signed_in = bool(ctx.get('guid'))
                    on_gate = signed_in and not on_login and _on_profile_gate(window)
                    # keep the off-screen session progressing past the profile gate (for
                    # playback) by entering the configured profile — but NOT while the window
                    # is surfaced for the user (/switch fallback): then it is theirs to click.
                    if on_gate and not AUTH_SHOWN[0]:
                        try:
                            window.evaluate_js('window.__PSEL=0')
                            window.evaluate_js(_select_profile_js())
                        except Exception:
                            pass
                    if AUTH_SHOWN[0]:
                        # surfaced for auth: once signed in and past the login form AND the
                        # profile gate, hide the window and hand the remote back to Kodi.
                        if signed_in and not on_login and not on_gate:
                            _hide_to_kodi(window)
                            AUTH_SHOWN[0] = False
                    else:
                        # not surfaced for auth: if our window re-grabbed the foreground, push
                        # it off-screen and return focus to Kodi. Runs every tick, so a late
                        # focus grab is reclaimed within ~0.5s without a mouse click.
                        try:
                            if _foreground_is_daemon():
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
                    # Only intercept while WE are the foreground window. The hook is global,
                    # so after Alt+Tab to another app our keys (Enter/arrows/Back) must pass
                    # through untouched instead of popping the OSD over someone else's window.
                    if _FS[0] and _is_foreground():
                        if vk in (0x1B, 0x08, 0xA6):          # ESC / Backspace / BrowserBack
                            if _NAV[0]:
                                _nav_queue('exit')            # close the OSD, stay in playback
                            else:
                                _BACK_FLAG[0] = True          # return to Kodi
                            return 1                          # swallow (Kodi shouldn't also act)
                        if vk == 0x0D:                        # OK -> skip / open OSD / activate
                            _nav_queue('ok')
                            return 1
                        if _NAV[0] and vk in (0x25, 0x26, 0x27, 0x28):
                            # arrows drive the OSD only while it's open; else they pass through
                            _nav_queue('left' if vk in (0x25, 0x26) else 'right')
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
    threading.Thread(target=_nav_handler, args=(window,), daemon=True).start()

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


def _gpu_driver_sig():
    """Signature of all display-adapter driver versions (registry, no WMI). Changes
    whenever any GPU driver is updated."""
    import winreg
    base = r'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
    sig = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return ''
    try:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            if not sub.isdigit():
                continue
            try:
                sk = winreg.OpenKey(key, sub)
                try:
                    ver, _ = winreg.QueryValueEx(sk, 'DriverVersion')
                    sig.append('%s=%s' % (sub, ver))
                finally:
                    sk.Close()
            except OSError:
                continue
    finally:
        key.Close()
    return ';'.join(sorted(sig))


def _playready_sig():
    """Fingerprint of the system PlayReady / MediaFoundation runtime DLLs. A Windows
    security update that swaps these files invalidates the hardware-DRM provisioning
    cached in this WebView2 profile, producing the SAME D7361 as a GPU-driver change
    (observed 2026-07-15: KB updates replaced PlayReady.dll/mfplat.dll -> D7361 next
    day, driver unchanged). Uses size+mtime (no version parse, no pywin32) -- both
    change when an update replaces the file."""
    parts = []
    sysroot = os.environ.get('SystemRoot', r'C:\Windows')
    for name in ('Windows.Media.Protection.PlayReady.dll', 'mfplat.dll'):
        path = os.path.join(sysroot, 'System32', name)
        try:
            st = os.stat(path)
            parts.append('%s=%d/%d' % (name, st.st_size, int(st.st_mtime)))
        except OSError:
            continue
    return ';'.join(parts)


def _drm_guard(profile):
    """Prevent recurring Netflix D7361. The PlayReady/MediaFoundation hardware-DRM
    provisioning cached in this WebView2 profile is bound to (a) the GPU driver and
    (b) the system PlayReady runtime DLLs. A GPU-driver update OR a Windows update that
    replaces those DLLs invalidates the cache, but neither a reboot nor a daemon restart
    clears it (it's files, not a process lock) -> playback errors with D7361 until the
    cache is purged. When the combined signature changes since last launch, delete
    MediaFoundationCdmStore so WebView2 re-provisions fresh on the next play. Login is
    untouched (cookies live elsewhere). Runs BEFORE the window is created, so the profile
    is unlocked. See memory nfbridge-d7361-cdmstore."""
    try:
        gpu = _gpu_driver_sig()
        pr = _playready_sig()
        if not gpu and not pr:
            return
        sig = gpu + '|' + pr
        marker = os.path.join(profile, '.nf_gpu_drv')
        prev = ''
        try:
            with open(marker, encoding='utf-8') as fh:
                prev = fh.read().strip()
        except OSError:
            prev = ''
        if prev and prev != sig:
            import shutil
            store = os.path.join(profile, 'EBWebView', 'Default', 'MediaFoundationCdmStore')
            try:
                shutil.rmtree(store)
                sys.stderr.write('nfbridge: DRM runtime changed (GPU driver / PlayReady update) -> cleared MediaFoundationCdmStore (D7361 guard)\n')
            except FileNotFoundError:
                pass
            except OSError as exc:
                sys.stderr.write('nfbridge: could not clear CDM store: %s\n' % exc)
        if prev != sig:
            try:
                if not os.path.isdir(profile):
                    os.makedirs(profile)
                with open(marker, 'w', encoding='utf-8') as fh:
                    fh.write(sig)
            except OSError:
                pass
    except Exception as exc:  # never let the guard break daemon startup
        try:
            sys.stderr.write('nfbridge: drm_guard error: %s\n' % exc)
        except Exception:
            pass


if RPC_PORT:
    _cleanup_stale(_profile, RPC_PORT)
    _drm_guard(_profile)

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
