// YDM Integration — a "Download video" button over videos (quality menu, like IDM's panel) and a
// "Save photo" button over big pictures. On Instagram, Facebook, X, TikTok, Reddit… a video or photo
// in a feed belongs to a post: the button hands YDM that post's address, which is what yt-dlp and
// gallery-dl understand (the feed page itself isn't).
// The button sits in a corner of the video/photo the user picks (drag it, or the corner squares in its
// menu), can be hidden for this page (×) or for the whole site, and stays out of screenshots.
// Also the page side of the screenshot tool: measuring and scrolling the page for a full-page capture,
// and the "select an area" overlay.
// Everything lives in closed shadow roots so page styles can't touch it (and it can't touch the page).

(() => {
  if (window.__ydmContent) return;
  window.__ydmContent = true;

  const SETTINGS = { videoButton: true, imageButton: true, buttonCorner: "tr", hiddenSites: [] };
  let enabled = true, photos = true, corner = "tr", hiddenSites = [];
  let hiddenHere = false;                    // × pressed: no button on this page until it reloads
  const site = location.hostname.replace(/^www\./, "");
  chrome.storage.local.get(SETTINGS).then((s) => {
    enabled = s.videoButton; photos = s.imageButton; corner = s.buttonCorner; hiddenSites = s.hiddenSites || [];
  });
  chrome.storage.onChanged.addListener((c) => {
    if (c.videoButton) enabled = c.videoButton.newValue;
    if (c.imageButton) photos = c.imageButton.newValue;
    if (c.buttonCorner) { corner = c.buttonCorner.newValue; if (current) place(); }
    if (c.hiddenSites) { hiddenSites = c.hiddenSites.newValue || []; if (siteHidden()) hide(); }
  });
  const siteHidden = () => hiddenSites.includes(site);

  const host = document.createElement("div");
  host.style.cssText = "position:fixed;z-index:2147483647;top:0;left:0;display:none;pointer-events:none;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
    <style>
      * { box-sizing: border-box; }
      .wrap { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; width: 100%;
        font: 13px "Segoe UI", system-ui, sans-serif; color: #ECE8E1; }
      .wrap.left { align-items: flex-start; }
      .wrap.up { flex-direction: column-reverse; }
      .bar, .menu { pointer-events: auto; }
      .bar { display: flex; filter: drop-shadow(0 4px 10px rgba(0, 0, 0, .35)); }
      button.main, button.x { all: unset; box-sizing: border-box; display: flex; align-items: center; height: 30px;
        cursor: pointer; color: #ECE8E1; background: rgba(18, 20, 23, .94); border: 1px solid #2C3137;
        transition: border-color .15s; }
      button.main { gap: 7px; padding: 0 10px 0 9px; border-radius: 6px; font: 600 12.5px "Segoe UI", system-ui, sans-serif;
        touch-action: none; }
      button.x { display: none; width: 26px; justify-content: center; border-left: none; border-radius: 0 6px 6px 0; }
      .bar:hover button.main { border-radius: 6px 0 0 6px; }
      .bar:hover button.x { display: flex; }
      button.main:hover, button.main.open, button.x:hover { border-color: #FFB547; }
      button.main.dragging { cursor: grabbing; border-color: #3DDBC0; }
      .chev { width: 10px; height: 10px; opacity: .7; transition: transform .15s; }
      .open .chev { transform: rotate(180deg); }
      .up .chev { transform: rotate(180deg); }
      .up .open .chev { transform: none; }
      svg.logo { width: 16px; height: 16px; }
      .menu { display: none; width: min(270px, calc(100vw - 12px)); padding: 5px; border-radius: 8px; background: #191C20;
        border: 1px solid #2C3137; box-shadow: 0 10px 30px rgba(0, 0, 0, .45); max-height: 360px;
        overflow-x: hidden; overflow-y: auto; overscroll-behavior: contain; }
      .menu.show { display: block; }
      .head { padding: 7px 9px 6px; font-size: 11px; font-weight: 600; letter-spacing: .9px; text-transform: uppercase;
        color: #969BA2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .opt { all: unset; box-sizing: border-box; width: 100%; display: flex; justify-content: space-between; gap: 12px;
        padding: 8px 10px; border-radius: 5px; cursor: pointer; }
      .opt:hover, .opt:focus-visible { background: #22262B; }
      .opt > span:first-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .opt .size { flex: none; white-space: nowrap; color: #969BA2; font-variant-numeric: tabular-nums; }
      .opt.sent .size { color: #A6E35A; }
      .sep { height: 1px; background: #2C3137; margin: 5px 6px; }
      .note { padding: 9px 10px; color: #969BA2; font-size: 12px; overflow-wrap: anywhere; }
      .note.err { color: #FF6B5E; }
      .more { color: #FFB547; }
      .foot { display: flex; align-items: center; gap: 4px; padding: 6px 6px 3px 10px; border-top: 1px solid #2C3137;
        margin-top: 5px; }
      .foot .cap { color: #5E646C; font-size: 11px; margin-right: 4px; }
      .corner { all: unset; box-sizing: border-box; width: 22px; height: 22px; display: grid; place-items: center;
        border-radius: 4px; cursor: pointer; }
      .corner:hover, .corner:focus-visible { background: #22262B; }
      .corner svg { width: 14px; height: 14px; }
      .corner.on rect.dot { fill: #FFB547; }
      .hide-site { all: unset; margin-left: auto; padding: 3px 6px; border-radius: 4px; cursor: pointer; color: #969BA2;
        font-size: 11.5px; white-space: nowrap; }
      .hide-site:hover, .hide-site:focus-visible { background: #22262B; color: #ECE8E1; }
      .scan { display: flex; gap: 3px; padding: 4px 10px 10px; }
      .scan i { width: 7px; height: 10px; border-radius: 1.5px; background: #262B31; animation: lit 1s infinite; }
      .scan i:nth-child(2) { animation-delay: .1s } .scan i:nth-child(3) { animation-delay: .2s }
      .scan i:nth-child(4) { animation-delay: .3s } .scan i:nth-child(5) { animation-delay: .4s }
      @keyframes lit { 0%, 100% { background: #262B31 } 40% { background: #3DDBC0 } }
    </style>
    <div class="wrap">
      <div class="bar">
        <button class="main" title="Download this video with YDM (drag to move it to another corner)">
          <svg class="logo" viewBox="0 0 16 16" aria-hidden="true">
            <rect x="6.5" y="1" width="3" height="2.4" rx=".6" fill="#3DDBC0" opacity=".55"/>
            <rect x="6.5" y="4.3" width="3" height="2.4" rx=".6" fill="#3DDBC0" opacity=".75"/>
            <rect x="2.6" y="6.6" width="3" height="2.4" rx=".6" fill="#3DDBC0" opacity=".85"/>
            <rect x="10.4" y="6.6" width="3" height="2.4" rx=".6" fill="#3DDBC0" opacity=".85"/>
            <rect x="6.5" y="9.6" width="3" height="2.4" rx=".6" fill="#3DDBC0"/>
            <rect x="3" y="13.6" width="10" height="1.4" rx=".7" fill="#FFB547"/>
          </svg>
          <span class="label">Download video</span>
          <svg class="chev" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="#ECE8E1" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <button class="x" title="Hide on this page (until it reloads)" aria-label="Hide">
          <svg viewBox="0 0 10 10" width="10" height="10" aria-hidden="true"><path d="M2 2 8 8M8 2 2 8" stroke="#969BA2" stroke-width="1.4" stroke-linecap="round"/></svg>
        </button>
      </div>
      <div class="menu" role="menu"></div>
    </div>`;
  const wrap = root.querySelector(".wrap");
  const button = root.querySelector("button.main");
  const closeBtn = root.querySelector("button.x");
  const label = root.querySelector(".label");
  const menu = root.querySelector(".menu");
  (document.body || document.documentElement).appendChild(host);

  let current = null;       // the <video> or <img> the button belongs to
  let pageUrl = "";         // what we hand to YDM for it
  let hideTimer = 0;
  let capturing = false;    // a screenshot is being taken: stay out of the picture

  const isYouTube = /(^|\.)youtube\.com$/i.test(location.hostname);
  // Addresses of a single post (reel, photo, tweet…) on the sites that show posts in feeds
  const POST = [
    /instagram\.com\/(?:[\w.]+\/)?(?:p|reels?|tv|stories)\/[\w-]+/i,
    /facebook\.com\/(?:.*\/(?:videos|posts|photos|reel|permalink)\/|reel\/|watch\/?\?v=|story\.php|photo(?:\.php|\/\?)|share\/[rvp]\/)/i,
    /fb\.watch\/\w+/i,
    /(?:twitter|x)\.com\/\w+\/status\/\d+/i,
    /tiktok\.com\/@[\w.-]+\/(?:video|photo)\/\d+/i,
    /reddit\.com\/r\/\w+\/comments\/\w+/i,
    /threads\.(?:net|com)\/@[\w.]+\/post\/[\w-]+/i,
    /pinterest\.[a-z.]+\/pin\/[\w-]+/i,
    /tumblr\.com\/(?:[\w-]+\/)?(?:post\/)?\d+/i,
    /bsky\.app\/profile\/[\w.:-]+\/post\/\w+/i,
  ];
  const isPost = (href) => POST.some((re) => re.test(href));

  // The post a video/photo belongs to: the nearest post link around it (a feed, comments), else the page itself.
  function postFor(el) {
    let node = el;
    for (let depth = 0; node && node !== document.body && depth < 14; depth++, node = node.parentElement) {
      if (node.tagName === "A" && isPost(node.href)) return node.href;
      const link = [...node.querySelectorAll("a[href]")].find((a) => isPost(a.href));
      if (link) return link.href;
      if (node.tagName === "ARTICLE" || node.getAttribute("role") === "article") break;   // don't wander into the next post
    }
    return isPost(location.href) ? location.href : "";
  }

  // Which page address describes this video? YouTube previews on the home page aren't the page itself.
  function urlFor(video) {
    if (!isYouTube) return postFor(video) || location.href;
    if (/^\/(watch|shorts|live|embed)/.test(location.pathname)) return location.href;
    const link = video.closest('a[href*="/watch"], a[href*="/shorts/"]');
    return link ? link.href : "";
  }

  // The biggest version of a picture (srcset lists sizes; the page usually shows a small one)
  function bestSrc(img) {
    let best = img.currentSrc || img.src, bestW = img.naturalWidth || 0;
    for (const part of (img.srcset || "").split(/,\s+/)) {
      const [u, d] = part.trim().split(/\s+/);
      const w = !d ? 0 : d.endsWith("w") ? parseInt(d, 10) : d.endsWith("x") ? parseFloat(d) * (img.naturalWidth || 0) : 0;
      if (u && w > bestW) { best = new URL(u, location.href).href; bestW = w; }
    }
    return /^https?:/i.test(best) ? best : "";
  }
  const isPhoto = (el) => el && el.tagName === "IMG";

  // ---- where the button sits: the chosen corner of the video/photo, always inside the window ----------
  const BAR_H = 30, INSET = 10;
  function place() {
    if (!current || !current.isConnected) return hide();
    const r = current.getBoundingClientRect();
    if (r.width < 200 || r.height < 110 || r.bottom < 0 || r.top > innerHeight) return hide();
    const w = Math.min(280, innerWidth - 8);
    const right = corner[1] !== "l", bottom = corner[0] === "b";
    wrap.classList.toggle("left", !right);
    wrap.classList.toggle("up", bottom);
    host.style.width = `${w}px`;
    host.style.left = `${Math.max(4, Math.min(innerWidth - w - 4, right ? r.right - INSET - w : r.left + INSET))}px`;
    if (bottom) {                                   // anchored by its bottom edge: the menu opens upwards
      const barBottom = Math.min(innerHeight - 4, Math.max(BAR_H + 4, r.bottom - INSET));
      host.style.top = "auto";
      host.style.bottom = `${innerHeight - barBottom}px`;
      menu.style.maxHeight = `${Math.max(120, Math.min(360, barBottom - BAR_H - 14))}px`;
    } else {
      const top = Math.max(4, Math.min(innerHeight - BAR_H - 4, r.top + INSET));
      host.style.bottom = "auto";
      host.style.top = `${top}px`;
      menu.style.maxHeight = `${Math.max(120, Math.min(360, innerHeight - top - 48))}px`;
    }
    host.style.display = capturing ? "none" : "block";
  }
  function hide() { host.style.display = "none"; menu.classList.remove("show"); button.classList.remove("open"); current = null; }
  function scheduleHide(ms = 1600) {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => { if (!menu.classList.contains("show") && !dragging) hide(); }, ms);
  }

  function attach(el) {
    if (hiddenHere || siteHidden() || capturing) return;
    const photo = isPhoto(el);
    const url = photo ? postFor(el) : urlFor(el);
    if (!photo && !url) return;
    if (photo && !bestSrc(el) && !url) return;
    clearTimeout(hideTimer);
    if (current !== el) {
      current = el; pageUrl = url;
      label.textContent = photo ? "Save photo" : "Download video";
      button.title = (photo ? "Save this photo with YDM" : "Download this video with YDM") +
        " (drag to move it to another corner)";
      menu.classList.remove("show"); button.classList.remove("open");
      if (!photo) chrome.runtime.sendMessage({ type: "video-info", url, prefetch: true }).catch(() => {});  // warm it up
    }
    place();
  }

  function bigPhoto(img) {
    if (!photos || !img.complete) return false;
    const r = img.getBoundingClientRect();
    return r.width >= 180 && r.height >= 140 && Math.max(img.naturalWidth, img.naturalHeight) >= 300;
  }

  // Sites cover their videos and photos with overlays (Instagram does on every post), so look at
  // everything under the pointer, not just the top element.
  let pending = false;
  document.addEventListener("mousemove", (e) => {
    if ((!enabled && !photos) || pending || dragging) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      if (host.contains(e.target) || e.target === host) return;
      let found = null;
      for (const el of document.elementsFromPoint(e.clientX, e.clientY)) {
        if (el === host) return;
        if (el.tagName === "VIDEO" && enabled) { found = el; break; }
        if (el.tagName === "IMG" && bigPhoto(el)) { found = el; break; }
      }
      if (found) attach(found);
      else if (current) scheduleHide();
    });
  }, { passive: true, capture: true });
  host.addEventListener("mouseenter", () => clearTimeout(hideTimer));
  host.addEventListener("mouseleave", () => scheduleHide(900));
  addEventListener("scroll", () => current && !dragging && place(), { passive: true, capture: true });
  addEventListener("resize", () => current && place(), { passive: true });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && menu.classList.contains("show")) hide(); });

  // ---- move it: drag the button, it lands in the nearest corner of the video/photo -------------------
  let dragging = false, dragStart = null, justDragged = false;
  button.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    dragStart = { x: e.clientX, y: e.clientY, left: host.getBoundingClientRect().left, top: button.getBoundingClientRect().top };
    button.setPointerCapture(e.pointerId);        // a quick drag leaves the button at once: keep its events
  });
  button.addEventListener("pointermove", (e) => {
    if (!dragStart) return;
    if (!(e.buttons & 1)) { dragStart = null; return; }   // the press ended somewhere we didn't hear about
    if (!dragging && Math.hypot(e.clientX - dragStart.x, e.clientY - dragStart.y) < 6) return;
    if (!dragging) {
      dragging = true;
      button.classList.add("dragging");
      menu.classList.remove("show"); button.classList.remove("open");
      wrap.classList.remove("up");
      host.style.bottom = "auto";
    }
    host.style.left = `${dragStart.left + e.clientX - dragStart.x}px`;
    host.style.top = `${dragStart.top + e.clientY - dragStart.y}px`;
  });
  const endDrag = (e) => {
    if (dragging && current) {
      const r = current.getBoundingClientRect();
      const c = (e.clientY < r.top + r.height / 2 ? "t" : "b") + (e.clientX < r.left + r.width / 2 ? "l" : "r");
      corner = c;
      chrome.storage.local.set({ buttonCorner: c });
      justDragged = true;
      setTimeout(() => { justDragged = false; }, 0);
    }
    dragging = false; dragStart = null;
    button.classList.remove("dragging");
    if (current) place();
  };
  button.addEventListener("pointerup", endDrag);
  button.addEventListener("pointercancel", endDrag);

  closeBtn.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    hiddenHere = true;
    hide();
  });

  function size(n) {
    if (!n) return "";
    const u = ["B", "KB", "MB", "GB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `≈ ${n.toFixed(i > 1 ? 1 : 0)} ${u[i]}`;
  }
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
  const SVG = "http://www.w3.org/2000/svg";

  // Footer of every menu: which corner the button sits in, and "Hide on this site"
  function footer() {
    const foot = el("div", "foot");
    foot.append(el("span", "cap", "Button"));
    for (const [c, name, x, y] of [["tl", "Top left", 2, 2], ["tr", "Top right", 8.5, 2],
                                   ["bl", "Bottom left", 2, 8.5], ["br", "Bottom right", 8.5, 8.5]]) {
      const b = el("button", "corner" + (c === corner ? " on" : ""));
      b.title = `Put the button in the ${name.toLowerCase()} corner`;
      b.setAttribute("aria-label", name);
      const svg = document.createElementNS(SVG, "svg");
      svg.setAttribute("viewBox", "0 0 14 14");
      const frame = document.createElementNS(SVG, "rect");
      Object.entries({ x: 0.7, y: 0.7, width: 12.6, height: 12.6, rx: 2, fill: "none", stroke: "#5E646C", "stroke-width": 1.2 })
        .forEach(([k, v]) => frame.setAttribute(k, v));
      const dot = document.createElementNS(SVG, "rect");
      Object.entries({ class: "dot", x, y, width: 3.5, height: 3.5, rx: 0.8, fill: "#969BA2" })
        .forEach(([k, v]) => dot.setAttribute(k, v));
      svg.append(frame, dot);
      b.append(svg);
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        corner = c;
        chrome.storage.local.set({ buttonCorner: c });
        foot.querySelectorAll(".corner").forEach((x) => x.classList.toggle("on", x === b));
        place();
      });
      foot.append(b);
    }
    const hideSite = el("button", "hide-site", "Hide on this site");
    hideSite.title = `No YDM button on ${site} (the extension's popup brings it back)`;
    hideSite.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!hiddenSites.includes(site)) hiddenSites = [...hiddenSites, site];
      chrome.storage.local.set({ hiddenSites });
      hide();
    });
    foot.append(hideSite);
    return foot;
  }

  function renderLoading() {
    menu.textContent = "";
    menu.append(el("div", "note", "Reading the qualities…"));
    const scan = el("div", "scan"); for (let i = 0; i < 5; i++) scan.append(el("i")); menu.append(scan);
    menu.append(footer());
  }

  // The streams this page played (seen by the background): when a site can't be read, they are the way to
  // the whole video, not its 2 MB pieces.
  function streamRows(here) {
    const out = [];
    if (!here || !here.list || !here.list.length) return out;
    out.push(el("div", "head", "Streams this page played"));
    for (const m of here.list.slice(0, 4)) {
      const label = m.stream === "master" ? "Stream · every quality" : m.stream === "dash" ? "DASH stream · whole video"
        : m.stream === "quality" ? "Stream · one quality" : m.kind === "video" ? "Stream" : m.whole ? "Whole video file" : "Video file";
      const b = el("button", "opt");
      b.setAttribute("role", "menuitem");
      b.title = m.url;
      b.append(el("span", "", label), el("span", "size", size(m.size)));
      b.addEventListener("click", () => send(b, { stream: m }));
      out.push(b);
    }
    return out;
  }

  function renderOptions(info, here) {
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
    const streams = streamRows(here);
    if (streams.length) menu.append(el("div", "sep"), ...streams);
    menu.append(footer());
  }

  function renderPhoto() {
    menu.textContent = "";
    menu.append(el("div", "head", "Save with YDM"));
    const src = bestSrc(current);
    if (src) {
      const b = el("button", "opt");
      b.setAttribute("role", "menuitem");
      const dims = current.naturalWidth ? `${current.naturalWidth} × ${current.naturalHeight}` : "";
      b.append(el("span", "", "This photo"), el("span", "size", dims));
      b.addEventListener("click", () => send(b, { photo: src }));
      menu.append(b);
    }
    if (pageUrl) {
      const b = el("button", "opt");
      b.setAttribute("role", "menuitem");
      b.append(el("span", "", "Everything in this post"), el("span", "size", "full size"));
      b.title = "Every photo and video of the post, in full size";
      b.addEventListener("click", () => send(b, { post: pageUrl }));
      menu.append(b);
    }
    menu.append(footer());
  }

  function renderError(message, here) {
    menu.textContent = "";
    menu.append(el("div", "note err", message));
    const src = current && (current.currentSrc || current.src);
    if (src && /^https?:/i.test(src)) {
      const b = el("button", "opt"); b.append(el("span", "", "Download the video file"));
      b.addEventListener("click", () => send(b, { src }));
      menu.append(b);
    }
    const streams = streamRows(here);
    if (streams.length) menu.append(...streams);
    else if (here && here.segments) {
      menu.append(el("div", "note", `This page streams the video in ${here.segments} small pieces without a playlist YDM ` +
                                    "can use yet. Reload the page and play the video for a moment, then try again."));
    }
    menu.append(footer());
  }

  async function send(row, choice) {
    row.classList.add("sent");
    const out = row.querySelector(".size") || row.firstChild;
    out.textContent = "Sending…";
    const msg = choice.stream ? { type: "send-media", url: choice.stream.url, kind: choice.stream.kind, page: location.href }
      : choice.photo || choice.post
        ? { type: "grab-photo", src: choice.photo || "", post: choice.post || "", page: location.href }
        : { type: "grab-video", url: pageUrl, ...choice };
    const ok = await chrome.runtime.sendMessage(msg).catch(() => false);
    out.textContent = ok ? "Started" : "YDM isn't running";
    if (ok) setTimeout(hide, 700);
  }

  button.addEventListener("click", async (e) => {
    e.preventDefault(); e.stopPropagation();
    if (!current || justDragged) return;
    if (menu.classList.toggle("show")) {
      button.classList.add("open");
      clearTimeout(hideTimer);
      if (isPhoto(current)) { renderPhoto(); return; }
      renderLoading();
      const [res, here] = await Promise.all([
        chrome.runtime.sendMessage({ type: "video-info", url: pageUrl }).catch(() => null),
        chrome.runtime.sendMessage({ type: "media-here" }).catch(() => null)]);
      if (!menu.classList.contains("show")) return;
      if (res && res.options) renderOptions(res, here);
      else renderError(res && res.offline ? "Start YDM on your PC first."
        : `Couldn't read this video${res && res.error ? `: ${res.error}` : "."}`, here);
    } else {
      button.classList.remove("open");
    }
  });

  // ---- screenshots: the page side ---------------------------------------------------------------------
  const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  let shot = null;          // what a full-page capture changed, to put back afterwards

  // Pages like Gmail or Notion don't scroll themselves: a big inner panel does. Capture that one.
  function pickScroller() {
    const de = document.scrollingElement || document.documentElement;
    if (de.scrollHeight > innerHeight + 4) return null;
    let best = null, area = 0, seen = 0;
    for (const e of document.querySelectorAll("body *")) {
      if (++seen > 8000) break;
      if (e.clientHeight < 200 || e.scrollHeight <= e.clientHeight + 40) continue;
      if (!/(auto|scroll|overlay)/.test(getComputedStyle(e).overflowY)) continue;
      const r = e.getBoundingClientRect();
      if (r.width * r.height > area) { area = r.width * r.height; best = e; }
    }
    return best;
  }

  function forEachStyled(test, fn) {
    let seen = 0;
    for (const e of document.querySelectorAll("body *")) {
      if (++seen > 20000) break;
      const pos = getComputedStyle(e).position;
      if (test(pos)) fn(e);
    }
  }

  function measure() {
    const de = document.scrollingElement || document.documentElement;
    const s = shot.scroller;
    if (s) {
      const r = s.getBoundingClientRect();
      const x = Math.max(0, r.left), y = Math.max(0, r.top);
      return { total: s.scrollHeight, view: s.clientHeight,
               rect: { x, y, w: Math.min(innerWidth, r.left + s.clientWidth) - x, h: Math.min(innerHeight, r.top + s.clientHeight) - y } };
    }
    return { total: de.scrollHeight, view: innerHeight, rect: { x: 0, y: 0, w: de.clientWidth, h: innerHeight } };
  }

  // Scrolling for a capture must land at once: sites that turn on smooth scrolling (CSS or script) would
  // otherwise still be gliding when the screen is taken, and the screens wouldn't line up.
  const scrollPos = () => (shot && shot.scroller ? shot.scroller.scrollTop : scrollY);
  function jump(y) {
    if (shot && shot.scroller) shot.scroller.scrollTo({ top: y, behavior: "instant" });
    else window.scrollTo({ top: y, left: shot ? shot.scrollX : scrollX, behavior: "instant" });
  }
  async function settled(limit = 40) {            // the position stops changing (scripts that animate it)
    let last = scrollPos(), still = 0;
    for (let i = 0; i < limit && still < 2; i++) {
      await new Promise((r) => requestAnimationFrame(r));
      const now = scrollPos();
      still = now === last ? still + 1 : 0;
      last = now;
    }
  }
  function picturesReady(ms) {                    // pictures on screen finish loading before the shot
    const pending = [...document.images].filter((img) => {
      if (img.complete) return false;
      const r = img.getBoundingClientRect();
      return r.bottom > 0 && r.top < innerHeight && r.width > 0;
    });
    if (!pending.length) return Promise.resolve();
    return Promise.race([Promise.all(pending.map((img) => img.decode().catch(() => {}))), wait(ms)]);
  }
  function setStyle(list, e, prop, value) {
    list.push([e, prop, e.style.getPropertyValue(prop), e.style.getPropertyPriority(prop)]);
    e.style.setProperty(prop, value, "important");
  }

  async function shotBegin() {
    if (shot) await shotEnd();
    capturing = true;
    host.style.display = "none";
    const de = document.scrollingElement || document.documentElement;
    shot = { scrollX, scrollY, scroller: pickScroller(), changed: [], fixedTop: [], style: document.createElement("style") };
    shot.top = shot.scroller ? shot.scroller.scrollTop : 0;
    // no smooth scrolling anywhere, no scrollbars in the picture (":root *" outranks a site's "html { … !important }")
    shot.style.textContent = ":root,:root *{scroll-behavior:auto!important}:root,:root body{scrollbar-width:none!important}" +
      "::-webkit-scrollbar{display:none!important;width:0!important;height:0!important}";
    (document.head || document.documentElement).append(shot.style);
    for (const e of [document.documentElement, document.body, shot.scroller]) {
      if (e) setStyle(shot.changed, e, "scroll-behavior", "auto");
    }
    // sticky bars would repeat on every screen: they stay where they are in the page instead.
    // Fixed things at the bottom of the screen (cookie bars, chat bubbles) would land mid-page: hidden.
    // Fixed things at the top (headers) show on the first screen only.
    forEachStyled((p) => p === "sticky" || p === "fixed", (e) => {
      if (e === host || e === picker) return;
      if (getComputedStyle(e).position === "sticky") return setStyle(shot.changed, e, "position", "static");
      const r = e.getBoundingClientRect();
      if (r.top >= innerHeight / 2) setStyle(shot.changed, e, "visibility", "hidden");
      else shot.fixedTop.push(e);
    });
    jump(0);
    await nextFrame();
    await settled();
    await picturesReady(400);
    return { ...measure(), vw: innerWidth, dpr: devicePixelRatio, title: document.title, url: location.href,
             inner: !!shot.scroller, de: de.clientWidth };
  }

  async function shotScroll(y, index) {
    if (!shot) return null;
    if (index === 1 && !shot.scroller) {
      for (const e of shot.fixedTop) setStyle(shot.changed, e, "visibility", "hidden");
    }
    jump(y);
    await nextFrame();
    await settled();
    await picturesReady(400);                     // lazy pictures get a moment to appear
    const m = measure();
    return { y: scrollPos(), total: m.total };
  }

  async function shotEnd() {
    if (!shot) return true;
    const s = shot;
    if (s.scroller) s.scroller.scrollTo({ top: s.top, behavior: "instant" });
    window.scrollTo({ top: s.scrollY, left: s.scrollX, behavior: "instant" });
    for (const [e, prop, v, p] of s.changed.reverse()) v ? e.style.setProperty(prop, v, p) : e.style.removeProperty(prop);
    s.style.remove();
    shot = null;
    capturing = false;
    return true;
  }

  // "Select an area": dim the page, drag a rectangle; Esc cancels
  let picker = null;
  function selectArea() {
    return new Promise((resolve) => {
      capturing = true;
      host.style.display = "none";
      picker = document.createElement("div");
      picker.style.cssText = "position:fixed;inset:0;z-index:2147483647;cursor:crosshair;";
      const pr = picker.attachShadow({ mode: "closed" });
      pr.innerHTML = `
        <style>
          .dim { position: fixed; inset: 0; background: rgba(8, 10, 12, .42); }
          .sel { position: fixed; display: none; outline: 1.5px solid #FFB547; box-shadow: 0 0 0 100vmax rgba(8, 10, 12, .42); }
          .size { position: fixed; display: none; padding: 3px 7px; border-radius: 4px; background: #191C20; color: #ECE8E1;
            font: 600 11.5px "Segoe UI", system-ui, sans-serif; font-variant-numeric: tabular-nums; pointer-events: none; }
          .hint { position: fixed; top: 14px; left: 50%; transform: translateX(-50%); padding: 8px 14px; border-radius: 7px;
            background: #191C20; border: 1px solid #2C3137; color: #ECE8E1; font: 13px "Segoe UI", system-ui, sans-serif;
            box-shadow: 0 8px 24px rgba(0, 0, 0, .4); pointer-events: none; }
          .hint b { color: #FFB547; font-weight: 600; }
        </style>
        <div class="dim"></div><div class="sel"></div><div class="size"></div>
        <div class="hint">Drag to select an area · <b>Esc</b> to cancel</div>`;
      const dim = pr.querySelector(".dim"), sel = pr.querySelector(".sel"), tag = pr.querySelector(".size");
      const hint = pr.querySelector(".hint");
      document.documentElement.append(picker);
      let start = null, rect = null;
      const draw = (x, y) => {
        rect = { x: Math.min(start.x, x), y: Math.min(start.y, y), w: Math.abs(x - start.x), h: Math.abs(y - start.y) };
        Object.assign(sel.style, { display: "block", left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.w}px`, height: `${rect.h}px` });
        tag.textContent = `${Math.round(rect.w)} × ${Math.round(rect.h)}`;
        Object.assign(tag.style, { display: "block", left: `${rect.x}px`, top: `${Math.max(4, rect.y - 26)}px` });
      };
      const finish = async (value) => {
        removeEventListener("keydown", onKey, true);
        picker.remove(); picker = null;
        await nextFrame();
        await wait(60);
        capturing = false;
        resolve(value);
      };
      const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(null); } };
      addEventListener("keydown", onKey, true);
      picker.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        start = { x: e.clientX, y: e.clientY };
        dim.style.display = "none"; hint.style.display = "none";
        picker.setPointerCapture(e.pointerId);
      });
      picker.addEventListener("pointermove", (e) => { if (start) draw(e.clientX, e.clientY); });
      picker.addEventListener("pointerup", (e) => {
        if (!start) return;
        draw(e.clientX, e.clientY);
        finish(rect && rect.w >= 8 && rect.h >= 8 ? rect : null);
      });
    });
  }

  // background → page
  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (window.top !== window) return;
    if (msg.type === "collect-links") {             // "Download all links" from the context menu
      const files = /\.(zip|rar|7z|tar|gz|xz|iso|exe|msi|apk|dmg|pdf|epub|docx?|xlsx?|pptx?|mp4|mkv|webm|avi|mov|mp3|flac|m4a|wav|jpe?g|png|gif)(\?|$)/i;
      const links = [...new Set([...document.querySelectorAll("a[href]")].map((a) => a.href)
        .filter((h) => /^https?:/i.test(h) && files.test(h)))];
      reply(links.slice(0, 500));
      return;
    }
    const work = { "shot-begin": () => shotBegin(), "shot-scroll": () => shotScroll(msg.y, msg.index),
                   "shot-end": () => shotEnd(), "shot-select": () => selectArea(),
                   "shot-ping": async () => ({ ok: true, dpr: devicePixelRatio, title: document.title, url: location.href,
                                               w: innerWidth, h: innerHeight }) }[msg.type];
    if (!work) return;
    work().then(reply, () => reply(null));
    return true;
  });
})();
