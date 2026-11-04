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
            for i in range(0, len(body), 128*1024):
                self.wfile.write(body[i:i+128*1024]); time.sleep(0.02)
        except OSError: pass
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"

def pump(sec):
    loop = QEventLoop(); QTimer.singleShot(int(sec*1000), loop.quit); loop.exec()

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
d = w.open_download_window(url=base + "/file.zip"); pump(1.5)
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
c = w.open_download_window(url=base + "/other.iso"); pump(1.0)
cid = m.get(c.item_id); part = cid.part_path; c.close(); pump(1.5)
assert m.get(cid.id) is None and not os.path.exists(part), "cancel left something behind"
print("prestart / confirm / rename / cancel OK")
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
rw = w.open_download_window(url=base + "/remember.bin"); pump(0.6)
rw.dir_edit.setText(os.path.join(os.environ["APPDATA"], "always")); rw.remember_box.setChecked(True); rw._start(True)
assert s.remember_dir.endswith("always") and m.resolve_dir(pre) == s.remember_dir
# running download: Later parks it (paused, in the list); Cancel removes it
pump(0.2); rw.close(); s.remember_dir = ""
# a server asking for a login: the download window shows the login row
from ydm.engine.item import LOGIN_NEEDED
lw = w.open_download_window(url=base + "/needs-login.bin"); pump(1.0)
li = m.get(lw.item_id); m.pause(li.id); m._wait_stopped(li.id)          # stand in for a server's 401
li.status, li.error = Status.ERROR, f"{LOGIN_NEEDED}: enter a user name and password"
pump(0.4); assert lw.form.isRowVisible(4), "login row didn't appear"; lw.grab().save(f"{out}/login.png"); lw.close()
assert find_urls("ftp://files.example/a.iso") == ["ftp://files.example/a.iso"]
print("theme / connections / login OK")
server.stop(); m.shutdown()
print("GUI OK")
