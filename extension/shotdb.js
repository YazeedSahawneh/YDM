// What the extension keeps between its background and its pages, in IndexedDB:
//   ShotDB — screenshots (the screen images as Blobs: big pages don't fit in messages or storage.session),
//   TextDB — texts copied with "Copy with YDM" (kept as you edit them, so a reload brings them back).
// Loaded by background.js (importScripts in Chromium, the manifest's "scripts" list in Firefox) and the pages.

function ydmStore(name, store, keep) {
  function open() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(name, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(store, { keyPath: "id" });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function run(mode, fn) {
    const db = await open();
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction(store, mode);
        const out = fn(tx.objectStore(store));
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
    all: () => run("readonly", (s) => s.getAll()).then((r) => (r || []).sort((a, b) => (b.updated || b.created) - (a.updated || a.created))),
    remove: (id) => run("readwrite", (s) => s.delete(id)),
    async prune() {                                   // the newest few stay
      const all = (await run("readonly", (s) => s.getAll())) || [];
      const old = all.sort((a, b) => (b.updated || b.created) - (a.updated || a.created)).slice(keep);
      for (const r of old) await run("readwrite", (s) => s.delete(r.id));
    },
  };
}

const ShotDB = ydmStore("ydm-shots", "shots", 6);
const TextDB = ydmStore("ydm-texts", "texts", 30);
