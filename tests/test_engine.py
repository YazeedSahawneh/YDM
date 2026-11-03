import hashlib, os, sys, threading, time, tempfile, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ydm.config import Settings
from ydm.engine import DownloadManager, Status

DATA = os.urandom(24 * 1024 * 1024 + 12345)
SHA = hashlib.sha256(DATA).hexdigest()

ACTIVE = [0]; ACTIVE_LOCK = threading.Lock(); MAX_SEEN = [0]

VALID = ["A"]          # /exp/<token>: only the current token works (an expiring link)


def _digest_ok(header, method, path):
    import hashlib as h
    f = dict(re.findall(r'(\w+)="?([^",]+)"?', header))
    md5 = lambda x: h.md5(x.encode()).hexdigest()
    ha1, ha2 = md5(f"u:ydm:p"), md5(f"{method}:{f.get('uri')}")
    want = md5(f"{ha1}:{f.get('nonce')}:{f.get('nc')}:{f.get('cnonce')}:{f.get('qop')}:{ha2}")
    return f.get("username") == "u" and f.get("response") == want


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _refuse(self, code, extra=()):
        self.send_response(code)
        for k, v in extra:
            self.send_header(k, v)
        self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self):
        if "/exp/" in self.path and self.path.rsplit("/", 1)[-1] != VALID[0]:
            return self._refuse(403)
        got = self.headers.get("Authorization", "")
        if "secret-basic" in self.path and got != "Basic dTpw":                       # u:p
            return self._refuse(401, [("WWW-Authenticate", 'Basic realm="ydm"')])
        if "secret-digest" in self.path and not (got.startswith("Digest ") and _digest_ok(got, "GET", self.path)):
            return self._refuse(401, [("WWW-Authenticate", 'Digest realm="ydm", qop="auth", nonce="n0nce42", '
                                                          'algorithm=MD5')])
        if "throttle" in self.path:          # allows at most 4 parallel connections
            with ACTIVE_LOCK:
                if ACTIVE[0] >= 4:
                    self.send_response(429); self.send_header("Content-Length", "0"); self.end_headers(); return
                ACTIVE[0] += 1; MAX_SEEN[0] = max(MAX_SEEN[0], ACTIVE[0])
            try: return self._serve()
            finally:
                with ACTIVE_LOCK: ACTIVE[0] -= 1
        self._serve()
    def _serve(self):
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
            except OSError:                 # client hung up (pause, retired connection)
                return
            time.sleep(0.25 if "crawl" in self.path else 0.03 if ("slow" in self.path or "/exp/" in self.path)
                       else 0.004)

srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
srv.handle_error = lambda *a: None          # clients hanging up mid-response is normal here
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"

tmp = Path(tempfile.mkdtemp())
from ydm.engine.hosts import HOSTS
HOSTS._path = tmp / "hosts.json"           # keep the real host memory untouched
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
assert not (a.status in Status.ACTIVE and b.status in Status.ACTIVE), "max_active ignored"
wait(b, {Status.COMPLETED}, 90); print("both done:", a.status, b.status, a.filename, b.filename)
check(a); check(b)

# 5. server that refuses a 5th connection: extra workers retire, the host limit is remembered
s.max_active = 3; s.connections = 8; m2.apply_settings(s)
t = m2.add(base + "/throttle")
wait(t, {Status.COMPLETED, Status.ERROR}, 90)
print("throttled:", t.status, t.error, "max parallel:", MAX_SEEN[0], "remembered limit:", HOSTS.limit("127.0.0.1"))
assert t.status == Status.COMPLETED, t.error
check(t)
assert HOSTS.limit("127.0.0.1") and HOSTS.limit("127.0.0.1") <= 4

# 6. sparse pre-allocation (Windows) and probe-connection reuse: a tiny file needs exactly one request
if os.name == "nt":
    from ydm.engine.http_downloader import _preallocate
    sp = tmp / "sparse.bin"; t0 = time.time(); _preallocate(str(sp), 4 * 1024 ** 3); took = time.time() - t0
    import ctypes
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(sp))
    assert attrs & 0x200, "file is not sparse"
    assert sp.stat().st_size == 4 * 1024 ** 3, "wrong size"
    assert took < 0.5, f"pre-allocating 4 GB took {took:.1f}s (zero-filling?)"
    sp.unlink(); print(f"OK sparse pre-allocation (4 GB in {took*1000:.0f} ms)")

HOSTS._data.clear()                         # forget the throttle test's limit of 4 for 127.0.0.1

# 8. connection count changed while downloading: more join, then surplus ones leave
lv = m2.add(base + "/crawl-live", connections=2)
wait(lv, {Status.DOWNLOADING}); time.sleep(0.8)
before = sum(1 for x in lv.segments if x.active)
m2.set_connections(lv.id, 8); time.sleep(1.0)
more = sum(1 for x in lv.segments if x.active)
m2.set_connections(lv.id, 3); time.sleep(1.0)
fewer = sum(1 for x in lv.segments if x.active)
print(f"live connections: {before} -> {more} -> {fewer}")
assert before <= 2 and more >= 6 and fewer <= 3, (before, more, fewer)
wait(lv, {Status.COMPLETED, Status.ERROR}, 90); assert lv.status == Status.COMPLETED, lv.error
check(lv); print("OK live connection change")

# 9. logins: Basic, Digest, user:pass@ in the address
from ydm.engine.item import LOGIN_NEEDED, EXPIRED
from ydm.engine import auth
import json
auth.LOGINS._path = tmp / "logins.json"
na = m2.add(base + "/secret-basic")
wait(na, {Status.ERROR, Status.COMPLETED}); assert na.error.startswith(LOGIN_NEEDED), na.error
m2.set_login(na.id, "u", "p")
wait(na, {Status.COMPLETED, Status.ERROR}); assert na.status == Status.COMPLETED, na.error; check(na)
dg = m2.add(base + "/secret-digest", connections=6, start=False); m2.set_login(dg.id, "u", "p"); m2.start(dg.id)
wait(dg, {Status.COMPLETED, Status.ERROR}); assert dg.status == Status.COMPLETED, dg.error; check(dg)
ui = m2.add(base.replace("http://", "http://u:p@") + "/secret-basic")
wait(ui, {Status.COMPLETED, Status.ERROR}); assert ui.status == Status.COMPLETED, ui.error; check(ui)
auth.LOGINS.save("127.0.0.1", "u", "p"); sl = m2.add(base + "/secret-digest")
wait(sl, {Status.COMPLETED, Status.ERROR}); assert sl.status == Status.COMPLETED, sl.error; check(sl)
stored = json.loads((tmp / "logins.json").read_text())["127.0.0.1"]["password"]
assert stored.startswith("dpapi:" if os.name == "nt" else "b64:"), "password stored in clear"
print("OK logins (basic, digest, address, saved site login)")

# 10. expired link: resume fails as expired, the fresh link from the browser is matched and continues
ex = m2.add(base + "/exp/A", connections=4)
while ex.downloaded < 3 * 1024 * 1024: time.sleep(0.05)
m2.pause(ex.id); wait(ex, {Status.PAUSED}); got_before = ex.downloaded
VALID[0] = "B"
m2.start(ex.id); wait(ex, {Status.ERROR}); assert ex.expired, ex.error
print("expired:", ex.error)
assert m2.find_renewal("test file.zip", len(DATA)) is ex
assert m2.find_renewal("other.zip", len(DATA)) is None
assert m2.find_renewal("", len(DATA)) is ex           # name unknown yet: the exact size of the one expired item
assert m2.find_renewal("", len(DATA) - 1) is None
m2.refresh_url(ex.id, base + "/exp/B")
wait(ex, {Status.COMPLETED, Status.ERROR}, 90); assert ex.status == Status.COMPLETED, ex.error; check(ex)
print(f"OK renewed (kept {got_before // 1024 // 1024} MB already downloaded)")

# 11. crash: a store still saying "Downloading" resumes by itself, re-fetching the last MB of each piece
import json
cr = m2.add(base + "/slow-crash", connections=4)
while cr.downloaded < 3 * 1024 * 1024: time.sleep(0.05)
m2.pause(cr.id); wait(cr, {Status.PAUSED}); m2.save()
store = json.loads((tmp / "store.json").read_text())
for d in store["items"]:
    if d["id"] == cr.id: d["status"] = "Downloading"; saved = [x["done"] for x in d["segments"]]
(tmp / "crash.json").write_text(json.dumps(store))
m2.remove(cr.id)
m3 = DownloadManager(s, store_path=tmp / "crash.json"); c3 = m3.get(cr.id)
assert c3.status in (Status.QUEUED, *Status.ACTIVE), c3.status
assert all(x.done == max(0, d - 1024 * 1024) for x, d in zip(c3.segments, saved))
wait(c3, {Status.COMPLETED, Status.ERROR}, 90); assert c3.status == Status.COMPLETED, c3.error; check(c3)
m3.shutdown(); print("OK crash resume")

# 12. FTP (needs pyftpdlib, a test-only package)
try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import ThreadedFTPServer
except ImportError:
    print("skip FTP test (pip install pyftpdlib)")
else:
    import logging; logging.getLogger("pyftpdlib").setLevel(logging.ERROR)
    root = tmp / "ftp"; root.mkdir(); (root / "big file.bin").write_bytes(DATA)
    au = DummyAuthorizer(); au.add_user("u", "p", str(root)); au.add_anonymous(str(root))
    class FH(FTPHandler):
        authorizer = au
        passive_ports = range(52000, 52200)
    fsrv = ThreadedFTPServer(("127.0.0.1", 0), FH); fsrv.max_cons_per_ip = 5
    threading.Thread(target=fsrv.serve_forever, kwargs={"handle_exit": False}, daemon=True).start()
    fport = fsrv.address[1]
    fa = m2.add(f"ftp://u:p@127.0.0.1:{fport}/big%20file.bin", connections=8)
    wait(fa, {Status.COMPLETED, Status.ERROR}, 90)
    assert fa.status == Status.COMPLETED, fa.error; check(fa)
    print("ftp:", fa.filename, "resumable:", fa.resumable, "host limit learned:", HOSTS.limit("127.0.0.1"))
    fb = m2.add(f"ftp://127.0.0.1:{fport}/big%20file.bin", connections=3)
    wait(fb, {Status.COMPLETED, Status.ERROR}, 90); assert fb.status == Status.COMPLETED, fb.error; check(fb)
    fc = m2.add(f"ftp://u:wrong@127.0.0.1:{fport}/big%20file.bin")
    wait(fc, {Status.ERROR, Status.COMPLETED}, 60); assert fc.error.startswith(LOGIN_NEEDED), fc.error
    fsrv.close_all(); print("OK FTP (login, anonymous, wrong password)")

# 7. bad url
e = m2.add(base.replace(str(srv.server_port), "1") + "/x")
wait(e, {Status.ERROR}, 60); print("error item:", e.error)
m2.shutdown(); m.shutdown()
print("ALL GOOD")
