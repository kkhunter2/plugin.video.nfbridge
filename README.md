# Nflix

English | [한국어](README.ko.md)

A Kodi add-on for watching Netflix. You pick titles from normal Kodi lists, and playback
opens in a full-screen Netflix window. That window runs on the Edge engine built into
Windows (WebView2), which Netflix accepts as a regular browser, so it plays in up to 4K.
Subtitles use the font and colours you set in Kodi.

Windows only.

## What you need

- Windows 10 or 11
- Kodi 20 or newer, the desktop version from kodi.tv. The Microsoft Store version of
  Kodi won't work.
- A Netflix account. For 4K you also need what Netflix asks for (Premium plan, a 4K
  screen and so on).

You don't need to install Python or anything else. Everything ships in the zip.

## Install

1. Download `plugin.video.nfbridge-<version>.zip` from
   [Releases](https://github.com/kkhunter2/plugin.video.nfbridge/releases).
2. In Kodi, go to Add-ons → Install from zip file and pick the file.
3. Open Nflix under Video add-ons. The first time, you'll only see "Sign in to Netflix".
   Select it and a Netflix window opens. Sign in, pick your profile, and the window goes
   away and you're back in Kodi. You only do this once.

To update, install the new zip the same way. You stay signed in.

If Windows 11 Smart App Control is on, it may block the player. See
[Troubleshooting](#troubleshooting).

## Using it

The home screen shows the same rows as the Netflix home page, with Search, My List,
Series & Movies and Profiles at the top.

Pick a movie and it starts playing. Pick a series and you get its seasons and episodes.
Resume points and watched marks show up in the Kodi lists too.

While a title is playing:

| Key | What it does |
|---|---|
| OK (Enter) | Presses "Skip intro", "Next episode" and similar buttons when Netflix shows them. Otherwise opens the menu |
| Arrows | Move through the menu when it's open. Otherwise they do what they do on Netflix (seek, volume) |
| Back (Esc, Backspace) | Closes the menu if it's open. Otherwise stops playback and returns to Kodi |

The menu has Play/Pause, Subtitles, Next episode (series only) and Back.

Subtitles follow your Kodi subtitle settings (font, size, colour, border). If you'd
rather have Netflix's own font, leave Kodi's subtitle font on the default.

## Settings

| Setting | What it does |
|---|---|
| Playback location | "Embedded" (the default) plays in the add-on's own window. "Delegate" hands the title to the Netflix app from the Microsoft Store, or to an Edge or Chrome window if the app isn't installed. With Delegate, your Kodi subtitle style and the remote controls above don't apply |
| Switch profile | Choose which Netflix profile to use. Same as Profiles on the home screen |
| Start Netflix with Kodi | On by default. Netflix starts in the background 10 seconds after Kodi does, so the add-on opens straight away. It costs about 1 GB of memory while Kodi is running. Turn it off if you don't use Netflix much; the first open then takes about 10 seconds |

There are a few more settings, such as custom Edge or Chrome paths, that you'll rarely
need.

## Good to know

- Lists are kept for 3 hours once loaded (My List for 1 hour). That's why they open
  instantly the second time, but a row Netflix just added can take a while to show up.
  Resume points and watched marks update right after you watch something.
- When you play something else in Kodi (your own files, another add-on), the Netflix
  window is closed so the two don't fight over the audio device. It comes back by itself
  the next time you open Netflix.
- Switching profiles clears the saved lists and loads them again.

## Troubleshooting

**The first open is slow.**
With "Start Netflix with Kodi" off, the first open takes about 10 seconds while Netflix
starts. Turn it on and Kodi gets Netflix ready at startup.

**The player doesn't start.**
Windows 11 Smart App Control blocks unsigned programs, and the player bundled with the
add-on is unsigned. You can have it run through a signed Python instead:

1. Install Python from [python.org](https://www.python.org/).
2. In a Command Prompt, run `py -m pip install pywebview fonttools brotli`.
3. In the add-on settings, under Embedded player, set "Python for the WebView2 host" to
   that `python.exe`.

Turning Smart App Control off also works, but it's a Windows security feature, so try
this first.

**Playback fails with error D7361.**
This Netflix DRM error tends to appear after a graphics driver or Windows update.
Restart Kodi: the add-on then clears the old DRM data and Netflix sets it up again.

**I want to use a different Netflix account.**
You can't sign out from inside the add-on yet. Close Kodi, delete
`%APPDATA%\Kodi\userdata\addon_data\plugin.video.nfbridge\webview_profile`, then start
Kodi again and sign in with the other account.

**A new Netflix row doesn't show up.**
Lists are reloaded every 3 hours. To get them now, go to Switch profile and pick the
profile you're already using. That clears the saved lists.

## Why Windows only

The add-on reads the lists from a Netflix window kept off-screen and plays in that same
window. The only browser engine that works that way and that Netflix accepts is
Windows' WebView2. macOS slows down hidden windows, Linux has no suitable engine out of
the box, and Android and LibreELEC have no browser to run at all. Other systems were
looked at in October 2026 and aren't planned.

## How it works

The add-on keeps the Netflix website open in a single WebView2 window placed off-screen,
so you never see it. When Kodi needs a list, the add-on reads the Netflix data already
in that window and turns it into Kodi items. To play, it brings the same window to the
front in full screen.

Nothing is decrypted or worked around. A browser engine Netflix trusts plays the video
the normal way, and everyone signs in with their own account. If you try the same thing
with another embedded browser such as Electron, Netflix refuses to play.

## Development

To build it yourself:

```
pip install pywebview fonttools brotli pyinstaller
python tools/package.py
```

`tools/package.py` rebuilds the player with PyInstaller when it's older than its sources,
then writes `plugin.video.nfbridge-<version>.zip` next to the repository folder. The
player is built as a folder (onedir) rather than a single exe, because a onefile exe
unpacks itself on every launch and starts about a second slower.

| File | Role |
|---|---|
| `runtime/webview/webview_host.py` | The Netflix window (daemon): reads list data, plays, draws the playback menu |
| `runtime/webview/fontfix.py` | Repairs the Kodi subtitle font so the browser will load it |
| `resources/lib/nfclient.py` | Talks to the daemon |
| `resources/lib/routes.py` | Builds the Kodi lists |
| `resources/lib/player.py` | Starts the daemon; embedded and delegated playback |
| `resources/lib/cache.py` | Saved lists |
| `service.py` | Starts Netflix with Kodi, restores audio settings after playback, stops the daemon when Kodi exits |
| `resources/language/` | Text in 12 languages |

Notes:

- The daemon takes HTTP requests on `127.0.0.1:8731` (`/health`, `/pinot`, `/falcor`,
  `/search`, `/episodes`, `/play` and others).
- Since October 2026, Netflix's browse pages are GraphQL ("Pinot") pages. The daemon
  scrolls the page to load more rows and reads them from the page's Apollo cache.
  Per-title details (synopsis, resume point, episodes) come from falcor.
- Sign-in and DRM data live in `addon_data\plugin.video.nfbridge\webview_profile`, so
  updating the add-on doesn't sign you out.
- Translations are in `resources/language/*/strings.po`. Everything except English is a
  first draft, so corrections are welcome.
