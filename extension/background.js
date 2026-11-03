// YDM Integration — hands downloads and videos from Chrome/Edge to the YDM desktop app.
// If YDM isn't running, the browser keeps the download as usual.

const DEFAULTS = { enabled: true, videoButton: true, port: 9614, minSizeKB: 0, skipExt: "" };
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
    url: d.url, name, size, type, kind: isManifest ? "video" : "file", page: d.initiator || "",
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
    chrome.contextMenus.create({ id: "ydm-all", title: "Download all links on this page with YDM",
      contexts: ["page"] });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  let ok;
  if (info.menuItemId === "ydm-link") {
    const url = info.linkUrl || info.srcUrl;
    if (!url || !/^https?:/i.test(url)) return;
    ok = await sendToApp({ url, kind: "file", referrer: info.pageUrl || "" });
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
});
