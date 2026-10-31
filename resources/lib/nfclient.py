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
from html import unescape as _html_unescape
from urllib.request import urlopen, Request

DEFAULT_PORT = 8731
# a launched daemon opens its port within a few seconds; one that hasn't by then is
# launched again (player._ensure_daemon's launch guard has expired by this time)
RELAUNCH_AFTER = 16


class NetflixError(Exception):
    pass


class RowGone(NetflixError):
    pass


def _txt(s):
    """Netflix titles/profile names are HTML-escaped (&#xBBF8;) -> unescape."""
    return _html_unescape(s) if isinstance(s, str) else s


def _digit_items(node):
    """Yield (int_index, value) for the numeric keys of a falcor node."""
    for k, v in (node or {}).items():
        if isinstance(k, str) and k.isdigit() and isinstance(v, dict):
            yield int(k), v


def _syn(val):
    """The 'synopsis' leaf is either a plain string or {'text': ...}."""
    if isinstance(val, dict):
        val = val.get('text')
    return _txt(val) if isinstance(val, str) else None


def _boxart(node):
    """Pull a box-art URL out of a falcor ['videos',id,'boxarts','_342x192','webp'] node.
    Unlike the Apollo cache read (/art), this is a data fetch so it covers EVERY id, not
    only the tiles the browser has rendered."""
    try:
        return (((node or {}).get('boxarts') or {}).get('_342x192') or {}).get('webp', {}).get('url')
    except Exception:
        return None


class NetflixClient:
    def __init__(self, port=DEFAULT_PORT, launcher=None, ready_timeout=60, code_ver=None):
        self.port = port
        self.base = 'http://127.0.0.1:%d' % port
        self.launcher = launcher
        self.ready_timeout = ready_timeout
        self.code_ver = code_ver   # if the running daemon's /health ver differs -> restart it

    # ---- transport ----
    def _get(self, path, timeout=20):
        return json.loads(urlopen(self.base + path, timeout=timeout).read().decode('utf-8'))

    def _post(self, path, obj, timeout=35):
        req = Request(self.base + path, data=json.dumps(obj).encode('utf-8'),
                      headers={'Content-Type': 'application/json'})
        return json.loads(urlopen(req, timeout=timeout).read().decode('utf-8'))

    def health(self, timeout=5):
        """The daemon's /health dict ({ready, login, ver, playing}); {} if it is down or
        didn't answer in time (its readiness probe can stall while the page navigates)."""
        if not self.running():      # Windows takes ~2 s to refuse a closed localhost port
            return {}
        try:
            return self._get('/health', timeout=timeout) or {}
        except Exception:
            return {}

    def ready(self):
        """Daemon up AND the Netflix page signed-in / data client live (one quick retry,
        since the readiness probe is skipped while the page is busy)."""
        for _ in range(2):
            h = self.health()
            if h.get('ready'):
                return True
            if h.get('login') or not h:
                return False
            time.sleep(1)
        return False

    def running(self):
        """Daemon process up (HTTP port open), regardless of sign-in state."""
        s = socket.socket()
        # an open localhost port connects at once (the kernel accepts), but Windows retries
        # a refused one for ~2 s: a long timeout here is paid in full whenever it's down
        s.settimeout(0.25)
        try:
            s.connect(('127.0.0.1', self.port))
            return True
        except Exception:
            return False
        finally:
            s.close()

    def down(self):
        """Daemon certainly not running: two failed probes, since acting on 'down' (the
        router relaunching it and going Home) shouldn't hang on a single connect that was slow."""
        return not self.running() and not self.running()

    def ensure(self, stop=None):
        """Make sure the daemon is up, current and signed in.

        A detached daemon survives Kodi restarts, so if the running one is from older
        code / another subtitle font (ver mismatch) stop it and launch a fresh one.
        Launch it if not running (when a launcher is wired; otherwise fail at once),
        then wait until ready — giving up early if the page sits on the sign-in form.
        stop: callable; True ends the wait (e.g. Kodi is exiting)."""
        up = self.running()
        if up and self.code_ver:
            ver = self.health().get('ver')
            if ver is not None and ver != self.code_ver:
                try:
                    self._get('/quit', timeout=3)   # stale daemon -> stop it
                except Exception:
                    pass
                for _ in range(12):
                    if not self.running():
                        break
                    time.sleep(0.5)
                up = self.running()
        if not up:
            # launcher() is False when the launch failed outright (nothing to wait for)
            if not self.launcher or self.launcher() is False:
                return False
        # poll 4x a second: a 1 s step cost ~0.5 s on every daemon launch. Monotonic: this
        # runs at Kodi start (the prefetch), when Windows time sync may step the clock.
        login_ticks = 0
        start = time.monotonic()
        relaunched = False
        while time.monotonic() - start < self.ready_timeout:
            if stop and stop():
                return False
            if (not up and not relaunched and time.monotonic() - start > RELAUNCH_AFTER
                    and not self.running()):
                # the launch this caller deferred to (another caller's, see
                # player._ensure_daemon) never opened its port: it died or was quit
                relaunched = True
                if self.launcher() is False:
                    return False
            h = self.health()
            if h.get('ready'):
                return True
            if h.get('login'):
                login_ticks += 1
                if login_ticks >= 12:       # parked on the sign-in form (3 s): don't wait it out
                    return False
            time.sleep(0.25)
        return False

    def context(self):
        return self._get('/context')

    def sign_out(self):
        """Sign the daemon's Netflix session out; it then waits on the sign-in form.
        {'ok': True} once the page is on that form."""
        return self._post('/signout', {}, timeout=60) or {}

    def go_home(self):
        """Put the daemon back on /browse (after search / a title page / playback)."""
        try:
            return self._post('/home', {}, timeout=60) or {}
        except Exception:
            return {}

    def falcor(self, paths, timeout=20):
        r = self._post('/falcor', {'paths': paths, 'timeout': timeout})
        if r.get('err'):
            raise NetflixError(r['err'])
        return r.get('json') or {}

    def art(self, ids):
        """Box art for video ids, read from the page's Apollo cache (best-effort)."""
        if not ids:
            return {}
        try:
            return self._post('/art', {'ids': list(ids)}) or {}
        except Exception:
            return {}

    def _artmap(self, ids):
        """Box art for ids via falcor (a data fetch -> covers EVERY id, unlike the
        render-dependent Apollo /art), with /art as a fallback for any falcor missed."""
        if not ids:
            return {}
        try:
            aj = self.falcor([['videos', vid, 'boxarts', '_342x192', 'webp']
                              for vid in ids]).get('videos', {})
        except Exception:
            aj = {}
        out = {}
        for vid in ids:
            u = _boxart(aj.get(vid))
            if u:
                out[vid] = u
        missing = [vid for vid in ids if vid not in out]
        if missing:
            for k, v in (self.art(missing) or {}).items():
                if v:
                    out[k] = v
        return out

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

    def search(self, q):
        """Search results [{id,type,title,art}] from Netflix's Pinot search (Apollo).
        Navigates the daemon to the search page; call home() to return to browse."""
        if not q:
            return []
        try:
            return (self._post('/search', {'q': q}, timeout=45) or {}).get('results', [])
        except Exception as exc:  # noqa
            raise NetflixError(str(exc))

    def my_list(self):
        """The user's My List: every row of the /browse/my-list page, flattened."""
        data = self._post('/pinot', {'path': '/browse/my-list', 'rows': 20, 'videos': True},
                          timeout=60) or {}
        out, seen = [], set()
        for r in data.get('rows') or []:
            for v in r.get('videos') or []:
                if v['id'] not in seen:
                    seen.add(v['id'])
                    out.append(v)
        return self._hydrate(out)

    def episodes(self, video_id):
        """Seasons of a show: [{id,title,number,eps:[{id,title,number,art}]}], read from
        the title page's Apollo cache (filtered to this show). Empty for movies.
        Navigates the daemon (call home() to return to browse)."""
        if not video_id:
            return []
        try:
            return (self._post('/episodes', {'videoId': video_id}, timeout=45) or {}).get('seasons', [])
        except Exception as exc:  # noqa
            raise NetflixError(str(exc))

    def video_info(self, video_id):
        """One title's basics in a single falcor call: {id,title,type,art,synopsis,
        bookmark,runtime}. Used to tell a movie from a show when a row didn't say
        (e.g. Top 10 tiles), without navigating the daemon."""
        vid = str(video_id)
        j = self.falcor([['videos', vid, ['summary', 'title', 'synopsis', 'bookmarkPosition', 'runtime']],
                         ['videos', vid, 'boxarts', '_342x192', 'webp']])
        v = (j.get('videos') or {}).get(vid) or {}
        summ = v.get('summary') or {}
        return {'id': vid, 'title': _txt(v.get('title')) or _txt(summ.get('title')) or vid,
                'type': summ.get('type'), 'art': _boxart(v), 'synopsis': _syn(v.get('synopsis')),
                'bookmark': v.get('bookmarkPosition'), 'runtime': v.get('runtime'),
                'isOriginal': summ.get('isOriginal')}

    # ---- browse pages (Pinot; see webview_host._pinot_page) ----
    # A "root" is the page path: /browse (home), /browse/genre/<id>, /browse/my-list.
    def _page(self, path, **opt):
        req = dict(opt)
        req['path'] = path
        data = self._post('/pinot', req, timeout=90) or {}
        if not data.get('rows'):
            raise NetflixError(data.get('err') or 'page {0} did not load (sign in / select a profile)'.format(path))
        return data

    @staticmethod
    def _rows(data):
        # cw: the Continue Watching row (its membership / order follows what was watched)
        return [{'index': r['index'], 'title': _txt(r.get('title')) or '', 'id': r.get('id'),
                 'cw': bool(r.get('cw'))}
                for r in data['rows'] if r.get('title')]

    def home(self, n_rows=40, quiet=False):
        """quiet: a background prefetch (the daemon then leaves the foreground alone)."""
        data = self._page('/browse', rows=n_rows, **({'quiet': True} if quiet else {}))
        return {'root': '/browse', 'rows': self._rows(data)}

    def genre(self, gid, n_rows=40):
        """Rows of a genre/category page (e.g. 83=Series, 34399=Movies)."""
        path = '/browse/genre/{0}'.format(gid)
        data = self._page(path, rows=n_rows)
        return {'root': path, 'rows': self._rows(data)}

    def row_videos(self, root, row_index, n=40, title=None):
        """One row's videos (the daemon pages the carousel up to n), hydrated. Raises
        RowGone if the page no longer has a row with this title (the daemon then fell back
        to the index, which now holds a different row)."""
        data = self._page(root or '/browse', row=int(row_index), title=title or '', n=n)
        row = data.get('row') or {}
        if title and _txt(row.get('title')) != title:
            if data.get('complete'):          # the whole page is loaded and it isn't there
                raise RowGone(title)
            raise NetflixError('"{0}" did not load - try again'.format(title))
        return self._hydrate(list(row.get('videos') or []))

    def _hydrate(self, vids):
        """Fill what the Pinot cache lacks for a tile — synopsis (only visible tiles carry
        one), bookmark/runtime (absent on plain boxshots), art (rare) — with ONE batched
        falcor call for just the videos that need it. Best-effort: never breaks the list."""
        need = [v['id'] for v in vids
                if not v.get('synopsis') or v.get('runtime') is None or v.get('bookmark') is None]
        if need:
            try:
                fj = self.falcor([['videos', vid, ['synopsis', 'bookmarkPosition', 'runtime']]
                                  for vid in need]).get('videos', {})
            except Exception:
                fj = {}
            for v in vids:
                f = fj.get(v['id']) or {}
                if not v.get('synopsis'):
                    v['synopsis'] = _syn(f.get('synopsis'))
                if v.get('runtime') is None:
                    v['runtime'] = f.get('runtime')
                if v.get('bookmark') is None:
                    v['bookmark'] = f.get('bookmarkPosition')
        missing_art = [v['id'] for v in vids if not v.get('art')]
        if missing_art:
            art = self._artmap(missing_art)
            for v in vids:
                if not v.get('art'):
                    v['art'] = art.get(v['id'])
        for v in vids:
            v['title'] = _txt(v.get('title'))
        return vids

    def refresh_marks(self, vids, show=None):
        """Re-read the bookmark (resume point) of already-listed videos in place: ONE
        falcor call, no page navigation. With show (a show's episodes), also which one
        Netflix resumes next. True if it was read (False leaves them as they were)."""
        ids = [v['id'] for v in vids if v.get('id')]
        if not ids:
            return False
        fj = {}
        try:
            # short timeout: this only freshens marks, the list is shown either way;
            # chunked so a long show's episode list doesn't outgrow it
            for i in range(0, len(ids), 100):
                fj.update(self.falcor([['videos', vid, 'bookmarkPosition'] for vid in ids[i:i + 100]],
                                      timeout=5).get('videos', {}))
        except Exception:
            return False
        for v in vids:
            f = fj.get(v.get('id'))
            if isinstance(f, dict) and 'bookmarkPosition' in f:
                v['bookmark'] = f['bookmarkPosition']
        if show:
            try:
                cur = self.falcor([['videos', str(show), 'current', 'summary']], timeout=5)
                cur = str(((cur.get('videos') or {}).get(str(show)) or {})
                          .get('current', {}).get('summary', {}).get('id') or '')
            except Exception:
                cur = ''
            if cur:
                for v in vids:
                    v['current'] = v.get('id') == cur
        return True
