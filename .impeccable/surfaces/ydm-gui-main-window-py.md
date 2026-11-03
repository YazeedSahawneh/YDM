---
version: 1
slug: "ydm-gui-main-window-py"
primary_target: "ydm/gui/main_window.py"
related_targets: ["ydm/gui/dialogs.py","ydm/gui/models.py","extension/popup.html"]
---

Scope: YDM main window, add-download dialog, settings, details pane, tray menus, extension popup. Mode: Operate. Audience: Yazeed plus family; job: watch fast downloads, grab videos, find finished files. User note: "make it look like Internet Download Manager and smooth like it, so fast to download and every menu fast to open and close."

## Direction contract
THESIS: Speed made visible. Each download is a segmented level meter whose lit blocks are the bytes its connections have filled, so the engine's parallel work shows instead of a flat percentage bar. Refuses the default of a grey list with one blue progress bar.
OWN-WORLD: Studio-rack hi-fi. Graphite anodised ground, a slightly lighter brushed panel for the toolbar, hairline rules, warm-white silk-screen small-caps labels, tabular numerals. Signal teal lights the meters. Amber is kept only for pressable controls. Fixed state inks: teal = downloading, violet = processing, steel = paused, muted = queued, lime = completed, red = error. They are the same everywhere (list, sidebar counts, details pane, tray).
STORY: The user sees what is moving and how fast, picks a video quality when asked, and opens finished files. Everything else stays out of the way.
FIRST VIEWPORT: IDM topology. A big icon-over-label toolbar (Add URL primary amber, Resume, Stop, Stop all, Delete, Queue, Scheduler, Grabber, Options). Right of the toolbar, a total-speed readout with a peak-hold meter. Left, a category tree with counts. Centre, the downloads list: Name, Size, Progress meter, Speed, Time left, Status, Added. Bottom, a details pane with the selected file's segment map and connection table. Finished-but-unseen rows stay highlighted until selected.
FORM: candidate 6 of 7 (hi-fi front-panel level meters); seed:d874d3970257a45e. Raises: action-only accent (from the warm consumer app); fixed state-ink language (from the orienteering map); unseen rows stay lit (from the gate board).
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
