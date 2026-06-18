# -*- coding: utf-8 -*-
"""Netflix (Bridge) — Kodi plugin entry point.

Parses the invocation URL and dispatches to a route handler.
Layer 1 (navigation) builds Kodi directories; Layer 2 (player) delegates playback.
"""
import sys

from resources.lib import router


def main():
    # sys.argv = [base_url, handle, query_string]
    base_url = sys.argv[0]
    handle = int(sys.argv[1])
    query = sys.argv[2][1:] if len(sys.argv) > 2 else ''
    router.dispatch(base_url, handle, query)


if __name__ == '__main__':
    main()
