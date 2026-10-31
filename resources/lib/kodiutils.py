# -*- coding: utf-8 -*-
"""Thin helpers over the Kodi Python API (xbmc/xbmcgui/xbmcplugin/xbmcaddon).

Keeps the rest of the addon free of Kodi boilerplate and easy to unit-test:
everything Kodi-specific funnels through here.
"""
import os
from urllib.parse import urlencode, urlsplit, parse_qsl

import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')


def profile_dir():
    """The addon's writable data dir (special://profile/addon_data/<id>/)."""
    p = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
    if not os.path.isdir(p):
        os.makedirs(p)
    return p


def _jsonrpc(method, params):
    import json
    return json.loads(xbmc.executeJSONRPC(json.dumps(
        {'jsonrpc': '2.0', 'method': method, 'params': params, 'id': 1})))


def _audio_marker():
    return os.path.join(profile_dir(), 'audio_saved.json')


def is_playing():
    """True while Kodi's OWN player is active (local file, music, another add-on).
    Embedded Netflix playback runs outside Kodi's player, so it never counts."""
    try:
        return bool(xbmc.Player().isPlaying())
    except Exception:
        return False


def stop_playback():
    """Stop any video Kodi is itself playing, so the embedded Netflix player isn't
    competing with Kodi for the audio/video device (which otherwise fails -> D7361)."""
    import time
    try:
        p = xbmc.Player()
        if p.isPlaying():
            p.stop()
            for _ in range(20):                # wait up to ~2s for the device to free
                if not p.isPlaying():
                    break
                time.sleep(0.1)
    except Exception as exc:  # noqa
        log('stop_playback failed: {0}'.format(exc))


def release_audio():
    """Prepare Kodi for embedded playback: (1) free the HDMI audio device — Kodi's
    passthrough / keep-device-alive hold it exclusively so the player can't open audio
    and Netflix fails (D7361); (2) silence GUI navigation sounds — each click opens the
    WASAPI device exclusively, and a click landing while the player opens audio fails it
    with D7361-1254-C00D4E85 (MF_E_AUDIO_PLAYBACK_DEVICE_IN_USE), reproduced reliably
    when the user keeps pressing remote keys during the (slow) first load after Kodi
    starts; (3) disable the screensaver + inhibit idle shutdown, since Kodi counts
    external playback as idle. The user's settings are saved for restore."""
    import json
    try:
        marker = _audio_marker()
        if not os.path.exists(marker):
            saved = {}
            for key in ('audiooutput.streamsilence', 'audiooutput.passthrough',
                        'audiooutput.guisoundmode', 'screensaver.mode'):
                saved[key] = (_jsonrpc('Settings.GetSettingValue',
                                       {'setting': key}).get('result') or {}).get('value')
            with open(marker, 'w') as f:
                json.dump(saved, f)
            xbmc.executebuiltin('InhibitIdleShutdown(true)')   # once, balanced by restore
        _jsonrpc('Settings.SetSettingValue', {'setting': 'audiooutput.streamsilence', 'value': 0})
        _jsonrpc('Settings.SetSettingValue', {'setting': 'audiooutput.passthrough', 'value': False})
        _jsonrpc('Settings.SetSettingValue', {'setting': 'audiooutput.guisoundmode', 'value': 0})
        _jsonrpc('Settings.SetSettingValue', {'setting': 'screensaver.mode', 'value': ''})
    except Exception as exc:  # noqa
        log('release_audio failed: {0}'.format(exc))


def restore_audio():
    """Restore the settings saved by release_audio() (when playback stops)."""
    import json
    try:
        marker = _audio_marker()
        if not os.path.exists(marker):
            return
        xbmc.executebuiltin('InhibitIdleShutdown(false)')
        saved = json.load(open(marker))
        for key, val in saved.items():
            if val is not None:
                _jsonrpc('Settings.SetSettingValue', {'setting': key, 'value': val})
        os.remove(marker)
    except Exception as exc:  # noqa
        log('restore_audio failed: {0}'.format(exc))

# populated per-invocation by router.set_context()
_CTX = {'base_url': '', 'handle': -1, 'params': {}}


def set_context(base_url, handle, params):
    _CTX['base_url'] = base_url
    _CTX['handle'] = handle
    _CTX['params'] = params


def handle():
    return _CTX['handle']


def param(name, default=None):
    return _CTX['params'].get(name, default)


def url_for(**kwargs):
    """Build a plugin:// URL for this addon with the given query params."""
    return '{0}?{1}'.format(_CTX['base_url'], urlencode(kwargs))


def setting(key, default=''):
    val = ADDON.getSetting(key)
    return val if val != '' else default


def set_setting(key, value):
    ADDON.setSetting(key, str(value))


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[{0}] {1}'.format(ADDON_ID, msg), level)


def notify(msg, heading=None):
    # heading defaults to the add-on's display name (addon.xml) so it always matches
    try:
        heading = heading or ADDON.getAddonInfo('name')
    except Exception:
        heading = heading or 'Nflix'
    xbmcgui.Dialog().notification(heading, msg)


def localize(string_id, default=''):
    try:
        return ADDON.getLocalizedString(string_id) or default
    except Exception:
        return default


def add_directory(label, url, art=None, info=None, is_folder=True, playable=False):
    """Add a folder or an action item to the current directory."""
    li = xbmcgui.ListItem(label=label)
    if art:
        li.setArt(art)
    if info:
        # InfoTagVideo setters are Kodi 20+ (xbmc.python 3.0.1, required in addon.xml)
        tag = li.getVideoInfoTag()
        try:
            if info.get('title'):
                tag.setTitle(info['title'])
            if info.get('plot'):
                tag.setPlot(info['plot'])
            if info.get('mediatype'):
                tag.setMediaType(info['mediatype'])
            if info.get('season') is not None:
                tag.setSeason(int(info['season']))
            if info.get('episode') is not None:
                tag.setEpisode(int(info['episode']))
            if info.get('year'):
                tag.setYear(int(info['year']))
            if info.get('duration'):
                tag.setDuration(int(info['duration']))
            if info.get('mpaa'):
                tag.setMpaa(str(info['mpaa']))
            if info.get('playcount'):
                tag.setPlaycount(int(info['playcount']))
            if info.get('resume'):
                tag.setResumePoint(float(info['resume'][0]), float(info['resume'][1]))
        except Exception as exc:  # noqa
            log('info tag set failed: {0}'.format(exc))
    # Playable titles are launched externally (Layer 2). They are NOT Kodi-playable,
    # so we mark them as plain action items: clicking runs the plugin's play route.
    if playable:
        li.setProperty('IsPlayable', 'false')
    xbmcplugin.addDirectoryItem(handle(), url, li, isFolder=is_folder)


def end_directory(content='videos', sort=True, succeeded=True):
    """succeeded=False: no listing; Kodi stays on the list the user came from."""
    if not succeeded:
        xbmcplugin.endOfDirectory(handle(), succeeded=False, cacheToDisc=False)
        return
    if sort:
        xbmcplugin.addSortMethod(handle(), xbmcplugin.SORT_METHOD_UNSORTED)
    xbmcplugin.setContent(handle(), content)
    # cacheToDisc=False: Kodi otherwise saves a listing that took over a second and shows
    # that copy on the next visit without asking the add-on, so a list outlived a sign-out,
    # a profile switch or a watched mark. The add-on's own cache keeps lists fast.
    xbmcplugin.endOfDirectory(handle(), cacheToDisc=False)


def focus_item(pos):
    """Open the list this invocation just ended on its item #pos (0 = the first, '..' not
    counted). Kodi has no API for it: the list appears only after endOfDirectory, and Kodi
    then restores the selection it remembers for that path. So wait for the list, move the
    focus and check that it held. Gives up after 3 s (e.g. the user has left the list)."""
    import time
    landed = False
    tries = 3       # a selection that won't stick mustn't keep taking the focus from the user
    for _ in range(30):
        time.sleep(0.1)
        path = xbmc.getInfoLabel('Container.FolderPath')
        if (dict(parse_qsl(urlsplit(path).query)) != _CTX['params']
                or xbmc.getCondVisibility('Container.IsUpdating')):
            continue
        # Container.CurrentItem counts from 1 without '..'; SetFocus's position counts '..'
        if xbmc.getInfoLabel('Container.CurrentItem') == str(pos + 1):
            if landed:
                return
            landed = True
        elif tries:
            tries -= 1
            landed = False
            xbmc.executebuiltin('SetFocus({0},{1},absolute)'.format(
                xbmc.getInfoLabel('System.CurrentControlId'),
                pos + (1 if xbmc.getCondVisibility('Container.HasParent') else 0)))
        else:
            return


def drop_kodi_listings():
    """Delete the copies of this add-on's lists that Kodi saved for its windows
    (special://temp/archive_cache/<window>-<crc>.fi). Kodi shows such a copy instead of
    asking the add-on; versions up to 0.3.2 let it save them."""
    d = xbmcvfs.translatePath('special://temp/archive_cache/')
    try:
        names = os.listdir(d)
    except OSError:
        return
    tag = ('plugin://' + ADDON_ID).encode('utf-8')
    for name in names:
        if not name.endswith('.fi'):
            continue
        path = os.path.join(d, name)
        try:
            with open(path, 'rb') as fh:
                ours = tag in fh.read()
            if ours:
                os.remove(path)
        except OSError:
            pass


def prompt(heading=None):
    kb = xbmc.Keyboard('', heading or localize(30052, 'Search Netflix'))
    kb.doModal()
    return kb.getText() if kb.isConfirmed() else None
