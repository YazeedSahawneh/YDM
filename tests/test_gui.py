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
from ydm.gui.dialogs import AddDownloadDialog, SettingsDialog
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

d = AddDownloadDialog(s, base + "/file.zip"); d.show(); pump(1.5)
print("dialog info:", d.info.text(), "| placeholder:", d.name_edit.placeholderText())
d.grab().save(f"{out}/add.png"); d.close()
sd = SettingsDialog(s, w, 2); sd.show(); pump(0.3); sd.grab().save(f"{out}/settings.png"); sd.close()
w.sidebar.setCurrentRow(1); pump(0.6); print("Downloading filter rows:", len(w.model.ids))
w.sidebar.setCurrentRow(0)
server.stop(); m.shutdown()
print("GUI OK")
