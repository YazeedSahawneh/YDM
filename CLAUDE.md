# YDM — project guide for Claude

YDM is a personal IDM-style download manager for **Windows**: a Python/PySide6 desktop app plus a Chrome/Edge MV3 extension. It's built from scratch (no IDM code or reverse engineering) and is Yazeed's personal project.

## Run, test, build

```bat
run.bat                          :: first run creates .venv and installs requirements
.venv\Scripts\python main.py     :: run with a console (to see errors)
.venv\Scripts\python tests\test_engine.py   :: engine tests (local range server, ~30 s)
.venv\Scripts\python tests\test_gui.py      :: offscreen GUI smoke test + local endpoint
build.bat                        :: PyInstaller → dist\YDM\YDM.exe, then Inno Setup → dist\YDM-Setup-<ver>.exe
python make_icons.py             :: redraw icons after changing ACCENT
```

The tests are plain scripts, not pytest. Each one prints `ALL GOOD` / `GUI OK` on success and exits non-zero on failure. **Run `tests/test_engine.py` after any change under `ydm/engine/`.** It checks SHA-256 of downloaded files against the source, so it catches corruption from segment math. The FTP part needs `pip install pyftpdlib` (test-only; skipped without it).

## Architecture

```
main.py                         single-instance check (pings the port), starts Qt + LocalServer
ydm/config.py                   APP_NAME, ACCENT, CATEGORIES, Settings dataclass (%APPDATA%\YDM\settings.json)
ydm/server.py                   127.0.0.1:9614 — GET /ping, POST /add (extension → app; kind file|video|batch),
                                POST /info (quality list for the extension's on-page menu)
ydm/tools.py                    FFmpeg/Deno install into %APPDATA%\YDM	ools, yt-dlp zip override (hot update)
ydm/updater.py                  GitHub Releases (UPDATE_REPO) check → download setup → silent install /RELAUNCH
ydm/engine/item.py              DownloadItem / Segment dataclasses (persisted to downloads.json)
ydm/engine/http_downloader.py   probe → pre-allocate → N worker threads → dynamic split → finalize
ydm/engine/video_downloader.py  yt-dlp wrapper (progress hooks, pause via exception in hook)
ydm/engine/manager.py           queue, max_active slots, scheduler window, speed/ETA, persistence
ydm/engine/limiter.py           shared token-bucket rate limiter
ydm/engine/hosts.py             per-host connection limit learned from 429/503 (hosts.json, 7-day expiry)
ydm/engine/net.py               shared SSL context + httpx clients (warm_up at start), 1 MB socket reads, proxy
ydm/engine/ftp_downloader.py    FTP/FTPS on the same segment engine (one session per piece, REST + RETR)
ydm/engine/auth.py              logins: AutoAuth (Basic/Digest/NTLM via pyspnego), SiteLogins (logins.json)
ydm/engine/gallery.py           photos/posts from social sites: gallery-dl.exe -J lists them, the engine downloads them
ydm/startup.py                  start with Windows: HKCU Run + StartupApproved (the same switch as Task Manager)
ydm/vault.py                    passwords at rest: DPAPI on Windows (base64 elsewhere, tests only)
installer/YDM.iss               Inno Setup script (per-user install, shortcuts, optional start-with-Windows)
ydm/gui/download_window.py      IDM-style small window per download: pre-start → choose → progress → done
ydm/gui/hudbar.py               the progress bar (HUD gauge, colour by %), used in the list, details pane, window
ydm/gui/                        main_window, dialogs (Batch, Settings, Login, Renew, Countdown), models (table),
                                widgets (SpeedMeter, DetailsPane, Banner), icons (drawn glyphs), theme (tokens + QSS)
extension/                      MV3: background.js (catch downloads, media sniffer, menus), content.js (video button), popup
```

### How a download works
1. `_probe()` sends `GET` with `Range: bytes=0-`. A `206` with `Content-Range: …/total` means it's resumable. The open response is kept in `self._first` and becomes segment 0's connection (no wasted round-trip).
2. The file is pre-allocated as `<name>.ydm` (sparse on Windows, then `truncate(total)`) and split into `connections` segments (capped by `hosts.limit`), minimum `MIN_SEGMENT` (1 MB).
3. Worker threads call `_next_segment()`. When no segment is free, a worker **steals half of the largest active segment** (down to `2 * STEAL_MIN`). This is dynamic segmentation. `_watch()` measures each connection every second and flags ones below `SLOW_RATIO` of the median for reconnect; a 429/503 on an extra connection retires that worker and the host's limit is recorded.
4. Each worker writes at `seg.pos` with `seek` + `write`. Progress lives in `Segment.done` and is saved every ~3 s, so a pause or crash can resume.
5. `_finalize()` checks the size, then `os.replace(.ydm → final)`.
6. Servers without range support fall back to `_run_single()` (one connection, restarts from 0 on retry).
7. Auto mode (`item.auto_connections`, default from `Settings.auto_connections`): `_connections()` starts at `hosts.best(host)` or `AUTO_START`, and `_Tuner` (fed once a second by `_watch`) steps up while a step buys ≥10 % more total speed, then settles on the best count and re-probes every 30 s; the best count is saved with `hosts.note_best`. A 429/503 stops tuning (the host limit wins). Any user change (`set_connections`) turns auto off for that item; the tuner itself uses `_resize`.
8. `set_connections(n)` changes the count while it runs: new workers join (they steal), surplus ones raise `_Surplus` between chunks and leave their piece to the others. The join loop in `_run_segmented` waits on `self._threads`, which can grow.

`FtpDownloader` subclasses `HttpDownloader` and only replaces `_probe`, `_open_range` and `_open_full` (each returns an object with `iter_raw`/`close`). Errors that retrying can't fix raise `Fatal` (no retries).

### Logins, proxy, expired links
- Credentials: the item's own (`auth_user`/`auth_pass`, vault-protected) > `user:pass@` in the URL > `auth.LOGINS` by host or parent domain. `AutoAuth` learns the scheme from the first 401 and then sends it up front.
- Proxy: `net.configure_proxy()` (called by `DownloadManager._apply_proxy`) sets the proxy for every `net.client()` and for yt-dlp. FTP ignores it. Proxy auth is Basic (HTTP proxy) or SOCKS5 user/password.
- Expired links: a resumed range answered 401/403/404/410, or a different total size, raises `Fatal` with `item.EXPIRED` / `item.LOGIN_NEEDED` as the error prefix (`item.expired`, `item.needs_login`). `manager.find_renewal()` matches the next browser link (the one the user is renewing, else same name + size, else the one expired item of that exact size) and `refresh_url()` continues it. Video items re-read the page instead (auto retry).
- Crash resume: an item still `Downloading` in downloads.json at load means YDM didn't shut down cleanly; each piece is rolled back 1 MB (`CRASH_ROLLBACK`) and the item is queued.

### Threading rules
- The engine is plain `threading`. The GUI polls `manager.items()` every 500 ms with a `QTimer`. Never touch Qt widgets from engine threads. Use the `MainWindow.item_event` / `browser_request` signals.
- `HttpDownloader._lock` guards segment boundaries and `item.downloaded`. **Replace `item.segments` with a new list instead of mutating it in place** (the save thread iterates it).
- Pausing = `downloader.stop()` sets an Event. Workers check it per chunk. Status becomes `Paused`.

### Pre-start (IDM-style)
A link from the browser or Add URL starts downloading immediately as a `pending` item (hidden from the list, see `DownloadManager.visible()`). `DownloadWindow` shows it filling while the user picks a name/folder. `confirm()` stores them in `dest_name`/`dest_dir`, which `_finalize()` applies at the end, so no bytes are thrown away. Cancel/close calls `discard()`. Pending items left over from a crash are deleted on load.

### Speed notes (measured)
- Never `fh.truncate()` to pre-allocate on Windows: it writes zeros (about 3 s per GB before the first byte). `_set_size_sparse` uses `SetEndOfFile` on a sparse file (4 GB in ~0 ms). The engine test asserts this.
- Build httpx clients through `net.client()` (shared SSL context). A fresh context costs 0.25–1.2 s.
- Workers read with `iter_raw` and one lock per chunk; `net` raises httpcore's read size to 1 MB. The UI stays smooth (event-loop p99 about 2 ms at 50 MB/s).

### Photos and posts
`gallery.fetch_media_info()` runs yt-dlp and (for `SOCIAL` hosts, or when yt-dlp finds nothing) gallery-dl side by side; a post with photos gets an extra quality-list entry whose format is `gallery.OPTION` ("gallery"). Choosing it calls `MainWindow.save_post()` → `gallery.take()` (the cached listing; signed links expire) → `manager.add_gallery()`, one queued item per file (`ytdl:` entries become video items). gallery-dl is a separate program in `tools\` (GPL, so it's not bundled into the exe); the dev venv's `gallery-dl.exe` is found too (`pip install gallery-dl`, test-only). The page's cookies go to gallery-dl as a temporary cookies.txt and are only forwarded to media URLs on the same site.

### Video
`VideoDownloader` asks yt-dlp for the streams (`extract_info(download=False)`, or the Add dialog's cached info). Plain HTTP(S) streams (YouTube video + audio) are downloaded by child `HttpDownloader`s in parallel and merged with FFmpeg; their segments are saved in `item.parts`, so a resume re-reads fresh links and continues. HLS/DASH/other protocols fall back to `ydl.process_info()`.

### Releases and updates
Raise `APP_VERSION`, commit, then `git tag -a v<version> -m "notes"` and push the tag: `.github/workflows/release.yml` builds `YDM-Setup-<version>.exe` on a Windows runner and attaches it to the release (it refuses a tag that doesn't match `APP_VERSION`). `updater.check()` reads the release list and skips a release whose installer isn't attached yet. Installed copies check daily (`Settings.auto_update`), download in the background and restart via `updater.launch_installer` (Inno Setup `/VERYSILENT /RELAUNCH`). `manager.shutdown(resume_later=True)` queues running downloads so they continue after the restart. A new release also drops any stale `tools\yt-dlp.zip` override.

## Look
The UI follows the direction in `PRODUCT.md` / `DESIGN.md`: studio-rack hi-fi, graphite panels, signal-teal meters, amber only on pressable controls, one fixed ink per status (`theme.INK`). The user can change accent/signal (Options → Appearance): read colours from `theme.ACCENT`/`theme.SIGNAL` at paint time, never copy them into module constants; `MainWindow.apply_theme` re-applies the QSS and recolours icons, and `DownloadWindow.apply_theme` does the same for open windows. The progress bar colour comes from `theme.progress_color()` (`hudbar.BAR_MODE`). Light/dark: `theme.set_mode()` swaps the ground tokens (`theme.MODES`); never hard-code a colour in QSS or a widget, use a token. Set icons on persistent buttons/actions with `icons.put(target, name, role)` so `icons.retint()` can recolour them; `icons.icon(name, "muted")` takes a role name and resolves it at call time. Icons are drawn in `gui/icons.py`; don't add emoji or unicode glyphs as icons.

## Conventions
- Python 3.10+, type hints, `from __future__ import annotations`.
- Keep user-facing errors short and friendly (`_friendly_error`). They show in the Status column.
- Windows first: `os.startfile`, `explorer /select,`, `%APPDATA%`. Keep Linux fallbacks working because the tests run offscreen on Linux too.
- `.bat` files must keep **CRLF** line endings.
- Branding lives in `APP_NAME` / `ACCENT` (config.py) and `extension/manifest.json`. The extension checks `app === "YDM"` in `background.js`.

## Security
- The local server binds **127.0.0.1 only** and rejects any request whose `Origin` isn't a browser extension. This stops web pages from adding downloads. Keep that check.
- Cookies from the extension are stored in `downloads.json` (item headers). Don't log them.
- This is a clean-room app. Don't add code that bypasses DRM, paywalls or other software's licensing.

## Roadmap ideas (not done yet)
- Mirror / multi-source downloads
- Optional aria2c backend
- Per-download speed limit, checksum verification
- Auto connections: also probe *down* (fewer connections) on servers that slow down with many
- Digest/NTLM proxy authentication (only Basic and SOCKS5 user/password today); FTP through a proxy
- Playlist download for the video grabber
- Publish the extension to the Chrome Web Store / Edge Add-ons (needs a developer account)
