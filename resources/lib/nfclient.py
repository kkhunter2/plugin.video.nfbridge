# -*- coding: utf-8 -*-
"""Client for the WebView-fetch daemon (runtime/webview, NF_RPC_PORT).

Browse data is fetched THROUGH Netflix's own in-page falcor client
(netflix.appContext.state.pathEvaluator.get), proxied over localhost HTTP by the
daemon — so there is no request format/header reverse-engineering and it stays
working across Netflix changes. Pure stdlib (no xbmc) so it is unit-testable.
"""
import json
import time
import socket

try:
    from urllib.request import urlopen, Request
except ImportError:  # py2 (Kodi 18)
    from urllib2 import urlopen, Request

try:
    from html import unescape as _html_unescape
except ImportError:  # py2
    try:
        from HTMLParser import HTMLParser
        _html_unescape = HTMLParser().unescape
    except Exception:
        def _html_unescape(s):
            return s

DEFAULT_PORT = 8731
BOXART = ['boxarts', '_342x192', 'webp', 'url']


class NetflixError(Exception):
    pass


def _txt(s):
    """Netflix titles/profile names are HTML-escaped (&#xBBF8;) -> unescape."""
    return _html_unescape(s) if isinstance(s, str) else s


def _digit_items(node):
    """Yield (int_index, value) for the numeric keys of a falcor node."""
    for k, v in (node or {}).items():
        if isinstance(k, str) and k.isdigit() and isinstance(v, dict):
            yield int(k), v


class NetflixClient:
    def __init__(self, port=DEFAULT_PORT, launcher=None, ready_timeout=60):
        self.port = port
        self.base = 'http://127.0.0.1:%d' % port
        self.launcher = launcher
        self.ready_timeout = ready_timeout

    # ---- transport ----
    def _get(self, path, timeout=20):
        return json.loads(urlopen(self.base + path, timeout=timeout).read().decode('utf-8'))

    def _post(self, path, obj, timeout=35):
        req = Request(self.base + path, data=json.dumps(obj).encode('utf-8'),
                      headers={'Content-Type': 'application/json'})
        return json.loads(urlopen(req, timeout=timeout).read().decode('utf-8'))

    def ready(self):
        """Daemon up AND the Netflix page signed-in / data client live."""
        try:
            return bool(self._get('/health', timeout=3).get('ready'))
        except Exception:
            return False

    def running(self):
        """Daemon process up (HTTP port open), regardless of sign-in state."""
        s = socket.socket()
        s.settimeout(1)
        try:
            s.connect(('127.0.0.1', self.port))
            return True
        except Exception:
            return False
        finally:
            s.close()

    def ensure(self):
        """Make sure the daemon is up and signed in. Launches it (once) if the
        process isn't running, then waits for the page to be ready."""
        if self.ready():
            return True
        if not self.running() and self.launcher:
            self.launcher()
        for _ in range(self.ready_timeout):
            if self.ready():
                return True
            time.sleep(1)
        return False

    def context(self):
        return self._get('/context')

    def falcor(self, paths, timeout=20):
        r = self._post('/falcor', {'paths': paths, 'timeout': timeout})
        if r.get('err'):
            raise NetflixError(r['err'])
        return r.get('json') or {}

    # ---- browse ----
    def profiles(self):
        j = self.falcor([['profilesList', {'from': 0, 'to': 20}, ['summary']]])
        out = []
        for _, v in _digit_items(j.get('profilesList', {})):
            s = v.get('summary')
            if s:
                out.append({'guid': s.get('guid'), 'name': _txt(s.get('profileName')),
                            'kids': s.get('isKids'), 'avatar': s.get('avatarName')})
        return out

    def home(self, n_rows=40):
        ctx = self.context()
        root = ctx.get('locoRoot')
        if not root:
            raise NetflixError('no locoRoot in context: %s' % ctx)
        j = self.falcor([['locos', root, {'from': 0, 'to': n_rows}, 'componentSummary']])
        node = j.get('locos', {}).get(root, {})
        rows = []
        for idx, v in _digit_items(node):
            cs = v.get('componentSummary')
            if cs and cs.get('displayName'):
                rows.append({'index': idx, 'title': _txt(cs.get('displayName')),
                             'id': cs.get('id'), 'type': cs.get('type'),
                             'context': cs.get('context')})
        rows.sort(key=lambda r: r['index'])
        return {'root': root, 'rows': rows}

    def row_videos(self, root, row_index, n=30):
        # step 1: resolve the row's video refs (id + type)
        rng = {'from': 0, 'to': n}
        j = self.falcor([['locos', root, row_index, rng, 'reference', 'summary']])
        node = j.get('locos', {}).get(root, {}).get(str(row_index), {})
        ids, meta = [], {}
        for _, cell in _digit_items(node):
            path = (cell.get('reference') or {}).get('$__path') or []
            vid = str(path[1]) if len(path) > 1 else None
            if vid:
                ids.append(vid)
                meta[vid] = (cell['reference'].get('summary') or {})
        if not ids:
            return []
        # step 2: titles in one batch (reliable via falcor). NOTE: box art is NOT
        # fetchable via falcor for non-prefetched videos (server-fetch is rejected) —
        # Netflix serves art through GraphQL (the MiniModalQuery op). TODO: art via the
        # page's graphqlClient. For now title + type + id drive browse/playback.
        vj = self.falcor([['videos', vid, 'title'] for vid in ids]).get('videos', {})
        out = []
        for vid in ids:
            out.append({'id': vid, 'title': _txt(vj.get(vid, {}).get('title')),
                        'type': meta[vid].get('type'), 'art': None,
                        'isOriginal': meta[vid].get('isOriginal')})
        return out
