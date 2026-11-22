// The screenshot editor: stitches the captured screens (from ShotDB) into one picture, lets you crop,
// draw, highlight, point, box, write and blur, then saves PNG / JPG / PDF or copies the picture.
// Only the part of the picture on screen is drawn (one window-sized canvas), so very long pages stay
// smooth. Edits are kept as shapes in picture coordinates: undo/redo is just the list of shapes.

(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const stage = $("stage"), spacer = $("spacer"), view = $("view"), vctx = view.getContext("2d");
  const PAD = 28;                                // room round the picture
  const MAX_SIDE = 32000, MAX_AREA = 140e6;      // canvas limits in Chrome and Firefox, with room to spare
  const STAGE = "#0B0D0F", ACCENT = "#FFB547";

  // icons: the app's drawing (24-unit grid, 1.7 strokes, round ends)
  const svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" ` +
    `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const ICON = {
    hand: '<path d="M8 12.5V6a1.5 1.5 0 0 1 3 0v5.5M11 11V4.5a1.5 1.5 0 0 1 3 0V11M14 11V6a1.5 1.5 0 0 1 3 0v8c0 3.6-2.6 6-6 6-2.6 0-4.1-1.3-5.4-3.5l-1.7-2.9a1.4 1.4 0 0 1 2.3-1.6L8 14"/>',
    crop: '<path d="M6 2.5V16a2 2 0 0 0 2 2h13.5M2.5 6H16a2 2 0 0 1 2 2v13.5"/>',
    pen: '<path d="M4 20l1-4L15.5 5.5a2.1 2.1 0 0 1 3 3L8 19z"/><path d="M13.5 7.5l3 3"/>',
    marker: '<path d="M9 15l-4 4h5l2-2"/><path d="M9 15l8.5-8.5a2.1 2.1 0 0 1 3 3L12 18z"/><path d="M14.5 21H20"/>',
    arrow: '<path d="M5 19L19 5M10 5h9v9"/>',
    box: '<rect x="4" y="5" width="16" height="14" rx="2"/>',
    text: '<path d="M5 7V4.5h14V7M12 4.5v15M9 19.5h6"/>',
    blur: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 12h16M12 4v16"/>' +
          '<path d="M4.8 4.8h6.4v6.4H4.8zM12.8 12.8h6.4v6.4h-6.4z" fill="currentColor" stroke="none" opacity=".45"/>',
    undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    redo: '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    minus: '<path d="M5 12h14"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 5V4H4v12h1"/>',
    save: '<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14"/>',
    image: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="10" r="1.6" fill="currentColor"/>' +
           '<path d="M3.5 17l6-4.5 3.5 3 3-2.5 4.5 3.5"/>',
    doc: '<path d="M6 3.5h8l4.5 4.5v12.5H6z"/><path d="M14 3.5V8h4.5"/>',
    caret: '<path d="M7 10l5 5 5-5"/>',
  };

  const TOOLS = [["hand", "Move around", "H"], ["crop", "Crop", "C"], ["pen", "Pen", "P"], ["marker", "Highlighter", "M"],
                 ["arrow", "Arrow", "A"], ["box", "Box", "R"], ["text", "Text", "T"], ["blur", "Blur (hide private things)", "B"]];
  const COLORS = [["#FF4D4D", "Red"], ["#FFB547", "Amber"], ["#FFE14D", "Yellow"], ["#3DDBC0", "Teal"],
                  ["#4DA3FF", "Blue"], ["#FFFFFF", "White"], ["#111111", "Black"]];
  const SIZES = [[2, "Thin"], [4, "Medium"], [8, "Thick"]];
  const TEXT_PX = { 2: 16, 4: 24, 8: 38 };

  // ---- state -------------------------------------------------------------------------------------------
  let rec = null;                    // the capture record from ShotDB
  let base = null, W = 0, H = 0;     // the stitched picture, full resolution
  let S = 1;                         // picture pixels per page (CSS) pixel
  let z = 1;                         // zoom: screen pixels per picture pixel
  let offX = 0, offY = 0;            // where the picture starts inside the scrolling area
  let tool = "hand", color = COLORS[0][0], size = 4;
  let doc = { annots: [], crop: null };
  const undoStack = [], redoStack = [];
  let draft = null, pan = null, typing = null;
  let pdfMode = (() => { try { return localStorage.getItem("ydm-pdf") || "long"; } catch { return "long"; } })();
  const blurCache = new WeakMap();

  // ---- small helpers -----------------------------------------------------------------------------------
  const area = () => doc.crop || { x: 0, y: 0, w: W, h: H };
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const dprNow = () => window.devicePixelRatio || 1;
  let toastTimer = 0;
  function toast(text, bad = false) {
    const t = $("toast");
    t.textContent = text;
    t.classList.toggle("err", bad);
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), bad ? 5000 : 2600);
  }
  function fail(text) {
    $("loading").hidden = true;
    $("failed").hidden = false;
    $("failedText").textContent = text;
  }
  function fileName() {
    const clean = (rec.title || "screenshot").replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ").replace(/\s+/g, " ").trim()
      .slice(0, 80) || "screenshot";
    const d = new Date(rec.created || Date.now());
    const p = (n) => String(n).padStart(2, "0");
    return `${clean} - ${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}.${p(d.getMinutes())}`;
  }
  function download(blob, ext, suffix = "") {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${fileName()}${suffix}.${ext}`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  }
  const toBlob = (c, type, quality) => new Promise((resolve, reject) =>
    c.toBlob((b) => (b ? resolve(b) : reject(new Error("the browser couldn't encode the picture"))), type, quality));
  function canvas(w, h) {
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(w));
    c.height = Math.max(1, Math.round(h));
    return c;
  }

  // ---- stitching -----------------------------------------------------------------------------------------
  async function stitch() {
    const frames = rec.frames;
    const first = await createImageBitmap(frames[0].blob);
    // picture pixels per page pixel (screen scaling × zoom): the page's own devicePixelRatio when the screen
    // matches it (width / width is off by rounding: 1573 / 1258 isn't 1.25, and that adds up down a long page)
    const ratio = first.width / (rec.vw || first.width);
    const s = rec.dpr && Math.abs(first.width - rec.vw * rec.dpr) <= 1 ? rec.dpr : ratio;
    // Everything in whole screen pixels: a screen taken at scroll position y starts at round(y × s) in the
    // picture, exactly where the browser drew it, so the screens meet without a seam.
    const px = (v) => Math.round(v * s);
    let outW, outH;
    if (rec.mode === "full") {
      const last = frames[frames.length - 1];
      outW = Math.min(px(rec.rect.w), first.width - px(rec.rect.x));                 // never past the screens
      outH = Math.min(px(rec.total), px(last.y) + Math.min(px(rec.rect.h), first.height - px(rec.rect.y)));
    } else if (rec.mode === "area") {
      outW = px(rec.crop.w);
      outH = px(rec.crop.h);
    } else {
      outW = first.width;
      outH = first.height;
    }
    const k = Math.min(1, MAX_SIDE / outW, MAX_SIDE / outH, Math.sqrt(MAX_AREA / (outW * outH)));
    base = canvas(outW * k, outH * k);
    W = base.width;
    H = base.height;
    S = s * k;
    const ctx = base.getContext("2d", { willReadFrequently: true });
    ctx.imageSmoothingQuality = "high";
    let covered = 0;                               // picture rows filled so far (full page)
    for (let i = 0; i < frames.length; i++) {
      const bmp = i === 0 ? first : await createImageBitmap(frames[i].blob);
      if (rec.mode === "full") {
        const r = rec.rect;
        const sx = px(r.x), sy = px(r.y), sw = Math.min(outW, bmp.width - sx), sh = Math.min(px(r.h), bmp.height - sy);
        let dy = px(frames[i].y);
        if (k === 1) {
          if (i > 0) dy = register(ctx, bmp, sx, sy, sw, dy, covered);
          // only what's new: the screen before keeps its rows, except the last two (they can be half drawn)
          const from = i === 0 ? 0 : Math.max(0, Math.min(sh, covered - 2 - dy));
          ctx.drawImage(bmp, sx, sy + from, sw, sh - from, 0, dy + from, sw, sh - from);
        } else {
          ctx.drawImage(bmp, sx, sy, sw, sh, 0, Math.round(dy * k), Math.round(sw * k), Math.round(sh * k) + 1);
        }
        covered = Math.max(covered, dy + sh);
      } else if (rec.mode === "area") {
        const c = rec.crop;
        ctx.drawImage(bmp, px(c.x), px(c.y), outW, outH, 0, 0, W, H);
      } else {
        ctx.drawImage(bmp, 0, 0, W, H);
      }
      bmp.close();
    }
    return k;
  }

  // Where does this screen really belong? Its first rows must repeat the rows the screen before already
  // put in the picture. Try the computed spot and up to 3 rows either side; keep the computed one unless
  // another fits clearly better (blank stretches fit everywhere, so they keep it too).
  const joins = [];                              // [computed row, correction, mean difference] per join
  function register(ctx, bmp, sx, sy, sw, dy, covered) {
    const n = Math.min(16, covered - 2 - dy);
    if (n < 2) return dy;
    const probe = canvas(sw, n);
    const px = probe.getContext("2d", { willReadFrequently: true });
    px.drawImage(bmp, sx, sy, sw, n, 0, 0, sw, n);
    const a = px.getImageData(0, 0, sw, n).data;
    const score = (d) => {
      if (dy + d < 0 || dy + d + n > covered - 2) return Infinity;
      const b = ctx.getImageData(0, dy + d, sw, n).data;
      let diff = 0;
      for (let i = 0; i < a.length; i += 8) diff += Math.abs(a[i] - b[i]) + Math.abs(a[i + 1] - b[i + 1]) + Math.abs(a[i + 2] - b[i + 2]);
      return diff;
    };
    let best = 0, bestScore = score(0);
    for (const d of [-1, 1, -2, 2, -3, 3]) {
      const s = score(d);
      if (s < bestScore * 0.5) { best = d; bestScore = s; }
    }
    joins.push([dy, best, Math.round(bestScore / (a.length / 8 * 3) * 100) / 100]);   // for the test
    return dy + best;
  }

  // ---- layout and drawing --------------------------------------------------------------------------------
  let layouts = 0;                               // how many times it was laid out (the test checks it settles)
  function layout() {
    layouts++;
    const a = area();
    // size the scroll area from the window, then again from what's left beside any scrollbars: it never
    // grows from its own scrollbars, so it can't rock back and forth
    const box = $("viewport");
    let cw = box.clientWidth, ch = box.clientHeight;
    const cwW = a.w * z + PAD * 2, chH = a.h * z + PAD * 2;
    for (let pass = 0; pass < 2; pass++) {
      spacer.style.width = `${Math.floor(Math.max(cw, cwW))}px`;
      spacer.style.height = `${Math.floor(Math.max(ch, chH))}px`;
      cw = stage.clientWidth;
      ch = stage.clientHeight;
    }
    const sw = Math.floor(Math.max(cw, cwW)), sh = Math.floor(Math.max(ch, chH));
    spacer.style.width = `${sw}px`;
    spacer.style.height = `${sh}px`;
    offX = Math.round((sw - a.w * z) / 2);
    offY = Math.round((sh - a.h * z) / 2);
    const dpr = dprNow();
    view.style.width = `${cw}px`;
    view.style.height = `${ch}px`;
    view.width = Math.max(1, Math.round(cw * dpr));
    view.height = Math.max(1, Math.round(ch * dpr));
    $("zoomLabel").textContent = `${Math.round(z * S * 100)}%`;
    render();
  }

  let raf = 0;
  const paint = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; render(); }); };

  function render() {
    const dpr = view.width / Math.max(1, stage.clientWidth);
    vctx.setTransform(1, 0, 0, 1, 0, 0);
    vctx.fillStyle = STAGE;
    vctx.fillRect(0, 0, view.width, view.height);
    if (!base) return;
    const a = area();
    const ox = offX - stage.scrollLeft, oy = offY - stage.scrollTop;     // picture's top-left on screen
    const cw = stage.clientWidth, ch = stage.clientHeight;
    // what part of the picture is on screen
    const x0 = Math.max(ox, 0), y0 = Math.max(oy, 0);
    const x1 = Math.min(ox + a.w * z, cw), y1 = Math.min(oy + a.h * z, ch);
    vctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // a soft shadow under the visible part of the sheet
    vctx.save();
    vctx.shadowColor = "rgba(0, 0, 0, .55)";
    vctx.shadowBlur = 22;
    vctx.shadowOffsetY = 4;
    vctx.fillStyle = "#000";
    vctx.fillRect(Math.max(ox, -40), Math.max(oy, -40), Math.min(ox + a.w * z, cw + 40) - Math.max(ox, -40),
                  Math.min(oy + a.h * z, ch + 40) - Math.max(oy, -40));
    vctx.restore();
    if (x1 <= x0 || y1 <= y0) return;
    const sx = a.x + (x0 - ox) / z, sy = a.y + (y0 - oy) / z, sw = (x1 - x0) / z, sh = (y1 - y0) / z;
    vctx.imageSmoothingEnabled = z < 2;
    vctx.imageSmoothingQuality = "high";
    vctx.drawImage(base, sx, sy, sw, sh, x0, y0, x1 - x0, y1 - y0);
    // shapes, in picture coordinates
    vctx.save();
    vctx.beginPath();
    vctx.rect(x0, y0, x1 - x0, y1 - y0);
    vctx.clip();
    vctx.setTransform(dpr * z, 0, 0, dpr * z, dpr * (ox - a.x * z), dpr * (oy - a.y * z));
    const seen = { x: sx, y: sy, w: sw, h: sh };
    for (const an of doc.annots) if (meets(bbox(an), seen)) drawShape(vctx, an);
    if (draft && draft.type !== "crop") drawShape(vctx, draft, true);
    vctx.restore();
    if (draft && draft.type === "crop") drawCropSelection(ox, oy, a, dpr);
    placeFloaters();
  }

  function drawCropSelection(ox, oy, a, dpr) {
    const r = norm(draft);
    const X = ox + (r.x - a.x) * z, Y = oy + (r.y - a.y) * z, Wd = r.w * z, Hd = r.h * z;
    vctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    vctx.save();
    vctx.beginPath();
    vctx.rect(ox, oy, a.w * z, a.h * z);
    vctx.rect(X, Y, Wd, Hd);
    vctx.fillStyle = "rgba(0, 0, 0, .55)";
    vctx.fill("evenodd");
    vctx.strokeStyle = ACCENT;
    vctx.lineWidth = 1.5;
    vctx.setLineDash([6, 4]);
    vctx.strokeRect(X, Y, Wd, Hd);
    vctx.setLineDash([]);
    const label = `${Math.round(r.w)} × ${Math.round(r.h)}`;
    vctx.font = "600 11.5px Segoe UI, system-ui, sans-serif";
    const tw = vctx.measureText(label).width + 12;
    const ly = Y > 26 ? Y - 24 : Y + 6;
    vctx.fillStyle = "#191C20";
    vctx.fillRect(X, ly, tw, 20);
    vctx.fillStyle = "#ECE8E1";
    vctx.fillText(label, X + 6, ly + 14);
    vctx.restore();
  }

  const norm = (d) => ({ x: Math.min(d.ax, d.bx), y: Math.min(d.ay, d.by), w: Math.abs(d.bx - d.ax), h: Math.abs(d.by - d.ay) });
  const meets = (p, q) => p.x < q.x + q.w && p.x + p.w > q.x && p.y < q.y + q.h && p.y + p.h > q.y;

  function bbox(an) {
    const m = (an.lw || 2) * 4;
    if (an.type === "pen" || an.type === "marker") {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const [x, y] of an.pts) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
      return { x: x0 - m, y: y0 - m, w: x1 - x0 + 2 * m, h: y1 - y0 + 2 * m };
    }
    if (an.type === "arrow") {
      return { x: Math.min(an.ax, an.bx) - m, y: Math.min(an.ay, an.by) - m,
               w: Math.abs(an.bx - an.ax) + 2 * m, h: Math.abs(an.by - an.ay) + 2 * m };
    }
    if (an.type === "text") return { x: an.x - 4, y: an.y - 4, w: an.tw + 8, h: an.th + 8 };
    const r = norm(an);
    return { x: r.x - m, y: r.y - m, w: r.w + 2 * m, h: r.h + 2 * m };
  }

  function halo(c) {                             // a thin outline in the opposite tone, so text reads anywhere
    const n = parseInt(c.slice(1), 16);
    const lum = (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
    return lum > 0.6 ? "rgba(0, 0, 0, .6)" : "rgba(255, 255, 255, .75)";
  }

  function pixelated(an) {                       // blur = big pixels, from the picture underneath
    let c = blurCache.get(an);
    if (c) return c;
    const r = norm(an);
    const block = Math.max(8, Math.round(Math.max(r.w, r.h) / 28));
    c = canvas(Math.ceil(r.w / block), Math.ceil(r.h / block));
    const x = c.getContext("2d");
    x.imageSmoothingEnabled = true;
    x.imageSmoothingQuality = "high";
    x.drawImage(base, r.x, r.y, r.w, r.h, 0, 0, c.width, c.height);
    blurCache.set(an, c);
    return c;
  }

  function drawShape(ctx, an, live = false) {
    ctx.save();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = ctx.fillStyle = an.color;
    ctx.lineWidth = an.lw;
    if (an.type === "pen" || an.type === "marker") {
      ctx.globalAlpha = an.type === "marker" ? 0.38 : 1;
      ctx.beginPath();
      const [p0, ...rest] = an.pts;
      ctx.moveTo(p0[0], p0[1]);
      if (!rest.length) ctx.lineTo(p0[0] + 0.01, p0[1]);
      for (let i = 0; i < rest.length; i++) {        // smooth: curve through the midpoints
        const p = rest[i], q = rest[i + 1];
        if (q) ctx.quadraticCurveTo(p[0], p[1], (p[0] + q[0]) / 2, (p[1] + q[1]) / 2);
        else ctx.lineTo(p[0], p[1]);
      }
      ctx.stroke();
    } else if (an.type === "arrow") {
      const ang = Math.atan2(an.by - an.ay, an.bx - an.ax);
      const head = Math.max(10 / Math.max(z, 0.01) * 0.6, an.lw * 4.2);
      const bx = an.bx - Math.cos(ang) * head * 0.6, by = an.by - Math.sin(ang) * head * 0.6;
      ctx.beginPath();
      ctx.moveTo(an.ax, an.ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(an.bx, an.by);
      ctx.lineTo(an.bx - Math.cos(ang - 0.42) * head, an.by - Math.sin(ang - 0.42) * head);
      ctx.lineTo(an.bx - Math.cos(ang + 0.42) * head, an.by - Math.sin(ang + 0.42) * head);
      ctx.closePath();
      ctx.lineWidth = an.lw * 0.6;
      ctx.fill();
      ctx.stroke();
    } else if (an.type === "box") {
      const r = norm(an);
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(r.x, r.y, r.w, r.h, Math.min(an.lw * 1.5, r.w / 2, r.h / 2));
      else ctx.rect(r.x, r.y, r.w, r.h);
      ctx.stroke();
    } else if (an.type === "blur") {
      const r = norm(an);
      if (live) {
        ctx.setLineDash([6 / z, 4 / z]);
        ctx.lineWidth = 1.5 / z;
        ctx.strokeStyle = ACCENT;
        ctx.strokeRect(r.x, r.y, r.w, r.h);
      } else if (r.w >= 1 && r.h >= 1) {
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(pixelated(an), r.x, r.y, r.w, r.h);
      }
    } else if (an.type === "text") {
      ctx.font = `600 ${an.fs}px "Segoe UI", system-ui, sans-serif`;
      ctx.textBaseline = "top";
      ctx.lineWidth = Math.max(1, an.fs / 7);
      ctx.strokeStyle = halo(an.color);
      an.text.split("\n").forEach((line, i) => {
        ctx.strokeText(line, an.x, an.y + i * an.fs * 1.25);
        ctx.fillText(line, an.x, an.y + i * an.fs * 1.25);
      });
    }
    ctx.restore();
  }

  // ---- editing -------------------------------------------------------------------------------------------
  const snapshot = () => ({ annots: doc.annots.slice(), crop: doc.crop });
  function change(fn) {
    undoStack.push(snapshot());
    if (undoStack.length > 200) undoStack.shift();
    redoStack.length = 0;
    fn();
    refreshButtons();
  }
  function undo() {
    if (typing) return cancelText();
    if (!undoStack.length) return;
    const cropBefore = doc.crop;
    redoStack.push(snapshot());
    doc = undoStack.pop();
    afterHistory(cropBefore);
  }
  function redo() {
    if (!redoStack.length) return;
    const cropBefore = doc.crop;
    undoStack.push(snapshot());
    doc = redoStack.pop();
    afterHistory(cropBefore);
  }
  function afterHistory(cropBefore) {
    draft = null;
    $("cropBar").hidden = true;
    refreshButtons();
    if (doc.crop !== cropBefore) fit(); else paint();
  }
  function refreshButtons() {
    $("undo").disabled = !undoStack.length;
    $("redo").disabled = !redoStack.length;
    const a = area();
    $("meta").textContent = [`${Math.round(a.w)} × ${Math.round(a.h)} px`,
      { full: "whole page", visible: "visible part", area: "selected area" }[rec.mode] || "",
      hostOf(rec.url)].filter(Boolean).join("  ·  ");
  }
  const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };

  function toImage(e) {
    const r = stage.getBoundingClientRect(), a = area();
    return { x: a.x + (e.clientX - r.left + stage.scrollLeft - offX) / z,
             y: a.y + (e.clientY - r.top + stage.scrollTop - offY) / z };
  }
  function toScreen(x, y) {                     // picture → position inside <main>
    const a = area();
    return { x: (x - a.x) * z + offX - stage.scrollLeft, y: (y - a.y) * z + offY - stage.scrollTop };
  }
  const inside = (p) => { const a = area(); return { x: clamp(p.x, a.x, a.x + a.w), y: clamp(p.y, a.y, a.y + a.h) }; };

  function setTool(t) {
    if (typing) commitText();
    if (tool === "crop" && t !== "crop") { draft = null; $("cropBar").hidden = true; }
    tool = t;
    document.querySelectorAll("[data-tool]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.tool === t)));
    stage.style.cursor = t === "hand" ? "grab" : t === "text" ? "text" : "crosshair";
    paint();
  }

  stage.addEventListener("pointerdown", (e) => {
    if (!base || e.button !== 0) return;
    if (typing) { commitText(); return; }
    if (tool === "hand") {
      pan = { x: e.clientX, y: e.clientY, sl: stage.scrollLeft, st: stage.scrollTop };
      stage.setPointerCapture(e.pointerId);
      stage.style.cursor = "grabbing";
      return;
    }
    const p = inside(toImage(e));
    if (tool === "text") { e.preventDefault(); openText(p); return; }
    e.preventDefault();
    stage.setPointerCapture(e.pointerId);
    const lw = size / z;
    if (tool === "pen" || tool === "marker") draft = { type: tool, color, lw: tool === "marker" ? lw * 3.5 : lw, pts: [[p.x, p.y]] };
    else draft = { type: tool, color, lw, ax: p.x, ay: p.y, bx: p.x, by: p.y };
    $("cropBar").hidden = true;
    paint();
  });
  stage.addEventListener("pointermove", (e) => {
    if (pan) {
      stage.scrollLeft = pan.sl - (e.clientX - pan.x);
      stage.scrollTop = pan.st - (e.clientY - pan.y);
      return;
    }
    if (!draft || !stage.hasPointerCapture(e.pointerId)) return;
    const p = inside(toImage(e));
    if (draft.pts) {
      const last = draft.pts[draft.pts.length - 1];
      if (Math.hypot(p.x - last[0], p.y - last[1]) * z >= 1.5) draft.pts.push([p.x, p.y]);
    } else if (draft.type === "arrow" && e.shiftKey) {           // Shift: straight, in 45° steps
      const ang = Math.round(Math.atan2(p.y - draft.ay, p.x - draft.ax) / (Math.PI / 4)) * (Math.PI / 4);
      const len = Math.hypot(p.x - draft.ax, p.y - draft.ay);
      draft.bx = draft.ax + Math.cos(ang) * len;
      draft.by = draft.ay + Math.sin(ang) * len;
    } else {
      draft.bx = p.x;
      draft.by = p.y;
    }
    paint();
  });
  const pointerEnd = (e) => {
    if (pan) {
      pan = null;
      stage.style.cursor = tool === "hand" ? "grab" : stage.style.cursor;
      return;
    }
    if (!draft || !stage.hasPointerCapture(e.pointerId)) return;
    stage.releasePointerCapture(e.pointerId);
    const d = draft;
    const tiny = 3 / z;
    if (d.type === "crop") {
      const r = norm(d);
      if (r.w < tiny || r.h < tiny) { draft = null; paint(); return; }
      $("cropBar").hidden = false;            // keep the selection: Crop / Cancel (Enter / Esc)
      paint();
      return;
    }
    draft = null;
    if (d.pts || (d.type === "arrow" ? Math.hypot(d.bx - d.ax, d.by - d.ay) >= tiny
                                     : Math.abs(d.bx - d.ax) >= tiny && Math.abs(d.by - d.ay) >= tiny)) {
      if (d.type === "blur") {                // whole pixels, so the blocks line up with the picture
        const r = norm(d);
        Object.assign(d, { ax: Math.floor(r.x), ay: Math.floor(r.y), bx: Math.ceil(r.x + r.w), by: Math.ceil(r.y + r.h) });
      }
      change(() => doc.annots.push(d));
    }
    paint();
  };
  stage.addEventListener("pointerup", pointerEnd);
  stage.addEventListener("pointercancel", pointerEnd);
  stage.addEventListener("scroll", () => paint(), { passive: true });

  function applyCrop() {
    if (!draft || draft.type !== "crop") return;
    const r = norm(draft), a = area();
    const x = Math.round(clamp(r.x, a.x, a.x + a.w)), y = Math.round(clamp(r.y, a.y, a.y + a.h));
    const crop = { x, y, w: Math.round(Math.min(r.x + r.w, a.x + a.w)) - x, h: Math.round(Math.min(r.y + r.h, a.y + a.h)) - y };
    draft = null;
    $("cropBar").hidden = true;
    if (crop.w >= 2 && crop.h >= 2) change(() => { doc.crop = crop; });
    setTool("hand");
    fit();
  }
  function cancelCrop() {
    draft = null;
    $("cropBar").hidden = true;
    paint();
  }

  function placeFloaters() {
    const bar = $("cropBar");
    if (!bar.hidden && draft && draft.type === "crop") {
      const r = norm(draft);
      const p = toScreen(r.x + r.w, r.y + r.h);
      const main = $("viewport");
      bar.style.left = `${clamp(p.x - bar.offsetWidth, 8, main.clientWidth - bar.offsetWidth - 8)}px`;
      bar.style.top = `${clamp(p.y + 10, 8, main.clientHeight - bar.offsetHeight - 8)}px`;
    }
    if (typing) {
      const p = toScreen(typing.x, typing.y);
      const box = $("textBox");
      box.style.left = `${p.x}px`;
      box.style.top = `${p.y}px`;
    }
  }

  // text: type straight onto the picture; Enter keeps it, Shift+Enter starts a new line, Esc drops it
  function openText(p) {
    const box = $("textBox");
    const px = TEXT_PX[size] || 24;
    typing = { x: p.x, y: p.y, fs: px / z, color };
    Object.assign(box.style, { fontSize: `${px}px`, color, width: "60px", height: `${px * 1.3}px` });
    box.value = "";
    box.hidden = false;
    placeFloaters();
    setTimeout(() => box.focus(), 0);
  }
  function fitTextBox() {
    const box = $("textBox");
    box.style.width = "10px";
    box.style.height = "10px";
    box.style.width = `${Math.max(60, box.scrollWidth + 6)}px`;
    box.style.height = `${box.scrollHeight}px`;
  }
  function commitText() {
    if (!typing) return;
    const box = $("textBox");
    const text = box.value.replace(/\s+$/g, "");
    const t = typing;
    typing = null;
    box.hidden = true;
    if (text.trim()) {
      vctx.save();
      vctx.font = `600 ${t.fs}px "Segoe UI", system-ui, sans-serif`;
      const tw = Math.max(...text.split("\n").map((l) => vctx.measureText(l).width));
      vctx.restore();
      change(() => doc.annots.push({ type: "text", color: t.color, x: t.x, y: t.y, fs: t.fs, text, tw,
                                     th: text.split("\n").length * t.fs * 1.25 }));
    }
    paint();
  }
  function cancelText() {
    typing = null;
    $("textBox").hidden = true;
    paint();
  }
  $("textBox").addEventListener("input", fitTextBox);
  $("textBox").addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commitText(); }
    else if (e.key === "Escape") { e.preventDefault(); cancelText(); }
  });
  $("textBox").addEventListener("blur", () => setTimeout(commitText, 0));

  // ---- zoom ----------------------------------------------------------------------------------------------
  function fitZoom() {
    const a = area();
    const zw = (stage.clientWidth - PAD * 2) / a.w, zh = (stage.clientHeight - PAD * 2) / a.h;
    const actual = 1 / S;                       // the size it had on the page
    return Math.max(0.02, rec.mode === "full" && !doc.crop ? Math.min(zw, actual) : Math.min(zw, zh, actual));
  }
  function fit() {
    z = fitZoom();
    layout();
    if (stage.scrollWidth > stage.clientWidth) {   // the vertical scrollbar took some width: fit again
      z = fitZoom();
      layout();
    }
    stage.scrollLeft = 0;
    stage.scrollTop = 0;
  }
  function setZoom(nz, cx, cy) {
    const r = stage.getBoundingClientRect();
    if (cx === undefined) { cx = r.left + stage.clientWidth / 2; cy = r.top + stage.clientHeight / 2; }
    const before = toImage({ clientX: cx, clientY: cy });
    z = clamp(nz, 0.02, 8 / S);
    layout();
    const a = area();
    stage.scrollLeft = (before.x - a.x) * z + offX - (cx - r.left);
    stage.scrollTop = (before.y - a.y) * z + offY - (cy - r.top);
    paint();
  }
  const STEPS = [0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1, 1.25, 1.5, 2, 3, 4];
  function zoomStep(dir) {
    const now = z * S;
    const next = dir > 0 ? STEPS.find((s) => s > now + 0.001) || now * 1.25 : [...STEPS].reverse().find((s) => s < now - 0.001) || now / 1.25;
    setZoom(next / S);
  }
  stage.addEventListener("wheel", (e) => {
    if (!e.ctrlKey || !base) return;
    e.preventDefault();
    setZoom(z * Math.exp(-e.deltaY * 0.0015), e.clientX, e.clientY);
  }, { passive: false });
  // the window, not the scroll area: the scroll area changes size whenever its scrollbars come and go
  let lastBox = "";
  new ResizeObserver(() => {
    const now = `${$("viewport").clientWidth}x${$("viewport").clientHeight}`;
    if (base && now !== lastBox) { lastBox = now; layout(); }
  }).observe($("viewport"));

  // ---- saving --------------------------------------------------------------------------------------------
  function compose(background) {
    const a = area();
    const c = canvas(a.w, a.h);
    const x = c.getContext("2d");
    if (background) { x.fillStyle = background; x.fillRect(0, 0, c.width, c.height); }
    x.drawImage(base, a.x, a.y, a.w, a.h, 0, 0, a.w, a.h);
    x.translate(-a.x, -a.y);
    for (const an of doc.annots) drawShape(x, an);
    return c;
  }

  async function busy(button, fn) {
    if (typing) commitText();
    const all = ["copy", "png", "jpg", "pdf", "pdfMore"].map($);
    all.forEach((b) => { b.disabled = true; });
    try { await fn(); } catch (e) { toast(`${button}: ${e.message || e}`, true); }
    finally { all.forEach((b) => { b.disabled = false; }); }
  }

  const savePng = () => busy("Couldn't save the PNG", async () => {
    download(await toBlob(compose(), "image/png"), "png");
    toast("Saved as PNG");
  });
  const saveJpg = () => busy("Couldn't save the JPG", async () => {
    download(await toBlob(compose("#FFFFFF"), "image/jpeg", 0.92), "jpg");
    toast("Saved as JPG");
  });
  const copyImage = () => busy("Couldn't copy", async () => {
    const blob = toBlob(compose(), "image/png");
    try {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    } catch (first) {                             // older engines want the Blob itself, not a promise
      await navigator.clipboard.write([new ClipboardItem({ "image/png": await blob })]).catch(() => { throw first; });
    }
    toast("Copied: paste it anywhere with Ctrl+V");
  });

  const PAGES = { a4: [595.28, 841.89], letter: [612, 792] };
  const savePdf = (mode = pdfMode) => busy("Couldn't save the PDF", async () => {
    const c = compose("#FFFFFF");
    const natural = 0.75 / S;                     // points per picture pixel at the size it had on the page
    const pages = [];
    let pw, ph, scale, sliceH, margin = 0;
    if (mode === "long") {
      scale = Math.min(natural, 14400 / c.width);
      pw = c.width * scale;
      sliceH = Math.floor(14400 / scale);         // a PDF page can't be taller than 200 inches
    } else {
      [pw, ph] = PAGES[mode];
      margin = 28.35;                             // 10 mm
      scale = Math.min(natural, (pw - margin * 2) / c.width);
      sliceH = Math.floor((ph - margin * 2) / scale);
    }
    for (let y = 0; y < c.height; y += sliceH) {
      const h = Math.min(sliceH, c.height - y);
      const part = canvas(c.width, h);
      part.getContext("2d").drawImage(c, 0, y, c.width, h, 0, 0, c.width, h);
      const jpeg = new Uint8Array(await (await toBlob(part, "image/jpeg", 0.9)).arrayBuffer());
      const dw = c.width * scale, dh = h * scale;
      const pageH = mode === "long" ? dh : ph;
      pages.push({ jpeg, w: part.width, h: part.height, pw, ph: pageH,
                   x: mode === "long" ? 0 : (pw - dw) / 2, y: mode === "long" ? 0 : pageH - margin - dh, dw, dh });
    }
    download(makePdf(pages), "pdf", { a4: " (A4)", letter: " (Letter)" }[mode] || "");
    toast(`Saved as PDF (${pages.length} page${pages.length === 1 ? "" : "s"})`);
  });

  // ---- toolbar -------------------------------------------------------------------------------------------
  function build() {
    $("copy").innerHTML = `${svg(ICON.copy)}Copy`;
    $("png").innerHTML = `${svg(ICON.save)}PNG`;
    $("jpg").innerHTML = `${svg(ICON.image)}JPG`;
    $("pdf").innerHTML = `${svg(ICON.doc)}PDF`;
    $("pdfMore").innerHTML = svg(ICON.caret);
    $("undo").innerHTML = svg(ICON.undo);
    $("redo").innerHTML = svg(ICON.redo);
    $("zoomOut").innerHTML = svg(ICON.minus);
    $("zoomIn").innerHTML = svg(ICON.plus);
    const tg = $("toolGroup");
    for (const [key, name, letter] of TOOLS) {
      const b = document.createElement("button");
      b.className = "tool";
      b.dataset.tool = key;
      b.title = `${name} (${letter})`;
      b.setAttribute("aria-label", name);
      b.setAttribute("aria-pressed", String(key === tool));
      b.innerHTML = svg(ICON[key]);
      b.addEventListener("click", () => setTool(key));
      tg.append(b);
    }
    const cg = $("colors");
    for (const [hex, name] of COLORS) {
      const b = document.createElement("button");
      b.className = "swatch";
      b.title = name;
      b.setAttribute("aria-label", name);
      b.innerHTML = `<i style="background:${hex}"></i>`;
      b.addEventListener("click", () => pickColor(hex, b));
      cg.append(b);
    }
    $("customColor").addEventListener("input", (e) => pickColor(e.target.value.toUpperCase(), null));
    const sg = $("sizes");
    SIZES.forEach(([px, name], i) => {
      const b = document.createElement("button");
      b.className = "size";
      b.title = `${name} lines and text`;
      b.setAttribute("aria-label", name);
      b.dataset.size = px;
      const d = [5, 8, 12][i];
      b.innerHTML = `<i style="width:${d}px;height:${d}px"></i>`;
      b.addEventListener("click", () => {
        size = px;
        sg.querySelectorAll(".size").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      });
      sg.append(b);
    });
    sg.querySelectorAll(".size").forEach((x) => x.setAttribute("aria-pressed", String(+x.dataset.size === size)));
    pickColor(color, cg.firstElementChild);
    document.querySelectorAll("#pdfMenu [data-pdf]").forEach((b) => {
      b.addEventListener("click", () => {
        pdfMode = b.dataset.pdf;
        try { localStorage.setItem("ydm-pdf", pdfMode); } catch { /* private window */ }
        markPdf();
        $("pdfMenu").hidden = true;
        savePdf(pdfMode);
      });
    });
    markPdf();
    $("copy").addEventListener("click", copyImage);
    $("png").addEventListener("click", savePng);
    $("jpg").addEventListener("click", saveJpg);
    $("pdf").addEventListener("click", () => savePdf());
    $("pdfMore").addEventListener("click", (e) => {
      e.stopPropagation();
      $("pdfMenu").hidden = !$("pdfMenu").hidden;
    });
    document.addEventListener("click", (e) => { if (!$("pdfMenu").contains(e.target)) $("pdfMenu").hidden = true; });
    $("undo").addEventListener("click", undo);
    $("redo").addEventListener("click", redo);
    $("zoomIn").addEventListener("click", () => zoomStep(1));
    $("zoomOut").addEventListener("click", () => zoomStep(-1));
    $("zoomLabel").addEventListener("click", () => {
      if (Math.abs(z - fitZoom()) < 0.001) setZoom(1 / S); else fit();
    });
    $("cropApply").addEventListener("click", applyCrop);
    $("cropCancel").addEventListener("click", cancelCrop);
    setTool("hand");
  }
  function pickColor(hex, button) {
    color = hex;
    document.querySelectorAll("#colors .swatch").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    document.querySelector(".swatch.custom").classList.toggle("on", !button);
    if (typing) { typing.color = hex; $("textBox").style.color = hex; }
  }
  function markPdf() {
    document.querySelectorAll("#pdfMenu [data-pdf]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.pdf === pdfMode)));
    $("pdf").title = `Save as PDF: ${{ long: "one long page", a4: "A4 pages", letter: "Letter pages" }[pdfMode]}`;
  }

  document.addEventListener("keydown", (e) => {
    if (!base || typing || e.target.closest?.("input, textarea")) return;
    const k = e.key.toLowerCase();
    if (e.ctrlKey || e.metaKey) {
      if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
      else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); redo(); }
      else if (k === "c" && !window.getSelection()?.toString()) { e.preventDefault(); copyImage(); }
      else if (k === "s") { e.preventDefault(); savePng(); }
      else if (k === "=" || k === "+") { e.preventDefault(); zoomStep(1); }
      else if (k === "-") { e.preventDefault(); zoomStep(-1); }
      else if (k === "0") { e.preventDefault(); fit(); }
      return;
    }
    if (e.altKey) return;
    if (k === "enter" && draft && draft.type === "crop") { e.preventDefault(); applyCrop(); return; }
    if (k === "escape") {
      if (!$("pdfMenu").hidden) $("pdfMenu").hidden = true;
      else if (draft) cancelCrop();
      else setTool("hand");
      return;
    }
    const t = TOOLS.find(([, , letter]) => letter.toLowerCase() === k);
    if (t) setTool(t[0]);
  });

  // ---- start ---------------------------------------------------------------------------------------------
  async function start() {
    build();
    const id = location.hash.slice(1);
    rec = id ? await ShotDB.get(id).catch(() => null) : null;
    if (!rec) return fail("This screenshot isn't kept any more. Take a new one from the YDM button in the toolbar.");
    document.title = `Screenshot · ${rec.title || hostOf(rec.url) || "page"}`;
    $("title").textContent = rec.title || rec.url || "Screenshot";
    $("title").title = rec.url || "";
    if (!rec.frames || !rec.frames.length) return fail(rec.error || "Nothing was captured.");
    let k;
    try {
      k = await stitch();
    } catch (e) {
      return fail(`The screens couldn't be put together: ${e.message || e}`);
    }
    const notes = [];
    if (rec.error) notes.push(`<b>Stopped early:</b> ${escapeHtml(rec.error)} What was captured so far is below.`);
    if (rec.note) notes.push(escapeHtml(rec.note));
    if (k < 1) notes.push(`The page is huge, so the picture was scaled to ${Math.round(k * 100)}% to fit what the browser can hold.`);
    if (notes.length) { $("note").innerHTML = notes.join("  "); $("note").hidden = false; }
    $("loading").hidden = true;
    ["copy", "png", "jpg", "pdf", "pdfMore"].forEach((b) => { $(b).disabled = false; });
    refreshButtons();
    fit();
  }
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // the test harness reads the state through this; nothing else uses it
  window.__ydmShot = { get state() { return { W, H, S, z, tool, doc, undo: undoStack.length, redo: redoStack.length,
                                              mode: rec && rec.mode, layouts, joins, ready: !$("loading").hidden ? false : true }; },
                       compose };
  start();
})();
