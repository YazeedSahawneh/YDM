# YDM design system

Studio-rack hi-fi, applied to a standard Windows download manager layout (IDM topology: menu bar, big icon-over-label toolbar, category tree, download list, details pane). The world contributes palette, type, density and one signature move. Layout and controls stay native and familiar.

Source of truth in code: `ydm/gui/theme.py` (tokens + QSS), `ydm/gui/icons.py` (glyphs), `ydm/gui/models.py` (`draw_meter`), `ydm/gui/widgets.py`. Extension popup mirrors the tokens in `extension/popup.html` `:root`.

## Signature move: the level meter
Every download's progress is a HUD gauge (`gui/hudbar.py`): slanted cells between two thin brackets, each cell one equal slice of the file. A cell's brightness is how much of that slice has actually arrived, taken from the real segment map, and the cell where a live connection is writing is mixed 28% toward white, so the parallel connections show up as separate hot points spreading across the bar. The whole gauge takes one colour from `theme.progress_color()`: by default it shifts with the percentage (HSV hue 4° → 108°, red → amber → yellow → green); the user can pin it to the signal colour instead. Error is always the error ink, finished the finished ink, paused/queued are desaturated. A two-step soft glow sits behind the lit stretch. The big version (download window, details pane) adds a 10 % tick scale, a small triangle under every working connection, the percentage on the right in the bar's colour, and a light sweep across the cells while bytes arrive (40 ms timer, only while running). Size unknown: a scanner sweeps back and forth. The toolbar's total-speed meter is log-scaled (64 KB/s → 256 MB/s) with a warm-white peak-hold block that holds for 2.5 s, then falls back.

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

## Components
- Toolbar: icon over label, transparent until hover (RAISED + LINE). Add URL in amber text.
- Sidebar: category tree with muted counts; selected = SELECT.
- List rows 34 px; unseen finished files keep a lime LED (with soft halo) at the end of the name cell until selected.
- Banner: one line above the list for updates and tools, with a 4 px SIGNAL progress line and an amber primary action.
- Dialogs: dark title bar (`apply_window_chrome`), 22 px margins, hairline above the button row, primary button rightmost.
- Settings: left nav list + pages with a title and a one-line plain-language blurb.
