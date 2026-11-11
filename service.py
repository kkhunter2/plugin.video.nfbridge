# -*- coding: utf-8 -*-
"""Background service: while the embedded Netflix WebView is playing, Kodi's audio
device is released (player.release_audio, called on Play) so the player can open the
HDMI audio device; when playback stops, this restores the user's audio settings."""
import threading

import xbmc

from resources.lib import kodiutils, player, cache
from resources.lib import nfclient


def _prestart():
    """Setting 'prestart': launch the daemon and cache home shortly after Kodi starts, so
    the first open of the add-on doesn't wait ~11 s for WebView2 + Netflix. Waits a bit
    first so it doesn't compete with Kodi's own startup."""
    monitor = xbmc.Monitor()
    if monitor.waitForAbort(10):
        return
    try:
        from resources.lib import routes
        routes.prefetch(stop=monitor.abortRequested)
    except Exception as exc:  # noqa
        kodiutils.log('prestart failed: {0}'.format(exc))


def _refresh_open_list():
    """Redraw the add-on's list if one is on screen."""
    try:
        if 'plugin.video.nfbridge' in (xbmc.getInfoLabel('Container.FolderPath') or ''):
            xbmc.executebuiltin('Container.Refresh')
    except Exception:
        pass


def main():
    monitor = xbmc.Monitor()
    client = nfclient.NetflixClient(port=player.DAEMON_PORT)
    was_playing = False
    # If a previous Kodi session died mid-playback, the saved-settings marker survives
    # and the user is left without GUI sounds / their audio settings. Hand everything
    # back at startup — unless the daemon is genuinely still playing across the restart.
    if not client.health().get('playing'):
        kodiutils.restore_audio()
    prestart = None
    if kodiutils.setting('prestart', 'true') == 'true':
        prestart = threading.Thread(target=_prestart, daemon=True)
        prestart.start()
    missed = False
    signins = None
    while not monitor.abortRequested():
        h = client.health()
        if not h and was_playing and not missed:
            # one failed probe mid-playback isn't a stop (restoring audio settings under the
            # player would hand it Kodi's passthrough device): wait for a second one
            missed = True
            playing = True
        else:
            missed = False
            playing = bool(h.get('playing'))
        # The daemon stays up while Kodi plays something of its own (local file, another
        # add-on): browsing needs no audio device, and the browse page's previews are muted
        # and paused by the daemon (MUTE_PREVIEWS_JS). Kodi's player is stopped only when a
        # Netflix title is actually played (player.play -> kodiutils.stop_playback).
        if was_playing and not playing:
            kodiutils.restore_audio()        # playback stopped -> give audio back to Kodi
            # the bookmark changed during playback; mark the cache watched (lists keep their
            # layout and re-read just the marks, see cache.mark_watched) and re-render the
            # open list so the watched / resume mark updates right away (not only on re-entry).
            # Wait first: the daemon reloads /browse on stop, so fresh bookmarks need a moment.
            monitor.waitForAbort(3)
            cache.mark_watched()
            _refresh_open_list()
        # a sign-in (or a profile picked in the window) just finished: a list on screen was
        # made before it (e.g. only 'Sign in to Netflix'), so draw it again
        n = h.get('signins')
        if n is not None:
            if signins is not None and n > signins:
                _refresh_open_list()
            signins = n
        was_playing = playing
        if monitor.waitForAbort(2):
            break
    kodiutils.restore_audio()                 # restore on Kodi shutdown too
    # Kodi is exiting -> take the daemon down with it so no orphan Netflix window/process
    # (and its audio device hold) lingers after Kodi is gone. Quick: Kodi kills a service
    # that takes over 5 s to stop. The prefetch stops waiting on abort; a daemon it
    # launched that hasn't opened its port yet can't take /quit, so it is killed.
    if prestart:
        prestart.join(1)
    try:
        if client.running():
            client._get('/quit', timeout=3)
        else:
            player.stop_spawned()
    except Exception:
        pass


if __name__ == '__main__':
    main()
