# -*- coding: utf-8 -*-
"""Layer 2 — playback delegation.

The addon never decrypts anything. On Play it hands the title to a client that
the OS / Netflix already trust:

  mode "delegate"  -> the platform's best Netflix client
        Windows : Netflix Store app deep-link  ->  Edge --kiosk (PlayReady SL3000, up to 4K)
        macOS   : Safari / Chrome fullscreen   (FairPlay, ~1080p)
        Linux   : Chrome --app fullscreen      (Widevine L3, 720p)
  mode "embedded"  -> a bundled VMP-signed Electron rendering netflix.com/watch
        (Widevine privacy mode -> 1080p, integrated subtitle overlay; no 4K)

Empirically validated: Edge produces a real SL3000 XMR license (4K path); a
castLabs Electron produces encrypted_client_id out-of-the-box (Widevine privacy
mode -> Netflix accepts at 1080p). See D:\\work\\netflix-*.md.
"""
import os
import sys
import json
import subprocess

from resources.lib import kodiutils

WATCH_URL = 'https://www.netflix.com/watch/{0}'
DAEMON_PORT = 8731   # localhost RPC port for the WebView browse+play daemon


# ---------------------------------------------------------------- platform ----
def _platform():
    if sys.platform.startswith('win'):
        return 'windows'
    if sys.platform == 'darwin':
        return 'macos'
    return 'linux'


def _spawn(args, env=None):
    """Launch detached; never block the Kodi UI thread."""
    kodiutils.log('player: launching {0}'.format(args[0]))
    e = dict(os.environ)
    if env:
        e.update(env)
    flags = 0
    if _platform() == 'windows':
        flags = 0x08000000  # CREATE_NO_WINDOW (no python console flashes up)
    return subprocess.Popen(args, env=e, creationflags=flags,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _first_existing(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


# ------------------------------------------------------------ delegate mode ---
def _launch_delegate(video_id):
    url = WATCH_URL.format(video_id)
    plat = _platform()

    if plat == 'windows':
        # 1) the official Netflix Store app (PlayReady SL3000 -> 4K/HDR), if installed
        if kodiutils.setting('use_uwp_app', 'true') == 'true' and _windows_launch_netflix_app(video_id):
            return
        # 2) Edge in kiosk fullscreen (PlayReady SL3000 -> 4K)
        edge = _first_existing([
            kodiutils.setting('edge_path'),
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        ])
        if edge:
            return _spawn([edge, '--kiosk', url, '--edge-kiosk-type=fullscreen',
                           '--no-first-run', '--no-default-browser-check'])
        # 3) Chrome fallback
        chrome = _first_existing([
            kodiutils.setting('chrome_path'),
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        ])
        if chrome:
            return _spawn([chrome, '--app=' + url, '--start-fullscreen', '--no-first-run'])
        kodiutils.notify('No Edge/Chrome/Netflix app found for playback')

    elif plat == 'macos':
        browser = kodiutils.setting('macos_browser', 'Safari')  # or "Google Chrome"
        return _spawn(['open', '-a', browser, url])

    else:  # linux
        chrome = _first_existing([
            kodiutils.setting('chrome_path'),
            '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable',
            '/usr/bin/chromium', '/usr/bin/chromium-browser',
        ])
        if chrome:
            return _spawn([chrome, '--app=' + url, '--start-fullscreen', '--no-first-run'])
        kodiutils.notify('No Chrome/Chromium found for playback')


def _windows_launch_netflix_app(video_id):
    """Launch the Netflix Store (UWP) app at a title, if installed. Returns True on success."""
    try:
        # The Netflix UWP app handles netflix.com/watch deep links; ask the shell to open it
        # with the app association. (A production build would resolve the exact AppUserModelId.)
        subprocess.Popen(['explorer.exe', WATCH_URL.format(video_id)])
        return False  # explorer opens the default handler; treat as "not guaranteed app" -> let Edge handle
    except Exception as exc:  # noqa
        kodiutils.log('uwp launch failed: {0}'.format(exc))
        return False


# ------------------------------------------------------------ embedded mode ---
# Embedded = Microsoft Edge WebView2 (Windows): the system Edge engine is Netflix-
# trusted, so it plays (incl. PlayReady SL3000 / 4K) in our own window, where we
# inject the Kodi subtitle font + style. (An Electron embed is rejected by Netflix.)
def _webview_cmd():
    """Base command to run the WebView2 host. Prefer a configured python.exe (signed
    -> passes Windows Smart App Control) + the host script; fall back to the bundled
    exe (unsigned PyInstaller build — may be blocked by Smart App Control)."""
    py = kodiutils.setting('webview_python')
    host = os.path.join(_runtime_dir(), 'webview', 'webview_host.py')
    if py and os.path.exists(py) and os.path.exists(host):
        return [py, host]
    exe = os.path.join(_runtime_dir(), 'webview', 'dist', 'nfbridge_player.exe')
    if os.path.exists(exe):
        return [exe]
    return None


def _fontfix_cmd(base):
    """How to invoke the font repairer given the webview base command."""
    if base[0].lower().endswith('.exe'):
        return base + ['fontfix']                       # `nfbridge_player.exe fontfix ...`
    return [base[0], os.path.join(_runtime_dir(), 'webview', 'fontfix.py')]


# One persistent WebView daemon serves BOTH browse (hidden, falcor RPC) and playback
# (/play navigates the same window to the watch page and shows it) — so there is a
# single Netflix session/login and a single user-data-dir (WebView2 locks it per
# process). nfclient connects over localhost; player.py owns the lifecycle.
def _webview_profile():
    return os.path.join(_runtime_dir(), 'webview', 'profile')


def _daemon_env():
    """Env for the browse+play daemon: hidden window, RPC, shared profile, and the
    Kodi subtitle font ready for when playback shows the window."""
    env = {'NF_URL': 'https://www.netflix.com/browse',
           'NF_RPC_PORT': str(DAEMON_PORT),
           'NF_MINIMIZE_AFTER_LOAD': '1',   # load visible to prefetch, then minimize
           'NF_PROFILE': _webview_profile(),
           'NF_PROFILE_INDEX': str(kodiutils.setting('profile_index', '0') or '0')}
    cmd = _webview_cmd()
    if cmd:
        _apply_kodi_subtitle_font(cmd, env)
    return env


def switch_profile(index):
    """Surface the 'Who's watching' gate so the user can pick a profile. Netflix blocks
    programmatic switching, so a real click in the window is the reliable path."""
    from resources.lib import nfclient
    c = nfclient.NetflixClient(port=DAEMON_PORT, launcher=_ensure_daemon)
    if not c.ensure():
        kodiutils.notify('Netflix not ready')
        return
    try:
        c._post('/switch', {'index': int(index)}, timeout=15)
        kodiutils.notify('Pick a profile in the Netflix window, then reopen Browse')
    except Exception as exc:  # noqa
        kodiutils.log('switch profile failed: {0}'.format(exc))


def _ensure_daemon():
    """Launch the daemon (hidden) if the runtime exists. Used as nfclient's launcher."""
    cmd = _webview_cmd()
    if not cmd:
        kodiutils.log('webview runtime not available (build runtime/webview or set webview_python)')
        return
    _spawn(cmd, env=_daemon_env())


def _code_ver():
    """mtime of the deployed daemon script -> lets nfclient restart a stale daemon."""
    try:
        return str(int(os.path.getmtime(os.path.join(_runtime_dir(), 'webview', 'webview_host.py'))))
    except Exception:
        return None


def browse_client():
    """A NetflixClient wired to launch/connect the daemon."""
    from resources.lib import nfclient
    return nfclient.NetflixClient(port=DAEMON_PORT, launcher=_ensure_daemon, code_ver=_code_ver())


def launch_signin(*_args):
    """Bring up the daemon's window on the Netflix sign-in page."""
    if not _webview_cmd():
        kodiutils.notify('Set "webview_python" in settings (or use the bundled exe)')
        return
    from resources.lib import nfclient
    c = nfclient.NetflixClient(port=DAEMON_PORT, launcher=_ensure_daemon)
    if not c.running():
        _ensure_daemon()
        for _ in range(20):
            if c.running():
                break
            import time
            time.sleep(1)
    try:
        c._get('/show', timeout=10)
        kodiutils.notify('Sign in to Netflix in the window, then return to Kodi')
    except Exception as exc:  # noqa
        kodiutils.notify('Could not open the sign-in window')
        kodiutils.log('signin /show failed: {0}'.format(exc))


def _apply_kodi_subtitle_font(cmd, env):
    """Resolve Kodi's subtitle font + style and pre-repair the font (OTS) so the
    embedded player can render it. Notifies the user if the font is corrupt/unusable
    (then falls back to Netflix's own font)."""
    try:
        from resources.lib import kodi_subs
        env['NF_STYLE'] = json.dumps(kodi_subs.style())
        font = kodi_subs.resolve_font_path()
    except Exception as exc:  # noqa
        kodiutils.log('subtitle settings read failed: {0}'.format(exc))
        return
    if not font:
        return  # no Kodi subtitle font configured -> Netflix's own font

    cache = os.path.join(_runtime_dir(), 'webview', 'cache')
    if not os.path.isdir(cache):
        os.makedirs(cache)
    result_file = os.path.join(cache, 'fontfix_result.txt')
    try:
        os.remove(result_file)
    except OSError:
        pass
    try:
        # result is written to result_file (robust for the windowed exe; no stdout)
        res = subprocess.run(_fontfix_cmd(cmd) + [font, cache, result_file],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        out = ''
        if os.path.exists(result_file):
            out = open(result_file, encoding='utf-8').read().strip()
        if res.returncode == 0 and out and not out.startswith('ERROR') and os.path.exists(out):
            env['NF_FONT_WOFF2'] = out
            kodiutils.log('subtitle font ready: {0}'.format(os.path.basename(font)))
        else:
            kodiutils.notify('Subtitle font "{0}" is corrupt/unsupported - using Netflix font'
                             .format(os.path.basename(font)))
            kodiutils.log('fontfix failed for {0}: {1}'.format(font, out or 'rc=%s' % res.returncode))
    except Exception as exc:  # noqa
        kodiutils.notify('Could not process subtitle font - using Netflix font')
        kodiutils.log('fontfix exception: {0}'.format(exc))


def _runtime_dir():
    # resources/lib/player.py -> addon root -> runtime/
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'runtime')


# ------------------------------------------------------------------- entry ----
def play(video_id):
    if not video_id:
        kodiutils.notify('No video id')
        return
    mode = kodiutils.setting('playback_mode', _default_mode())
    kodiutils.log('play {0} (mode={1}, platform={2})'.format(video_id, mode, _platform()))
    # Embedded uses the WebView daemon: the bundled exe on Windows, else a configured
    # python + pywebview host (works on macOS/Linux too, where it is less tested).
    if mode == 'embedded' and _webview_cmd():
        kodiutils.stop_playback()   # stop any local Kodi video so it doesn't compete
        kodiutils.release_audio()   # free the (passthrough/exclusive) audio device first
        _play_embedded(video_id)
    else:
        _launch_delegate(video_id)
    # Tell Kodi we did NOT resolve a playable stream (playback is external),
    # so it does not show a spinner / "playback failed" for an unresolved item.
    try:
        import xbmcplugin
        import xbmcgui
        xbmcplugin.setResolvedUrl(kodiutils.handle(), False, xbmcgui.ListItem())
    except Exception:
        pass


def _play_embedded(video_id):
    """Tell the running daemon to navigate to the watch page and show its window."""
    from resources.lib import nfclient
    c = nfclient.NetflixClient(port=DAEMON_PORT, launcher=_ensure_daemon)
    if not c.ensure():
        kodiutils.notify('Netflix not signed in - use "Sign in" first')
        return
    try:
        c._post('/play', {'videoId': video_id})
    except Exception as exc:  # noqa
        kodiutils.log('play rpc failed: {0}; delegating'.format(exc))
        _launch_delegate(video_id)


def _default_mode():
    # Windows: embedded WebView2 plays Netflix (up to 4K via PlayReady SL3000) inside
    # our own window AND lets us apply the Kodi subtitle font/style — so it's the best
    # mode there (when the webview runtime is configured; otherwise it falls back to
    # delegate automatically). Other OSes have no Netflix-trusted embeddable -> delegate.
    return 'embedded' if _platform() == 'windows' else 'delegate'
