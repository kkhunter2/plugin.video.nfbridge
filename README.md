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

## Sign-in / session (Layer 1)
The user signs in **once** in the embedded player window; the host extracts the
shakti session (authURL, build, profile guid, cookies) from `netflix.reactContext`
+ pywebview `get_cookies()` into `session.json` (no browser-credential-store read).
Layer 1 (`netflixapi`) loads it so browsing and playback share one login.
*(The exact current falcor/GraphQL request format is still being tuned — the browse
API is a moving target; session capture + plumbing are in place.)*

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
