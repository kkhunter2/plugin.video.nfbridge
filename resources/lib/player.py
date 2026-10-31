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
        Windows : the Store Netflix app, else an Edge app window (PlayReady SL3000, up to 4K)
        macOS   : Safari / Chrome fullscreen   (FairPlay, ~1080p)
        Linux   : Chrome --app fullscreen      (Widevine L3, 720p)

nfclient connects to the daemon over localhost; this module owns launching it.
"""
import os
import sys
import json
import time
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
        # 2) Edge as an app window in the user's own profile, so it is signed in (a
        # --kiosk window is InPrivate: it always landed on the Netflix sign-in page).
        # Fullscreen only takes when Edge isn't running yet; else press F in the player.
        edge = _edge_exe()
        if edge:
            return _spawn([edge, '--app=' + url, '--start-fullscreen',
                           '--no-first-run', '--no-default-browser-check'])
        # 3) Chrome fallback
        chrome = _first_existing([
            kodiutils.setting('chrome_path'),
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        ])
        if chrome:
            return _spawn([chrome, '--app=' + url, '--start-fullscreen', '--no-first-run'])
        kodiutils.notify(kodiutils.localize(30030, 'No Netflix app, Edge or Chrome found for playback'))

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
        kodiutils.notify(kodiutils.localize(30030, 'No Netflix app, Edge or Chrome found for playback'))


def _edge_exe():
    return _first_existing([
        kodiutils.setting('edge_path'),
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    ])


_NETFLIX_PKG = '4DF9E0F8.Netflix_'     # the Microsoft Store Netflix app's package
_PACKAGES = r'Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages'


def _netflix_app_id():
    """The Edge web-app id the Store Netflix app runs, or None if that app isn't installed.
    Today's Store app is no native app: its manifest has Edge host Netflix's web app
    (HostId="PWA", --app-id=<id>), so the id is read from there."""
    import re
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PACKAGES) as key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    return None
                i += 1
                if not name.startswith(_NETFLIX_PKG):
                    continue
                with winreg.OpenKey(key, name) as pkg:
                    root = winreg.QueryValueEx(pkg, 'PackageRootFolder')[0]
                with open(os.path.join(root, 'AppxManifest.xml'), encoding='utf-8') as fh:
                    m = re.search(r'--app-id=([a-p]{32})', fh.read())
                if m:
                    return m.group(1)
    except Exception as exc:  # noqa
        kodiutils.log('Netflix app lookup failed: {0}'.format(exc))
    return None


def _windows_launch_netflix_app(video_id):
    """Open the title in the Microsoft Store Netflix app, if installed. That app ignores a
    URL passed on activation (it always opens its start page); Edge's
    --app-launch-url-for-shortcuts-menu-item opens the web app AT a page instead.
    Returns True if launched; False lets the caller fall back to Edge."""
    app_id = _netflix_app_id()
    edge = _edge_exe()
    if not app_id or not edge:
        return False
    try:
        _spawn([edge, '--app-id=' + app_id,
                '--app-launch-url-for-shortcuts-menu-item=' + WATCH_URL.format(video_id)])
        return True
    except Exception as exc:  # noqa
        kodiutils.log('Netflix app launch failed: {0}'.format(exc))
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
    # a PyInstaller onedir build: it starts in ~0.2 s, where a onefile exe unpacks
    # itself to %TEMP% first (~1 s on every launch)
    return os.path.join(_runtime_dir(), 'webview', 'dist', 'nfbridge_player', 'nfbridge_player.exe')


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
        kodiutils.notify(kodiutils.localize(30031, 'Netflix is not ready'))
        return
    try:
        res = c._post('/switch', {'index': index, 'guid': guid or ''}, timeout=40) or {}
    except Exception as exc:  # noqa
        kodiutils.log('switch profile failed: {0}'.format(exc))
        kodiutils.notify(kodiutils.localize(30032, 'Profile switch failed'))
        return
    if res.get('ok'):
        kodiutils.notify(kodiutils.localize(30011, 'Profile switched'))
        import xbmc
        # re-render from the new profile's home if the user is inside the add-on
        # (from the Settings button there is no list to refresh)
        if 'plugin.video.nfbridge' in (xbmc.getInfoLabel('Container.FolderPath') or ''):
            xbmc.executebuiltin('Container.Update({0},replace)'.format(kodiutils.url_for(action='root')))
    else:
        kodiutils.notify(kodiutils.localize(30033, 'Pick a profile in the Netflix window, then open the add-on again'))


def _ensure_daemon():
    """Launch the daemon (off-screen) if the runtime exists. Used as nfclient's launcher."""
    cmd = _webview_cmd()
    if not cmd:
        kodiutils.log('webview runtime not available (build runtime/webview or set webview_python)')
        return False
    # The Kodi-start prefetch and the user opening the add-on can both find the daemon
    # down: launch only once. A second one would take the first one's still-starting
    # WebView2 for orphans and kill it (and two first font repairs would clobber each
    # other's result file). The others wait for that launch's port (nfclient.ensure).
    stamp = _launch_stamp()
    if not _claim_launch(stamp):
        kodiutils.log('player: daemon launch already under way')
        return
    env = _daemon_env(cmd)
    import xbmc
    if xbmc.Monitor().abortRequested():
        # Kodi is exiting (the env can take a while): a daemon started now would outlive
        # the service that quits it
        _remove(stamp)
        return
    try:
        _SPAWNED[0] = _spawn(cmd, env=env)
    except OSError as exc:
        # nothing will open the port: free the guard at once (callers would otherwise wait
        # out its grace), and say why. Smart App Control blocks an unsigned exe it has no
        # reputation for (WinError 4551; 1260 = blocked by a policy).
        _remove(stamp)
        kodiutils.log('player: daemon launch failed: {0}'.format(exc))
        if getattr(exc, 'winerror', None) in (4551, 1260):
            kodiutils.notify(kodiutils.localize(
                30056, 'Windows blocked the Netflix player (Smart App Control) - see the README'))
        else:
            kodiutils.notify(kodiutils.localize(30057, 'Could not start the Netflix player: {0}').format(exc))
        return False
    _write(stamp, 'spawned')


_SPAWNED = [None]   # the daemon process this (Python) process launched, if any


def stop_spawned():
    """Kill the daemon this process launched if it is still starting (its port not open
    yet, so it can't be told to /quit). For the service on Kodi exit: a daemon the
    prefetch launched a moment ago must not outlive Kodi."""
    proc = _SPAWNED[0]
    if proc is not None and proc.poll() is None:
        kodiutils.log('player: stopping the daemon still starting')
        proc.terminate()


# daemon.launch: 'env' while a launch builds its env (a first font repair runs up to
# 120 s), then 'spawned' until its port opens (a few seconds); its age is the mtime's
_ENV_GRACE = 130
_LAUNCH_GRACE = 15


def _launch_stamp():
    return os.path.join(kodiutils.profile_dir(), 'daemon.launch')


def launch_pending():
    """A daemon launch is under way: its env is being built, or it was spawned moments ago."""
    path = _launch_stamp()
    try:
        with open(path) as fh:
            state = fh.read().strip()
        age = time.time() - os.path.getmtime(path)
    except (OSError, ValueError):
        return False
    # a future mtime (the clock was stepped back) is no launch under way
    return 0 <= age < (_ENV_GRACE if state == 'env' else _LAUNCH_GRACE)


def _claim_launch(stamp):
    """True if this caller may launch the daemon (no other launch under way). The stamp is
    created exclusively, so two callers can't both claim a fresh one."""
    for _ in range(2):
        try:
            fd = os.open(stamp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if launch_pending():
                return False
            _remove(stamp)                  # an earlier launch's stamp
            continue
        except OSError:
            return True                     # no stamp possible: launch anyway
        with os.fdopen(fd, 'w') as fh:
            fh.write('env')
        return True
    return False


def _write(path, text):
    try:
        with open(path, 'w') as fh:
            fh.write(text)
    except OSError:
        pass


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


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
        runtime_file = cmd[-1]      # [bundled exe] or [python.exe, host script]
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
        kodiutils.notify(kodiutils.localize(30034, 'Netflix runtime not found - reinstall the add-on'))
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
        kodiutils.notify(kodiutils.localize(30035, 'Sign in to Netflix in the window, then return to Kodi'))
    except Exception as exc:  # noqa
        kodiutils.notify(kodiutils.localize(30036, 'Could not open the sign-in window'))
        kodiutils.log('signin /show failed: {0}'.format(exc))


def _woff2_target(font, cache):
    """Where fontfix keeps (or will keep) the WOFF2 built from font; None if unknown. Asks
    fontfix.py itself (pure stdlib) so the cache key has one definition."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'nfbridge_fontfix', os.path.join(_runtime_dir(), 'webview', 'fontfix.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.woff2_path(font, cache)
    except Exception as exc:  # noqa
        kodiutils.log('fontfix cache check failed: {0}'.format(exc))
        return None


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
    # already repaired (the usual case): skip the fontfix run, which starts the whole exe
    # (1-3 s) and would delay every daemon launch
    target = _woff2_target(font, cache)
    if target and os.path.isfile(target) and os.path.getsize(target):
        env['NF_FONT_WOFF2'] = target
        kodiutils.log('subtitle font ready (cached): {0}'.format(os.path.basename(font)))
        return
    if target and os.path.exists(target + '.bad'):
        # this very font file was rejected before and the user was told then: don't repeat
        # the repair (and the notice) on every launch, e.g. the prefetch at each Kodi start
        kodiutils.log('subtitle font {0} is unusable (known) - using Netflix font'.format(font))
        return
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
            if target:
                _write(target + '.bad', out or 'rc=%s' % res.returncode)
            kodiutils.notify(kodiutils.localize(
                30037, 'Subtitle font "{0}" is unusable - using the Netflix font').format(os.path.basename(font)))
            kodiutils.log('fontfix failed for {0}: {1}'.format(font, out or 'rc=%s' % res.returncode))
    except Exception as exc:  # noqa
        kodiutils.notify(kodiutils.localize(30038, 'Could not process the subtitle font - using the Netflix font'))
        kodiutils.log('fontfix exception: {0}'.format(exc))


# ------------------------------------------------------------------- entry ----
def play(video_id):
    if not video_id:
        kodiutils.notify(kodiutils.localize(30039, 'No video selected'))
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
        kodiutils.notify(kodiutils.localize(30040, 'Not signed in to Netflix - use "Sign in to Netflix" first'))
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
