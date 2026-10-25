# -*- coding: utf-8 -*-
"""Route handlers: render the Netflix browse (via the WebView-fetch daemon /
nfclient) as Kodi directories, and Play by handing the title to the daemon's
window (Layer 2)."""
import hashlib

from resources.lib import kodiutils, player, cache, nfclient


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
        kodiutils.notify(kodiutils.localize(30031, 'Netflix is not ready'))
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


# TTLs. Lists outlive playback (cache.mark_watched): what watching changes — resume /
# watched marks — is re-read via refresh=, so a TTL only bounds how stale the LAYOUT
# (row titles, a row's titles, a show's episodes) may get, and must outlast an episode
# or a movie, or the list re-opened after it is rebuilt from scratch.
PAGE_TTL = 3 * 3600     # home / category row titles
ROW_TTL = 3 * 3600      # a row's videos
LIST_TTL = 3600         # My List
EPS_TTL = 3 * 3600      # a show's seasons + episodes (new episodes show within it); a title's basics


def _row_items(data):
    for r in data['rows']:
        q = {'action': 'row', 'root': data['root'], 'index': str(r['index']), 'title': r['title']}
        if r.get('cw'):
            q['cw'] = '1'
        kodiutils.add_directory(r['title'], kodiutils.url_for(**q))


def _cw_marks(c):
    """refresh= for Continue Watching, whose order follows what was watched: move the
    title just played to the front (as Netflix does) and re-read the marks. A title that
    wasn't in the row yet means it changed more than that -> rebuild it."""
    def refresh(vids):
        played = cache.last_watched()
        hit = next((v for v in vids if v.get('id') in played), None)
        if played and not hit:
            return cache.REBUILD
        if hit:
            vids.remove(hit)
            vids.insert(0, hit)
        return c.refresh_marks(vids)
    return refresh


def _home_refresh(data):
    # Home has no marks, but the first playback of a profile adds a Continue Watching row
    return None if any(r.get('cw') for r in data['rows']) else cache.REBUILD


def _page_key(root):
    """Cache key of the page (home / category) whose row list links to rows of root."""
    return 'home' if root.rstrip('/') == '/browse' else 'genre_' + root.rstrip('/').rsplit('/', 1)[-1]


def _seasons(c, sid):
    def _eps():
        s = c.episodes(sid)
        if not s:                           # one retry: the daemon may have been mid-navigation
            import time
            time.sleep(1)
            s = c.episodes(sid)
        return s
    # cached_ok never caches an empty result, so a failed load isn't stuck for the TTL
    return cache.cached_ok('eps_{0}'.format(sid), EPS_TTL, _eps,
                           refresh=lambda seasons: c.refresh_marks(
                               [e for s in seasons for e in s.get('eps', [])]))


def _home_data(c, quiet=False):
    # row titles only (no marks); rebuilding it scrolls the daemon's whole home
    return cache.cached('home', PAGE_TTL, lambda: c.home(n_rows=40, quiet=quiet),
                        refresh=_home_refresh)


def prefetch(stop=None):
    """Kodi start (service, setting 'prestart'): launch the daemon and cache home, so the
    first open of the add-on doesn't wait for WebView2 + Netflix (~11 s). stop: callable,
    True once Kodi is exiting."""
    c = _client()
    # a daemon still playing across a Kodi restart (Kodi crashed mid-film) keeps playing:
    # loading home would navigate its window away from the film
    if c.health().get('playing'):
        return
    if c.ensure(stop=stop) and not (stop and stop()):
        _home_data(c, quiet=True)


# ---------------------------------------------------------------- routes ------
def root():
    home()


def home():
    c = _client()
    if not c.ensure():
        return _not_ready(c)
    try:
        data = _home_data(c)
    except Exception as exc:  # noqa
        kodiutils.notify(kodiutils.localize(30041, 'Could not load home: {0}').format(exc))
        return kodiutils.end_directory(content='files')
    def _m(sid, default):
        return '[B]{0}[/B]'.format(kodiutils.localize(sid, default))
    kodiutils.add_directory(_m(30001, 'Search'), kodiutils.url_for(action='search'))
    kodiutils.add_directory(_m(30002, 'My List'), kodiutils.url_for(action='mylist'))
    kodiutils.add_directory(_m(30003, 'Series & Movies'), kodiutils.url_for(action='categories'))
    kodiutils.add_directory(_m(30004, 'Profiles'), kodiutils.url_for(action='profiles'))
    _row_items(data)
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
    # keyed by the row's TITLE (index only without one): a re-built page may have moved
    # rows, and a key by index would then serve another row's videos under this title
    ident = hashlib.md5(title.encode('utf-8')).hexdigest()[:10] if title else str(index)
    try:
        vids = cache.cached_when(
            'row_{0}_{1}'.format(root_id[-20:], ident), ROW_TTL,
            lambda: c.row_videos(root_id, index, n=40, title=title), keep=_warm,
            refresh=_cw_marks(c) if kodiutils.param('cw') else c.refresh_marks)
    except nfclient.RowGone:
        # the (fully loaded) page no longer has this row (Netflix shows some rows only for
        # a while, e.g. at certain times of day): stay on the row list and reload it from
        # the current page, rather than opening an empty folder
        import xbmc
        cache.drop(_page_key(root_id))
        kodiutils.notify(kodiutils.localize(30042, 'This row is no longer on Netflix'))
        kodiutils.end_directory(succeeded=False)
        return xbmc.executebuiltin('Container.Refresh')
    except Exception as exc:  # noqa
        kodiutils.notify(kodiutils.localize(30043, 'Could not load row: {0}').format(exc))
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
        return kodiutils.notify(kodiutils.localize(30031, 'Netflix is not ready'))
    try:
        profs = c.profiles()
    except Exception as exc:  # noqa
        return kodiutils.notify(str(exc))
    if not profs:
        return kodiutils.notify(kodiutils.localize(30044, 'No profiles found'))
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
        data = cache.cached('genre_{0}'.format(gid), PAGE_TTL, lambda: c.genre(gid))
    except Exception as exc:  # noqa
        kodiutils.notify(kodiutils.localize(30045, 'Could not load category: {0}').format(exc))
        return kodiutils.end_directory()
    _row_items(data)
    kodiutils.end_directory(content='files')


# signin / signout are ACTION items (is_folder=False): Kodi runs the plugin without a
# directory handle, so there is no directory to end.
def signin():
    player.launch_signin()


def signout():
    """Sign out of Netflix, after a confirmation. The daemon's session ends and it waits on
    the sign-in form, the saved lists and the remembered profile are dropped, and Kodi goes
    back to its home screen; opening the add-on again offers 'Sign in to Netflix' (any
    account)."""
    import xbmc
    import xbmcgui
    if not xbmcgui.Dialog().yesno(kodiutils.localize(30005, 'Sign out'),
                                  kodiutils.localize(30046, 'Sign out of Netflix on this PC?')):
        return
    c = _client()
    ok = False
    if c.ensure():
        try:
            ok = bool(c.sign_out().get('ok'))
        except Exception as exc:  # noqa
            kodiutils.log('sign out failed: {0}'.format(exc))
    elif c.health().get('login'):
        ok = True                   # already on the sign-in form
    if not ok:
        return kodiutils.notify(kodiutils.localize(30054, 'Could not sign out - try again'))
    cache.clear()
    kodiutils.set_setting('profile_index', 0)
    kodiutils.set_setting('profile_guid', '')
    kodiutils.notify(kodiutils.localize(30053, 'Signed out of Netflix'))
    # leave the add-on: the lists on screen belonged to the session that just ended; the
    # next open offers 'Sign in to Netflix'
    xbmc.executebuiltin('ActivateWindow(Home)')


def play():
    # The cache is kept; the lists re-read only their resume marks on the next render.
    # The service marks the stop (cache.mark_watched); marking here too records what is
    # played (for Continue Watching, by its tile: the show of an episode) and covers a
    # stop the service misses (it polls every 2 s).
    cache.mark_watched(kodiutils.param('id'), kodiutils.param('show'))
    player.play(kodiutils.param('id'))


def my_list():
    c = _client()
    if not c.ensure():
        return _not_ready(c)
    try:
        items = cache.cached_when('mylist', LIST_TTL, c.my_list, keep=_warm,
                                  refresh=c.refresh_marks)
    except Exception as exc:  # noqa
        kodiutils.notify(kodiutils.localize(30047, 'Could not load My List: {0}').format(exc))
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
        kodiutils.notify(kodiutils.localize(30048, 'Search failed: {0}').format(exc))
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


def _render_episodes(eps, show_id):
    for e in eps:
        title = e.get('title') or e['id']
        label = 'E{0}  {1}'.format(e['number'], title) if e.get('number') else title
        info = _watch_info({'title': e.get('title'), 'mediatype': 'episode',
                            'plot': e.get('synopsis'), 'season': e.get('season'),
                            'episode': e.get('number'), 'year': e.get('year'),
                            'duration': e.get('runtime'), 'mpaa': e.get('rating')},
                           e.get('bookmark'), e.get('runtime'))
        kodiutils.add_directory(
            label, kodiutils.url_for(action='play', id=e['id'], show=show_id),
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
            info = cache.cached_ok('info_{0}'.format(sid), EPS_TTL, lambda: c.video_info(sid),
                                   refresh=lambda i: c.refresh_marks([i]))
        except Exception as exc:  # noqa
            kodiutils.log('video_info failed for {0}: {1}'.format(sid, exc))
            info = {}
        if info.get('type') == 'movie':
            _add_item(info)
            return kodiutils.end_directory(content='movies')
        if info.get('type') == 'show':
            typ = 'show'

    try:
        seasons = _seasons(c, sid)
    except Exception as exc:  # noqa
        kodiutils.notify(kodiutils.localize(30049, 'Could not load episodes: {0}').format(exc))
        return kodiutils.end_directory()
    if not seasons:
        if typ == 'show':
            # a real series whose episodes failed to load -> let the user retry
            kodiutils.notify(kodiutils.localize(30050, 'Could not load episodes - try again'))
            return kodiutils.end_directory()
        # no type and no episodes: offer it as a playable item rather than guessing
        _add_item({'id': sid, 'title': (info or {}).get('title') or sid, 'type': 'movie',
                   'art': (info or {}).get('art'), 'synopsis': (info or {}).get('synopsis')})
        return kodiutils.end_directory(content='movies')
    if len(seasons) == 1:
        return _render_episodes(seasons[0].get('eps', []), sid)
    for i, s in enumerate(seasons):
        label = s.get('title') or kodiutils.localize(30051, 'Season {0}').format(s.get('number') or i + 1)
        cnt = len(s.get('eps', []))
        kodiutils.add_directory(
            '{0}  ({1})'.format(label, cnt) if cnt else label,
            kodiutils.url_for(action='season', show=sid, i=str(i)), is_folder=True)
    kodiutils.end_directory(content='seasons')


def season():
    c = _client()
    if not c.ensure():
        return _not_ready(c)
    sid = kodiutils.param('show')
    idx = int(kodiutils.param('i') or '0')
    try:
        seasons = _seasons(c, sid)
    except Exception as exc:  # noqa
        kodiutils.notify(str(exc))
        return kodiutils.end_directory()
    if idx < len(seasons):
        return _render_episodes(seasons[idx].get('eps', []), sid)
    kodiutils.end_directory(content='episodes')
