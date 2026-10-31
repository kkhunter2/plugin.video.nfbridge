# -*- coding: utf-8 -*-
"""URL routing: map plugin:// paths to handlers.

Routes
------
  (root)                       -> home (LoCo carousels + Search / My List / Categories / Profiles)
  action=home                  -> home
  action=row&root=&index=      -> a home row's videos
  action=categories            -> Series / Movies
  action=genre&id=<gid>        -> a category's rows (then action=row with root=/browse/genre/<gid>)
  action=mylist                -> My List
  action=search                -> search prompt + results
  action=show&id=<videoid>     -> seasons / episodes of a show (or a lone movie item)
  action=season&show=&i=       -> one season's episodes
  action=profiles              -> profile list (click = switch)
  action=switchprofile&index=  -> switch the daemon to that profile
  action=pickprofile           -> profile picker dialog (Settings button / RunPlugin)
  action=signin / signout
  action=play&id=<videoid>     -> Layer 2: play in the daemon window (no Kodi playback)
"""
from urllib.parse import parse_qsl

from resources.lib import routes, kodiutils

# actions that neither browse nor need the daemon recovered first
_NO_BROWSE = ('play', 'signin', 'signout', 'switchprofile', 'pickprofile')
# actions whose URL params don't depend on a cached page layout
_ROOT_SAFE = ('root', 'home', 'profiles', 'categories', 'search', 'mylist', 'genre', 'show', 'season')


def dispatch(base_url, handle, query):
    params = dict(parse_qsl(query))
    action = params.get('action', 'root')
    kodiutils.set_context(base_url, handle, params)

    # Safety net: any non-play action means the user is browsing, not watching, so make
    # sure the audio device / screensaver are back to the user's settings (in case the
    # service missed the stop).
    if action != 'play':
        kodiutils.restore_audio()

    # Daemon recovery: the detached daemon can die (or is quit by the service on Kodi
    # shutdown, or when Kodi plays something else) while Kodi still shows a list. If a
    # browse action finds the daemon down, (re)launch it and land the user on Home. The
    # saved lists stay: they don't depend on the daemon process, and watching a local file
    # shouldn't cost every list. Not while Kodi is playing
    # local / other-addon content: the service quits the daemon whenever Kodi's player is
    # active (to free the audio device), so launching here would thrash launch<->quit
    # and can orphan the WebView2 profile lock ("Main window failed to start").
    if action not in _NO_BROWSE and not kodiutils.is_playing():
        from resources.lib import player
        client = player.browse_client()
        if client.down():
            kodiutils.notify(kodiutils.localize(30009, 'Starting Netflix...'))
            if client.ensure() and action not in _ROOT_SAFE:
                action = 'root'

    table = {
        'root': routes.root,
        'profiles': routes.profiles,
        'home': routes.home,
        'row': routes.row,
        'categories': routes.categories,
        'genre': routes.genre,
        'mylist': routes.my_list,
        'search': routes.search,
        'show': routes.show,
        'season': routes.season,
        'play': routes.play,
        'signin': routes.signin,
        'signout': routes.signout,
        'switchprofile': routes.switchprofile,
        'pickprofile': routes.pickprofile,
    }
    fn = table.get(action, routes.root)
    fn()
