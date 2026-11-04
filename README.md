# YDM — Download Manager

A personal IDM-style download manager for Windows, written from scratch in Python.

**Features**
- **Automatic connection count**: YDM measures the speed while it adds connections (6 → 9 → 13 → 19…), keeps adding while each step still buys 10 % more, settles on the fastest count, tries again every 30 s, and remembers the best count per server for next time. A fixed count (1–32) is one click away (Options → Connection, or per download)
- **Mirrors (several servers at once)**: give a download other addresses of the same file (right-click → *Mirrors…*, or the Mirrors button in its window), or let YDM find them: servers that announce copies (Metalink headers, or the Metalink mirror list that MirrorCache/MirrorBrain sites like openSUSE send) are picked up by themselves. Every mirror is checked first (same size, same bytes), each server keeps its own connection limit so the limits add up, faster servers get more connections, and if one link dies the others finish the file. Measured on openSUSE's ISO: 10 MB/s from the main server, 25–42 MB/s with its mirrors, SHA-256 identical
- **Servers that refuse extra connections** (429/503, a 403, or just hanging up) no longer fail the download: the refused connection steps aside and the server's limit is remembered
- **File missing**: when a finished file is deleted or moved, the list says *File missing* (in red) instead of Completed, and opening it offers *Download again*
- Multi-connection downloads with **dynamic segmentation**: a finished connection takes half of the largest remaining piece, down to 256 KB at the end, so speed stays high to the last byte
- **Speed tricks**: the first request's data is kept (no separate probe round-trip), files are pre-allocated sparse (no NTFS zero-fill stall on big files), slow connections are reconnected, and a server that refuses extra connections makes YDM back off and remember that server's limit
- **HTTP, HTTPS and FTP/FTPS**, all multi-connection (FTP uses one session per piece with `REST`)
- **Proxy**: HTTP or SOCKS5 (with a user name and password), or the Windows proxy settings (Options → Proxy & logins)
- **Logins**: HTTP Basic, Digest and NTLM/Negotiate, and FTP. Type one in the download window, put it in the address (`https://user:pass@host/…`), or save it per site; passwords are encrypted with Windows DPAPI
- **Change the connection count while a download runs**: new connections join at once, extra ones finish their piece and leave
- **Pause / resume** that survives restarts, crashes (a crashed download picks up by itself when YDM starts) and app updates; network errors retry by themselves
- **Renew expired downloads**: when a link stops working, right-click → *Renew download address*, then click the link in the browser again. YDM catches the fresh link, checks it is the same file (same size), and continues where it stopped
- **Download window** per download: starts before you confirm; Save as (name and folder, any time, even after it finished), Remember this folder, Cancel, Download later, Open with…, the address with a copy button, and the live connection count
- **Light and dark mode** (or match Windows), plus theme colours, in Options → Appearance
- **HUD progress bar** that lights each slice of the file as it arrives, marks every working connection, and shifts colour with the percentage (red → amber → yellow → green); **theme colours** in Options → Appearance
- **Video grabber** (yt-dlp: YouTube and 1,800+ sites) with a quality list showing sizes. YouTube's video and audio streams are downloaded with YDM's own multi-connection engine at the same time and merged with FFmpeg
- **Photos, reels and posts from social sites** (Instagram, Facebook, X, Threads, TikTok, Reddit, Pinterest, Tumblr and a few hundred more, via gallery-dl): paste a post's link with *Video / photos*, or use the **Save photo** button / the video button on the page. "All N photos + M videos" saves the whole post in full size. Your browser's login for that site goes along, so posts your account can see work
- **Video tools on demand**: YDM downloads FFmpeg, Deno and gallery-dl itself (one click, once) and keeps yt-dlp and gallery-dl current between releases
- **Start with Windows** (in the tray): Options → General. It's the same switch as YDM in Task Manager → Startup apps, so turning it off in either place shows there too
- **Browser extension** for Chrome/Edge/Brave: catches downloads automatically, a **Download video** button over videos and a **Save photo** button over big pictures (both find the post a feed item belongs to), "Save every photo and video of this post" on post links, a list of media streams the page plays, right-click "Download with YDM" / "Download all links"
- **Updates itself** from GitHub Releases: downloads the new version in the background, one click to restart, downloads carry on
- Queue, scheduler, speed limit, "when everything finishes: sleep / shut down", batch add, drag & drop and Ctrl+V for links, category folders, tray, notifications

## 1. Run it

Requires **Python 3.10+** (python.org, tick "Add to PATH").

Double-click **`run.bat`**. The first run creates a virtual environment and installs dependencies.

Or manually:
```
pip install -r requirements.txt
python main.py
```

Video tools: YDM offers to install FFmpeg and Deno the first time you grab a video (Options → Video → Install). Without them videos are limited to combined formats, usually 720p.

## 2. Install the browser extension

1. Open `chrome://extensions` (or `edge://extensions`)
2. Turn on **Developer mode**
3. Click **Load unpacked** and select the `extension` folder (installed copies: Start menu → *YDM browser extension folder*)
4. Pin the YDM icon; the popup should show **Connected** while the app is running

If YDM isn't running, downloads stay in the browser as normal.

## 3. Build and release

Double-click **`build.bat`** → `dist\YDM\YDM.exe` plus the installer `dist\YDM-Setup-<version>.exe` (needs Inno Setup: `winget install -e --id JRSoftware.InnoSetup`). The installer adds Start menu/desktop shortcuts, an uninstaller, the browser extension folder and an optional "Start with Windows" (minimized to tray).

**In-app updates (one-time setup):** create a public GitHub repo, then set `UPDATE_REPO = "your-name/YDM"` in `ydm/config.py`.

**Each release** (GitHub builds the installer: `.github/workflows/release.yml`):
1. Raise `APP_VERSION` in `ydm/config.py` (e.g. `1.4.0` → `1.5.0`) and commit
2. Tag and push: `git tag -a v1.5.0 -m "What's new…"` then `git push origin main v1.5.0`
3. GitHub Actions builds `YDM-Setup-1.5.0.exe` on Windows (about 10 minutes) and attaches it to the release, with the tag message as the notes

To attach a missing installer to an existing tag: Actions → Release → Run workflow → enter the tag. `build.bat` still builds locally for testing.

Installed copies check once a day, download the setup in the background and offer **Restart to update**. The installer runs silently and starts YDM again. Nobody reinstalls by hand.

## Make it yours

| What | Where |
|---|---|
| App name | `APP_NAME` / `APP_TITLE` in `ydm/config.py` (also rename it in `extension/manifest.json` and the `"YDM"` check in `background.js`) |
| Colours | Options → Appearance (presets and custom); defaults are `ACCENT` / `SIGNAL` in `ydm/config.py`, presets in `theme.PRESETS` |
| Update source | `UPDATE_REPO` in `ydm/config.py` |
| Categories / extensions | `CATEGORIES` in `ydm/config.py` |
| Video sites auto-detected in the Add dialog | `VIDEO_SITES` in `ydm/gui/dialogs.py` |
| Segment sizes, retries, slow-connection rule | constants at the top of `ydm/engine/http_downloader.py` |

After changing colours, run `python make_icons.py` to redraw the app and extension icons.

## How it's built

```
main.py                        app start, single instance, yt-dlp override, local server
ydm/config.py                  settings, categories, branding, update repo
ydm/server.py                  127.0.0.1:9614 endpoint for the extension (/ping, /add)
ydm/tools.py                   FFmpeg / Deno install, yt-dlp hot update
ydm/updater.py                 GitHub Releases check, download, silent install
ydm/engine/http_downloader.py  probe-as-first-segment → split → parallel ranges → steal/reconnect → finalize
ydm/engine/video_downloader.py yt-dlp finds streams; HTTP streams go through the segmented engine + FFmpeg merge
ydm/engine/manager.py          queue, scheduler, retries, speed calc, persistence (downloads.json)
ydm/engine/hosts.py            per-server connection limits learned from 429/503
ydm/gui/                       main window, dialogs, list model + meter, widgets, icons, theme
extension/                     Chrome/Edge MV3 extension (background, content button, popup)
```

Data lives in `%APPDATA%\YDM` (`settings.json`, `downloads.json`, `hosts.json`, `tools\`). Unfinished files are saved as `name.ext.ydm` next to the final file.

**Security notes:** the local endpoint only listens on 127.0.0.1 and rejects requests from web pages (only extensions and local programs are accepted). Cookies forwarded by the extension are stored in `downloads.json` until the download is removed from the list. Updates are only taken from the GitHub repo in `UPDATE_REPO`.

**Please use it responsibly.** Only download content you have the right to download, and respect each site's terms.
