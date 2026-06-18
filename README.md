# Netflix (Bridge) — `plugin.video.nfbridge`

A new Netflix Kodi addon built on the conclusion of the June‑2026 DRM investigation
(see `../netflix-*.md`, `../ISA-1850-report.md`): **an open addon cannot decrypt
Netflix on desktop** (VMP / Media‑Foundation‑TEE / device‑key boundaries), but a
trusted client already plays it on every desktop OS. So this addon **delegates
playback** and does what Kodi does well — a native browse UI.

## Architecture (two layers)

```
Layer 1  resources/lib/netflixapi.py   read‑only catalog/metadata
         resources/lib/routes.py       -> Kodi directories
         (falcor pathEvaluator + GraphQL gateway persistedQuery v102; LoCo; My List)

Layer 2  resources/lib/player.py       playback delegation — NO DRM code
         delegate : Netflix app / Edge kiosk (PlayReady SL3000 -> 4K on Windows)
                    Safari/Chrome (mac) · Chrome --app (linux, 720p)
         embedded : bundled VMP‑signed Electron (Widevine privacy mode -> 1080p,
                    integrated libass‑wasm subtitle overlay)
```

`playback_mode` (setting) chooses delegate vs embedded; default is delegate on
Windows (4K), embedded on macOS/Linux (no 4K to lose, gains integration + subs).

## Legitimacy
No DRM circumvention, no key handling. Each user signs in to **their own** account;
playback runs in a client Netflix already trusts. Empirically validated:
Edge issues a real SL3000 XMR license (4K); a castLabs Electron produces
`encrypted_client_id` out‑of‑the‑box (Widevine privacy mode → Netflix accepts, 1080p).

## Status — kickoff skeleton
- [x] Addon scaffold (`addon.xml`, `default.py`, router, settings)
- [x] **Layer 2 playback delegation — functional** (platform detection + launchers)
- [x] Layer 1 client structure with the real endpoints/paths + defensive parsers
- [ ] **Session bootstrap** (`routes._load_auth`): cookies + build id + authURL + profile
      guid — reuse the upstream addon's proven login flow. *Required before browse works.*
- [ ] GraphQL persisted‑query op‑ids/SHA256 for search & episodes (v102)
- [ ] Embedded runtime bundling (castLabs Electron + netflix‑web wrapper + subtitle module)
- [ ] Subtitle re‑render module (libass‑wasm / JASSUB, synced to `video.currentTime`)
- [ ] icon/fanart, localisation, Netflix app AppUserModelId resolution

## Files
| file | role |
|---|---|
| `default.py` | entry point |
| `resources/lib/router.py` | URL → handler dispatch |
| `resources/lib/routes.py` | handlers → Kodi directories / external play |
| `resources/lib/netflixapi.py` | Netflix browse API (Layer 1) |
| `resources/lib/player.py` | playback delegation (Layer 2) |
| `resources/lib/kodiutils.py` | Kodi API helpers |
| `resources/settings.xml` | playback mode + path overrides |
