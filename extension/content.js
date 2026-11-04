// YDM Integration — a "Download video" button over videos (quality menu, like IDM's panel) and a
// "Save photo" button over big pictures. On Instagram, Facebook, X, TikTok, Reddit… a video or photo
// in a feed belongs to a post: the button hands YDM that post's address, which is what yt-dlp and
// gallery-dl understand (the feed page itself isn't).
// Lives in a closed shadow root so page styles can't touch it (and it can't touch the page).

(() => {
  if (window.__ydmContent) return;
  window.__ydmContent = true;

  let enabled = true, photos = true;
  chrome.storage.local.get({ videoButton: true, imageButton: true }).then((s) => {
    enabled = s.videoButton; photos = s.imageButton;
  });
  chrome.storage.onChanged.addListener((c) => {
    if (c.videoButton) enabled = c.videoButton.newValue;
    if (c.imageButton) photos = c.imageButton.newValue;
  });

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
        <span class="label">Download video</span>
        <svg class="chev" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="#ECE8E1" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
      <div class="menu" role="menu"></div>
    </div>`;
  const button = root.querySelector("button.main");
  const label = root.querySelector(".label");
  const menu = root.querySelector(".menu");
  (document.body || document.documentElement).appendChild(host);

  let current = null;       // the <video> or <img> the button belongs to
  let pageUrl = "";         // what we hand to YDM for it
  let hideTimer = 0;

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

  function attach(el) {
    const photo = isPhoto(el);
    const url = photo ? postFor(el) : urlFor(el);
    if (!photo && !url) return;
    if (photo && !bestSrc(el) && !url) return;
    clearTimeout(hideTimer);
    if (current !== el) {
      current = el; pageUrl = url;
      label.textContent = photo ? "Save photo" : "Download video";
      button.title = photo ? "Save this photo with YDM" : "Download this video with YDM";
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
    if ((!enabled && !photos) || pending) return;
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
    const msg = choice.photo || choice.post
      ? { type: "grab-photo", src: choice.photo || "", post: choice.post || "", page: location.href }
      : { type: "grab-video", url: pageUrl, ...choice };
    const ok = await chrome.runtime.sendMessage(msg).catch(() => false);
    label.textContent = ok ? "Started" : "YDM isn't running";
    if (ok) setTimeout(hide, 700);
  }

  button.addEventListener("click", async (e) => {
    e.preventDefault(); e.stopPropagation();
    if (!current) return;
    if (menu.classList.toggle("show")) {
      button.classList.add("open");
      clearTimeout(hideTimer);
      if (isPhoto(current)) { renderPhoto(); return; }
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
