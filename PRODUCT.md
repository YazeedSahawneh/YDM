# Product

<!-- impeccable:product-schema 1 -->

## Platform

Windows desktop (PySide6 / Qt Widgets) plus a Chromium MV3 browser extension. Not a web product; the extension popup is the only HTML surface.

## Users
Yazeed, plus friends and family he installs it for. Mixed technical skill: power-user density is fine for Yazeed, but first run, wording and defaults must make sense to someone who never opens Settings.

## Product Purpose
A personal IDM-style download manager: grab files and videos from the browser or a pasted link and pull them down as fast as the connection allows, with resume, queue, scheduler and categories. Success = downloads finish faster than the browser would, and nobody has to copy a link by hand.

## Positioning
Clean-room, free, personal. Multi-connection segmented engine with dynamic splitting, a built-in yt-dlp video grabber, and a browser extension that hands downloads over automatically. Updates itself from GitHub Releases.

## Operating Context
Runs in the tray all day; the window is opened to check progress, pick a video quality, or find a finished file. Most downloads arrive from the browser extension rather than typed URLs. Long downloads (ISOs, games, videos) are watched for speed and ETA.

## Capabilities and Constraints
- Engine: segmented HTTP with resume, dynamic segment stealing, shared rate limiter, scheduler window, max-active queue.
- Video: yt-dlp (YouTube and ~1800 sites). Needs FFmpeg for merged best quality and a JS runtime (Deno) for YouTube.
- Local server 127.0.0.1 only, extension-origin check must stay.
- Clean room: no IDM code/assets, no DRM or paywall bypass.
- Update channel: GitHub Releases of a public repo (repo name not decided yet; `UPDATE_REPO` in config.py).

## Brand Commitments
Name "YDM". Accent colour and branding centralised in `ydm/config.py` (`APP_NAME`, `ACCENT`) and `extension/manifest.json`.

## Product Principles
1. Speed is visible: show throughput and the connections doing the work, not just a percentage.
2. Zero friction: the browser hands things over; dialogs appear only when a real choice exists (video quality).
3. Plain words: errors and statuses read like a person wrote them.
4. Never lose progress: pause, crash, update or restart all resume.
