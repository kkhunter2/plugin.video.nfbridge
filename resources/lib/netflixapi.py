# -*- coding: utf-8 -*-
"""Layer 1 — Netflix browse API client (read-only catalog/metadata).

This reuses the endpoint knowledge proven while fixing the "404 navigation"
breakage (June 2026):

  * Falcor pathEvaluator (home/genres/My List): the legacy `reference` indirection
    now 404s; fetch leaf fields directly. LoCo is served via
    ['loco', range, 'componentSummary']; My List via ['mylist','az', range, LEAFS].
  * Seasons / episodes / search moved to the GraphQL gateway
    web.prod.cloud.netflix.com/graphql  (persistedQuery version 102).

NOTE ON SESSION BOOTSTRAP
-------------------------
A live session needs: NetflixId/SecureNetflixId cookies, the shakti `authURL`,
the website build id, and the active profile guid. That bootstrap (login form ->
cookies, react-context scrape for authURL/buildId) is identical to the upstream
addon's proven flow and is intentionally factored behind `Session.bootstrap()`.
This module focuses on the *request shapes*; wire `bootstrap()` to your auth.
"""
import json

try:
    import requests
except ImportError:  # Kodi provides script.module.requests at runtime
    requests = None

from resources.lib import graphql_ops

# ---- endpoints ----
WEBSITE = 'https://www.netflix.com'
PATH_EVALUATOR = WEBSITE + '/nq/website/memberapi/{build}/pathEvaluator'
GRAPHQL = 'https://web.prod.cloud.netflix.com/graphql'
GRAPHQL_PQ_VERSION = graphql_ops.GRAPHQL_VERSION  # persistedQuery version

BROWSER_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36'),
    'Accept': 'application/json, text/javascript, */*',
    'Content-Type': 'application/json',
    'X-Netflix.Request.Client.User.Guid': '',  # filled with active profile guid
}

# Leaf fields that materialise without the dead `reference` indirection.
# (Excludes fields that now 404: watched, requestId, info, evidence,
#  regularSynopsis, dpSupplementalMessage, sequiturEvidence, promoVideo,
#  interactiveBookmark.)
SAFE_LEAFS = ['summary', 'title', 'itemSummary', 'boxarts']
MYLIST_LEAFS = ['summary', 'title', 'itemSummary',
                ['boxarts', '_342x192', 'webp']]
LOCO_ALL_ROWS = list(range(50))   # genre rows spread across ~20+ rows; range() 404s, key-set works


class NetflixError(Exception):
    pass


class Session(object):
    def __init__(self):
        self.http = requests.Session() if requests else None
        self.build = None          # website build id, e.g. "vXXXXXXXX"
        self.auth_url = None        # shakti authURL token
        self.profile_guid = None

    # -- bootstrap (wire to your auth/cookie layer) --
    def bootstrap(self, cookies=None, build=None, auth_url=None, profile_guid=None):
        """Attach an existing session. `cookies` = list of {name,value,domain,...}."""
        if cookies and self.http:
            for c in cookies:
                self.http.cookies.set(c['name'], c['value'],
                                      domain=c.get('domain', '.netflix.com'),
                                      path=c.get('path', '/'))
        self.build = build
        self.auth_url = auth_url
        self.profile_guid = profile_guid

    # -- low-level falcor --
    def _path_eval(self, paths):
        if not self.http:
            raise NetflixError('requests unavailable')
        headers = dict(BROWSER_HEADERS)
        headers['X-Netflix.Request.Client.User.Guid'] = self.profile_guid or ''
        body = {'paths': paths, 'authURL': self.auth_url}
        url = PATH_EVALUATOR.format(build=self.build)
        r = self.http.post(url, headers=headers, data=json.dumps(body), timeout=20)
        if r.status_code == 404:
            raise NetflixError('pathEvaluator 404 (stale build id or dead path)')
        r.raise_for_status()
        data = r.json()
        if 'error' in data:
            raise NetflixError('auth/path error: %s' % data.get('error'))
        return data.get('jsonGraph', {})

    # -- low-level graphql (persisted query) --
    def _graphql(self, operation_name, variables, sha256=None):
        if not self.http:
            raise NetflixError('requests unavailable')
        sha256 = sha256 or graphql_ops.sha256_for(operation_name)
        if not sha256:
            raise NetflixError('PersistedQueryNotFound: no sha256 for %s '
                               '(populate resources/lib/graphql_ops.py)' % operation_name)
        ext = {'persistedQuery': {'version': GRAPHQL_PQ_VERSION, 'sha256Hash': sha256}}
        body = {'operationName': operation_name, 'variables': variables, 'extensions': ext}
        r = self.http.post(GRAPHQL, headers=BROWSER_HEADERS, data=json.dumps(body), timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get('errors'):
            # surface PersistedQueryNotFound clearly (op-id/version drift)
            raise NetflixError('graphql error: %s' % json.dumps(data['errors'])[:200])
        return data.get('data', {})

    # ---------------------------------------------------------- browse API ----
    def profiles(self):
        graph = self._path_eval([['profilesList', 'summary'],
                                 ['profilesList', {'from': 0, 'to': 40}, ['summary', 'avatar']]])
        return _extract_profiles(graph)

    def home(self):
        """Home = LoCo carousels (componentSummary, no NES root id needed)."""
        graph = self._path_eval([['loco', {'from': 0, 'to': 40}, 'componentSummary']])
        return _extract_loco(graph)

    def video_list(self, list_id):
        graph = self._path_eval([
            ['locos', list_id, {'from': 0, 'to': 40}, SAFE_LEAFS],
        ])
        return _extract_videos(graph)

    def my_list(self):
        graph = self._path_eval([
            ['mylist', 'az', {'from': 0, 'to': 48}, MYLIST_LEAFS],
        ])
        return _extract_videos(graph)

    def genres_all(self):
        """Full genre listing: rows spread across many LoCo rows -> key-set, not range()."""
        graph = self._path_eval([['loco', LOCO_ALL_ROWS, 'componentSummary']])
        return _extract_loco(graph)

    def search(self, query):
        return self._graphql('SearchPage', {'query': query, 'first': 40})

    def show(self, video_id):
        """Seasons/episodes via GraphQL gateway (v102)."""
        return self._graphql('DetailModalEpisodeSelector',
                              {'videoId': str(video_id), 'first': 50})


# ------------------------------------------------------------- extractors -----
# (Defensive parsers; Netflix jsonGraph is deeply nested + sentinel-laden.)
def _deref(node):
    while isinstance(node, dict) and node.get('$type') in ('atom', 'leaf'):
        return node.get('value')
    return node


def _extract_profiles(graph):
    out = []
    pl = graph.get('profiles', {})
    for guid, prof in pl.items():
        summ = _deref((prof or {}).get('summary', {}))
        if isinstance(summ, dict):
            out.append({'guid': guid,
                        'name': summ.get('profileName', guid),
                        'avatar': summ.get('avatarUrl')})
    return out


def _extract_loco(graph):
    rows = []
    loco = graph.get('loco', {})
    for key, row in loco.items():
        cs = _deref((row or {}).get('componentSummary', {}))
        if isinstance(cs, dict) and cs.get('context'):
            rows.append({'id': cs.get('id') or key,
                         'title': cs.get('title') or cs.get('context'),
                         'context': cs.get('context')})
    return rows


def _extract_videos(graph):
    items = []
    videos = graph.get('videos', {})
    for vid, v in videos.items():
        summ = _deref((v or {}).get('summary', {}))
        title = _deref((v or {}).get('title', {}))
        art = _deref((v or {}).get('itemSummary', {}))
        boxart = None
        if isinstance(art, dict):
            boxart = art.get('boxart', {}).get('url') if isinstance(art.get('boxart'), dict) else None
        items.append({
            'id': vid,
            'title': title if isinstance(title, str) else (summ or {}).get('title', vid),
            'type': (summ or {}).get('type', 'movie') if isinstance(summ, dict) else 'movie',
            'boxart': boxart,
        })
    return items
