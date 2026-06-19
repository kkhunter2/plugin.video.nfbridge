# -*- coding: utf-8 -*-
"""Background service: while the embedded Netflix WebView is playing, Kodi's audio
device is released (player.release_audio, called on Play) so the player can open the
HDMI audio device; when playback stops, this restores the user's audio settings."""
import xbmc

from resources.lib import kodiutils, player
from resources.lib import nfclient


def main():
    monitor = xbmc.Monitor()
    client = nfclient.NetflixClient(port=player.DAEMON_PORT)
    was_playing = False
    while not monitor.abortRequested():
        try:
            playing = bool((client._get('/health', timeout=2) or {}).get('playing'))
        except Exception:
            playing = False
        if was_playing and not playing:
            kodiutils.restore_audio()        # playback stopped -> give audio back to Kodi
        was_playing = playing
        if monitor.waitForAbort(2):
            break
    kodiutils.restore_audio()                 # restore on Kodi shutdown too


if __name__ == '__main__':
    main()
