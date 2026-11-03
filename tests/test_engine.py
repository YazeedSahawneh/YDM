import hashlib, os, sys, threading, time, tempfile, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ydm.config import Settings
from ydm.engine import DownloadManager, Status

DATA = os.urandom(24 * 1024 * 1024 + 12345)
SHA = hashlib.sha256(DATA).hexdigest()

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_GET(self):
        ranges = "norange" not in self.path
        rng = self.headers.get("Range")
        if ranges and rng:
            a, b = re.match(r"bytes=(\d+)-(\d*)", rng).groups()
            a = int(a); b = int(b) if b else len(DATA) - 1
            body = DATA[a:b + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {a}-{b}/{len(DATA)}")
        else:
            body = DATA
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", 'attachment; filename="test file.zip"')
        self.end_headers()
        # throttle a bit so pause can catch it mid-flight
        for i in range(0, len(body), 256 * 1024):
            try:
                self.wfile.write(body[i:i + 256 * 1024])
            except (BrokenPipeError, ConnectionResetError):
                return
            time.sleep(0.004)

srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"

tmp = Path(tempfile.mkdtemp())
s = Settings(download_dir=str(tmp / "dl"), connections=8)
m = DownloadManager(s, store_path=tmp / "store.json")

def wait(item, states, t=60):
    end = time.time() + t
    while time.time() < end:
        if item.status in states: return
        time.sleep(0.1)
    raise SystemExit(f"timeout waiting {states}, now {item.status} {item.error}")

def check(item):
    got = hashlib.sha256(Path(item.path).read_bytes()).hexdigest()
    assert got == SHA, "hash mismatch"
    assert not os.path.exists(item.part_path)

# 1. segmented + pause/resume
it = m.add(base + "/file")
while it.downloaded < 6 * 1024 * 1024: time.sleep(0.05)
print("segments while running:", len(it.segments), "resumable:", it.resumable, "file:", it.filename, "cat:", it.category)
m.pause(it.id); wait(it, {Status.PAUSED})
d1 = it.downloaded; print("paused at", d1)
m.save()
# simulate restart: new manager reading store
m2 = DownloadManager(s, store_path=tmp / "store.json")
it2 = m2.get(it.id); print("reloaded:", it2.status, it2.downloaded, len(it2.segments))
m2.start(it2.id); wait(it2, {Status.COMPLETED, Status.ERROR})
print("result:", it2.status, it2.error, "segments split to:", "n/a")
check(it2); print("OK segmented+resume sha matches:", it2.path)

# 2. no-range server
it3 = m2.add(base + "/norange")
wait(it3, {Status.COMPLETED, Status.ERROR}); print("norange:", it3.status, it3.error, it3.filename, it3.resumable)
check(it3); print("OK single-connection")

# 3. speed limit
s.speed_limit_kbps = 2048; m2.apply_settings(s)
it4 = m2.add(base + "/file"); t0 = time.time()
time.sleep(3); rate = it4.downloaded / (time.time() - t0)
print(f"limited rate ~{rate/1024:.0f} KB/s (limit 2048)")
m2.pause(it4.id); wait(it4, {Status.PAUSED})
s.speed_limit_kbps = 0; m2.apply_settings(s)

# 4. queue with max_active
s.max_active = 1; m2.apply_settings(s)
a = m2.add(base + "/file", start=False); b = m2.add(base + "/file", start=False)
m2.queue(a.id); m2.queue(b.id); time.sleep(1.5)
print("queue states:", a.status, b.status)
wait(b, {Status.COMPLETED}, 90); print("both done:", a.status, b.status, a.filename, b.filename)
check(a); check(b)

# 5. bad url
e = m2.add(base.replace(str(srv.server_port), "1") + "/x")
wait(e, {Status.ERROR}, 60); print("error item:", e.error)
m2.shutdown(); m.shutdown()
print("ALL GOOD")
