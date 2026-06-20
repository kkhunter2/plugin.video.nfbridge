# -*- coding: utf-8 -*-
"""Background service: while the embedded Netflix WebView is playing, Kodi's audio
device is released (player.release_audio, called on Play) so the player can open the
HDMI audio device; when playback stops, this restores the user's audio settings."""
import xbmc

from resources.lib import kodiutils, player, cache
from resources.lib import nfclient


def main():
    monitor = xbmc.Monitor()
    kplayer = xbmc.Player()
    client = nfclient.NetflixClient(port=player.DAEMON_PORT)
    was_playing = False
    while not monitor.abortRequested():
        try:
            playing = bool((client._get('/health', timeout=2) or {}).get('playing'))
        except Exception:
            playing = False
        # If Kodi started its OWN (local) playback while the Netflix window is still up,
        # stop Netflix so it releases the audio device (else local playback misbehaves).
        if playing:
            try:
                if kplayer.isPlaying():
                    client._post('/stop', {}, timeout=5)
                    playing = False
            except Exception:
                pass
        if was_playing and not playing:
            kodiutils.restore_audio()        # playback stopped -> give audio back to Kodi
            # the bookmark changed during playback; drop the stale cache and re-render the
            # open list so the watched / resume mark updates right away (not only on re-entry).
            # Wait first: the daemon reloads /browse on stop, so fresh bookmarks need a moment.
            monitor.waitForAbort(3)
            cache.clear()
            try:
                if 'plugin.video.nfbridge' in (xbmc.getInfoLabel('Container.FolderPath') or ''):
                    xbmc.executebuiltin('Container.Refresh')
            except Exception:
                pass
        was_playing = playing
        if monitor.waitForAbort(2):
            break
    kodiutils.restore_audio()                 # restore on Kodi shutdown too


if __name__ == '__main__':
    main()
