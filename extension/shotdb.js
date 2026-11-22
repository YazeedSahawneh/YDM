// Screenshots between the background (which captures) and the editor page (which stitches and saves).
// IndexedDB keeps the screen images as Blobs: big pages don't fit in messages or storage.session.
// Loaded by background.js (importScripts in Chromium, the manifest's "scripts" list in Firefox) and shot.html.

const ShotDB = (() => {
  const NAME = "ydm-shots", STORE = "shots", KEEP = 6;      // the last few captures stay re-openable

  function open() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(NAME, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "id" });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function run(mode, fn) {
    const db = await open();
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, mode);
        const out = fn(tx.objectStore(STORE));
        tx.oncomplete = () => resolve(out && "result" in out ? out.result : undefined);
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
      });
    } finally {
      db.close();
    }
  }

  return {
    put: (rec) => run("readwrite", (s) => s.put(rec)),
    get: (id) => run("readonly", (s) => s.get(id)),
    remove: (id) => run("readwrite", (s) => s.delete(id)),
    async prune() {
      const all = (await run("readonly", (s) => s.getAll())) || [];
      const old = all.sort((a, b) => b.created - a.created).slice(KEEP);
      for (const r of old) await run("readwrite", (s) => s.delete(r.id));
    },
  };
})();
