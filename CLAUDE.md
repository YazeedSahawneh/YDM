# YDM — project guide for Claude

YDM is a personal IDM-style download manager for **Windows**: a Python/PySide6 desktop app plus a Chrome/Edge MV3 extension. It's built from scratch (no IDM code or reverse engineering) and is Yazeed's personal project.

## Run, test, build

```bat
run.bat                          :: first run creates .venv and installs requirements
.venv\Scripts\python main.py     :: run with a console (to see errors)
.venv\Scripts\python tests\test_engine.py   :: engine tests (local range server, ~30 s)
.venv\Scripts\python tests\test_gui.py      :: offscreen GUI smoke test + local endpoint
build.bat                        :: PyInstaller → dist\YDM\YDM.exe
python make_icons.py             :: redraw icons after changing ACCENT
```

The tests are plain scripts, not pytest. Each one prints `ALL GOOD` / `GUI OK` on success and exits non-zero on failure. **Run `tests/test_engine.py` after any change under `ydm/engine/`.** It checks SHA-256 of downloaded files against the source, so it catches corruption from segment math.

## Architecture

```
main.py                         single-instance check (pings the port), starts Qt + LocalServer
ydm/config.py                   APP_NAME, ACCENT, CATEGORIES, Settings dataclass (%APPDATA%\YDM\settings.json)
ydm/server.py                   127.0.0.1:9614 — GET /ping, POST /add (extension → app)
ydm/engine/item.py              DownloadItem / Segment dataclasses (persisted to downloads.json)
ydm/engine/http_downloader.py   probe → pre-allocate → N worker threads → dynamic split → finalize
ydm/engine/video_downloader.py  yt-dlp wrapper (progress hooks, pause via exception in hook)
ydm/engine/manager.py           queue, max_active slots, scheduler window, speed/ETA, persistence
ydm/engine/limiter.py           shared token-bucket rate limiter
ydm/gui/                        main_window, dialogs (Add, Settings), models (table + progress delegate), theme (QSS + drawn icon)
extension/                      MV3: background.js intercepts downloads, context menus, popup
```

### How a download works
1. `_probe()` sends `GET` with `Range: bytes=0-0`. A `206` with `Content-Range: …/total` means it's resumable.
2. The file is pre-allocated as `<name>.ydm` (`truncate(total)`) and split into `connections` segments, minimum `MIN_SEGMENT` (1 MB).
3. Worker threads call `_next_segment()`. When no segment is free, a worker **steals half of the largest active segment**. This is dynamic segmentation.
4. Each worker writes at `seg.pos` with `seek` + `write`. Progress lives in `Segment.done` and is saved every ~3 s, so a pause or crash can resume.
5. `_finalize()` checks the size, then `os.replace(.ydm → final)`.
6. Servers without range support fall back to `_run_single()` (one connection, restarts from 0 on retry).

### Threading rules
- The engine is plain `threading`. The GUI polls `manager.items()` every 500 ms with a `QTimer`. Never touch Qt widgets from engine threads. Use the `MainWindow.item_event` / `browser_request` signals.
- `HttpDownloader._lock` guards segment boundaries and `item.downloaded`. **Replace `item.segments` with a new list instead of mutating it in place** (the save thread iterates it).
- Pausing = `downloader.stop()` sets an Event. Workers check it per chunk. Status becomes `Paused`.

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
- Start receiving data during the probe (an open-ended first range) instead of a separate probe round-trip
- Detect and restart connections that are much slower than average
- Adaptive connection count per host (back off on HTTP 429 or refused ranges, remember per domain)
- Mirror / multi-source downloads
- Optional aria2c backend
- Per-download speed limit, checksum verification, "shutdown PC when done"
