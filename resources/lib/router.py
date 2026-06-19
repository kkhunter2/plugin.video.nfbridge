# -*- coding: utf-8 -*-
"""URL routing: map plugin:// paths to handlers.

Routes
------
  (root)                       -> profiles (or home if a profile is active)
  action=profiles              -> profile chooser
  action=home&profile=<guid>   -> home (LoCo carousels)
  action=list&id=<listid>      -> a video list / carousel
  action=mylist                -> My List
  action=search                -> search prompt + results
  action=show&id=<videoid>     -> seasons / episodes of a show
  action=play&id=<videoid>     -> Layer 2: launch external player (no Kodi playback)
"""
try:
    from urllib.parse import parse_qsl
except ImportError:  # py2 safety (Kodi 18-)
    from urlparse import parse_qsl

from resources.lib import routes, kodiutils


def dispatch(base_url, handle, query):
    params = dict(parse_qsl(query))
    action = params.get('action', 'root')
    kodiutils.set_context(base_url, handle, params)

    # Daemon recovery: the detached daemon can die while Kodi still shows cached lists, so
    # any click would just error. If a browse action runs while the daemon is dead,
    # relaunch it, drop the now-stale cache (the LoCo root changes on relaunch) and land
    # the user on a fresh Home instead of the broken view.
    if action not in ('play', 'signin', 'signout', 'switchprofile'):
        from resources.lib import player, cache
        client = player.browse_client()
        if not client.running():
            cache.clear()
            if client.ensure():
                kodiutils.notify('Netflix reconnected')
                if action not in ('root', 'home', 'profiles', 'categories'):
                    action = 'root'

    table = {
        'root': routes.root,
        'profiles': routes.profiles,
        'home': routes.home,
        'row': routes.row,
        'categories': routes.categories,
        'genre': routes.genre,
        'rowlist': routes.rowlist,
        'list': routes.video_list,
        'mylist': routes.my_list,
        'search': routes.search,
        'show': routes.show,
        'season': routes.season,
        'play': routes.play,
        'signin': routes.signin,
        'signout': routes.signout,
        'switchprofile': routes.switchprofile,
    }
    fn = table.get(action, routes.root)
    fn()
