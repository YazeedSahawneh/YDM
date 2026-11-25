import os, sys, time, threading, re, json, tempfile, urllib.request
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QEventLoop
from ydm.config import Settings
from ydm.engine import DownloadManager, Status
from ydm.gui import theme
from ydm.gui.main_window import MainWindow
from ydm.gui.dialogs import SettingsDialog
from ydm.server import LocalServer

DATA = os.urandom(40 * 1024 * 1024)
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_GET(self):
        rng = self.headers.get("Range"); a, b = 0, len(DATA) - 1
        if rng:
            x, y = re.match(r"bytes=(\d+)-(\d*)", rng).groups(); a = int(x); b = int(y) if y else b
            self.send_response(206); self.send_header("Content-Range", f"bytes {a}-{b}/{len(DATA)}")
        else: self.send_response(200)
        body = DATA[a:b+1]
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        try:
            pause = 0.1 if "/slow/" in self.path else 0.02       # /slow/: still running when the test looks
            for i in range(0, len(body), 128*1024):
                self.wfile.write(body[i:i+128*1024]); time.sleep(pause)
        except OSError: pass
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"

def pump(sec):
    loop = QEventLoop(); QTimer.singleShot(int(sec*1000), loop.quit); loop.exec()

# 1.6: the old default folder (Downloads\YDM, sorted by category) becomes Downloads itself; a folder the user picked stays
from ydm.config import OLD_DEFAULT_DIR, default_download_dir
Settings(download_dir=str(OLD_DEFAULT_DIR), use_category_folders=True).save()
mig = Settings.load()
assert mig.download_dir == str(default_download_dir()) and not mig.use_category_folders and mig.layout_version == 1
Settings(download_dir=os.path.join("D:" + os.sep, "Mine"), use_category_folders=True).save()
keep = Settings.load(); assert keep.download_dir.endswith("Mine") and keep.use_category_folders and keep.layout_version == 1
os.remove(Settings.path())
assert Settings().download_dir == str(default_download_dir()) and not Settings().use_category_folders

app = QApplication(sys.argv); app.setStyle("Fusion"); app.setStyleSheet(theme.QSS)
s = Settings(download_dir=os.path.join(os.environ["APPDATA"], "dl"), show_add_dialog=False)
m = DownloadManager(s); w = MainWindow(m, s); w.show()
server = LocalServer(19614, lambda p: w.browser_request.emit(p)); server.start()
out = tempfile.mkdtemp(prefix="ydm-shots-"); print("screenshots in", out)
w.grab().save(f"{out}/empty.png")

# extension-style request
def post(origin):
    req = urllib.request.Request("http://127.0.0.1:19614/add", method="POST",
        data=json.dumps({"url": base + "/ubuntu-24.04-desktop-amd64.iso", "userAgent": "UA"}).encode(),
        headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try: return urllib.request.urlopen(req, timeout=3).status
    except urllib.error.HTTPError as e: return e.code
print("ping:", urllib.request.urlopen("http://127.0.0.1:19614/ping").read())
print("from website origin ->", post("https://evil.example"))
print("from extension origin ->", post("chrome-extension://abc"))
pump(0.3)
for name in ("setup.exe", "song.mp3", "report.pdf"):
    m.add(base + "/" + name, filename=name, start=False)
it2 = m.items()[1]; m.queue(it2.id)
m.add(base + "/broken", filename="old-movie.mkv", start=False).status = Status.PAUSED
pump(3.5)
print([ (i.filename, i.status, f"{i.progress:.0%}") for i in m.items()])
w.grab().save(f"{out}/running.png")

# IDM-style pre-start: the window opens and the file is already downloading before "Start"
d = w.open_download_window(url=base + "/slow/file.zip"); pump(1.5)
pre = m.get(d.item_id)
print("prestart:", d.live.text(), "| placeholder:", d.name_edit.placeholderText())
assert pre and pre.pending and pre.downloaded > 0, "did not start before confirming"
assert pre.id not in w.model.ids, "pending download leaked into the list"
d.grab().save(f"{out}/add.png")
d.name_edit.setText("renamed.zip"); d._start(True); pump(0.6)
assert not pre.pending and pre.id in w.model.ids and d.pages.currentIndex() == 1
d.grab().save(f"{out}/progress.png")
d.conn_spin.setValue(3); assert pre.connections == 3, "window's connection count didn't reach the download"
for _ in range(60):
    if pre.status == Status.COMPLETED: break
    pump(0.5)
assert pre.filename == "renamed.zip" and os.path.exists(pre.path), (pre.status, pre.filename)
pump(0.4); d.grab().save(f"{out}/done.png"); d.close()
# cancelling a pre-started download removes it and its partial file
c = w.open_download_window(url=base + "/slow/other.iso"); pump(1.0)
cid = m.get(c.item_id); part = cid.part_path; c.close(); pump(1.5)
assert m.get(cid.id) is None and not os.path.exists(part), "cancel left something behind"
print("prestart / confirm / rename / cancel OK")

def wait_for(cond, sec=20):
    for _ in range(int(sec / 0.25)):
        if cond(): return True
        pump(0.25)
    return cond()

# a file that finishes before Start is pressed: the window moves on to "Download complete" by itself
q = w.open_download_window(url=base + "/quick.bin")
assert wait_for(lambda: q.pages.currentIndex() == 1), "the window waited for Start on a finished file"
qi = m.get(q.item_id); pump(0.3)
assert qi.status == Status.COMPLETED and not qi.pending, (qi.status, qi.pending)
assert wait_for(lambda: qi.id in w.model.ids, 2), "confirmed download isn't in the list"
assert q.quiet_box.isVisible() and not q.open_box_p.isVisible(), "finish screen controls"
assert q.titlebar.label.text() == "Download complete" and q.windowTitle() == "quick.bin", q.titlebar.label.text()
q.grab().save(f"{out}/auto-done.png")
# "Don't show this window when a download finishes": the next window closes by itself once it's done
q.quiet_box.setChecked(True); assert s.finish_window is False; q.close()
q2 = w.open_download_window(url=base + "/quick2.bin")
assert wait_for(lambda: q2 not in w._windows), "window stayed although finish_window is off"
s.finish_window = True
# Open when done: the file opens and the window gets out of the way
import ydm.gui.download_window as dwm, ydm.gui.main_window as mwm
opened = []; real_open = dwm.open_path; dwm.open_path = mwm.open_path = opened.append
q3 = w.open_download_window(url=base + "/quick3.bin"); q3.open_box.setChecked(True)
assert m.get(q3.item_id).open_when_done and q3.open_box_p.isChecked()
assert wait_for(lambda: q3 not in w._windows) and len(opened) == 1 and opened[0].endswith("quick3.bin"), opened
# a window hidden while its download runs comes back when it's done; no Windows notification on the side
toasts = []; real_toast = w.tray.showMessage; w.tray.showMessage = lambda *a: toasts.append(a)
h = w.open_download_window(url=base + "/slow/hidden.bin"); pump(0.4); h._start(True); hid = h.item_id; h.close()
assert w.window_for(hid) is None and hid in w._watched
assert wait_for(lambda: w.window_for(hid) is not None, 30), "finished download's window didn't come back"
back = w.window_for(hid); pump(0.3)
assert back.pages.currentIndex() == 1 and back.btn_open.isVisible() and not toasts, toasts
back.close()
# hidden + Open when done: no window, the file just opens
h2 = w.open_download_window(url=base + "/slow/hidden2.bin"); pump(0.3); h2.open_box.setChecked(True)
h2._start(True); hid2 = h2.item_id; h2.close()
assert wait_for(lambda: m.get(hid2).status == Status.COMPLETED, 30); pump(0.5)
assert w.window_for(hid2) is None and opened[-1].endswith("hidden2.bin"), opened
dwm.open_path = mwm.open_path = real_open; w.tray.showMessage = real_toast
# compact view: the list only, in a small window; the button above the list brings the full one back
w.btn_view.click(); pump(0.3)
assert w._compact and s.compact_view and not w.side.isVisible() and not w.toolbar.isVisible()
from ydm.gui.models import COL_SIZE, COL_SPEED
assert w.table.isColumnHidden(COL_SIZE) and not w.table.isColumnHidden(COL_SPEED) and w.btn_add_c.isVisible()
assert "downloading" in w.titlebar.label.text(); w.grab().save(f"{out}/compact.png")
w.btn_view.click(); pump(0.2)
assert not w._compact and not s.compact_view and w.side.isVisible() and not w.table.isColumnHidden(COL_SIZE)
# Clear missing: finished downloads whose file was deleted leave the list (others stay)
gone = m.get(hid); os.remove(gone.path); m.check_files(); w.refresh()
assert w.btn_missing.isVisible() and "1 missing" in w.btn_missing.text() and w.a_clear_missing.isEnabled()
w.grab().save(f"{out}/missing.png")
w.clear_missing(); assert m.get(hid) is None and m.get(hid2) is not None and not w.btn_missing.isVisible()
print("finish window / open when done / compact view / clear missing OK")
sd = SettingsDialog(s, w, 2); sd.show(); pump(0.3); sd.grab().save(f"{out}/settings.png"); sd.close()
w.sidebar.setCurrentItem(w._tree_items["Downloading"]); pump(0.6); print("Downloading filter rows:", len(w.model.ids))
w.sidebar.setCurrentItem(w._tree_items["All downloads"])
w.table.selectRow(0); pump(0.8); w.grab().save(f"{out}/details.png")
assert w.details.item is not None, "details pane did not follow the selection"
from ydm.gui.dialogs import BatchDialog, find_urls
assert find_urls("see https://a.example/x.zip, and https://b.example/y.iso.") == ["https://a.example/x.zip", "https://b.example/y.iso"]
bd = BatchDialog(["https://a.example/x.zip", "https://b.example/y.iso"], w); bd.show(); pump(0.2)
assert len(bd.urls()) == 2; bd.grab().save(f"{out}/batch.png"); bd.close()
for tab in (4, 5, 6, 7):
    sd = SettingsDialog(s, w, tab); sd.show(); pump(0.2); sd.grab().save(f"{out}/settings{tab}.png"); sd.close()
vd = w.open_download_window(kind="video"); pump(0.3); vd.grab().save(f"{out}/add-video.png"); vd.close()
# connections: automatic by default, the fixed count only when it's off; start-with-Windows untouched unless changed
sd = SettingsDialog(s, w, 1); sd.show(); pump(0.1)
assert sd.auto_conn.isChecked() and not sd.conn.isEnabled()
sd.auto_conn.setChecked(False); assert sd.conn.isEnabled()
assert sd.updated_settings().auto_connections is False
sd.auto_conn.setChecked(True); assert sd.updated_settings().auto_connections is True
assert sd.startup_box.isChecked() == sd._startup_was; sd.close()
# mirrors dialog: lists every server's state, takes new addresses
from ydm.gui.dialogs import MirrorsDialog
from ydm.engine import DownloadItem
mit = DownloadItem(url="https://a.example/f.iso", mirrors=["https://b.example/f.iso"])
mit.sources = [{"host": "a.example", "url": "https://a.example/f.iso", "state": "ok", "note": "", "limit": 2,
                "primary": True},
               {"host": "b.example", "url": "https://b.example/f.iso", "state": "bad",
                "note": "a different file (the bytes don't match)", "limit": 0}]
md = MirrorsDialog(mit, w); md.show(); pump(0.2); md.grab().save(f"{out}/mirrors.png")
assert md.status.count() == 2 and "takes 2 connections" in md.status.item(0).text()
md.edit.setPlainText("https://b.example/f.iso\nhttps://c.example/f.iso  junk"); assert md.urls() == [
    "https://b.example/f.iso", "https://c.example/f.iso"]; md.close()
# a post with photos: "All N photos" in the quality list hands off to save_post
from ydm.engine import gallery
gd = w.open_download_window(kind="video"); pump(0.1)
gd._on_info(gd._ticket, {"title": "Saturn", "uploader": "NASA", "options": [("All 3 photos", gallery.OPTION, 0, "")]})
asked = []
w.save_post = lambda url, headers, folder: asked.append(url)
gd.url_edit.blockSignals(True); gd.url_edit.setText("https://www.instagram.com/p/C1/"); gd._start(True)
assert asked == ["https://www.instagram.com/p/C1/"], asked
del w.save_post
# theme colours switch in place, Cancel in Appearance restores them
sd = SettingsDialog(s, w, 6); sd.show(); pump(0.1)
sd.theme_preview.connect(w.apply_theme)
sd._pick_colors(*theme.PRESETS["Rose"]); assert theme.ACCENT == theme.PRESETS["Rose"][0] and theme.ACCENT in app.styleSheet()
sd.reject(); assert theme.ACCENT == s.accent.upper(), "Cancel didn't restore the theme"
# designs: Studio / Simple / Modern switch in place; the bar and the icons can be picked on their own
sd = SettingsDialog(s, w, 6); sd.show(); pump(0.1)
sd.theme_preview.connect(w.apply_theme)
designs = {b.property("design"): b for b in sd.design_group.buttons()}
assert set(designs) == {"studio", "simple", "modern", "terminal", "ocean"}
designs["modern"].click(); pump(0.2)
assert (theme.DESIGN, theme.BAR, theme.ICONS, theme.RADIUS) == ("modern", "pill", "duotone", 10), theme.DESIGN
assert w.table.verticalHeader().defaultSectionSize() == theme.ROW == 40 and theme.BG == theme.DESIGNS["modern"]["grounds"]["dark"]["BG"]
assert "border-radius: 10px" in app.styleSheet()
w.grab().save(f"{out}/design-modern.png")
sd.bar_style.setCurrentIndex(sd.bar_style.findData("cells")); pump(0.1)
assert theme.BAR == "cells" and theme.DESIGN == "modern", "picking a bar on its own"
sd.icon_style.setCurrentIndex(sd.icon_style.findData("thin")); pump(0.1); assert theme.ICONS == "thin"
designs["simple"].click(); pump(0.2)
assert (theme.DESIGN, theme.BAR, theme.ICONS) == ("simple", "line", "thin"), (theme.DESIGN, theme.BAR, theme.ICONS)
assert sd.bar_style.currentIndex() == 0 and sd.icon_style.currentIndex() == 0, "a new design brings its own bar and icons"
assert sd.bar_style.itemText(0) == "As the design (Thin line)"
w.grab().save(f"{out}/design-simple.png")
designs["terminal"].click(); pump(0.2)
assert (theme.DESIGN, theme.BAR, theme.RADIUS) == ("terminal", "blocks", 0) and "border-radius: 0px" in app.styleSheet()
assert theme.FONT_FAMILY in ("Cascadia Mono", "Consolas", "Segoe UI")
w.grab().save(f"{out}/design-terminal.png")
designs["ocean"].click(); pump(0.2)
assert (theme.DESIGN, theme.BAR, theme.ICONS) == ("ocean", "wave", "duotone") and theme.BG == theme.DESIGNS["ocean"]["grounds"]["dark"]["BG"]
sd.bar_style.setCurrentIndex(sd.bar_style.findData("beads")); pump(0.1); assert theme.BAR == "beads"
designs["simple"].click(); pump(0.2)
look = sd._look()
assert look[4:] == ("simple", "", "") and theme.ROW == 30
sd.reject(); pump(0.1)
assert (theme.DESIGN, theme.BAR, theme.ICONS) == ("studio", "cells", "line"), "Cancel puts the design back"
# the three bars paint at every state without errors (list, big window bar, preview)
from ydm.gui import hudbar
from PySide6.QtGui import QImage, QPainter as _QP
from PySide6.QtCore import QRectF
img = QImage(400, 40, QImage.Format_ARGB32)
for bar in theme.BAR_STYLES:
    theme.BAR = bar
    for it in m.items():
        for big in (False, True):
            pp = _QP(img); hudbar.draw_bar(pp, QRectF(0, 10, 380, 14), it, big=big); pp.end()
    pp = _QP(img); hudbar.draw_bar(pp, QRectF(0, 10, 380, 14), None, fraction=0.4, status=Status.PAUSED); pp.end()
theme.set_design("studio")
# light mode switches in place and back
w.apply_theme(s.accent, s.signal, s.bar_colors, "light"); pump(0.3)
assert theme.MODE == "light" and theme.BG in app.styleSheet(); w.grab().save(f"{out}/light.png")
lw2 = w.open_download_window(item_id=pre.id); pump(0.4); lw2.grab().save(f"{out}/light-window.png")
assert lw2.btn_with.isVisible() and lw2.btn_save.isVisible() and not lw2.btn_cancel.isVisible()
assert pre.url in lw2.addr.toolTip(); lw2.close()
w.apply_theme(s.accent, s.signal, s.bar_colors, "dark"); assert theme.MODE == "dark"
# Save as on a finished file moves it now
newdir = os.path.join(os.environ["APPDATA"], "moved"); m.rename(pre.id, "final-name.zip", newdir)
assert pre.path == os.path.join(newdir, "final-name.zip") and os.path.exists(pre.path), pre.path
# remember this folder: the next downloads go there
rw = w.open_download_window(url=base + "/slow/remember.bin"); pump(0.6)
rw.dir_edit.setText(os.path.join(os.environ["APPDATA"], "always")); rw.remember_box.setChecked(True); rw._start(True)
assert s.remember_dir.endswith("always") and m.resolve_dir(pre) == s.remember_dir
# running download: Later parks it (paused, in the list); Cancel removes it
pump(0.2); rw.close(); s.remember_dir = ""
# a server asking for a login: the download window shows the login row
from ydm.engine.item import LOGIN_NEEDED
lw = w.open_download_window(url=base + "/slow/needs-login.bin"); pump(1.0)
li = m.get(lw.item_id); m.pause(li.id); m._wait_stopped(li.id)          # stand in for a server's 401
li.status, li.error = Status.ERROR, f"{LOGIN_NEEDED}: enter a user name and password"
pump(0.4); assert lw.form.isRowVisible(4), "login row didn't appear"; lw.grab().save(f"{out}/login.png"); lw.close()
assert find_urls("ftp://files.example/a.iso") == ["ftp://files.example/a.iso"]
print("theme / connections / login OK")

# 1.9.1: the same link from the browser twice in a row is one download (Pixabay sent it twice)
s.progress_window = False
n0 = len(m.items())
dup = {"url": base + "/slow/twice.mp3", "kind": "file", "filename": "twice.mp3", "quick": True}
w.handle_browser_request(dict(dup)); w.handle_browser_request(dict(dup)); pump(0.2)
assert len(m.items()) == n0 + 1, [i.url for i in m.items()][-3:]
w._from_browser[("file", dup["url"])] = (time.monotonic() - 11, None)          # later on: a new download again
w.handle_browser_request(dict(dup)); pump(0.2)
assert len(m.items()) == n0 + 2
s.progress_window = True
for it in [i for i in m.items() if "twice.mp3" in i.url]:
    m.remove(it.id)
print("same link twice = one download OK")

# 1.9.1: drag a box over the list to select several downloads
from PySide6.QtTest import QTest
from PySide6.QtCore import QPoint, Qt as _Qt
w.show_normal(); w.resize(1300, 1150); pump(0.4)
w.sidebar.setCurrentItem(w.sidebar.topLevelItem(0)); pump(0.3)
for i in range(6):
    m.add(f"{base}/box{i}.bin", start=False)
w.refresh(); pump(0.3)
t, vp = w.table, w.table.viewport()
t.scrollToTop(); t.clearSelection(); pump(0.1)
picked = lambda: sorted({i.row() for i in t.selectionModel().selectedRows()})
a, b = t.visualRect(w.model.index(0, 1)).center(), t.visualRect(w.model.index(3, 1)).center()
QTest.mousePress(vp, _Qt.LeftButton, _Qt.NoModifier, a); pump(0.05)
QTest.mouseMove(vp, QPoint(a.x() + 30, (a.y() + b.y()) // 2)); pump(0.05)
QTest.mouseMove(vp, QPoint(a.x() + 60, b.y())); pump(0.05)
assert t.selecting and t._band.isVisible()
held = list(w.model.ids); w.refresh(); assert w.model.ids == held          # the list holds still while dragging
w.grab().save(f"{out}/box-select.png")
QTest.mouseRelease(vp, _Qt.LeftButton, _Qt.NoModifier, QPoint(a.x() + 60, b.y())); pump(0.1)
assert picked() == [0, 1, 2, 3] and not t._band.isVisible() and len(w._selected_ids()) == 4, picked()
print("box selection OK")
server.stop(); m.shutdown()
print("GUI OK")
