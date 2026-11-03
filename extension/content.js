// YDM Integration — a "Download video" button over videos with a quality menu, like IDM's panel.
// Lives in a closed shadow root so page styles can't touch it (and it can't touch the page).

(() => {
  if (window.__ydmContent) return;
  window.__ydmContent = true;

  let enabled = true;
  chrome.storage.local.get({ videoButton: true }).then((s) => { enabled = s.videoButton; });
  chrome.storage.onChanged.addListener((c) => { if (c.videoButton) enabled = c.videoButton.newValue; });

  const host = document.createElement("div");
  host.style.cssText = "position:fixed;z-index:2147483647;top:0;left:0;display:none;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
    <style>
      * { box-sizing: border-box; }
      .wrap { display: flex; flex-direction: column; align-items: flex-end; gap: 6px;
        font: 13px "Segoe UI", system-ui, sans-serif; color: #ECE8E1; }
      button.main { all: unset; box-sizing: border-box; display: flex; align-items: center; gap: 7px;
        height: 30px; padding: 0 10px 0 9px; border-radius: 6px; cursor: pointer;
        font: 600 12.5px "Segoe UI", system-ui, sans-serif; color: #ECE8E1;
        background: rgba(18, 20, 23, .94); border: 1px solid #2C3137;
        box-shadow: 0 4px 14px rgba(0, 0, 0, .35); transition: border-color .15s, transform .15s; }
      button.main:hover, button.main.open { border-color: #FFB547; }
      button.main:hover { transform: translateY(-1px); }
      .chev { width: 10px; height: 10px; opacity: .7; transition: transform .15s; }
      .open .chev { transform: rotate(180deg); }
      svg.logo { width: 16px; height: 16px; }
      .menu { display: none; width: 270px; padding: 5px; border-radius: 8px; background: #191C20;
        border: 1px solid #2C3137; box-shadow: 0 10px 30px rgba(0, 0, 0, .45); max-height: 360px; overflow: auto; }
      .menu.show { display: block; }
      .head { padding: 7px 9px 6px; font-size: 11px; font-weight: 600; letter-spacing: .9px; text-transform: uppercase;
        color: #969BA2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .opt { all: unset; box-sizing: border-box; width: 100%; display: flex; justify-content: space-between; gap: 12px;
        padding: 8px 10px; border-radius: 5px; cursor: pointer; }
      .opt:hover, .opt:focus-visible { background: #22262B; }
      .opt .size { color: #969BA2; font-variant-numeric: tabular-nums; }
      .opt.sent .size { color: #A6E35A; }
      .sep { height: 1px; background: #2C3137; margin: 5px 6px; }
      .note { padding: 9px 10px; color: #969BA2; font-size: 12px; }
      .note.err { color: #FF6B5E; }
      .more { color: #FFB547; }
      .scan { display: flex; gap: 3px; padding: 4px 10px 10px; }
      .scan i { width: 7px; height: 10px; border-radius: 1.5px; background: #262B31; animation: lit 1s infinite; }
      .scan i:nth-child(2) { animation-delay: .1s } .scan i:nth-child(3) { animation-delay: .2s }
      .scan i:nth-child(4) { animation-delay: .3s } .scan i:nth-child(5) { animation-delay: .4s }
      @keyframes lit { 0%, 100% { background: #262B31 } 40% { background: #3DDBC0 } }
    </style>
    <div class="wrap">
      <button class="main" title="Download this video with YDM">
        <svg class="logo" viewBox="0 0 16 16" aria-hidden="true">
          <rect x="6.5" y="1" width="3" height="2.4" rx=".6" fill="#3DDBC0" opacity=".55"/>
          <rect x="6.5" y="4.3" width="3" height="2.4" rx=".6" fill="#3DDBC0" opacity=".75"/>
          <rect x="2.6" y="6.6" width="3" height="2.4" rx=".6" fill="#3DDBC0" opacity=".85"/>
          <rect x="10.4" y="6.6" width="3" height="2.4" rx=".6" fill="#3DDBC0" opacity=".85"/>
          <rect x="6.5" y="9.6" width="3" height="2.4" rx=".6" fill="#3DDBC0"/>
          <rect x="3" y="13.6" width="10" height="1.4" rx=".7" fill="#FFB547"/>
        </svg>
        <span>Download video</span>
        <svg class="chev" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="#ECE8E1" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
      <div class="menu" role="menu"></div>
    </div>`;
  const button = root.querySelector("button.main");
  const menu = root.querySelector(".menu");
  (document.body || document.documentElement).appendChild(host);

  let current = null;       // the <video> the button belongs to
  let pageUrl = "";         // what we hand to YDM for it
  let hideTimer = 0;

  const isYouTube = /(^|\.)youtube\.com$/i.test(location.hostname);

  // Which page address describes this video? YouTube previews on the home page aren't the page itself.
  function urlFor(video) {
    if (!isYouTube) return location.href;
    if (/^\/(watch|shorts|live|embed)/.test(location.pathname)) return location.href;
    const link = video.closest('a[href*="/watch"], a[href*="/shorts/"]');
    return link ? link.href : "";
  }

  function place() {
    if (!current || !current.isConnected) return hide();
    const r = current.getBoundingClientRect();
    if (r.width < 200 || r.height < 110 || r.bottom < 0 || r.top > innerHeight) return hide();
    host.style.left = `${Math.max(4, Math.min(innerWidth - 284, r.right - 280))}px`;
    host.style.top = `${Math.max(4, r.top + 10)}px`;
    host.style.display = "block";
  }
  function hide() { host.style.display = "none"; menu.classList.remove("show"); button.classList.remove("open"); current = null; }
  function scheduleHide(ms = 1600) {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => { if (!menu.classList.contains("show")) hide(); }, ms);
  }

  function attach(video) {
    const url = urlFor(video);
    if (!url) return;
    clearTimeout(hideTimer);
    if (current !== video) {
      current = video; pageUrl = url;
      menu.classList.remove("show"); button.classList.remove("open");
      chrome.runtime.sendMessage({ type: "video-info", url, prefetch: true }).catch(() => {});  // warm it up
    }
    place();
  }

  document.addEventListener("mouseover", (e) => {
    if (!enabled) return;
    const v = e.target.closest ? e.target.closest("video") : null;
    if (v) attach(v);
    else if (current && !host.contains(e.target)) scheduleHide();
  }, { passive: true, capture: true });
  // Players often cover the <video> with an overlay; also track the pointer over the video's box.
  document.addEventListener("mousemove", (e) => {
    if (!enabled || current) return;
    for (const v of document.querySelectorAll("video")) {
      const r = v.getBoundingClientRect();
      if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
        attach(v); scheduleHide(); return;
      }
    }
  }, { passive: true });
  host.addEventListener("mouseenter", () => clearTimeout(hideTimer));
  host.addEventListener("mouseleave", () => scheduleHide(900));
  addEventListener("scroll", () => current && place(), { passive: true, capture: true });
  addEventListener("resize", () => current && place(), { passive: true });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && menu.classList.contains("show")) hide(); });

  function size(n) {
    if (!n) return "";
    const u = ["B", "KB", "MB", "GB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `≈ ${n.toFixed(i > 1 ? 1 : 0)} ${u[i]}`;
  }
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }

  function renderLoading() {
    menu.textContent = "";
    menu.append(el("div", "note", "Reading the qualities…"));
    const scan = el("div", "scan"); for (let i = 0; i < 5; i++) scan.append(el("i")); menu.append(scan);
  }

  function renderOptions(info) {
    menu.textContent = "";
    menu.append(el("div", "head", info.title || "Choose quality"));
    let audioStarted = false;
    for (const o of info.options) {
      const audio = /^Audio/.test(o.label);
      if (audio && !audioStarted) { menu.append(el("div", "sep")); audioStarted = true; }
      const b = el("button", "opt");
      b.setAttribute("role", "menuitem");
      b.append(el("span", "", o.label), el("span", "size", size(o.size)));
      b.addEventListener("click", () => send(b, { format: o.format, convert: o.convert, title: info.title }));
      menu.append(b);
    }
    menu.append(el("div", "sep"));
    const more = el("button", "opt more");
    more.append(el("span", "", "More options in YDM…"));
    more.addEventListener("click", () => send(more, {}));
    menu.append(more);
  }

  function renderError(message) {
    menu.textContent = "";
    menu.append(el("div", "note err", message));
    const src = current && (current.currentSrc || current.src);
    if (src && /^https?:/i.test(src)) {
      const b = el("button", "opt"); b.append(el("span", "", "Download the video file"));
      b.addEventListener("click", () => send(b, { src }));
      menu.append(b);
    }
  }

  async function send(row, choice) {
    row.classList.add("sent");
    const label = row.querySelector(".size") || row.firstChild;
    label.textContent = "Sending…";
    const ok = await chrome.runtime.sendMessage({ type: "grab-video", url: pageUrl, ...choice }).catch(() => false);
    label.textContent = ok ? "Started" : "YDM isn't running";
    if (ok) setTimeout(hide, 700);
  }

  button.addEventListener("click", async (e) => {
    e.preventDefault(); e.stopPropagation();
    if (!current) return;
    if (menu.classList.toggle("show")) {
      button.classList.add("open");
      clearTimeout(hideTimer);
      renderLoading();
      const res = await chrome.runtime.sendMessage({ type: "video-info", url: pageUrl }).catch(() => null);
      if (!menu.classList.contains("show")) return;
      if (res && res.options) renderOptions(res);
      else renderError(res && res.offline ? "Start YDM on your PC first." : `Couldn't read this video${res && res.error ? `: ${res.error}` : "."}`);
    } else {
      button.classList.remove("open");
    }
  });

  // "Download all links" from the context menu
  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg.type !== "collect-links" || window.top !== window) return;
    const files = /\.(zip|rar|7z|tar|gz|xz|iso|exe|msi|apk|dmg|pdf|epub|docx?|xlsx?|pptx?|mp4|mkv|webm|avi|mov|mp3|flac|m4a|wav|jpe?g|png|gif)(\?|$)/i;
    const links = [...new Set([...document.querySelectorAll("a[href]")].map((a) => a.href)
      .filter((h) => /^https?:/i.test(h) && files.test(h)))];
    reply(links.slice(0, 500));
  });
})();
