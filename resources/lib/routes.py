# -*- coding: utf-8 -*-
"""Route handlers: render the Netflix browse (via the WebView-fetch daemon /
nfclient) as Kodi directories, and Play by handing the title to the daemon's
window (Layer 2)."""
from resources.lib import kodiutils, player, cache


def _client():
    return player.browse_client()


def _not_ready(c):
    """Explain why the daemon isn't usable and end the (empty) directory."""
    h = c.health()
    if kodiutils.is_playing():
        # the service quits the daemon while Kodi plays, so it can't be (re)started now
        kodiutils.notify(kodiutils.localize(30010, 'Stop the current playback to browse Netflix'))
    elif h.get('login') or (h and not h.get('ready')):
        # parked on the sign-in form (or signed out): offer sign-in as an ACTION item (not
        # a folder), so clicking runs the signin route without opening an empty directory
        kodiutils.add_directory('[B]{0}[/B]'.format(kodiutils.localize(30006, 'Sign in to Netflix')),
                                kodiutils.url_for(action='signin'), is_folder=False)
        kodiutils.notify(kodiutils.localize(30006, 'Sign in to Netflix'))
    elif c.running():
        # up and signed in but it didn't answer in time (page mid-navigation): just retry
        kodiutils.notify(kodiutils.localize(30012, 'Netflix is busy - try again'))
    else:
        kodiutils.notify('Nflix browse not ready')
    return kodiutils.end_directory(content='files')


def _add_item(v):
    """Render a video: a show -> folder (episode list); a movie -> playable."""
    if not v.get('id'):
        return
    art = {'thumb': v.get('art'), 'poster': v.get('art')}
    if v.get('type') == 'movie':
        info = _watch_info({'title': v['title'], 'mediatype': 'movie',
                            'plot': v.get('synopsis')},
                           v.get('bookmark'), v.get('runtime'))
        kodiutils.add_directory(
            v['title'] or v['id'], kodiutils.url_for(action='play', id=v['id']),
            art=art, info=info, is_folder=False, playable=True)
    else:
        # 'show' OR unknown type -> open as a folder; show() resolves an unknown type
        # with one falcor call and renders a lone playable item for a movie.
        kodiutils.add_directory(
            v['title'] or v['id'],
            kodiutils.url_for(action='show', id=v['id'], type=v.get('type') or ''),
            art=art, info={'title': v['title'], 'mediatype': 'tvshow',
                           'plot': v.get('synopsis')}, is_folder=True)


# a row/list is cacheable only when it has content AND art (a 'cold' snapshot right
# after a daemon restart has no art; an empty result is a transient failure) — so the
# next visit retries instead of serving it for the full TTL.
def _warm(vs):
    return bool(vs) and any(v.get('art') for v in vs)


# ---------------------------------------------------------------- routes ------
def root():
    home()


def home():
    c = _client()
    if not c.ensure():
        return _not_ready(c)
    try:
        data = cache.cached('home', 180, lambda: c.home(n_rows=40))
    except Exception as exc:  # noqa
        kodiutils.notify('Could not load home: {0}'.format(exc))
        return kodiutils.end_directory(content='files')
    def _m(sid, default):
        return '[B]{0}[/B]'.format(kodiutils.localize(sid, default))
    kodiutils.add_directory(_m(30001, 'Search'), kodiutils.url_for(action='search'))
    kodiutils.add_directory(_m(30002, 'My List'), kodiutils.url_for(action='mylist'))
    kodiutils.add_directory(_m(30003, 'Series & Movies'), kodiutils.url_for(action='categories'))
    kodiutils.add_directory(_m(30004, 'Profiles'), kodiutils.url_for(action='profiles'))
    for r in data['rows']:
        kodiutils.add_directory(
            r['title'],
            kodiutils.url_for(action='row', root=data['root'], index=str(r['index']),
                              title=r['title']))
    kodiutils.add_directory(kodiutils.localize(30005, 'Sign out'), kodiutils.url_for(action='signout'),
                            is_folder=False)
    kodiutils.end_directory(content='files')


def row():
    c = _client()
    if not c.ensure():
        return _not_ready(c)
    root_id = kodiutils.param('root') or '/browse'
    index = int(kodiutils.param('index') or '0')
    title = kodiutils.param('title') or ''
    try:
        vids = cache.cached_when(
            'row_{0}_{1}'.format(root_id[-20:], index), 300,
            lambda: c.row_videos(root_id, index, n=40, title=title), keep=_warm)
    except Exception as exc:  # noqa
        kodiutils.notify('Could not load row: {0}'.format(exc))
        return kodiutils.end_directory()
    for v in vids:
        _add_item(v)
    kodiutils.end_directory(content='movies')


def profiles():
    c = _client()
    if not c.ensure():
        return _not_ready(c)
    try:
        for i, p in enumerate(c.profiles()):
            kodiutils.add_directory(
                p['name'], kodiutils.url_for(action='switchprofile', index=str(i),
                                             guid=p.get('guid') or ''),
                art={'icon': p.get('avatar')}, is_folder=False)
    except Exception as exc:  # noqa
        kodiutils.notify(str(exc))
    kodiutils.end_directory(content='files')


def switchprofile():
    cache.clear()
    player.switch_profile(kodiutils.param('index') or '0', kodiutils.param('guid') or '')


def pickprofile():
    """Standalone profile picker (used by the Settings button, via RunPlugin). Pops a
    select dialog of Netflix profiles, then switches to the chosen one — no directory,
    so it works from outside a plugin listing."""
    import xbmcgui
    c = _client()
    if not c.ensure():
        return kodiutils.notify('Netflix not ready')
    try:
        profs = c.profiles()
    except Exception as exc:  # noqa
        return kodiutils.notify(str(exc))
    if not profs:
        return kodiutils.notify('No profiles found')
    names = [p.get('name', '?') for p in profs]
    sel = xbmcgui.Dialog().select(kodiutils.localize(30004, 'Profiles'), names)
    if sel < 0:
        return
    cache.clear()
    player.switch_profile(str(sel), profs[sel].get('guid') or '')


CATEGORIES = [('83', 30007, 'Series'), ('34399', 30008, 'Movies')]


def categories():
    for gid, sid, name in CATEGORIES:
        kodiutils.add_directory(kodiutils.localize(sid, name), kodiutils.url_for(action='genre', id=gid))
    kodiutils.end_directory(content='files')


def genre():
    c = _client()
    if not c.ensure():
        return _not_ready(c)
    gid = kodiutils.param('id')
    try:
        data = cache.cached('genre_{0}'.format(gid), 300, lambda: c.genre(gid))
    except Exception as exc:  # noqa
        kodiutils.notify('Could not load category: {0}'.format(exc))
        return kodiutils.end_directory()
    for r in data['rows']:
        kodiutils.add_directory(
            r['title'],
            kodiutils.url_for(action='row', root=data['root'], index=str(r['index']),
                              title=r['title']))
    kodiutils.end_directory(content='files')


# signin / signout are ACTION items (is_folder=False): Kodi runs the plugin without a
# directory handle, so there is no directory to end.
def signin():
    player.launch_signin()


def signout():
    cache.clear()
    kodiutils.notify('To sign out, use the account menu inside the Netflix window')


def play():
    player.play(kodiutils.param('id'))
    # Watching changes the browse state: 'Continue Watching' gains this title and Netflix
    # re-orders the home rows on the next load. Drop the cached home + rows so the next
    # browse rebuilds from the daemon's refreshed page (otherwise the stale, old-root menu
    # is served for up to its TTL: wrong row order, missing just-watched title).
    cache.drop('home', 'mylist')
    cache.drop_prefix('row_')


def my_list():
    c = _client()
    if not c.ensure():
        return _not_ready(c)
    try:
        items = cache.cached_when('mylist', 120, c.my_list, keep=_warm)
    except Exception as exc:  # noqa
        kodiutils.notify('Could not load My List: {0}'.format(exc))
        return kodiutils.end_directory()
    for v in items:
        _add_item(v)
    kodiutils.end_directory(content='movies')


def search():
    # Search runs in the daemon (Netflix's own Pinot search page, read from the Apollo
    # cache); the daemon returns to /browse on the next home() call.
    q = kodiutils.prompt()
    if not q:
        return kodiutils.end_directory()
    c = _client()
    if not c.ensure():
        return _not_ready(c)
    try:
        results = cache.cached_ok('search_{0}'.format(q[:40]), 300, lambda: c.search(q))
    except Exception as exc:  # noqa
        kodiutils.notify('Search failed: {0}'.format(exc))
        return kodiutils.end_directory()
    for v in results:
        _add_item(v)
    kodiutils.end_directory(content='movies')


def _watch_info(info, bookmark, runtime):
    """Mark watched (playcount) or in-progress (resume point) from the Netflix bookmark."""
    if bookmark and runtime:
        if bookmark >= runtime * 0.9:
            info['playcount'] = 1
        elif bookmark > 0:
            info['resume'] = (bookmark, runtime)
    return info


def _render_episodes(eps):
    for e in eps:
        title = e.get('title') or e['id']
        label = 'E{0}  {1}'.format(e['number'], title) if e.get('number') else title
        info = _watch_info({'title': e.get('title'), 'mediatype': 'episode',
                            'plot': e.get('synopsis'), 'season': e.get('season'),
                            'episode': e.get('number'), 'year': e.get('year'),
                            'duration': e.get('runtime'), 'mpaa': e.get('rating')},
                           e.get('bookmark'), e.get('runtime'))
        kodiutils.add_directory(
            label, kodiutils.url_for(action='play', id=e['id']),
            art={'thumb': e.get('art'), 'poster': e.get('art')},
            info=info, is_folder=False, playable=True)
    kodiutils.end_directory(content='episodes')


def show():
    c = _client()
    if not c.ensure():
        return _not_ready(c)
    sid = kodiutils.param('id')
    typ = kodiutils.param('type')
    info = {}

    if typ != 'show':
        # Unknown type (e.g. Top 10 tiles): ONE falcor call tells movie from show, so a
        # movie doesn't first sit through the (navigating, ~15 s) episode lookup. A movie
        # is rendered as a single playable item — this is a folder handle, so playing
        # from here directly isn't possible.
        try:
            info = cache.cached_ok('info_{0}'.format(sid), 600, lambda: c.video_info(sid))
        except Exception as exc:  # noqa
            kodiutils.log('video_info failed for {0}: {1}'.format(sid, exc))
            info = {}
        if info.get('type') == 'movie':
            _add_item(info)
            return kodiutils.end_directory(content='movies')
        if info.get('type') == 'show':
            typ = 'show'

    def _eps():
        s = c.episodes(sid)
        if not s:                           # one retry: the daemon may have been mid-navigation
            import time
            time.sleep(1)
            s = c.episodes(sid)
        return s
    try:
        # cached_ok never caches an empty result, so a failed load isn't stuck for 600s
        seasons = cache.cached_ok('eps_{0}'.format(sid), 600, _eps)
    except Exception as exc:  # noqa
        kodiutils.notify('Could not load episodes: {0}'.format(exc))
        return kodiutils.end_directory()
    if not seasons:
        if typ == 'show':
            # a real series whose episodes failed to load -> let the user retry
            kodiutils.notify('Could not load episodes - try again')
            return kodiutils.end_directory()
        # no type and no episodes: offer it as a playable item rather than guessing
        _add_item({'id': sid, 'title': (info or {}).get('title') or sid, 'type': 'movie',
                   'art': (info or {}).get('art'), 'synopsis': (info or {}).get('synopsis')})
        return kodiutils.end_directory(content='movies')
    if len(seasons) == 1:
        return _render_episodes(seasons[0].get('eps', []))
    for i, s in enumerate(seasons):
        label = s.get('title') or 'Season {0}'.format(s.get('number') or i + 1)
        cnt = len(s.get('eps', []))
        kodiutils.add_directory(
            '{0}  ({1})'.format(label, cnt) if cnt else label,
            kodiutils.url_for(action='season', show=sid, i=str(i)), is_folder=True)
    kodiutils.end_directory(content='seasons')


def season():
    c = _client()
    sid = kodiutils.param('show')
    idx = int(kodiutils.param('i') or '0')
    try:
        seasons = cache.cached('eps_{0}'.format(sid), 600, lambda: c.episodes(sid))
    except Exception as exc:  # noqa
        kodiutils.notify(str(exc))
        return kodiutils.end_directory()
    if idx < len(seasons):
        return _render_episodes(seasons[idx].get('eps', []))
    kodiutils.end_directory(content='episodes')
