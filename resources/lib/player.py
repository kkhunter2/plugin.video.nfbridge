# -*- coding: utf-8 -*-
"""Layer 2 — playback delegation + the WebView daemon's lifecycle.

The addon never decrypts anything. On Play it hands the title to a client that
the OS / Netflix already trust:

  mode "embedded"  -> ONE persistent Microsoft Edge WebView2 window (Windows), run by
        runtime/webview/webview_host.py (bundled as nfbridge_player.exe). The same
        window serves browse (off-screen, falcor RPC over localhost) and playback
        (/play brings it forward, fullscreen; PlayReady SL3000 -> up to 4K) with the
        Kodi subtitle font/style injected. Single sign-in, single user-data dir.
  mode "delegate"  -> the platform's best Netflix client
        Windows : Edge --kiosk (PlayReady SL3000, up to 4K)
        macOS   : Safari / Chrome fullscreen   (FairPlay, ~1080p)
        Linux   : Chrome --app fullscreen      (Widevine L3, 720p)

nfclient connects to the daemon over localhost; this module owns launching it.
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


_NO_WINDOW = 0x08000000 if sys.platform.startswith('win') else 0   # CREATE_NO_WINDOW


def _spawn(args, env=None):
    """Launch detached; never block the Kodi UI thread."""
    kodiutils.log('player: launching {0}'.format(args[0]))
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.Popen(args, env=e, creationflags=_NO_WINDOW,
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
def _runtime_dir():
    # resources/lib/player.py -> addon root -> runtime/
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'runtime')


def _host_script():
    return os.path.join(_runtime_dir(), 'webview', 'webview_host.py')


def _host_exe():
    return os.path.join(_runtime_dir(), 'webview', 'dist', 'nfbridge_player.exe')


def _webview_cmd():
    """Base command to run the WebView2 host. Prefer a configured python.exe (signed
    -> passes Windows Smart App Control) + the host script; fall back to the bundled
    exe (unsigned PyInstaller build — may be blocked by Smart App Control)."""
    py = kodiutils.setting('webview_python')
    host = _host_script()
    if py and os.path.exists(py) and os.path.exists(host):
        return [py, host]
    exe = _host_exe()
    if os.path.exists(exe):
        return [exe]
    return None


def _fontfix_cmd(base):
    """How to invoke the font repairer given the webview base command."""
    if base[0].lower().endswith('.exe'):
        return base + ['fontfix']                       # `nfbridge_player.exe fontfix ...`
    return [base[0], os.path.join(_runtime_dir(), 'webview', 'fontfix.py')]


# The WebView2 user-data dir (login cookies, DRM provisioning) and the repaired-font
# cache live in the addon's DATA dir, not the addon dir: Kodi wipes the addon dir on
# update/reinstall, which would sign the user out (and re-provision DRM) every time.
def _webview_profile():
    return os.path.join(kodiutils.profile_dir(), 'webview_profile')


def _font_cache_dir():
    return os.path.join(kodiutils.profile_dir(), 'fontcache')


def _osd_labels():
    """Localized labels for the in-player OSD (webview_host NAV_JS)."""
    return {'play': kodiutils.localize(30020, 'Play'),
            'pause': kodiutils.localize(30021, 'Pause'),
            'subtitles': kodiutils.localize(30022, 'Subtitles'),
            'next': kodiutils.localize(30023, 'Next episode'),
            'back': kodiutils.localize(30024, 'Back')}


def _daemon_env(cmd):
    """Env for the browse+play daemon: off-screen window, RPC port, shared profile, the
    profile to auto-enter and the Kodi subtitle font ready for when playback shows it."""
    env = {'NF_URL': 'https://www.netflix.com/browse',
           'NF_RPC_PORT': str(DAEMON_PORT),
           'NF_PROFILE': _webview_profile(),
           'NF_PROFILE_INDEX': str(kodiutils.setting('profile_index', '0') or '0'),
           'NF_PROFILE_GUID': kodiutils.setting('profile_guid', ''),
           'NF_OSD_LABELS': json.dumps(_osd_labels(), ensure_ascii=False)}
    _apply_kodi_subtitle_font(cmd, env)
    return env


def switch_profile(index, guid=''):
    """Switch the daemon to a profile (by guid, else gate position #index: it clicks that
    tile at the 'Who's watching' gate off-screen) and remember it, so the next daemon
    launch auto-enters the same one."""
    from resources.lib import nfclient
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 0
    kodiutils.set_setting('profile_index', index)
    kodiutils.set_setting('profile_guid', guid or '')
    c = nfclient.NetflixClient(port=DAEMON_PORT, launcher=_ensure_daemon, code_ver=_code_ver())
    if not c.ensure():
        kodiutils.notify('Netflix not ready')
        return
    try:
        res = c._post('/switch', {'index': index, 'guid': guid or ''}, timeout=40) or {}
    except Exception as exc:  # noqa
        kodiutils.log('switch profile failed: {0}'.format(exc))
        kodiutils.notify('Profile switch failed')
        return
    if res.get('ok'):
        kodiutils.notify(kodiutils.localize(30011, 'Profile switched'))
        import xbmc
        # re-render from the new profile's home if the user is inside the add-on
        # (from the Settings button there is no list to refresh)
        if 'plugin.video.nfbridge' in (xbmc.getInfoLabel('Container.FolderPath') or ''):
            xbmc.executebuiltin('Container.Update({0},replace)'.format(kodiutils.url_for(action='root')))
    else:
        kodiutils.notify('Pick a profile in the Netflix window, then reopen Browse')


def _ensure_daemon():
    """Launch the daemon (off-screen) if the runtime exists. Used as nfclient's launcher."""
    cmd = _webview_cmd()
    if not cmd:
        kodiutils.log('webview runtime not available (build runtime/webview or set webview_python)')
        return
    _spawn(cmd, env=_daemon_env(cmd))


def _code_ver():
    """Version key of the daemon we WOULD launch now: mtime of the runtime file (the exe
    when the bundled exe is used, else the host script — the same rule webview_host
    applies to itself) + the chosen subtitle font NAME. nfclient compares it to the
    running daemon's /health 'ver' and restarts a stale or font-changed daemon."""
    try:
        from resources.lib import kodi_subs
        cmd = _webview_cmd()
        if not cmd:
            return None
        runtime_file = cmd[0] if cmd[0].lower().endswith('.exe') else cmd[1]
        mtime = str(int(os.path.getmtime(runtime_file)))
        return mtime + '|' + str(kodi_subs._get('subtitles.fontname') or '')
    except Exception:
        return None


def browse_client():
    """A NetflixClient wired to launch/connect the daemon. While Kodi itself is playing
    (local file / other add-on) the daemon is NOT launched: the background service quits
    it whenever Kodi's player is active (to free the audio device), so launching here
    would just thrash launch<->quit. Cached lists still render; fresh ones wait."""
    from resources.lib import nfclient
    launcher = None if kodiutils.is_playing() else _ensure_daemon
    return nfclient.NetflixClient(port=DAEMON_PORT, launcher=launcher, code_ver=_code_ver())


def launch_signin(*_args):
    """Bring up the daemon's window on the Netflix sign-in page."""
    if not _webview_cmd():
        kodiutils.notify('Set "webview_python" in settings (or use the bundled exe)')
        return
    from resources.lib import nfclient
    c = nfclient.NetflixClient(port=DAEMON_PORT, launcher=_ensure_daemon)
    if not c.running():
        _ensure_daemon()
        import time
        for _ in range(20):
            if c.running():
                break
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
        env['NF_FONT_NAME'] = str(kodi_subs._get('subtitles.fontname') or '')  # daemon version key
        font = kodi_subs.resolve_font_path()
    except Exception as exc:  # noqa
        kodiutils.log('subtitle settings read failed: {0}'.format(exc))
        return
    if not font:
        return  # no Kodi subtitle font configured -> Netflix's own font

    cache = _font_cache_dir()
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
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=_NO_WINDOW, timeout=120)
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
    c = nfclient.NetflixClient(port=DAEMON_PORT, launcher=_ensure_daemon, code_ver=_code_ver())
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
