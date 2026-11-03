# YDM — Download Manager

A personal IDM-style download manager for Windows, written from scratch in Python.

**Features**
- Multi-connection downloads (8 by default, up to 32) with **dynamic segmentation** — a finished connection takes half of the slowest remaining part, so speed stays high to the end
- **Pause / resume**, survives app restarts and crashes; auto-retry on network drops
- **Queue** with a limit on simultaneous downloads, plus a **scheduler** (e.g. 02:00 → 08:00, chosen days)
- Global **speed limit**
- **Video grabber** (yt-dlp): YouTube and 1,800+ sites, quality picker
- **Browser extension** for Chrome/Edge: catches downloads automatically, right-click "Download with YDM", "Grab video from this tab"; forwards cookies so logged-in downloads work
- Auto-sort into category folders (Video, Music, Compressed, Documents, Programs, Images), tray icon, notifications, clipboard watcher

## 1. Run it

Requires **Python 3.10+** (python.org, tick "Add to PATH").

Double-click **`run.bat`**. The first run creates a virtual environment and installs dependencies.

Or manually:
```
pip install -r requirements.txt
python main.py
```

**FFmpeg (recommended for video):** YouTube serves 1080p+ as separate video and audio streams, which need FFmpeg to merge.
```
winget install Gyan.FFmpeg
```
Without FFmpeg the grabber still works, but is limited to combined formats (usually up to 720p).

## 2. Install the browser extension

1. Open `chrome://extensions` (or `edge://extensions`)
2. Turn on **Developer mode**
3. Click **Load unpacked** and select the `extension` folder
4. Pin the YDM icon; the popup should show **Connected** while the app is running

If YDM isn't running, downloads stay in the browser as normal.

## 3. Build a standalone .exe (optional)

Double-click **`build.bat`** → `dist\YDM\YDM.exe`. Put a shortcut in `shell:startup` with `--minimized` to start it with Windows in the tray.

## Make it yours

| What | Where |
|---|---|
| App name | `APP_NAME` / `APP_TITLE` in `ydm/config.py` (also rename it in `extension/manifest.json` and the `"YDM"` check in `background.js`) |
| Accent colour | `ACCENT` in `ydm/config.py`, then the colours in `ydm/gui/theme.py` |
| Categories / extensions | `CATEGORIES` in `ydm/config.py` |
| Video sites auto-detected in the Add dialog | `VIDEO_SITES` in `ydm/gui/dialogs.py` |
| Segment size, retries | `MIN_SEGMENT`, `MAX_RETRIES` in `ydm/engine/http_downloader.py` |

After changing the colour, run `python make_icons.py` to redraw the app and extension icons.

## How it's built

```
main.py                      app start, single instance, local server
ydm/config.py                settings + categories
ydm/server.py                127.0.0.1:9614 endpoint for the extension (/ping, /add)
ydm/engine/http_downloader.py  probe → split → parallel Range requests → dynamic split → finalize
ydm/engine/video_downloader.py yt-dlp wrapper with progress + pause
ydm/engine/manager.py          queue, scheduler, speed calc, persistence (downloads.json)
ydm/engine/limiter.py          token-bucket speed limiter shared by all connections
ydm/gui/                       PySide6 window, dialogs, dark theme, progress bars
extension/                     Chrome/Edge MV3 extension
```

Data lives in `%APPDATA%\YDM` (`settings.json`, `downloads.json`). Unfinished files are saved as `name.ext.ydm` next to the final file.

**Security notes:** the local endpoint only listens on 127.0.0.1 and rejects requests from web pages (only extensions and local programs are accepted). Cookies forwarded by the extension are stored in `downloads.json` until the download is removed from the list.

**Please use it responsibly.** Only download content you have the right to download, and respect each site's terms.
