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

    table = {
        'root': routes.root,
        'profiles': routes.profiles,
        'home': routes.home,
        'row': routes.row,
        'list': routes.video_list,
        'mylist': routes.my_list,
        'search': routes.search,
        'show': routes.show,
        'play': routes.play,
        'signin': routes.signin,
        'signout': routes.signout,
    }
    fn = table.get(action, routes.root)
    fn()
