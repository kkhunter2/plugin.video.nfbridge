# -*- coding: utf-8 -*-
"""Thin helpers over the Kodi Python API (xbmc/xbmcgui/xbmcplugin/xbmcaddon).

Keeps the rest of the addon free of Kodi boilerplate and easy to unit-test:
everything Kodi-specific funnels through here.
"""
try:
    from urllib.parse import urlencode
except ImportError:
    from urllib import urlencode

import os

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
    and Netflix fails (D7361); (2) disable the screensaver + inhibit idle shutdown, since
    Kodi counts external playback as idle. The user's settings are saved for restore."""
    import json
    try:
        marker = _audio_marker()
        if not os.path.exists(marker):
            saved = {}
            for key in ('audiooutput.streamsilence', 'audiooutput.passthrough', 'screensaver.mode'):
                saved[key] = (_jsonrpc('Settings.GetSettingValue',
                                       {'setting': key}).get('result') or {}).get('value')
            with open(marker, 'w') as f:
                json.dump(saved, f)
        _jsonrpc('Settings.SetSettingValue', {'setting': 'audiooutput.streamsilence', 'value': 0})
        _jsonrpc('Settings.SetSettingValue', {'setting': 'audiooutput.passthrough', 'value': False})
        _jsonrpc('Settings.SetSettingValue', {'setting': 'screensaver.mode', 'value': ''})
        xbmc.executebuiltin('InhibitIdleShutdown(true)')
    except Exception as exc:  # noqa
        log('release_audio failed: {0}'.format(exc))


def restore_audio():
    """Restore the settings saved by release_audio() (when playback stops)."""
    import json
    try:
        xbmc.executebuiltin('InhibitIdleShutdown(false)')
        marker = _audio_marker()
        if not os.path.exists(marker):
            return
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


def notify(msg, heading='Netflix (Bridge)'):
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
        tag = li.getVideoInfoTag()
        if info.get('title'):
            tag.setTitle(info['title'])
        if info.get('plot'):
            tag.setPlot(info['plot'])
        if info.get('mediatype'):
            tag.setMediaType(info['mediatype'])
        try:
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


def end_directory(content='videos', sort=True):
    if sort:
        xbmcplugin.addSortMethod(handle(), xbmcplugin.SORT_METHOD_UNSORTED)
    xbmcplugin.setContent(handle(), content)
    xbmcplugin.endOfDirectory(handle())


def prompt(heading='Search Netflix'):
    kb = xbmc.Keyboard('', heading)
    kb.doModal()
    return kb.getText() if kb.isConfirmed() else None
