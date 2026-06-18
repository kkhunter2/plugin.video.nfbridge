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
# Embedded = Microsoft Edge WebView2 (Windows): the system Edge engine is Netflix-
# trusted, so it plays (incl. PlayReady SL3000 / 4K) in our own window, where we
# inject the Kodi subtitle font + style. (An Electron embed is rejected by Netflix.)
def _launch_embedded(video_id):
    url = WATCH_URL.format(video_id)
    if _platform() != 'windows':
        kodiutils.log('embedded (WebView2) is Windows-only; delegating')
        return _launch_delegate(video_id)

    py = kodiutils.setting('webview_python')
    host = os.path.join(_runtime_dir(), 'webview', 'webview_host.py')
    if not py or not os.path.exists(py) or not os.path.exists(host):
        kodiutils.log('embedded runtime not configured (webview_python); delegating')
        return _launch_delegate(video_id)

    env = {'NF_URL': url}
    try:
        from resources.lib import session
        env['NF_SESSION_OUT'] = session.path()   # refresh the browse session while playing
    except Exception:
        pass
    _apply_kodi_subtitle_font(py, env)
    return _spawn([py, host], env=env)


def launch_signin(session_out):
    """Open the embedded WebView2 player so the user can sign in; the host captures
    the browse session (cookies + authURL + build + profile) to session_out."""
    if _platform() != 'windows':
        kodiutils.notify('Embedded sign-in needs Windows (WebView2)')
        return
    py = kodiutils.setting('webview_python')
    host = os.path.join(_runtime_dir(), 'webview', 'webview_host.py')
    if not py or not os.path.exists(py) or not os.path.exists(host):
        kodiutils.notify('Set "webview_python" in add-on settings first')
        return
    return _spawn([py, host], env={'NF_URL': 'https://www.netflix.com/browse',
                                   'NF_SESSION_OUT': session_out})


def _apply_kodi_subtitle_font(py, env):
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
    fontfix = os.path.join(_runtime_dir(), 'webview', 'fontfix.py')
    try:
        res = subprocess.run([py, fontfix, font, cache],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        if res.returncode == 0 and res.stdout.strip():
            env['NF_FONT_WOFF2'] = res.stdout.decode('utf-8', 'replace').strip()
            kodiutils.log('subtitle font ready: {0}'.format(os.path.basename(font)))
        else:
            err = (res.stderr.decode('utf-8', 'replace').strip() or 'unknown error')
            kodiutils.notify('Subtitle font "{0}" is corrupt/unsupported - using Netflix font'
                             .format(os.path.basename(font)))
            kodiutils.log('fontfix failed for {0}: {1}'.format(font, err))
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
    # Windows: embedded WebView2 plays Netflix (up to 4K via PlayReady SL3000) inside
    # our own window AND lets us apply the Kodi subtitle font/style — so it's the best
    # mode there (when the webview runtime is configured; otherwise it falls back to
    # delegate automatically). Other OSes have no Netflix-trusted embeddable -> delegate.
    return 'embedded' if _platform() == 'windows' else 'delegate'
