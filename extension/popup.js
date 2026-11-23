const DEFAULTS = { enabled: true, videoButton: true, imageButton: true, port: 9614, minSizeKB: 0, skipExt: "",
                   buttonCorner: "tr", hiddenSites: [], catchPlayable: true, catchPdf: false };
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
  for (const id of ["catchPlayable", "catchPdf"]) {
    $(id).checked = s[id];
    $(id).addEventListener("change", (e) => chrome.storage.local.set({ [id]: e.target.checked }));
  }
  $("port").value = s.port;
  $("minSizeKB").value = s.minSizeKB;
  $("skipExt").value = s.skipExt;

  $("enabled").addEventListener("change", (e) => chrome.storage.local.set({ enabled: e.target.checked }));
  $("videoButton").addEventListener("change", (e) => chrome.storage.local.set({ videoButton: e.target.checked }));
  $("imageButton").addEventListener("change", (e) => chrome.storage.local.set({ imageButton: e.target.checked }));
  $("port").addEventListener("change", (e) => { chrome.storage.local.set({ port: Number(e.target.value) || 9614 }); check(); });
  $("minSizeKB").addEventListener("change", (e) => chrome.storage.local.set({ minSizeKB: Number(e.target.value) || 0 }));
  $("skipExt").addEventListener("change", (e) => chrome.storage.local.set({ skipExt: e.target.value }));

  // where the on-page button sits
  const corners = document.querySelectorAll("[data-corner]");
  const markCorner = (c) => corners.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.corner === c)));
  markCorner(s.buttonCorner);
  corners.forEach((b) => b.addEventListener("click", () => {
    chrome.storage.local.set({ buttonCorner: b.dataset.corner });
    markCorner(b.dataset.corner);
  }));

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const web = !!tab?.url && /^https?:/.test(tab.url);

  // buttons hidden on this site ("Hide on this site" in the button's menu)
  const site = web ? new URL(tab.url).hostname.replace(/^www\./, "") : "";
  if (site && (s.hiddenSites || []).includes(site)) {
    $("siteHiddenText").textContent = `The buttons are hidden on ${site}`;
    $("siteHidden").classList.add("show");
    $("showHere").addEventListener("click", async () => {
      const { hiddenSites } = await chrome.storage.local.get({ hiddenSites: [] });
      await chrome.storage.local.set({ hiddenSites: hiddenSites.filter((h) => h !== site) });
      $("siteHidden").classList.remove("show");
    });
  }

  // screenshots: the background does the work; the popup gets out of the way first
  document.querySelectorAll("[data-shot]").forEach((b) => b.addEventListener("click", async () => {
    if (!tab) return;
    await chrome.runtime.sendMessage({ type: "shot", mode: b.dataset.shot, tabId: tab.id });
    window.close();
  }));
  chrome.commands?.getAll?.().then((cmds) => {
    const key = cmds.find((c) => c.name === "shot-full")?.shortcut;
    if (key) $("shotHint").textContent = `${key} takes the whole page. Then crop, draw, blur and save as PNG, JPG or PDF.`;
  }).catch(() => {});

  $("textEditor").addEventListener("click", async () => {
    await chrome.runtime.sendMessage({ type: "text-new" });
    window.close();
  });

  $("grab").addEventListener("click", async () => {
    if (!web) { $("hint").textContent = "Open a page with a video first."; return; }
    $("grab").disabled = true;
    const ok = await chrome.runtime.sendMessage({ type: "grab-video", url: tab.url });
    $("hint").textContent = ok ? "Sent. Pick the quality in YDM." : "Couldn't reach YDM. Is it running?";
    $("grab").disabled = false;
  });
  if (tab) showMedia(tab.id);
  check();
  access();
}

// Firefox lets people install an add-on without its site access: ask for it here (Chrome grants it at install)
async function access() {
  const all = { origins: ["<all_urls>"] };
  if (!chrome.permissions || await chrome.permissions.contains(all)) return;
  $("access").classList.add("show");
  $("allow").addEventListener("click", async () => {
    if (await chrome.permissions.request(all)) $("access").classList.remove("show");
  });
}

const KIND = (type) => (/^video\//.test(type) ? "video file" : /^audio\//.test(type) ? "audio file" : type === "application/pdf" ? "PDF file"
  : /^image\//.test(type) ? "picture" : /^text\//.test(type) ? "text file" : "file");

async function showMedia(tabId) {
  const info = await chrome.runtime.sendMessage({ type: "media-list", tabId });
  if (!info) return;
  const { list, segments, doc, kept } = info;
  // the tab itself is a file the browser opened (a video, a PDF…)
  if (doc && doc.url) {
    $("docName").textContent = doc.name;
    $("docName").title = doc.url;
    $("docMeta").textContent = `This tab is a ${KIND(doc.type)}${doc.size ? ` · ${size(doc.size)}` : ""}`;
    $("docCard").classList.add("show");
    $("docGet").addEventListener("click", async () => {
      $("docGet").disabled = true;
      const ok = await chrome.runtime.sendMessage({ type: "send-media", url: doc.url, kind: "file", page: "" });
      $("docGet").textContent = ok ? "Sent" : "Retry";
      $("docGet").disabled = ok;
    });
  }
  // the last download YDM left to the browser, and why
  if (kept && Date.now() - kept.at < 10 * 60 * 1000) {
    const k = $("kept");
    k.textContent = "";
    const b = document.createElement("b");
    b.textContent = kept.name;
    k.append(b, ` stayed in the browser: ${kept.reason}.`);
    k.classList.add("show");
  }
  if (!list.length && !segments) return;
  const box = $("media");
  box.textContent = "";
  if (!list.length) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = `This page plays a stream in ${segments} pieces but no playlist YDM can use was seen yet. ` +
      "Reload the page and play it again, or use “Download the video on this tab”.";
    box.append(e);
    return;
  }
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
    const what = m.stream === "master" ? "stream · every quality" : m.stream === "dash" ? "DASH stream · whole video"
      : m.stream === "quality" ? "stream · one quality" : m.kind === "video" ? "stream" : `${m.whole ? "whole " : ""}${KIND(m.type)}`;
    meta.innerHTML = "";
    const tag = document.createElement("span");
    tag.className = m.kind === "video" ? "tag" : "";
    tag.textContent = what;
    meta.append(tag, m.size ? ` · ${size(m.size)}` : "");
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
  if (segments) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = `${segments} small pieces of a stream were left out: the stream above is the whole video.`;
    box.append(e);
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
