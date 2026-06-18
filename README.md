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

`playback_mode` (setting) chooses delegate vs embedded; default is **embedded on
Windows** (WebView2), delegate elsewhere.

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
> <result>` OTS-repairs a font. `player.py` auto-detects the exe (else a
> `webview_python` + host script, else delegate mode).

## Browse (Layer 1) — the WebView-fetch daemon
Netflix's browse data is **not** plain GraphQL and **not** in the page's static
react context; it is served by falcor, and a raw `pathEvaluator` request returns 412
even from inside the page (Netflix's client adds opaque `X-Netflix.*` headers). So
instead of reimplementing the API, **one persistent WebView daemon** runs netflix.com
and the addon proxies queries *through the page's own data client* over localhost
(`netflix.appContext.state.pathEvaluator.get` for falcor; the Apollo cache for art /
search / episodes). This needs no header reverse-engineering and survives Netflix
changes. The daemon serves browse (hidden, after a visible prefetch) and playback
(`/play` brings the window forward) from a single login.

`resources/lib/nfclient.py` (pure-stdlib) calls the daemon:
- `home()`  — LoCo rows (`["locos",root,{range},"componentSummary"]`)
- `row_videos()` — a row's videos (title via falcor, **box art** from the Apollo cache)
- `search()` — Netflix **Pinot** search results (Apollo)
- `episodes()` — a show's episodes from the title page's Apollo cache
- `profiles()` — profile list; the daemon auto-selects one at the "Who's watching" gate

Daemon endpoints (`runtime/webview/webview_host.py`, `NF_RPC_PORT`): `/health`,
`/context`, `/falcor`, `/art`, `/search`, `/episodes`, `/home`, `/play`, `/show`,
`/quit`, `/eval`. Single sign-in: the user signs in once in the daemon window.

## Legitimacy
No DRM circumvention, no key handling. Each user signs in to **their own** account;
playback runs in a client Netflix already trusts. Empirically validated:
Edge issues a real SL3000 XMR license (4K); a castLabs Electron produces
`encrypted_client_id` out‑of‑the‑box (Widevine privacy mode → Netflix accepts, 1080p).

## Status
- [x] Addon scaffold (`addon.xml`, `default.py`, router, settings)
- [x] **Playback** — embedded WebView2 (4K + Kodi subtitle font) / delegate fallback
- [x] **Self-contained runtime** (`nfbridge_player.exe`, no external Python)
- [x] **Subtitle font** — Kodi `media/Fonts` font + OTS repair + user notification
- [x] **Browse daemon** — home rows, **box art**, **search**, **episodes**, profiles
- [x] **Single sign-in** in the daemon window; auto profile-select at the gate
- [x] **Play** from Kodi → daemon surfaces the window and plays
- [~] Profile switching — index selected at the gate; switching a *persisted* profile
      is best-effort (account-menu flow varies by Netflix build)
- [ ] macOS WKWebView host; Linux remains delegate
- [ ] icon/fanart, localisation

## Files
| file | role |
|---|---|
| `default.py` | entry point |
| `resources/lib/router.py` | URL → handler dispatch |
| `resources/lib/routes.py` | handlers → Kodi directories / play |
| `resources/lib/nfclient.py` | browse client for the WebView-fetch daemon (Layer 1) |
| `resources/lib/session.py` | captured browse session helper |
| `resources/lib/player.py` | daemon lifecycle + playback (Layer 2) |
| `resources/lib/kodi_subs.py` | Kodi subtitle font/style |
| `resources/lib/kodiutils.py` | Kodi API helpers |
| `runtime/webview/webview_host.py` | WebView2 daemon (browse RPC + playback) |
| `runtime/webview/fontfix.py` | OTS font repair (CLI) |
| `runtime/webview/build_exe.py` | PyInstaller build of the runtime exe |
| `resources/settings.xml` | playback mode, profile index, path overrides |
