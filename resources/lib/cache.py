# -*- coding: utf-8 -*-
"""Tiny file cache so repeated Kodi navigations don't re-query the daemon.

Each plugin invocation is a fresh process, so the cache is on disk (addon profile).
Keys are short slugs; values are JSON-serialisable. Cleared on profile switch / sign-out.
"""
import os
import re
import json
import time

from resources.lib import kodiutils


def _safe(key):
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(key))[:80]


def _dir():
    d = os.path.join(kodiutils.profile_dir(), 'httpcache')
    if not os.path.isdir(d):
        os.makedirs(d)
    return d


def cached(key, ttl, fn):
    """Return the cached value for key if younger than ttl seconds, else call fn(),
    store and return it."""
    path = os.path.join(_dir(), _safe(key) + '.json')
    try:
        if os.path.isfile(path) and (time.time() - os.path.getmtime(path)) < ttl:
            with open(path, encoding='utf-8') as fh:
                return json.load(fh)
    except Exception:
        pass
    value = fn()
    try:
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(value, fh, ensure_ascii=False)
    except Exception:
        pass
    return value


def cached_ok(key, ttl, fn):
    """Like cached(), but does NOT store an empty/falsey result, so a transient failure
    (e.g. episodes that didn't load) isn't cached and the next call retries."""
    path = os.path.join(_dir(), _safe(key) + '.json')
    try:
        if os.path.isfile(path) and (time.time() - os.path.getmtime(path)) < ttl:
            with open(path, encoding='utf-8') as fh:
                return json.load(fh)
    except Exception:
        pass
    value = fn()
    if value:
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(value, fh, ensure_ascii=False)
        except Exception:
            pass
    return value


def cached_when(key, ttl, fn, keep):
    """Like cached(), but only stores the fresh value when keep(value) is True.
    Used to avoid caching a 'cold' snapshot (e.g. a row whose box art hasn't been
    rendered yet right after a daemon restart), so the next visit retries."""
    path = os.path.join(_dir(), _safe(key) + '.json')
    try:
        if os.path.isfile(path) and (time.time() - os.path.getmtime(path)) < ttl:
            with open(path, encoding='utf-8') as fh:
                return json.load(fh)
    except Exception:
        pass
    value = fn()
    try:
        if keep(value):
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(value, fh, ensure_ascii=False)
    except Exception:
        pass
    return value


def drop(*keys):
    """Remove specific cache entries (e.g. after playback invalidates 'home')."""
    for key in keys:
        try:
            os.remove(os.path.join(_dir(), _safe(key) + '.json'))
        except Exception:
            pass


def drop_prefix(prefix):
    """Remove every cache entry whose key begins with prefix (e.g. all 'row_' rows)."""
    safe = _safe(prefix)
    try:
        d = _dir()
        for fn in os.listdir(d):
            if fn.startswith(safe):
                try:
                    os.remove(os.path.join(d, fn))
                except Exception:
                    pass
    except Exception:
        pass


def clear():
    import shutil
    try:
        shutil.rmtree(_dir())
    except Exception:
        pass
