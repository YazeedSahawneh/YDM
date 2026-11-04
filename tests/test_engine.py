import hashlib, os, sys, threading, time, tempfile, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ydm.config import Settings
from ydm.engine import DownloadManager, Status

DATA = os.urandom(24 * 1024 * 1024 + 12345)
SHA = hashlib.sha256(DATA).hexdigest()

ACTIVE = [0]; ACTIVE_LOCK = threading.Lock(); MAX_SEEN = [0]; LIMIT3 = [0]

VALID = ["A"]          # /exp/<token>: only the current token works (an expiring link)


def _digest_ok(header, method, path):
    import hashlib as h
    f = dict(re.findall(r'(\w+)="?([^",]+)"?', header))
    md5 = lambda x: h.md5(x.encode()).hexdigest()
    ha1, ha2 = md5(f"u:ydm:p"), md5(f"{method}:{f.get('uri')}")
    want = md5(f"{ha1}:{f.get('nonce')}:{f.get('nc')}:{f.get('cnonce')}:{f.get('qop')}:{ha2}")
    return f.get("username") == "u" and f.get("response") == want


BIG = bytes(200 * 1024 * 1024)        # only the auto-connection tests (never finished)


class _Bucket:
    def __init__(self, rate):
        self.rate, self.t, self.lock = rate, time.monotonic(), threading.Lock()
    def take(self, n):
        with self.lock:
            self.t = max(self.t, time.monotonic()) + n / self.rate
            wait = self.t - time.monotonic()
        if wait > 0:
            time.sleep(wait)


TOTAL_BUCKET = _Bucket(3 * 1024 * 1024)


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
        if "percap" in self.path or "totalcap" in self.path:
            return self._serve_capped()
        if "limit3" in self.path:            # 3 parallel connections; extras get a 403 or are cut off
            with ACTIVE_LOCK:
                full = LIMIT3[0] >= 3
                if not full:
                    LIMIT3[0] += 1
            if full:
                if "cut" in self.path:
                    self.close_connection = True
                    return                      # hang up without an answer
                return self._refuse(403)
            try: return self._serve()
            finally:
                with ACTIVE_LOCK: LIMIT3[0] -= 1
        if "throttle" in self.path:          # allows at most 4 parallel connections
            with ACTIVE_LOCK:
                if ACTIVE[0] >= 4:
                    self.send_response(429); self.send_header("Content-Length", "0"); self.end_headers(); return
                ACTIVE[0] += 1; MAX_SEEN[0] = max(MAX_SEEN[0], ACTIVE[0])
            try: return self._serve()
            finally:
                with ACTIVE_LOCK: ACTIVE[0] -= 1
        self._serve()
    def _serve_capped(self):
        """percap: each connection gets 256 KB/s. totalcap: all connections share 3 MB/s."""
        a, b = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", "bytes=0-")).groups()
        a = int(a); b = int(b) if b else len(BIG) - 1
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {a}-{b}/{len(BIG)}")
        self.send_header("Content-Length", str(b - a + 1)); self.end_headers()
        step = 32 * 1024
        for i in range(a, b + 1, step):
            n = min(step, b + 1 - i)
            if "totalcap" in self.path:
                TOTAL_BUCKET.take(n)
            else:
                time.sleep(n / (256 * 1024))
            try:
                self.wfile.write(BIG[i:i + n])
            except OSError:
                return

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
s = Settings(download_dir=str(tmp / "dl"), connections=8, auto_connections=False)   # fixed counts below
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

# "Download later" parks it: paused, and the queue doesn't start it again by itself
lt = m2.add(base + "/crawl-later", connections=2)
wait(lt, {Status.DOWNLOADING}); m2.later(lt.id); wait(lt, {Status.PAUSED}); time.sleep(1.5)
assert lt.status == Status.PAUSED, lt.status
m2.remove(lt.id, delete_file=True); print("OK download later")

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

# 6a. servers that refuse extra connections rudely (403, or hanging up) instead of 429/503
for how in ("403", "cut"):
    HOSTS._data.clear()
    rf = m2.add(base + f"/slow-limit3-{how}/f.bin", connections=8)
    wait(rf, {Status.COMPLETED, Status.ERROR}, 120)
    assert rf.status == Status.COMPLETED, f"{how}: {rf.error}"
    check(rf)
    assert HOSTS.limit("127.0.0.1") == 3, (how, HOSTS._data)
    print(f"refused with {how}: completed on 3 connections, limit remembered")
HOSTS._data.clear()
print("OK servers that refuse extra connections (403 / hang up)")

# 6b. automatic connection count
from ydm.engine.http_downloader import AUTO_START, _Tuner
t = _Tuner(6, 32)
steps, rate = [], lambda n: min(n, 14) * 1e6          # each connection 1 MB/s until the line is full at 14
for _ in range(80):
    got = t.feed(rate(t.n), 10**12)
    if got is not None: steps.append(got)
    if t.settled: break
assert steps[:3] == [9, 13, 19], steps
assert t.settled and t.n == 13 and t.best_n == 13, (t.n, t.best_n, steps)      # 19 bought nothing: back to 13
t2 = _Tuner(6, 32)
for _ in range(20):
    t2.feed(5e6, 10**12)                               # full line already: one try up, then back
assert t2.settled and t2.n == 6, t2.n
print("tuner:", steps, "-> settles at", t.n)

s_auto = Settings(download_dir=str(tmp / "dl-auto"))
assert s_auto.auto_connections
ma = DownloadManager(s_auto, store_path=tmp / "store-auto.json")
HOSTS._data.clear()
pc = ma.add(base + "/percap/big.bin")
assert pc.auto_connections
wait(pc, {Status.DOWNLOADING}, 30)
assert pc.connections == AUTO_START, pc.connections
end = time.time() + 25
while time.time() < end and pc.connections < 13: time.sleep(0.2)
assert pc.connections >= 13, f"auto mode didn't add connections on a per-connection cap: {pc.connections}"
print("auto, per-connection cap: climbed to", pc.connections, "connections")
ma.set_connections(pc.id, 4)                          # the user takes over
assert not pc.auto_connections
time.sleep(1.5)
assert pc.connections == 4 and sum(1 for x in pc.segments if x.active) <= 4
ma.discard(pc.id)

HOSTS._data.clear()
tc = ma.add(base + "/totalcap/big.bin")
wait(tc, {Status.DOWNLOADING}, 30)
seen = set()
end = time.time() + 22
while time.time() < end:
    seen.add(tc.connections); time.sleep(0.2)
    if len(seen) > 1 and tc.connections == AUTO_START: break
assert tc.connections == AUTO_START and max(seen) > AUTO_START, (seen, tc.connections)
print("auto, shared cap: tried", sorted(seen), "-> back to", tc.connections)
ma.pause(tc.id); wait(tc, {Status.PAUSED}, 15)
assert HOSTS.best("127.0.0.1") == AUTO_START, HOSTS._data
ma.discard(tc.id); ma.shutdown()
print("OK auto connections (climbs on per-connection caps, holds on a full line, remembers per server)")

# 6c. photos / posts (gallery-dl's JSON → downloads)
from ydm.engine import gallery
msgs = [[2, {"username": "nasa", "fullname": "NASA", "description": "Saturn\nrings", "post_shortcode": "C1"}],
        [3, "https://scontent.cdninstagram.com/a.jpg?sig=1", {"username": "nasa", "post_shortcode": "C1", "num": 1,
                                                            "extension": "jpg"}],
        [3, "https://scontent.cdninstagram.com/b.jpg?sig=2", {"username": "nasa", "post_shortcode": "C1", "num": 2,
                                                            "extension": "jpg"}],
        [3, "ytdl:https://www.instagram.com/p/C1/", {"username": "nasa", "post_shortcode": "C1", "num": 3,
                                                   "extension": "mp4"}]]
post = gallery.parse("https://www.instagram.com/p/C1/", msgs,
                     {"Cookie": "sessionid=x", "User-Agent": "UA"})
assert [f.filename for f in post.files] == ["nasa_C1.jpg", "nasa_C1_2.jpg", "nasa_C1_3.mp4"], post.files
assert post.title == "Saturn rings" and post.uploader == "NASA" and post.photos == 2
assert post.files[2].kind == "video" and post.files[2].url == "https://www.instagram.com/p/C1/"
assert "Cookie" not in post.files[0].headers                  # never hand the page's cookies to the CDN
assert post.files[2].headers["Cookie"] == "sessionid=x" and post.files[0].headers["Referer"].endswith("/p/C1/")
assert post.label() == "All 2 photos + 1 video", post.label()
try:
    gallery.parse("https://www.instagram.com/p/C2/", [[-1, {"error": "AbortExtraction",
                                                             "message": "HTTP redirect to login page"}]])
    raise SystemExit("an error answer should raise")
except RuntimeError as exc:
    assert "Log in" in str(exc), exc
cf = gallery._cookie_file("https://www.instagram.com/p/C1/", "sessionid=abc; csrftoken=def")
lines = Path(cf).read_text().splitlines(); os.unlink(cf)
assert lines[1].split("\t") == [".instagram.com", "TRUE", "/", "TRUE", "0", "sessionid", "abc"], lines
if gallery.available():
    pics = gallery.collect(base + "/pics/photo.jpg")         # gallery-dl's direct-link support, end to end
    assert [f.filename for f in pics.files] == ["photo.jpg"], pics.files
    got = m2.add_gallery(pics, save_dir=str(tmp / "posts"))
    wait(got[0], {Status.COMPLETED, Status.ERROR}, 60)
    assert got[0].status == Status.COMPLETED and got[0].category == "Images", got[0].error
    check(got[0])
    print("OK posts (gallery-dl listing, names, cookies stay on the site, files downloaded)")
else:
    print("OK posts parsing (skip gallery-dl end to end: pip install gallery-dl)")

# 6d. start with Windows: same switch as Task Manager's Startup apps
from ydm import startup
if startup.supported():
    import winreg
    startup.NAME = "YDM-selftest"
    try:
        startup.set_enabled(True); assert startup.is_enabled()
        startup.set_enabled(False); assert not startup.is_enabled()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup.RUN) as k:
            assert "--minimized" in winreg.QueryValueEx(k, startup.NAME)[0]   # still listed, just disabled
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup.APPROVED, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, startup.NAME, 0, winreg.REG_BINARY, bytes([2]) + bytes(11))  # Task Manager: Enable
        assert startup.is_enabled()
    finally:
        for key in (startup.RUN, startup.APPROVED):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, startup.NAME)
            except OSError:
                pass
    print("OK start with Windows (on/off shared with Task Manager)")

# 7. bad url
e = m2.add(base.replace(str(srv.server_port), "1") + "/x")
wait(e, {Status.ERROR}, 60); print("error item:", e.error)
m2.shutdown(); m.shutdown()
print("ALL GOOD")
