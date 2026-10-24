# -*- coding: utf-8 -*-
"""Background service: while the embedded Netflix WebView is playing, Kodi's audio
device is released (player.release_audio, called on Play) so the player can open the
HDMI audio device; when playback stops, this restores the user's audio settings."""
import xbmc

from resources.lib import kodiutils, player, cache
from resources.lib import nfclient


def main():
    monitor = xbmc.Monitor()
    client = nfclient.NetflixClient(port=player.DAEMON_PORT)
    was_playing = False
    # If a previous Kodi session died mid-playback, the saved-settings marker survives
    # and the user is left without GUI sounds / their audio settings. Hand everything
    # back at startup — unless the daemon is genuinely still playing across the restart.
    if not client.health().get('playing'):
        kodiutils.restore_audio()
    while not monitor.abortRequested():
        playing = bool(client.health().get('playing'))
        # If Kodi is playing ANY content through its own player -- a local file, YouTube, or
        # any other streaming add-on -- fully terminate the daemon so its background WebView
        # cannot hold or contend the audio device. That tab otherwise keeps the (HDMI /
        # AV-receiver) audio endpoint open, which can silence the other player on some
        # setups. The daemon relaunches automatically on the next Netflix browse once Kodi's
        # player is idle (router/browse_client skip the launch while it is active).
        # (Netflix's own embedded playback does NOT make Kodi's player report isPlaying,
        # so this never fires during Netflix playback.)
        if kodiutils.is_playing():
            try:
                if client.running():
                    kodiutils.restore_audio()        # hand audio settings back first
                    try:
                        client._get('/quit', timeout=3)
                    except Exception:
                        pass
            except Exception:
                pass
            playing = False
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
    # Kodi is exiting -> take the daemon down with it so no orphan Netflix window/process
    # (and its audio device hold) lingers after Kodi is gone.
    try:
        if client.running():
            client._get('/quit', timeout=3)
    except Exception:
        pass


if __name__ == '__main__':
    main()
