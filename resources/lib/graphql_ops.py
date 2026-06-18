# -*- coding: utf-8 -*-
"""Netflix GraphQL persisted-query operation registry.

The gateway (web.prod.cloud.netflix.com/graphql) uses Apollo persisted queries:
each request sends extensions.persistedQuery = {version, sha256Hash}. The hash is
baked into Netflix's web JS bundle and rotates periodically (current scheme = v102).

HOW TO POPULATE the sha256 hashes (either works, no addon change needed):
  A) Live capture (logged-in): hook the /graphql POST (the embedded player's
     network hook already does this) and read extensions.persistedQuery.sha256Hash
     per operationName.
  B) Static extract: fetch cadmium-playercore JS and grep the
     {operationName -> sha256Hash} map (no login needed, but bundle-version fragile).

Until filled, GraphQL-backed routes (search, episodes) surface a clear
PersistedQueryNotFound error instead of silently failing — wire these in and the
rest of the addon keeps working (falcor browse is independent).
"""

GRAPHQL_VERSION = 102

# operationName -> sha256Hash (fill from a capture; None until then)
OPS = {
    'SearchPage': None,
    'DetailModalEpisodeSelector': None,
    'PreviewModal': None,
    'BobGalleryRow': None,
}


def sha256_for(operation_name):
    return OPS.get(operation_name)


def is_ready(operation_name):
    return bool(OPS.get(operation_name))
