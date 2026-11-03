// YDM Integration — hands downloads from Chrome/Edge to the YDM desktop app.
// If YDM isn't running, the browser keeps the download as usual.

const DEFAULTS = { enabled: true, port: 9614, minSizeKB: 0, skipExt: "" };
const bypass = new Set();          // URLs we deliberately give back to the browser

const getSettings = () => chrome.storage.local.get(DEFAULTS);
const base = (port) => `http://127.0.0.1:${port}`;

async function ping(port) {
  try {
    const r = await fetch(`${base(port)}/ping`, { signal: AbortSignal.timeout(800) });
    return r.ok && (await r.json()).app === "YDM";
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

// ---- right-click menus -----------------------------------------------------
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "ydm-link", title: "Download with YDM",
      contexts: ["link", "image", "video", "audio"] });
    chrome.contextMenus.create({ id: "ydm-video", title: "Grab video from this page with YDM",
      contexts: ["page", "video", "frame"] });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  let ok;
  if (info.menuItemId === "ydm-link") {
    const url = info.linkUrl || info.srcUrl;
    if (!url || !/^https?:/i.test(url)) return;
    ok = await sendToApp({ url, kind: "file", referrer: info.pageUrl || "" });
  } else {
    const url = info.frameUrl && info.frameUrl !== info.pageUrl ? info.frameUrl : (tab?.url || info.pageUrl);
    ok = await sendToApp({ url, kind: "video", referrer: info.pageUrl || "" });
  }
  if (!ok) await flagOffline();
});

async function flagOffline() {
  await chrome.action.setBadgeBackgroundColor({ color: "#EF4444" });
  await chrome.action.setBadgeText({ text: "off" });
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 4000);
}

// popup → background
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === "grab-video") {
    sendToApp({ url: msg.url, kind: "video", referrer: msg.url }).then(reply);
    return true;
  }
  if (msg.type === "ping") {
    getSettings().then((s) => ping(s.port)).then(reply);
    return true;
  }
});
