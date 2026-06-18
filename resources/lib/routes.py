# -*- coding: utf-8 -*-
"""Route handlers: turn Netflix API results into Kodi directories, and Play into
an external launch (Layer 2)."""
from resources.lib import kodiutils, player
from resources.lib.netflixapi import Session, NetflixError


# --------------------------------------------------------------- session ------
_SESSION = None


def _session():
    """Return a bootstrapped Netflix Session.

    Auth wiring lives here: load the stored web session (cookies + build id +
    authURL + active profile guid) — the same material the upstream addon already
    persists after login. Kept behind one function so the rest of the addon is
    auth-agnostic and unit-testable.
    """
    global _SESSION
    if _SESSION is None:
        s = Session()
        store = _load_auth()
        if store:
            s.bootstrap(**store)
        _SESSION = s
    return _SESSION


def _load_auth():
    """Map the captured WebView session -> netflixapi.Session.bootstrap kwargs."""
    from resources.lib import session
    s = session.load()
    if not s:
        return None
    return {'cookies': s.get('cookies', []), 'build': s.get('build'),
            'auth_url': s.get('authURL'), 'profile_guid': s.get('guid')}


def _require_session():
    s = _session()
    if not s.build or not s.auth_url:
        kodiutils.notify('Sign in to Netflix to browse (auth not configured yet)')
        return None
    return s


# ---------------------------------------------------------------- routes ------
def root():
    from resources.lib import session
    if not session.is_signed_in():
        kodiutils.add_directory('[B]Sign in to Netflix[/B]', kodiutils.url_for(action='signin'))
        return kodiutils.end_directory(content='files')
    if kodiutils.param('profile'):
        home()
    else:
        profiles()


def signin():
    from resources.lib import session, player
    player.launch_signin(session.path())
    kodiutils.notify('Sign in to Netflix in the player window, then reopen the addon')
    kodiutils.end_directory()


def signout():
    from resources.lib import session
    session.clear()
    kodiutils.notify('Signed out')
    kodiutils.end_directory()


def profiles():
    s = _require_session()
    if not s:
        return kodiutils.end_directory()
    try:
        for p in s.profiles():
            kodiutils.add_directory(
                p['name'], kodiutils.url_for(action='home', profile=p['guid']),
                art={'icon': p.get('avatar'), 'thumb': p.get('avatar')})
    except NetflixError as exc:
        kodiutils.notify(str(exc))
    kodiutils.end_directory(content='files')


def home():
    s = _require_session()
    if not s:
        return kodiutils.end_directory()
    # fixed entries first
    kodiutils.add_directory('[B]My List[/B]', kodiutils.url_for(action='mylist'))
    kodiutils.add_directory('[B]Search[/B]', kodiutils.url_for(action='search'))
    kodiutils.add_directory('[B]Genres[/B]', kodiutils.url_for(action='home', genres='1'))
    kodiutils.add_directory('Sign out', kodiutils.url_for(action='signout'))
    try:
        rows = s.genres_all() if kodiutils.param('genres') else s.home()
        for row in rows:
            kodiutils.add_directory(
                row['title'], kodiutils.url_for(action='list', id=row['id']))
    except NetflixError as exc:
        kodiutils.notify(str(exc))
    kodiutils.end_directory(content='files')


def video_list():
    s = _require_session()
    if not s:
        return kodiutils.end_directory()
    try:
        for v in s.video_list(kodiutils.param('id')):
            _add_video(v)
    except NetflixError as exc:
        kodiutils.notify(str(exc))
    kodiutils.end_directory()


def my_list():
    s = _require_session()
    if not s:
        return kodiutils.end_directory()
    try:
        for v in s.my_list():
            _add_video(v)
    except NetflixError as exc:
        kodiutils.notify(str(exc))
    kodiutils.end_directory()


def search():
    s = _require_session()
    if not s:
        return kodiutils.end_directory()
    q = kodiutils.prompt()
    if not q:
        return kodiutils.end_directory()
    try:
        data = s.search(q)
        for v in _videos_from_graphql(data):
            _add_video(v)
    except NetflixError as exc:
        kodiutils.notify(str(exc))
    kodiutils.end_directory()


def show():
    s = _require_session()
    if not s:
        return kodiutils.end_directory()
    try:
        data = s.show(kodiutils.param('id'))
        for ep in _episodes_from_graphql(data):
            _add_video(ep, mediatype='episode')
    except NetflixError as exc:
        kodiutils.notify(str(exc))
    kodiutils.end_directory(content='episodes')


def play():
    player.play(kodiutils.param('id'))


# --------------------------------------------------------------- helpers ------
def _add_video(v, mediatype=None):
    is_show = (v.get('type') in ('show', 'series'))
    if is_show:
        url = kodiutils.url_for(action='show', id=v['id'])
        kodiutils.add_directory(v['title'], url,
                                art={'thumb': v.get('boxart'), 'poster': v.get('boxart')},
                                info={'title': v['title'], 'mediatype': 'tvshow'},
                                is_folder=True)
    else:
        url = kodiutils.url_for(action='play', id=v['id'])
        kodiutils.add_directory(v['title'], url,
                                art={'thumb': v.get('boxart'), 'poster': v.get('boxart')},
                                info={'title': v['title'],
                                      'mediatype': mediatype or 'movie'},
                                is_folder=False, playable=True)


def _videos_from_graphql(data):
    # shape-tolerant: walk SearchPage -> results edges -> video nodes
    out = []
    try:
        edges = data.get('searchPage', {}).get('results', {}).get('edges', [])
        for e in edges:
            n = e.get('node', {}).get('video', e.get('node', {}))
            out.append({'id': n.get('videoId') or n.get('id'),
                        'title': n.get('title'),
                        'type': n.get('__typename', '').lower(),
                        'boxart': _gql_art(n)})
    except Exception:  # noqa
        pass
    return [v for v in out if v.get('id')]


def _episodes_from_graphql(data):
    out = []
    try:
        seasons = (data.get('videos', {}) or data).get('seasons', {}).get('edges', [])
        for se in seasons:
            for ee in se.get('node', {}).get('episodes', {}).get('edges', []):
                n = ee.get('node', {})
                out.append({'id': n.get('videoId') or n.get('id'),
                            'title': n.get('title'),
                            'type': 'episode', 'boxart': _gql_art(n)})
    except Exception:  # noqa
        pass
    return [v for v in out if v.get('id')]


def _gql_art(node):
    art = node.get('artwork') or node.get('boxart') or {}
    if isinstance(art, dict):
        return art.get('url')
    return None
