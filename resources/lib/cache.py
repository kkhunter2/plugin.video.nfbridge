# -*- coding: utf-8 -*-
"""Tiny file cache so repeated Kodi navigations don't re-query the daemon.

Each plugin invocation is a fresh process, so the cache is on disk (addon profile).
Keys are short slugs; values are JSON-serialisable. Cleared on profile switch / sign-in / sign-out.

Playback does NOT clear it: rebuilding a list means re-navigating the daemon's page
(seconds per list), while watching mostly changes resume / watched marks. Playback stops
are recorded by mark_watched(); an entry whose marks were read before that is passed to
the caller's `refresh` (e.g. NetflixClient.refresh_marks), which re-reads them in place and
returns True (store it), REBUILD (rebuild the entry) or anything else (serve it as is);
refresh=REBUILD always rebuilds such an entry.
Each entry is stored as {'t': fetched, 'm': marks read, 'v': value}; the TTL runs from 't'.
"""
import os
import re
import json
import time

from resources.lib import kodiutils

_WATCHED = 'watched.stamp'
REBUILD = 'rebuild'


def _safe(key):
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(key))[:80]


def _dir():
    d = os.path.join(kodiutils.profile_dir(), 'httpcache')
    if not os.path.isdir(d):
        os.makedirs(d)
    return d


def _path(key):
    return os.path.join(_dir(), _safe(key) + '.json')


def _load(path):
    try:
        with open(path, encoding='utf-8') as fh:
            e = json.load(fh)
    except Exception:
        return None
    # anything else (e.g. a pre-envelope entry) is a miss
    return e if isinstance(e, dict) and 't' in e and 'm' in e and 'v' in e else None


def _store(path, entry):
    # write + rename, so a list rendering at the same time never reads half a file
    tmp = '{0}.{1}.tmp'.format(path, os.getpid())
    try:
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(entry, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _watched_at():
    try:
        return os.path.getmtime(os.path.join(_dir(), _WATCHED))
    except OSError:
        return 0


def mark_watched(*ids):
    """Something was watched: marks read before now are stale. ids: what was played
    (routes.play); without ids (the service, on stop) the recorded ones are kept."""
    path = os.path.join(_dir(), _WATCHED)
    try:
        if ids or not os.path.exists(path):
            with open(path, 'w') as fh:
                fh.write(' '.join(i for i in ids if i))
        else:
            os.utime(path)
    except Exception:
        pass


def last_watched():
    """The ids routes.play recorded for the last playback ([] if unknown)."""
    try:
        with open(os.path.join(_dir(), _WATCHED)) as fh:
            return fh.read().split()
    except Exception:
        return []


def _cached(key, ttl, fn, keep, refresh):
    path = _path(key)
    e = _load(path)
    if e and time.time() - e['t'] >= ttl:
        e = None
    if e:
        if e['m'] >= _watched_at():
            return e['v']
        # marks read before the last playback: re-read them in place, stored once per playback
        res = refresh
        if refresh is not None and refresh != REBUILD:
            now = time.time()
            try:
                res = refresh(e['v'])
            except Exception as exc:  # noqa
                kodiutils.log('cache refresh {0} failed: {1}'.format(key, exc))
                res = None
            if res is True:
                e['m'] = now
                _store(path, e)
        if res != REBUILD:
            return e['v']
    start = time.time()        # marks are as of the fetch START (it may straddle a stop)
    try:
        value = fn()
    except Exception as exc:  # noqa
        if not e:
            raise
        # a rebuild that failed (e.g. the page is still reloading after a stop): the
        # cached list is still better than an error
        kodiutils.log('cache {0}: rebuild failed, serving the cached one: {1}'.format(key, exc))
        return e['v']
    try:
        ok = keep(value)
    except Exception:
        ok = False
    if ok:
        _store(path, {'t': start, 'm': start, 'v': value})
    return value


def cached(key, ttl, fn, refresh=None):
    """Return the cached value for key if younger than ttl seconds, else call fn(),
    store and return it."""
    return _cached(key, ttl, fn, lambda v: True, refresh)


def cached_ok(key, ttl, fn, refresh=None):
    """Like cached(), but does NOT store an empty/falsey result, so a transient failure
    (e.g. episodes that didn't load) isn't cached and the next call retries."""
    return _cached(key, ttl, fn, bool, refresh)


def cached_when(key, ttl, fn, keep, refresh=None):
    """Like cached(), but only stores the fresh value when keep(value) is True.
    Used to avoid caching a 'cold' snapshot (e.g. a row whose box art hasn't been
    rendered yet right after a daemon restart), so the next visit retries."""
    return _cached(key, ttl, fn, keep, refresh)


def drop(key):
    try:
        os.remove(_path(key))
    except Exception:
        pass


def clear():
    """Forget every saved list: ours, and the copies Kodi kept of this add-on's lists."""
    import shutil
    try:
        shutil.rmtree(_dir())
    except Exception:
        pass
    kodiutils.drop_kodi_listings()
