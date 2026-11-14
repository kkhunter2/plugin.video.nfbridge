# -*- coding: utf-8 -*-
"""Background service: while the embedded Netflix WebView is playing, Kodi's audio
device is released (player.release_audio, called on Play) so the player can open the
HDMI audio device; when playback stops, this restores the user's audio settings."""
import threading

import xbmc

from resources.lib import kodiutils, player, cache, router
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


def _open_list_params():
    """Plugin params of the add-on's list on screen, or None."""
    from urllib.parse import parse_qsl, urlsplit
    path = xbmc.getInfoLabel('Container.FolderPath') or ''
    if not path.startswith('plugin://' + kodiutils.ADDON_ID):
        return None
    params = dict(parse_qsl(urlsplit(path).query))
    params.setdefault('action', 'root')
    return params


def _requeue(items):
    for it in items:
        cache.queue(dict((k, v) for k, v in it.items() if k != '_redraw'), redraw=bool(it.get('_redraw')))


def _revalidate(monitor):
    """Worker thread: bring the lists the add-on served stale up to date (cache.queue),
    in the background, so Back never waits for the daemon. It gives way while the add-on
    is building a list for the user (cache.touch_foreground) and stops once a Netflix
    title starts (the rest waits in the queue). A list queued with redraw (the one on
    screen when a playback stopped) is redrawn afterwards, only if it is still on screen
    and Kodi is idle."""
    from resources.lib import routes
    while not monitor.abortRequested():
        items = cache.take_queue()
        if not items:
            return
        for n, item in enumerate(items):
            # give way to a list the user is opening (the daemon runs one request at a time)
            waited = 0
            while cache.foreground_age() < 3 and waited < 20 and not monitor.abortRequested():
                monitor.waitForAbort(0.5)
                waited += 0.5
            if monitor.abortRequested() or kodiutils.netflix_playing():
                return _requeue(items[n:])  # the daemon window must stay on the film
            params = dict((k, v) for k, v in item.items() if k != '_redraw')
            try:
                routes.warm(params)
            except Exception as exc:  # noqa
                kodiutils.log('background update of {0} failed: {1}'.format(params, exc))
            if kodiutils.netflix_playing():
                return _requeue(items[n:])  # interrupted by a playback: redo it later
            if item.get('_redraw') and _idle_on(params, monitor):
                xbmc.executebuiltin('Container.Refresh')


def _idle_on(params, monitor):
    """The list is on screen and Kodi idle, twice 0.4 s apart, and the add-on hasn't just
    started a list (a key press just before the check: the busy dialog shows only after a
    moment, and a refresh during a list load overlaps it)."""
    for i in range(2):
        if (kodiutils.netflix_playing() or cache.foreground_age() < 1.5
                or not kodiutils.showing_list(params)):
            return False
        if i == 0 and monitor.waitForAbort(0.4):
            return False
    return True


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
    worker = None
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
            # The worker re-reads the open list's marks, then redraws it (only when Kodi is
            # idle: a redraw during a list load overlaps it).
            monitor.waitForAbort(3)
            cache.mark_watched()
            shown = _open_list_params()
            if shown and shown.get('action') in router.DEFERRED:
                cache.queue(shown, redraw=True)
            elif shown:
                _refresh_open_list()        # a list the service doesn't update (search...)
        # a sign-in (or a profile picked in the window) just finished: a list on screen was
        # made before it (e.g. only 'Sign in to Netflix'), so draw it again
        n = h.get('signins')
        if n is not None:
            if signins is not None and n > signins:
                _refresh_open_list()
            signins = n
        was_playing = playing
        # only once the daemon answers: the worker never launches it (Kodi start, a daemon
        # that died), the add-on or the prestart does
        if (h and not playing and not (worker and worker.is_alive()) and cache.queued()):
            worker = threading.Thread(target=_revalidate, args=(monitor,), daemon=True)
            worker.start()
        if monitor.waitForAbort(2):
            break
    kodiutils.restore_audio()                 # restore on Kodi shutdown too
    # Kodi is exiting -> take the daemon down with it so no orphan Netflix window/process
    # (and its audio device hold) lingers after Kodi is gone. Quick: Kodi kills a service
    # that takes over 5 s to stop. The prefetch stops waiting on abort; a daemon it
    # launched that hasn't opened its port yet can't take /quit, so it is killed.
    if prestart:
        prestart.join(1)
    if worker:
        worker.join(1)
    try:
        if client.running():
            client._get('/quit', timeout=3)
        else:
            player.stop_spawned()
    except Exception:
        pass


if __name__ == '__main__':
    main()
