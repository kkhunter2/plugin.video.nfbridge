# Nflix — `plugin.video.nfbridge`

A Netflix Kodi addon built on the conclusion of the June‑2026 DRM investigation
(see `../netflix-*.md`, `../ISA-1850-report.md`): **an open addon cannot decrypt
Netflix on desktop** (VMP / Media‑Foundation‑TEE / device‑key boundaries), but a
trusted client already plays it on every desktop OS. So this addon **delegates
playback** and does what Kodi does well — a native browse UI.

## Architecture

```
ONE persistent WebView2 daemon  (runtime/webview/webview_host.py -> nfbridge_player.exe)
   runs netflix.com off-screen, signed in once, and serves BOTH:
   - browse : localhost RPC that proxies falcor queries through the page's own data
              client (netflix.appContext.state.pathEvaluator) + Apollo-cache reads
   - play   : /play navigates the same window to /watch and brings it forward
              fullscreen (PlayReady SL3000 -> up to 4K) with the Kodi subtitle font

resources/lib/nfclient.py   browse client for that daemon (pure stdlib)
resources/lib/routes.py     -> Kodi directories
resources/lib/player.py     daemon lifecycle + Play (embedded) / delegate fallback
```

`playback_mode` (setting) chooses embedded vs delegate; default is **embedded on
Windows** (WebView2), delegate (OS browser / app) elsewhere.

> **Embedded = Microsoft Edge WebView2 (Windows).** The system Edge engine is
> Microsoft-signed and Netflix-trusted, so Netflix **plays in our own window, up to
> 4K (PlayReady SL3000)** — verified (overlay ESN `NFCDIE`, 3840×2160). A bundled
> *Electron* does NOT work (Netflix rejects it: M7121/D7353/e100 — VMP-signature
> provenance), so embedded uses WebView2, not Electron.
>
> **Subtitles (embedded):** we keep Netflix's own subtitle text/timing/position and
> only **swap the font to Kodi's configured subtitle font** (`media/Fonts`, via
> `kodi_subs.py`) + colour/outline, injected with `!important` CSS. The font is
> first OTS-repaired by `fontfix.py` (Chromium's sanitizer rejects some fonts that
> Kodi/FreeType load — e.g. a malformed `post` table); if a font is too corrupt to
> repair, the user is **notified** and Netflix's own font is used.
>
> **Runtime:** the embedded player is a self-contained `nfbridge_player.exe`
> (`runtime/webview/`, built by `build_exe.py` — bundles Python + pywebview +
> fonttools, ~18 MB) so no external Python is needed. Dual-purpose:
> `nfbridge_player.exe` runs the player; `nfbridge_player.exe fontfix <src> <cache>
> <result>` OTS-repairs a font. `player.py` prefers a configured `webview_python`
> (a signed python.exe passes Smart App Control) + the host script, else the exe,
> else delegate mode.

## Browse — the WebView-fetch daemon
The daemon reads Netflix's data *through the page's own clients* over localhost, so
there is no header/API reverse-engineering (a raw `pathEvaluator` request returns 412
even from inside the page). (A direct-HTTP browse layer existed up to 0.2.0 but was
never wired in and was removed in 0.3.0: one verified path beats two drifting ones.)

**Since the 2026-10 Netflix build, browse pages are GraphQL "Pinot" pages, not falcor
LoCo.** `/browse`, `/browse/genre/<id>` and `/browse/my-list` live in the page's Apollo
cache (`Pinot*Page → sections → PinotCarouselSection → entities → *EntityTreatment →
Movie|Show:{videoId}`), and are paginated by the page itself. The daemon (`/pinot`)
loads more sections by bringing the page's `browse-page+sentinel` into view and more of
a row's tiles by clicking that carousel's right arrow, then reads the cache. Falcor
still serves per-video data (`videos[id]` synopsis/bookmark/runtime, season episodes).

`resources/lib/nfclient.py` calls the daemon:
- `home()` / `genre()` — a page's rows (Pinot); a row's "root" is the page path
- `row_videos()` — one row's tiles (type, title, art, bookmark/runtime when present),
  topped up with one batched falcor call for synopsis / bookmark / runtime
- `my_list()` — every row of `/browse/my-list`, flattened
- `video_info()` — one title's basics (used to tell movie from show for untyped tiles)
- `search()` — Netflix Pinot search results (Apollo)
- `episodes()` — a show's seasons/episodes (Apollo season list + falcor episodes)
- `profiles()` — profile list (falcor). `/switch` clicks the header account menu's
  `switch-profile-<guid>` button (`/ProfilesGate` now redirects to `/browse`); the
  ACTIVE profile is Apollo's `currentProfile` (`userInfo.guid` is the account owner's)

Daemon endpoints (`NF_RPC_PORT`): `/health` (`ready`, `login`, `ver`, `playing`),
`/context` (incl. `profile`, `home`), `/falcor`, `/art`, `/pinot`, `/search`,
`/episodes`, `/home`, `/switch`, `/play`, `/show`, `/quit`, `/eval`.

**Lifecycle.** The daemon is detached and survives Kodi restarts; `nfclient.ensure()`
restarts it when its `/health` `ver` (runtime-file mtime + subtitle font name) differs
from what the addon would launch. `service.py` quits it whenever Kodi's own player is
active (so the hidden tab can't hold the audio device) and on Kodi exit; the router
relaunches it on the next browse. Its WebView2 profile (login cookies, DRM
provisioning) and the repaired-font cache live in the addon's **data** dir
(`addon_data/plugin.video.nfbridge/webview_profile`, `fontcache`), so an addon
update/reinstall doesn't sign the user out.

## Legitimacy
No DRM circumvention, no key handling. Each user signs in to **their own** account;
playback runs in a client Netflix already trusts (Edge issues a real SL3000 XMR
license, 4K).

## Status
- [x] Addon scaffold (`addon.xml`, `default.py`, router, settings, en_GB / ko_KR)
- [x] **Playback** — embedded WebView2 (4K + Kodi subtitle font + OSD) / delegate fallback
- [x] **Self-contained runtime** (`nfbridge_player.exe`, no external Python)
- [x] **Subtitle font** — Kodi `media/Fonts` font + OTS repair + user notification
- [x] **Browse daemon** — home rows, box art, search, categories, episodes, profiles
- [x] **Single sign-in** in the daemon window; auto profile-select at the gate
- [x] **Profile switching** — `/switch` clicks the chosen profile off-screen and the
      choice is persisted (`profile_index`); falls back to showing the gate
- [x] **D7361 guard** — clears the WebView2 CDM store when the GPU driver or the
      PlayReady/MediaFoundation DLLs change
- [ ] macOS WKWebView host; Linux remains delegate

## Files
| file | role |
|---|---|
| `default.py` | entry point |
| `service.py` | audio-device hand-back, daemon quit on local playback / Kodi exit |
| `resources/lib/router.py` | URL → handler dispatch, daemon recovery |
| `resources/lib/routes.py` | handlers → Kodi directories / play |
| `resources/lib/nfclient.py` | browse client for the WebView-fetch daemon |
| `resources/lib/player.py` | daemon lifecycle + playback |
| `resources/lib/kodi_subs.py` | Kodi subtitle font/style |
| `resources/lib/kodiutils.py` | Kodi API helpers |
| `resources/lib/cache.py` | on-disk list cache |
| `runtime/webview/webview_host.py` | WebView2 daemon (browse RPC + playback + OSD) |
| `runtime/webview/fontfix.py` | OTS font repair (CLI) |
| `runtime/webview/build_exe.py` | PyInstaller build of the runtime exe |
| `tools/package.py` | builds the exe if stale and zips the addon |
| `resources/settings.xml` | playback mode, profile index, path overrides |

## Build / install
```
pip install pywebview fonttools brotli pyinstaller   # once, in a real Python
python tools/package.py                               # rebuilds the exe if stale, writes ../plugin.video.nfbridge-<ver>.zip
```
Install the zip in Kodi (Add-ons → Install from zip). Requires Kodi 20+.
