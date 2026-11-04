const DEFAULTS = { enabled: true, videoButton: true, imageButton: true, port: 9614, minSizeKB: 0, skipExt: "" };
const $ = (id) => document.getElementById(id);

function size(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

async function init() {
  const s = await chrome.storage.local.get(DEFAULTS);
  $("enabled").checked = s.enabled;
  $("videoButton").checked = s.videoButton;
  $("imageButton").checked = s.imageButton;
  $("port").value = s.port;
  $("minSizeKB").value = s.minSizeKB;
  $("skipExt").value = s.skipExt;

  $("enabled").addEventListener("change", (e) => chrome.storage.local.set({ enabled: e.target.checked }));
  $("videoButton").addEventListener("change", (e) => chrome.storage.local.set({ videoButton: e.target.checked }));
  $("imageButton").addEventListener("change", (e) => chrome.storage.local.set({ imageButton: e.target.checked }));
  $("port").addEventListener("change", (e) => { chrome.storage.local.set({ port: Number(e.target.value) || 9614 }); check(); });
  $("minSizeKB").addEventListener("change", (e) => chrome.storage.local.set({ minSizeKB: Number(e.target.value) || 0 }));
  $("skipExt").addEventListener("change", (e) => chrome.storage.local.set({ skipExt: e.target.value }));

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  $("grab").addEventListener("click", async () => {
    if (!tab?.url?.startsWith("http")) { $("hint").textContent = "Open a page with a video first."; return; }
    $("grab").disabled = true;
    const ok = await chrome.runtime.sendMessage({ type: "grab-video", url: tab.url });
    $("hint").textContent = ok ? "Sent. Pick the quality in YDM." : "Couldn't reach YDM. Is it running?";
    $("grab").disabled = false;
  });
  if (tab) showMedia(tab.id);
  check();
}

async function showMedia(tabId) {
  const list = await chrome.runtime.sendMessage({ type: "media-list", tabId });
  if (!list.length) return;
  const box = $("media");
  box.textContent = "";
  for (const m of list) {
    const row = document.createElement("div");
    row.className = "row";
    const name = document.createElement("div");
    name.className = "name";
    const title = document.createElement("div");
    title.textContent = m.name;
    title.title = m.url;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = [m.kind === "video" ? "stream" : (m.type || "media"), size(m.size)].filter(Boolean).join(" · ");
    name.append(title, meta);
    const btn = document.createElement("button");
    btn.textContent = "Download";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      const ok = await chrome.runtime.sendMessage({ type: "send-media", url: m.url, kind: m.kind, page: m.page });
      btn.textContent = ok ? "Sent" : "Retry";
      btn.className = ok ? "sent" : "";
      btn.disabled = ok;
    });
    row.append(name, btn);
    box.append(row);
  }
}

async function check() {
  const online = await chrome.runtime.sendMessage({ type: "ping" });
  $("led").className = online ? "on" : "off";
  $("statusText").textContent = online ? "Connected" : "App not running";
  $("grab").disabled = !online;
  if (!online) $("hint").textContent = "Start YDM on your PC. Until then, downloads stay in the browser.";
}

init();
