const DEFAULTS = { enabled: true, port: 9614, minSizeKB: 0, skipExt: "" };
const $ = (id) => document.getElementById(id);

async function init() {
  const s = await chrome.storage.local.get(DEFAULTS);
  $("enabled").checked = s.enabled;
  $("port").value = s.port;
  $("minSizeKB").value = s.minSizeKB;
  $("skipExt").value = s.skipExt;

  $("enabled").addEventListener("change", (e) => chrome.storage.local.set({ enabled: e.target.checked }));
  $("port").addEventListener("change", (e) => { chrome.storage.local.set({ port: Number(e.target.value) || 9614 }); check(); });
  $("minSizeKB").addEventListener("change", (e) => chrome.storage.local.set({ minSizeKB: Number(e.target.value) || 0 }));
  $("skipExt").addEventListener("change", (e) => chrome.storage.local.set({ skipExt: e.target.value }));

  $("grab").addEventListener("click", async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url?.startsWith("http")) { $("hint").textContent = "Open a video page first."; return; }
    $("grab").disabled = true;
    const ok = await chrome.runtime.sendMessage({ type: "grab-video", url: tab.url });
    $("hint").textContent = ok ? "Sent to YDM — pick the quality there." : "Couldn't reach YDM. Is it running?";
    $("grab").disabled = false;
  });
  check();
}

async function check() {
  const online = await chrome.runtime.sendMessage({ type: "ping" });
  $("dot").style.background = online ? "#22C55E" : "#EF4444";
  $("statusText").textContent = online ? "Connected" : "App not running";
  $("grab").disabled = !online;
  if (!online) $("hint").textContent = "Start YDM on your PC. Until then, downloads stay in the browser.";
}

init();
