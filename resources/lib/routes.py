# -*- coding: utf-8 -*-
"""Route handlers: render the Netflix browse (via the WebView-fetch daemon /
nfclient) as Kodi directories, and Play by handing the title to the daemon's
window (Layer 2)."""
from resources.lib import kodiutils, player, cache


def _client():
    return player.browse_client()


def _add_item(v):
    """Render a video: a show -> folder (episode list); a movie -> playable."""
    if not v.get('id'):
        return
    art = {'thumb': v.get('art'), 'poster': v.get('art')}
    if v.get('type') == 'show':
        kodiutils.add_directory(
            v['title'] or v['id'], kodiutils.url_for(action='show', id=v['id']),
            art=art, info={'title': v['title'], 'mediatype': 'tvshow'}, is_folder=True)
    else:
        info = _watch_info({'title': v['title'], 'mediatype': 'movie'},
                           v.get('bookmark'), v.get('runtime'))
        kodiutils.add_directory(
            v['title'] or v['id'], kodiutils.url_for(action='play', id=v['id']),
            art=art, info=info, is_folder=False, playable=True)


# ---------------------------------------------------------------- routes ------
def root():
    home()


def home():
    c = _client()
    if not c.ensure():
        kodiutils.add_directory('[B]{0}[/B]'.format(kodiutils.localize(30006, 'Sign in to Netflix')),
                                kodiutils.url_for(action='signin'))
        kodiutils.notify(kodiutils.localize(30006, 'Sign in to Netflix'))
        return kodiutils.end_directory(content='files')
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
            kodiutils.url_for(action='row', root=data['root'], index=str(r['index'])))
    kodiutils.add_directory(kodiutils.localize(30005, 'Sign out'), kodiutils.url_for(action='signout'))
    kodiutils.end_directory(content='files')


def row():
    c = _client()
    if not c.ready():
        kodiutils.notify('Netflix browse not ready')
        return kodiutils.end_directory()
    root_id = kodiutils.param('root')
    index = int(kodiutils.param('index') or '0')
    try:
        vids = cache.cached('row_{0}_{1}'.format(root_id[-12:], index), 300,
                            lambda: c.row_videos(root_id, index, n=40))
    except Exception as exc:  # noqa
        kodiutils.notify('Could not load row: {0}'.format(exc))
        return kodiutils.end_directory()
    for v in vids:
        _add_item(v)
    kodiutils.end_directory(content='movies')


def profiles():
    c = _client()
    if not c.ready():
        return kodiutils.end_directory()
    try:
        for i, p in enumerate(c.profiles()):
            kodiutils.add_directory(
                p['name'], kodiutils.url_for(action='switchprofile', index=str(i)),
                art={'icon': p.get('avatar')})
    except Exception as exc:  # noqa
        kodiutils.notify(str(exc))
    kodiutils.end_directory(content='files')


def switchprofile():
    cache.clear()
    player.switch_profile(kodiutils.param('index') or '0')
    kodiutils.end_directory()


CATEGORIES = [('83', 30007, 'Series'), ('34399', 30008, 'Movies')]


def categories():
    for gid, sid, name in CATEGORIES:
        kodiutils.add_directory(kodiutils.localize(sid, name), kodiutils.url_for(action='genre', id=gid))
    kodiutils.end_directory(content='files')


def genre():
    c = _client()
    if not c.ensure():
        return kodiutils.end_directory()
    gid = kodiutils.param('id')
    try:
        data = cache.cached('genre_{0}'.format(gid), 300, lambda: c.genre(gid))
    except Exception as exc:  # noqa
        kodiutils.notify('Could not load category: {0}'.format(exc))
        return kodiutils.end_directory()
    for r in data['rows']:
        if r.get('id'):
            kodiutils.add_directory(r['title'], kodiutils.url_for(action='rowlist', list=r['id']))
    kodiutils.end_directory(content='files')


def rowlist():
    c = _client()
    if not c.ready():
        return kodiutils.end_directory()
    lid = kodiutils.param('list')
    try:
        vids = cache.cached('list_{0}'.format(lid[-16:]), 300, lambda: c.row_by_list(lid, n=40))
    except Exception as exc:  # noqa
        kodiutils.notify('Could not load list: {0}'.format(exc))
        return kodiutils.end_directory()
    for v in vids:
        _add_item(v)
    kodiutils.end_directory(content='movies')


def signin():
    player.launch_signin()
    kodiutils.end_directory()


def signout():
    cache.clear()
    kodiutils.notify('To sign out, use the account menu inside the Netflix window')
    kodiutils.end_directory()


def play():
    player.play(kodiutils.param('id'))


# --- kept so existing router entries resolve (folded into the daemon browse) ---
def video_list():
    home()


def my_list():
    c = _client()
    if not c.ensure():
        return kodiutils.end_directory()
    try:
        items = cache.cached('mylist', 120, c.my_list)
    except Exception as exc:  # noqa
        kodiutils.notify('Could not load My List: {0}'.format(exc))
        return kodiutils.end_directory()
    for v in items:
        _add_item(v)
    kodiutils.end_directory(content='movies')


def search():
    c = _client()
    if not c.ensure():
        return kodiutils.end_directory()
    q = kodiutils.prompt()
    if not q:
        return kodiutils.end_directory()
    try:
        results = c.search(q)
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
        return kodiutils.end_directory()
    sid = kodiutils.param('id')
    try:
        seasons = cache.cached('eps_{0}'.format(sid), 600, lambda: c.episodes(sid))
    except Exception as exc:  # noqa
        kodiutils.notify('Could not load episodes: {0}'.format(exc))
        return kodiutils.end_directory()
    if not seasons:
        return player.play(sid)             # movie / no episode list -> play
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
