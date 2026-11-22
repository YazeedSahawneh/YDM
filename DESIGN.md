# YDM design system

Studio-rack hi-fi, applied to a standard Windows download manager layout (IDM topology: menu bar, big icon-over-label toolbar, category tree, download list, details pane). The world contributes palette, type, density and one signature move. Layout and controls stay native and familiar.

Source of truth in code: `ydm/gui/theme.py` (tokens + QSS), `ydm/gui/icons.py` (glyphs), `ydm/gui/models.py` (`draw_meter`), `ydm/gui/widgets.py`. Extension popup mirrors the tokens in `extension/popup.html` `:root`.

## Signature move: the level meter
Every download's progress is a HUD gauge (`gui/hudbar.py`): upright cells between two thin brackets, each cell one equal slice of the file. A cell's brightness is how much of that slice has actually arrived, taken from the real segment map, and the cell where a live connection is writing is mixed 28% toward white, so the parallel connections show up as separate hot points spreading across the bar. The whole gauge takes one colour from `theme.progress_color()`: by default it shifts with the percentage (HSV hue 4° → 108°, red → amber → yellow → green); the user can pin it to the signal colour instead. Error is always the error ink, finished the finished ink, paused/queued are desaturated. No glow: flat cells only (the user tried a faint one and preferred it without). The big version (download window, details pane) adds a 10 % tick scale, a small triangle under every working connection, the percentage on the right in the bar's colour, and a light sweep across the cells while bytes arrive (40 ms timer, only while running). Size unknown: a scanner sweeps back and forth. The toolbar's total-speed meter is log-scaled (64 KB/s → 256 MB/s) with a warm-white peak-hold block that holds for 2.5 s, then falls back.

Theme colours are user-selectable (Options → Appearance: presets in `theme.PRESETS` plus custom pickers). `theme.set_colors()` derives SELECT, ON_ACCENT and the hover/pressed shades from the chosen accent and rebuilds the QSS.

Unknown size: a single lit block sweeps across (searching for signal). Completed: all blocks lit lime at 50%.

## Colour
Strategy: restrained ground, committed state inks. Dark, because the app sits in the tray all day and is glanced at next to other dark tools. Pick it again if the use scene changes.

| Token | Value | Use |
|---|---|---|
| BG | `#121417` | graphite ground: lists, dialogs |
| PANEL | `#191C20` | brushed panel: toolbar, sidebar, menus, details pane, title bar |
| RAISED | `#22262B` | inputs, hover |
| LINE | `#2C3137` | hairlines (1px only) |
| TEXT | `#ECE8E1` | warm-white silk-screen |
| MUTED | `#969BA2` | secondary text, legends (≥ 6:1 on BG) |
| FAINT | `#5E646C` | disabled only |
| UNLIT | `#262B31` | empty meter block |
| SELECT | `#2B2721` | selected row (amber-tinted graphite) |
| ACCENT | `#FFB547` | **only** things you can press: primary buttons, checked toggles, focus rings, Add URL |
| ON_ACCENT | `#1A1408` | text on amber |
| SIGNAL | `#3DDBC0` | meter light |

Fixed state inks, the same everywhere (list status, meter, details, extension):
Downloading `#3DDBC0` · Connecting `#2E9C8A` · Processing `#B49CFF` · Paused `#9AA7B8` · Queued `#7A828C` · Completed `#A6E35A` · Error `#FF6B5E`.

## Type
One family: Segoe UI (system). Body 9.5 pt. Titles 13 pt semibold. Panel legends: 7–7.5 pt demibold, uppercase, +0.9 px tracking (`theme.label_font`), used for column headers and detail labels. Numbers (sizes, speeds, times) use tabular figures (`theme.number_font`). No display faces, no monospace.

## Shape and depth
Radius 6 px for controls, 4–5 px for list and nav items, 1.2–1.5 px for meter blocks. Elevation is a 1 px hairline or a panel tone step, never both. No gradients except the app icon tile.

## Icons
Drawn in code on a 24-unit grid, 1.7 stroke, round caps (`icons.py`). Tinted MUTED in navigation, TEXT in the toolbar, ACCENT for Add URL. No emoji or unicode glyphs as icons.

## Motion
Product-speed only. Menu, combo and tooltip animations are disabled (`main.py`), so menus open instantly. The list refreshes every 400 ms: cells fill and connection heads move with real data. The one decorative motion is the big gauge's sweep while a download runs. The extension button gets a 150 ms border/lift on hover.

## Designs
Options → Appearance offers three designs (`theme.DESIGNS`); each sets its own grounds over the dark/light tokens, the corner radius, list row height, text font, progress bar and icon style. Status inks and the user's accent/signal stay the same across all three.
- **Studio** (default): everything described above. Radius 6, rows 34, HUD cells, line icons (1.7 strokes).
- **Simple**: neutral greys without the graphite tint (dark #141414 / light #FFFFFF), radius 4, rows 30, a 4 px (6 px big) rounded line that fills smoothly, thin icons (1.15 strokes, solid shapes outlined, dots stay solid).
- **Modern**: cool slate (dark #0E1320 / light #EEF2F9), radius 10 (sidebar items rounded too), rows 40, Segoe UI Variable Text, a 12 px (16 px big) pill with a light-to-colour gradient and, on the big bar while bytes arrive, white stripes drifting right; duotone icons (a 28 % tint inside every closed outline).
Only the HUD gauge shows the individual connections; the line and the pill stay calm. Settings pages scroll when they're taller than the window.

## Screenshot editor (extension)
`shot.html` uses the extension's own tokens (graphite, amber pressables, teal signal). Header: page title and size, then Copy · PNG (primary) · JPG · PDF with a page-size menu. Tool rail: icon tools in the app's drawing (24 grid, 1.7 strokes), seven colour swatches plus any colour, three sizes, undo/redo, zoom. The picture sits on a near-black stage with a soft shadow; only the part on screen is drawn, so very long pages stay smooth.

## Components
- Toolbar: icon over label, transparent until hover (RAISED + LINE). Add URL in amber text.
- Sidebar: category tree with muted counts; selected = SELECT.
- List rows 34 px; unseen finished files keep a lime LED (with soft halo) at the end of the name cell until selected.
- Banner: one line above the list for updates and tools, with a 4 px SIGNAL progress line and an amber primary action.
- Window edges (`gui/frame.py`): YDM's own title strip, Windows keeps the frame's shadow, rounded corners, Snap and resizing. Main window: 34 px PANEL strip with the icon and the menus, caption buttons 46 px wide (Windows 11 glyphs, 1 px strokes; close turns #C42B1C on hover). Download window and dialogs: 32 px BG strip with the icon and a muted title (the download window's title is live: "42 % · 3.1 MB/s", "Download complete"); text dims to FAINT when the window is inactive. The Windows 11 border takes the EDGE token.
- List strip above the downloads: the filter's name, its count, and amber flat buttons on the right (*Clear N missing* only when there are some, *Compact* / *Full view*). Compact view hides the sidebar, toolbar, menus, details pane, status bar and the Size / Time left / Added columns; the title strip then shows "YDM · N downloading · speed".
- Dialogs: YDM's title strip, 22 px margins, hairline above the button row, primary button rightmost.
- Settings: left nav list + pages with a title and a one-line plain-language blurb.
