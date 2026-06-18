# -*- coding: utf-8 -*-
"""Netflix browse session, captured from the embedded WebView2 player.

The WebView host (runtime/webview/webview_host.py) writes session.json after the
user signs in: { authURL, build, guid, cookies[], country, membership }. Layer 1
(netflixapi) uses it for falcor pathEvaluator + GraphQL calls — so the user signs
in once (in the player window) and both playback and browsing share that session.
"""
import os
import json

from resources.lib import kodiutils


def path():
    return os.path.join(kodiutils.profile_dir(), 'session.json')


def load():
    """Return the stored session dict, or None if not signed in / incomplete."""
    p = path()
    if not os.path.isfile(p):
        return None
    try:
        d = json.load(open(p, encoding='utf-8'))
        if d.get('authURL') and d.get('build'):
            return d
    except Exception as exc:  # noqa
        kodiutils.log('session load failed: {0}'.format(exc))
    return None


def is_signed_in():
    return load() is not None


def clear():
    try:
        os.remove(path())
    except OSError:
        pass
