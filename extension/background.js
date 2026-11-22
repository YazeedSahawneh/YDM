// YDM Integration — hands downloads and videos from Chrome, Edge, Brave, Opera and Firefox to the YDM
// desktop app. If YDM isn't running, the browser keeps the download as usual.
// One file for every browser: Chromium runs it as a service worker, Firefox as an event page
// (manifest "background" lists both); Firefox answers to the chrome.* names too.

if (typeof importScripts === "function") importScripts("shotdb.js");   // Firefox loads it from the manifest

const DEFAULTS = { enabled: true, videoButton: true, imageButton: true, port: 9614, minSizeKB: 0, skipExt: "" };
const bypass = new Set();          // URLs we deliberately give back to the browser

const getSettings = () => chrome.storage.local.get(DEFAULTS);
const base = (port) => `http://127.0.0.1:${port}`;

let lastSeen = 0;                  // when YDM last answered: skip the ping for back-to-back downloads

async function ping(port) {
  if (Date.now() - lastSeen < 15000) return true;
  try {
    const r = await fetch(`${base(port)}/ping`, { signal: AbortSignal.timeout(800) });
    const ok = r.ok && (await r.json()).app === "YDM";
    if (ok) lastSeen = Date.now();
    return ok;
  } catch { return false; }
}

async function cookiesFor(url) {
  try {
    const list = await chrome.cookies.getAll({ url });
    return list.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch { return ""; }
}

async function sendToApp(payload) {
  const { port } = await getSettings();
  try {
    const r = await fetch(`${base(port)}/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...payload,
        cookies: await cookiesFor(payload.url),
        userAgent: navigator.userAgent,
      }),
      signal: AbortSignal.timeout(3000),
    });
    return r.ok;
  } catch { return false; }
}

function extOf(name) {
  const m = /\.([a-z0-9]{1,8})(?:$|\?)/i.exec(name || "");
  return m ? m[1].toLowerCase() : "";
}

// ---- intercept normal browser downloads ------------------------------------
chrome.downloads.onCreated.addListener(async (item) => {
  const s = await getSettings();
  const url = item.finalUrl || item.url;
  if (!s.enabled || !/^https?:/i.test(url) || item.state !== "in_progress") return;
  if (bypass.has(url)) { bypass.delete(url); return; }
  if (s.minSizeKB > 0 && item.totalBytes > 0 && item.totalBytes < s.minSizeKB * 1024) return;
  const skip = s.skipExt.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
  if (skip.length && skip.includes(extOf(item.filename || url))) return;
  if (!(await ping(s.port))) return;               // app not running → leave it to the browser

  try {
    await chrome.downloads.cancel(item.id);
    await chrome.downloads.erase({ id: item.id });
  } catch { /* already finished */ }

  const ok = await sendToApp({
    url, kind: "file",
    filename: (item.filename || "").split(/[\\/]/).pop(),
    referrer: item.referrer || "",
    fileSize: item.totalBytes,
  });
  if (!ok) {                                        // give it back to the browser
    bypass.add(url);
    chrome.downloads.download({ url });
  }
});

// ---- media sniffer: streams the page plays (listed in the popup) -----------
const MEDIA_TYPES = /^(video\/|audio\/|application\/(vnd\.apple\.mpegurl|x-mpegurl|dash\+xml))/i;
const MANIFEST = /\.(m3u8|mpd)(\?|$)/i;
const FRAGMENT = /\.(ts|m4s|aac|vtt|webvtt)(\?|$)|[?&](range|rn|rbuf)=|\/seg-?\d|segment\d/i;
const SKIP_HOSTS = /(^|\.)googlevideo\.com$|(^|\.)doubleclick\.net$|(^|\.)googlesyndication\.com$/i;
const key = (tabId) => `media:${tabId}`;

async function remember(tabId, entry) {
  const k = key(tabId);
  const list = (await chrome.storage.session.get(k))[k] || [];
  if (list.some((m) => m.url === entry.url)) return;
  list.unshift(entry);
  await chrome.storage.session.set({ [k]: list.slice(0, 25) });
  chrome.action.setBadgeBackgroundColor({ color: "#3DDBC0", tabId });
  chrome.action.setBadgeText({ text: String(list.length), tabId });
}

chrome.webRequest.onResponseStarted.addListener((d) => {
  if (d.tabId < 0 || !/^https?:/i.test(d.url) || d.statusCode >= 400) return;
  let host = "";
  try { host = new URL(d.url).hostname; } catch { return; }
  if (SKIP_HOSTS.test(host)) return;                      // YouTube chunks: the video button handles YouTube
  const header = (name) => (d.responseHeaders || []).find((h) => h.name.toLowerCase() === name)?.value || "";
  const type = header("content-type").split(";")[0].trim();
  const size = Number(header("content-length")) || 0;
  const isManifest = MANIFEST.test(d.url) || /mpegurl|dash\+xml/i.test(type);
  if (!isManifest && (!MEDIA_TYPES.test(type) || FRAGMENT.test(d.url) || (size && size < 512 * 1024))) return;
  const name = decodeURIComponent(new URL(d.url).pathname.split("/").pop() || host);
  remember(d.tabId, {
    url: d.url, name, size, type, kind: isManifest ? "video" : "file",
    page: d.initiator || d.documentUrl || d.originUrl || "",          // Chromium || Firefox
  });
}, { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"] }, ["responseHeaders"]);

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.url) {                                         // new page in this tab: forget old streams
    chrome.storage.session.remove(key(tabId));
    chrome.action.setBadgeText({ text: "", tabId });
  }
});
chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove(key(tabId)));

// ---- right-click menus -----------------------------------------------------
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "ydm-link", title: "Download with YDM",
      contexts: ["link", "image", "video", "audio"] });
    chrome.contextMenus.create({ id: "ydm-video", title: "Download the video on this page with YDM",
      contexts: ["page", "video", "frame"] });
    chrome.contextMenus.create({ id: "ydm-post", title: "Save every photo and video of this post with YDM",
      contexts: ["link"], targetUrlPatterns: ["*://*.instagram.com/*", "*://*.facebook.com/*", "*://*.x.com/*",
        "*://*.twitter.com/*", "*://*.tiktok.com/*", "*://*.reddit.com/*", "*://*.pinterest.com/*",
        "*://*.threads.net/*", "*://*.threads.com/*", "*://*.tumblr.com/*", "*://bsky.app/*"] });
    chrome.contextMenus.create({ id: "ydm-all", title: "Download all links on this page with YDM",
      contexts: ["page"] });
    chrome.contextMenus.create({ id: "ydm-shot", title: "Screenshot with YDM", contexts: ["page", "image", "video"] });
    for (const [mode, title] of [["full", "Whole page"], ["visible", "Visible part"], ["area", "Select an area…"]]) {
      chrome.contextMenus.create({ id: `ydm-shot-${mode}`, parentId: "ydm-shot", title,
        contexts: ["page", "image", "video"] });
    }
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  let ok;
  if (String(info.menuItemId).startsWith("ydm-shot-")) {
    screenshot(tab, String(info.menuItemId).slice(9));
    return;
  }
  if (info.menuItemId === "ydm-link") {
    const url = info.linkUrl || info.srcUrl;
    if (!url || !/^https?:/i.test(url)) return;
    ok = await sendToApp({ url, kind: "file", referrer: info.pageUrl || "" });
  } else if (info.menuItemId === "ydm-post") {
    ok = await sendToApp({ url: info.linkUrl, kind: "gallery", referrer: info.pageUrl || "" });
  } else if (info.menuItemId === "ydm-all") {
    const links = await chrome.tabs.sendMessage(tab.id, { type: "collect-links" }).catch(() => []);
    ok = links.length ? await sendToApp({ url: links[0], urls: links, kind: "batch", referrer: info.pageUrl || "" }) : true;
  } else {
    const url = info.frameUrl && info.frameUrl !== info.pageUrl ? info.frameUrl : (tab?.url || info.pageUrl);
    ok = await sendToApp({ url, kind: "video", referrer: info.pageUrl || "" });
  }
  if (!ok) await flagOffline();
});

async function flagOffline() {
  await chrome.action.setBadgeBackgroundColor({ color: "#FF6B5E" });
  await chrome.action.setBadgeText({ text: "off" });
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 4000);
}

// ---- quality list for the on-page menu (asked when the button appears, so it's ready on click)
const infoCache = new Map();         // url -> { t, promise }

async function fetchInfo(url, referrer) {
  const { port } = await getSettings();
  try {
    const r = await fetch(`${base(port)}/info`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, referrer, cookies: await cookiesFor(url), userAgent: navigator.userAgent }),
      signal: AbortSignal.timeout(45000),
    });
    const data = await r.json();
    return r.ok ? data : { error: data.error || `HTTP ${r.status}` };
  } catch { return { offline: true }; }
}

function videoInfo(url, referrer) {
  const hit = infoCache.get(url);
  if (hit && Date.now() - hit.t < 4 * 60 * 1000) return hit.promise;
  const promise = fetchInfo(url, referrer).then((res) => {
    if (!res.options) infoCache.delete(url);            // don't keep failures
    return res;
  });
  infoCache.set(url, { t: Date.now(), promise });
  return promise;
}

// popup / content script → background
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === "video-info") {
    videoInfo(msg.url, sender.tab?.url || msg.url).then(reply);
    return true;
  }
  if (msg.type === "grab-video") {
    // a direct file (progressive MP4) goes in as a file; anything else (YouTube, blob:) as a video page
    const src = msg.src && /^https?:/i.test(msg.src) ? msg.src : "";
    let payload;
    if (src) payload = { url: src, kind: "file", referrer: msg.url };
    else if (msg.format) {
      payload = { url: msg.url, kind: "video", quick: true, video_format: msg.format, convert: msg.convert || "",
                  title: msg.title || "", referrer: sender.tab?.url || msg.url };
    } else payload = { url: msg.url, kind: "video", referrer: sender.tab?.url || msg.url };
    sendToApp(payload).then((ok) => { if (!ok) flagOffline(); reply(ok); });
    return true;
  }
  if (msg.type === "grab-photo") {
    // one picture straight away, or every photo/video of the post (YDM asks gallery-dl for the list)
    const payload = msg.post
      ? { url: msg.post, kind: "gallery", referrer: msg.page }
      : { url: msg.src, kind: "file", quick: true, referrer: msg.page };
    sendToApp(payload).then((ok) => { if (!ok) flagOffline(); reply(ok); });
    return true;
  }
  if (msg.type === "send-media") {
    sendToApp({ url: msg.url, kind: msg.kind, referrer: msg.page || "" }).then(reply);
    return true;
  }
  if (msg.type === "media-list") {
    chrome.storage.session.get(key(msg.tabId)).then((r) => reply(r[key(msg.tabId)] || []));
    return true;
  }
  if (msg.type === "ping") {
    getSettings().then((s) => ping(s.port)).then(reply);
    return true;
  }
  if (msg.type === "shot") {                    // from the popup (it closes itself right after)
    chrome.tabs.get(msg.tabId).then((tab) => screenshot(tab, msg.mode), () => {});
    reply(true);
    return;
  }
});

// ---- screenshots (whole page / visible part / an area) → the editor page -------------------------
// The page scrolls itself one screen at a time (content.js), each screen is captured here, the screens
// go to IndexedDB (shotdb.js) and shot.html stitches, edits and saves them.
const SHOT_MAX_FRAMES = 60;       // a very long page (endless feeds) stops there, and says so
let shooting = false, lastCapture = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const toPage = (tabId, msg) => chrome.tabs.sendMessage(tabId, msg, { frameId: 0 });

chrome.commands?.onCommand.addListener(async (command, tab) => {
  if (!command.startsWith("shot-")) return;
  tab = tab || (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
  screenshot(tab, command.slice(5));
});

async function pageReady(tabId) {
  try { if (await toPage(tabId, { type: "shot-ping" })) return true; } catch { /* not there yet */ }
  try {                                         // a tab that was open before the extension was (re)loaded
    await chrome.scripting.executeScript({ target: { tabId, frameIds: [0] }, files: ["content.js"] });
    return !!(await toPage(tabId, { type: "shot-ping" }));
  } catch { return false; }
}

async function grab(windowId) {
  const gap = 600 - (Date.now() - lastCapture);  // Chrome allows two captures a second
  if (gap > 0) await sleep(gap);
  lastCapture = Date.now();
  const url = await chrome.tabs.captureVisibleTab(windowId, { format: "png" });
  return (await fetch(url)).blob();
}

function shotBadge(tabId, text) {
  chrome.action.setBadgeBackgroundColor({ color: "#3DDBC0", tabId }).catch(() => {});
  chrome.action.setBadgeText({ text, tabId }).catch(() => {});
}

function friendly(err) {
  const m = String(err && err.message || err || "");
  if (/cannot access|chrome:\/\/|edge:\/\/|about:|extensions gallery|webstore|cannot be scripted|host permission|activeTab|not in effect/i.test(m)) {
    return "The browser doesn't let extensions capture this page (its own pages, the extension store and some PDFs).";
  }
  return m || "The capture stopped.";
}

async function screenshot(tab, mode) {
  if (shooting || !tab || !["full", "visible", "area"].includes(mode)) return;
  shooting = true;
  const rec = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), created: Date.now(),
                title: tab.title || "", url: tab.url || "", mode, frames: [], note: "", error: "" };
  let began = false;
  try {
    const ready = await pageReady(tab.id);
    if (!ready && mode !== "visible") {
      rec.note = "This page doesn't let extensions scroll it, so only the visible part was captured.";
      mode = rec.mode = "visible";
    }
    const info = ready ? await toPage(tab.id, { type: "shot-ping" }) : null;
    if (info) rec.vw = info.w;
    if (mode === "area") {
      const rect = await toPage(tab.id, { type: "shot-select" });
      if (!rect) return;                          // Esc: nothing to show
      rec.crop = rect;
      rec.frames.push({ y: 0, blob: await grab(tab.windowId) });
    } else if (mode === "visible") {
      rec.frames.push({ y: 0, blob: await grab(tab.windowId) });
    } else {
      const m = await toPage(tab.id, { type: "shot-begin" });
      began = true;
      Object.assign(rec, { vw: m.vw, rect: m.rect, inner: m.inner, title: m.title || rec.title });
      let y = 0, total = m.total, last = -1;
      for (let i = 0; ; i++) {
        const r = await toPage(tab.id, { type: "shot-scroll", y, index: i });
        if (!r) throw new Error("The page stopped answering.");
        if (!(await chrome.tabs.get(tab.id)).active) throw new Error("You switched tabs, so the capture stopped.");
        if (r.y <= last) break;                   // it doesn't scroll any further
        rec.frames.push({ y: r.y, blob: await grab(tab.windowId) });
        last = r.y;
        total = r.total;
        shotBadge(tab.id, `${Math.min(99, Math.round(((r.y + m.view) / total) * 100))}%`);
        if (r.y + m.view >= total - 1) break;
        if (rec.frames.length >= SHOT_MAX_FRAMES) {
          rec.note = `The page is very long: its first ${rec.frames.length} screens were captured.`;
          total = r.y + m.view;
          break;
        }
        y = r.y + m.view;
      }
      rec.total = total;
    }
  } catch (e) {
    rec.error = friendly(e);
  } finally {
    if (began) await toPage(tab.id, { type: "shot-end" }).catch(() => {});
    shotBadge(tab.id, "");
    shooting = false;
  }
  if (!rec.frames.length && !rec.error) return;
  try {
    await ShotDB.put(rec);
    await ShotDB.prune();
  } catch (e) {
    rec.error = `Couldn't keep the screenshot: ${e.message || e}`;
  }
  chrome.tabs.create({ url: chrome.runtime.getURL(`shot.html#${rec.id}`), index: tab.index + 1, openerTabId: tab.id });
}
