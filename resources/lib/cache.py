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

Stale-while-revalidate (defer(True), set by the router for list routes): an entry that is
expired, or whose marks predate the last playback, is returned AT ONCE and the list is
queued (queue()); the service brings it up to date in the background (take_queue()), so the
next visit shows the new version. Kodi re-runs the add-on on every Back: without this each
Back waited for the daemon (4-10 s each after a playback or once the lists expired
overnight, measured 2026-11-15), and Back presses stacked up meanwhile are acted on by
Kodi as a cancel just as the add-on finishes (seen in its debug log); a Kodi crash on
2026-11-15 coincided with the end of such a 5 s rebuild. Stale marks are still re-read
at once when the daemon answers ready (defer(marks_now=)). The add-on itself never redraws
a list from the background: a Container.Refresh that lands while Kodi loads another list
starts a second, overlapping load (seen live); only the service redraws, and only the list
that was on screen at a playback stop, when Kodi is idle. Past MAX_STALE an expired entry
is rebuilt in the foreground again (a background update that keeps failing).
"""
import os
import re
import json
import time

from resources.lib import kodiutils

_WATCHED = 'watched.stamp'
_QUEUE = 'revalidate.queue'
REBUILD = 'rebuild'

MAX_STALE = 7 * 86400   # s past which an expired entry is no longer served as is
_FG = 'foreground.stamp'

_DEFER = [False]
_STALE = [False]        # this invocation served an entry that needs bringing up to date
_MARKS_NOW = [None]     # deferred mode: callable -> True if stale marks may be re-read now


def defer(on=True, marks_now=None):
    """marks_now: callable, True when the daemon can re-read marks at once (one quick
    probe, no waiting); stale marks are then re-read in the foreground as before, so the
    lists show the new resume / watched marks right after a playback."""
    _DEFER[0] = bool(on)
    _STALE[0] = False
    memo = []

    def once():
        if not memo:
            try:
                memo.append(bool(marks_now and marks_now()))
            except Exception:
                memo.append(False)
        return memo[0]
    _MARKS_NOW[0] = once


def served_stale():
    return _STALE[0]


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
        # Windows refuses to replace a file another process (the add-on vs the service)
        # has open for reading at this instant: retry briefly
        for i in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if i == 4:
                    raise
                time.sleep(0.05)
    except Exception as exc:  # noqa
        kodiutils.log('cache: could not store {0}: {1}'.format(os.path.basename(path), exc))
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
    # past MAX_STALE the deferred path is skipped: rebuilt now, as without defer
    if e and _DEFER[0] and time.time() - e['t'] < MAX_STALE:
        expired = time.time() - e['t'] >= ttl
        stale_marks = refresh is not None and e['m'] < _watched_at()
        # an expired list is rebuilt in the background; stale marks are re-read now only
        # if the daemon can do it at once
        if expired or (stale_marks and not _MARKS_NOW[0]()):
            _STALE[0] = True
            return e['v']
    old = e if _DEFER[0] else None     # past MAX_STALE: still the fallback if a rebuild fails
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
        if _DEFER[0]:               # a rebuild navigates the daemon: background it too
            _STALE[0] = True
            return e['v']
    start = time.time()        # marks are as of the fetch START (it may straddle a stop)
    try:
        value = fn()
    except Exception as exc:  # noqa
        e = e or old
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


def queue(params, redraw=False):
    """Ask the service to bring the list behind these plugin params up to date. redraw:
    redraw it afterwards if it is still on screen (only the service's own post-stop case)."""
    item = dict(params)
    if redraw:
        item['_redraw'] = '1'
    path = os.path.join(_dir(), _QUEUE)
    try:
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + '\n')
    except Exception as exc:  # noqa
        kodiutils.log('cache: could not queue {0}: {1}'.format(params, exc))


def touch_foreground():
    """The add-on is running a list for the user now (the service's worker waits)."""
    try:
        with open(os.path.join(_dir(), _FG), 'w'):
            pass
    except Exception:
        pass


def foreground_age():
    try:
        return time.time() - os.path.getmtime(os.path.join(_dir(), _FG))
    except OSError:
        return 1e9


def queued():
    return os.path.exists(os.path.join(_dir(), _QUEUE))


def take_queue():
    """The queued lists (oldest first, each once), removed from the queue."""
    path = os.path.join(_dir(), _QUEUE)
    taken = '{0}.{1}.taken'.format(path, os.getpid())
    try:
        os.replace(path, taken)
    except OSError:
        return []
    out = []
    try:
        with open(taken, encoding='utf-8') as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                plain = dict((k, v) for k, v in item.items() if k != '_redraw')
                same = next((o for o in out if dict((k, v) for k, v in o.items()
                                                    if k != '_redraw') == plain), None)
                if same is None:
                    out.append(item)
                elif item.get('_redraw'):
                    same['_redraw'] = '1'
    finally:
        try:
            os.remove(taken)
        except OSError:
            pass
    return out


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
