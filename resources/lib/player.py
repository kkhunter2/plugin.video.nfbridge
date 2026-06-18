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
import subprocess

from resources.lib import kodiutils

WATCH_URL = 'https://www.netflix.com/watch/{0}'


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
        flags = 0x00000008  # DETACHED_PROCESS
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
def _launch_embedded(video_id):
    # NOTE: verified NOT to work for Netflix — Netflix rejects the castLabs Electron
    # client (M7121/D7353/e100) despite a valid encrypted_client_id. Kept for other
    # Widevine services / future use. For Netflix, use mode "delegate".
    url = WATCH_URL.format(video_id)
    electron = _first_existing([
        kodiutils.setting('electron_path'),
        os.path.join(_addon_runtime_dir(), 'electron',
                     'electron.exe' if _platform() == 'windows' else 'electron'),
    ])
    if not electron:
        kodiutils.notify('Embedded player not installed; switch to "delegate" mode in settings')
        return
    app_dir = os.path.join(_addon_runtime_dir(), 'player')  # the netflix-web wrapper + subtitle module
    # NF_URL is read by the wrapper's main.js to load the watch page directly.
    return _spawn([electron, app_dir], env={'NF_URL': url})


def _addon_runtime_dir():
    """Where the bundled Electron + player wrapper live (downloaded on first use)."""
    base = kodiutils.setting('runtime_dir')
    if base:
        return base
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'runtime')


# ------------------------------------------------------------------- entry ----
def play(video_id):
    if not video_id:
        kodiutils.notify('No video id')
        return
    mode = kodiutils.setting('playback_mode', _default_mode())
    kodiutils.log('play {0} (mode={1}, platform={2})'.format(video_id, mode, _platform()))
    if mode == 'embedded':
        _launch_embedded(video_id)
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


def _default_mode():
    # NETFLIX: only "delegate" works. Empirically, a bundled (castLabs) Electron is
    # rejected by Netflix's license server (error M7121/D7353/e100) even though it
    # produces a valid encrypted_client_id — Netflix validates the host's VMP-signature
    # provenance and trusts only official browsers/apps. So default to delegate on every
    # platform. (Embedded remains available for other, less-strict Widevine services.)
    return 'delegate'
