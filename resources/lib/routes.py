# -*- coding: utf-8 -*-
"""Route handlers: render the Netflix browse (via the WebView-fetch daemon /
nfclient) as Kodi directories, and Play by handing the title to the daemon's
window (Layer 2)."""
from resources.lib import kodiutils, player


def _client():
    return player.browse_client()


# ---------------------------------------------------------------- routes ------
def root():
    home()


def home():
    c = _client()
    if not c.ensure():
        kodiutils.add_directory('[B]Sign in to Netflix[/B]', kodiutils.url_for(action='signin'))
        kodiutils.notify('Sign in to Netflix to browse')
        return kodiutils.end_directory(content='files')
    try:
        data = c.home(n_rows=40)
    except Exception as exc:  # noqa
        kodiutils.notify('Could not load home: {0}'.format(exc))
        return kodiutils.end_directory(content='files')
    kodiutils.add_directory('[B]Search[/B]', kodiutils.url_for(action='search'))
    kodiutils.add_directory('[B]Profiles[/B]', kodiutils.url_for(action='profiles'))
    for r in data['rows']:
        kodiutils.add_directory(
            r['title'],
            kodiutils.url_for(action='row', root=data['root'], index=str(r['index'])))
    kodiutils.add_directory('Sign out', kodiutils.url_for(action='signout'))
    kodiutils.end_directory(content='files')


def row():
    c = _client()
    if not c.ready():
        kodiutils.notify('Netflix browse not ready')
        return kodiutils.end_directory()
    root_id = kodiutils.param('root')
    index = int(kodiutils.param('index') or '0')
    try:
        vids = c.row_videos(root_id, index, n=40)
    except Exception as exc:  # noqa
        kodiutils.notify('Could not load row: {0}'.format(exc))
        return kodiutils.end_directory()
    for v in vids:
        if not v.get('id'):
            continue
        mediatype = 'tvshow' if v.get('type') == 'show' else 'movie'
        kodiutils.add_directory(
            v['title'] or v['id'],
            kodiutils.url_for(action='play', id=v['id']),
            art={'thumb': v.get('art'), 'poster': v.get('art')},
            info={'title': v['title'], 'mediatype': mediatype},
            is_folder=False, playable=True)
    kodiutils.end_directory(content='movies')


def profiles():
    c = _client()
    if not c.ready():
        return kodiutils.end_directory()
    try:
        for p in c.profiles():
            # profile switching in the daemon is a TODO; for now just enter home
            kodiutils.add_directory(p['name'], kodiutils.url_for(action='home'),
                                    art={'icon': p.get('avatar')})
    except Exception as exc:  # noqa
        kodiutils.notify(str(exc))
    kodiutils.end_directory(content='files')


def signin():
    player.launch_signin()
    kodiutils.end_directory()


def signout():
    kodiutils.notify('To sign out, use the account menu inside the Netflix window')
    kodiutils.end_directory()


def play():
    player.play(kodiutils.param('id'))


# --- kept so existing router entries resolve (folded into the daemon browse) ---
def video_list():
    home()


def my_list():
    kodiutils.notify('My List shows as a row on the home screen')
    home()


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
        if not v.get('id'):
            continue
        mediatype = 'tvshow' if v.get('type') == 'show' else 'movie'
        kodiutils.add_directory(
            v['title'] or v['id'],
            kodiutils.url_for(action='play', id=v['id']),
            art={'thumb': v.get('art'), 'poster': v.get('art')},
            info={'title': v['title'], 'mediatype': mediatype},
            is_folder=False, playable=True)
    kodiutils.end_directory(content='movies')


def show():
    # Episodes need the GraphQL path; for now Netflix /watch/<id> resumes the show.
    play()
