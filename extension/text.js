// The text editor ("Copy with YDM"): the copied text, checked as you type (JSON / YAML / XML, with the line
// of the first problem), coloured, with line numbers, find & replace, and a palette of tools (textkit.js):
// format, convert, encode, hash, hide secrets, encrypt, case and line tools. Copy it back or save it as a file.
// Edits go through document.execCommand("insertText") so the browser's own undo (Ctrl+Z) covers the tools too.
// The text is kept in IndexedDB (TextDB) as you type, so a reload or reopening the tab brings it back.

(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ta = $("ta"), hl = $("hl"), gutter = $("gutter"), editor = $("editor"), mark = $("lineMark");
  const HL_LIMIT = 300000;                         // longer texts aren't coloured (stays fast)
  const PREFIX = "YDM-AES256:";

  const svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" ` +
    `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const ICON = {
    open: '<path d="M3 7v11.5h18V9h-9.5l-2-3H3z"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 5V4H4v12h1"/>',
    save: '<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14"/>',
    caret: '<path d="M7 10l5 5 5-5"/>',
    plugins: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/>' +
             '<rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><path d="M16.75 13.5v6.5M13.5 16.75H20"/>',
    format: '<path d="M4 6h16M8 10h12M8 14h12M4 18h16"/>',
    secrets: '<path d="M3 12s3.5-6 9-6c1.6 0 3 .5 4.2 1.2M21 12s-3.5 6-9 6c-1.6 0-3-.5-4.2-1.2"/><path d="M4 20L20 4"/>',
    lock: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    unlock: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 7.6-1.7"/>',
    find: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>',
    wrap: '<path d="M4 6h16M4 12h13a3 3 0 0 1 0 6h-5"/><path d="M14 15.5L11.5 18l2.5 2.5"/><path d="M4 18h4"/>',
    smaller: '<path d="M4 18l4.5-12L13 18M5.7 14h5.6"/><path d="M15 12h6"/>',
    bigger: '<path d="M4 18l4.5-12L13 18M5.7 14h5.6"/><path d="M15 12h6M18 9v6"/>',
    undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    redo: '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    up: '<path d="M7 14l5-5 5 5"/>',
    down: '<path d="M7 10l5 5 5-5"/>',
    close: '<path d="M7 7l10 10M17 7L7 17"/>',
  };
  const TYPES = [["txt", "Text"], ["json", "JSON"], ["yaml", "YAML"], ["yml", "YAML"], ["xml", "XML"], ["md", "Markdown"],
                 ["csv", "CSV"], ["env", "Environment"], ["ini", "Settings (INI)"], ["log", "Log"], ["html", "HTML"],
                 ["sql", "SQL"], ["sh", "Shell script"], ["ps1", "PowerShell"], ["py", "Python"], ["js", "JavaScript"]];

  // ---- state -------------------------------------------------------------------------------------------
  let rec = null;
  let kind = "text";                               // what the text is now (auto-detected or picked)
  let saveType = "";                               // the file type the user picked ("" = from the kind)
  let check = { ok: true, message: "" };
  const pref = (k, d) => { try { return JSON.parse(localStorage.getItem(`ydm-text-${k}`)) ?? d; } catch { return d; } };
  const setPref = (k, v) => { try { localStorage.setItem(`ydm-text-${k}`, JSON.stringify(v)); } catch { /* private */ } };
  let fontSize = pref("font", 13.5), wrap = pref("wrap", false);

  // ---- little helpers ------------------------------------------------------------------------------------
  let toastTimer = 0;
  function toast(text, bad = false) {
    const t = $("toast");
    t.textContent = text;
    t.classList.toggle("err", bad);
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), bad ? 5500 : 2600);
  }
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
  const lineHeight = () => Math.round(fontSize * 1.55);
  const unit = () => (kind === "json" || kind === "yaml" ? "  " : "\t");
  function lineOf(pos) {
    let n = 1;
    for (let i = ta.value.indexOf("\n"); i !== -1 && i < pos; i = ta.value.indexOf("\n", i + 1)) n++;
    return n;
  }

  // Replace [a, b) with text through the browser's editing, so Ctrl+Z undoes it like typing.
  function replaceRange(a, b, text, select = true) {
    ta.focus();
    ta.setSelectionRange(a, b);
    let ok = false;
    try { ok = document.execCommand("insertText", false, text); } catch { ok = false; }
    if (!ok || ta.value.slice(a, a + text.length) !== text) {
      ta.setRangeText(text, a, b, "end");
      ta.dispatchEvent(new Event("input"));
    }
    if (select) ta.setSelectionRange(a, a + text.length);
    else ta.setSelectionRange(a + text.length, a + text.length);
  }

  // ---- colouring -------------------------------------------------------------------------------------------
  function hlJson(t) {
    return t.replace(/("(?:\\.|[^"\\\n])*")(\s*:)?|(-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|\b(true|false|null)\b|([{}[\],:])|([&<>])/g,
      (m, str, colon, num, kw, p, ch) => {
        if (str) return colon ? `<span class="k">${esc(str)}</span><span class="p">${esc(colon)}</span>` : `<span class="s">${esc(str)}</span>`;
        if (num) return `<span class="n">${num}</span>`;
        if (kw) return `<span class="b">${kw}</span>`;
        if (p) return `<span class="p">${p}</span>`;
        return esc(ch);
      });
  }
  function commentAt(line) {
    let q = null;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (q) { if (c === "\\" && q === '"') i++; else if (c === q) q = null; continue; }
      if ((c === '"' || c === "'") && (i === 0 || /[\s:\[{,-]/.test(line[i - 1]))) q = c;
      else if (c === "#" && (i === 0 || /\s/.test(line[i - 1]))) return i;
    }
    return -1;
  }
  function yamlValue(v) {
    const t = v.trim();
    if (!t) return esc(v);
    const lead = v.slice(0, v.length - v.trimStart().length), trail = v.slice(lead.length + t.length);
    if (/^[[{]/.test(t)) return esc(lead) + hlJson(t) + esc(trail);
    const cls = /^["']/.test(t) ? "s" : /^[&*!]\S/.test(t) ? "a" : /^(true|false|null|~|yes|no|on|off)$/i.test(t) ? "b"
      : /^[-+]?(\d[\d_]*(\.\d*)?([eE][-+]?\d+)?|\.inf|\.nan|0x[\da-f]+|0o[0-7]+)$/i.test(t) ? "n" : /^[|>][-+0-9]*$/.test(t) ? "p" : "s";
    return `${esc(lead)}<span class="${cls}">${esc(t)}</span>${esc(trail)}`;
  }
  function hlYaml(t) {
    const out = [];
    let blockInd = -1;                                  // inside a | or > block: lines indented past this are text
    for (const line of t.split("\n")) {
      const ind = line.length - line.trimStart().length;
      if (blockInd >= 0) {
        if (line.trim() === "" || ind > blockInd) { out.push(`<span class="s">${esc(line)}</span>`); continue; }
        blockInd = -1;
      }
      const ci = commentAt(line);
      const body = ci >= 0 ? line.slice(0, ci) : line, comment = ci >= 0 ? line.slice(ci) : "";
      let html;
      if (/^\s*(---|\.\.\.)\s*$/.test(body) || /^%/.test(body)) html = `<span class="p">${esc(body)}</span>`;
      else {
        const m = /^(\s*(?:-\s+)*)("(?:\\.|[^"\\])*"|'(?:''|[^'])*'|[^\s#'"[{][^#]*?)(\s*:)(?=\s|$)(.*)$/.exec(body);
        const dash = (s) => esc(s).replace(/-/g, '<span class="p">-</span>');
        if (m) html = dash(m[1]) + `<span class="k">${esc(m[2])}</span><span class="p">${esc(m[3])}</span>` + yamlValue(m[4]);
        else {
          const d = /^(\s*(?:-(?:\s+|$))*)(.*)$/.exec(body);
          html = dash(d[1]) + yamlValue(d[2]);
        }
        if (/(^|\s)[|>][-+0-9]*\s*$/.test(body)) blockInd = ind;
      }
      out.push(html + (comment ? `<span class="c">${esc(comment)}</span>` : ""));
    }
    return out.join("\n");
  }
  function hlXml(t) {
    return t.replace(/(<!--[\s\S]*?-->)|(<!\[CDATA\[[\s\S]*?\]\]>)|(<\?[\s\S]*?\?>)|(<\/?)([\w:.-]+)((?:\s+[\w:.-]+(?:\s*=\s*(?:"[^"]*"|'[^']*'))?)*)(\s*\/?>)|([&<>])/g,
      (m, com, cdata, pi, open, name, attrs, close, ch) => {
        if (com) return `<span class="c">${esc(com)}</span>`;
        if (cdata) return `<span class="s">${esc(cdata)}</span>`;
        if (pi) return `<span class="p">${esc(pi)}</span>`;
        if (name) {
          const at = (attrs || "").replace(/([\w:.-]+)(\s*=\s*)?("[^"]*"|'[^']*')?/g, (x, k, eq, v) =>
            k ? `<span class="a">${esc(k)}</span>${eq ? esc(eq) : ""}${v ? `<span class="s">${esc(v)}</span>` : ""}` : esc(x));
          return `<span class="p">${esc(open)}</span><span class="t">${esc(name)}</span>${at}<span class="p">${esc(close)}</span>`;
        }
        return esc(ch);
      });
  }

  let paintQueued = false;
  function paint() {
    if (paintQueued) return;
    paintQueued = true;
    requestAnimationFrame(() => {
      paintQueued = false;
      const v = ta.value;
      const lit = v.length <= HL_LIMIT && kind !== "text";
      editor.classList.toggle("lit", lit);
      hl.hidden = !lit;
      if (lit) hl.innerHTML = (kind === "json" ? hlJson(v) : kind === "yaml" ? hlYaml(v) : hlXml(v)) + "\n";
      // line numbers, the first problem's line in red
      const n = v.split("\n").length;
      let nums = "";
      for (let i = 1; i <= n; i++) nums += (i === check.line ? `<b>${i}</b>` : i) + "\n";
      gutter.innerHTML = nums + "\n\n";
      fit();
      sync();
    });
  }
  function fit() {                                   // the coloured copy may not run under the scrollbars
    hl.style.right = `${ta.offsetWidth - ta.clientWidth}px`;
    hl.style.bottom = `${ta.offsetHeight - ta.clientHeight}px`;
  }
  function sync() {
    hl.scrollTop = ta.scrollTop;
    hl.scrollLeft = ta.scrollLeft;
    gutter.scrollTop = ta.scrollTop;
    if (!check.ok && check.line && !wrap) {
      mark.hidden = false;
      mark.style.top = `${12 + (check.line - 1) * lineHeight() - ta.scrollTop}px`;
    } else mark.hidden = true;
  }
  ta.addEventListener("scroll", sync, { passive: true });
  new ResizeObserver(() => { fit(); sync(); }).observe(ta);

  // ---- checking, counts, the status bar ------------------------------------------------------------------
  let checkTimer = 0;
  function recheck() {
    const lang = $("lang").value;
    kind = lang === "auto" ? TextKit.detect(ta.value) : lang;
    check = TextKit.validate(ta.value, kind);
    const pill = $("check");
    pill.classList.toggle("ok", check.ok && kind !== "text" && !!ta.value.trim());
    pill.classList.toggle("bad", !check.ok);
    pill.querySelector("span").textContent = !ta.value.trim() ? "Empty"
      : kind === "text" ? "Plain text" : check.ok ? check.message : check.message;
    pill.title = check.ok ? "" : "Click to go to the problem";
    const v = ta.value;
    const words = v.length < 2e6 ? (v.match(/\S+/g) || []).length : null;
    const bytes = new TextEncoder().encode(v).length;
    const size = bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
    $("counts").textContent = [`${v.length.toLocaleString()} characters`, words === null ? "" : `${words.toLocaleString()} words`,
                               `${v.split("\n").length.toLocaleString()} lines`, size].filter(Boolean).join("  ·  ");
    $("eol").textContent = v.includes("\r\n") ? "CRLF" : "LF";
    $("save").innerHTML = `${svg(ICON.save)}Save .${saveType || defaultType()}`;
    const locked = v.trimStart().startsWith(PREFIX);
    $("lock").innerHTML = `${svg(locked ? ICON.unlock : ICON.lock)}<span class="long">${locked ? "Decrypt" : "Encrypt"}</span>`;
    $("lock").title = locked ? "Decrypt with the password" : "Encrypt with a password (AES-256)";
    paint();
  }
  function position() {
    const a = ta.selectionStart, b = ta.selectionEnd;
    const before = ta.value.slice(Math.max(0, ta.value.lastIndexOf("\n", a - 1) + 1), a);
    $("pos").textContent = `Ln ${lineOf(a).toLocaleString()}, Col ${before.length + 1}`;
    $("selInfo").textContent = a !== b ? `${(b - a).toLocaleString()} selected` : "";
  }
  document.addEventListener("selectionchange", () => { if (document.activeElement === ta) position(); });
  $("check").addEventListener("click", () => {
    if (check.ok || !check.line) return;
    goToLine(check.line, check.col || 1);
  });
  function goToLine(line, col = 1) {
    const ls = ta.value.split("\n");
    let pos = 0;
    for (let i = 0; i < Math.min(line - 1, ls.length - 1); i++) pos += ls[i].length + 1;
    pos += Math.min(col - 1, (ls[line - 1] || "").length);
    ta.focus();
    ta.setSelectionRange(pos, pos);
    ta.scrollTop = Math.max(0, (line - 1) * lineHeight() - ta.clientHeight / 3);
    position();
  }

  // ---- keeping it ------------------------------------------------------------------------------------------
  let saveTimer = 0;
  function changed() {
    clearTimeout(checkTimer);
    checkTimer = setTimeout(recheck, 220);
    paint();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(keep, 700);
  }
  async function keep() {
    if (!rec) return;
    rec.text = ta.value;
    rec.lang = $("lang").value;
    rec.saveType = saveType;
    rec.updated = Date.now();
    try { await TextDB.put(rec); } catch { /* the editor still works; it just won't come back after a reload */ }
  }
  ta.addEventListener("input", changed);

  // ---- typing help: Tab indents, Enter keeps the indentation -------------------------------------------
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Tab" && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      const v = ta.value, a = ta.selectionStart, b = ta.selectionEnd;
      if (a === b && !e.shiftKey) { replaceRange(a, b, unit(), false); return; }
      const ls = v.lastIndexOf("\n", a - 1) + 1;
      let le = v.indexOf("\n", b > a && v[b - 1] === "\n" ? b - 1 : b);
      if (le < 0) le = v.length;
      const u = unit();
      const block = v.slice(ls, le).split("\n").map((l) => (e.shiftKey ? (l.startsWith(u) ? l.slice(u.length) : l.replace(/^[ \t]{1,4}/, "")) : u + l));
      replaceRange(ls, le, block.join("\n"));
      return;
    }
    if (e.key === "Enter" && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.isComposing) {
      const v = ta.value, a = ta.selectionStart;
      const cur = v.slice(v.lastIndexOf("\n", a - 1) + 1, a);
      const ind = /^[ \t]*/.exec(cur)[0];
      const more = (kind === "yaml" && /:\s*$/.test(cur)) || (kind === "json" && /[[{]\s*$/.test(cur)) ? unit() : "";
      if (!ind && !more) return;
      e.preventDefault();
      replaceRange(a, ta.selectionEnd, "\n" + ind + more, false);
    }
  });

  // ---- the tools ---------------------------------------------------------------------------------------------
  const TOOLS = TextKit.TOOLS;
  const byId = (id) => TOOLS.find((t) => t[1] === id);

  async function runTool(tool) {
    const [, , label, fn, opt = {}] = tool;
    const a = ta.selectionStart, b = ta.selectionEnd, sel = a !== b;
    const input = sel ? ta.value.slice(a, b) : ta.value;
    if (!opt.insert && !input) { toast("Nothing to work on yet: type or paste some text first.", true); return false; }
    try {
      let out;
      if (opt.password) {
        const pw = await askPassword(opt.password);
        if (pw === null) return false;
        toast(opt.password === "new" ? "Encrypting…" : "Decrypting…");
        out = opt.password === "new" ? await TextKit.encrypt(input, pw) : await TextKit.decrypt(input, pw);
      } else out = await fn(input);
      let note = "";
      if (out && typeof out === "object") { note = out.note; out = out.text; }
      if (opt.insert || sel) replaceRange(a, b, out, !opt.insert);
      else replaceRange(0, ta.value.length, out, false);
      if (!opt.insert && !sel) { ta.setSelectionRange(0, 0); ta.scrollTop = 0; }
      if (["json-pretty", "json-min", "json-sort", "yaml-json", "csv-json"].includes(tool[1]) && !sel) setLang("auto");
      recheck();
      toast(note || `${label}: done${sel ? " (on the selection)" : ""}`);
      return true;
    } catch (e) {
      toast(`${label}: ${e.message}`, true);
      return false;
    }
  }
  function setLang(v) { $("lang").value = v; recheck(); }

  // ---- palette (Ctrl+Shift+P) ------------------------------------------------------------------------------
  let palItems = [], palIndex = 0;
  function openPalette() {
    $("palette").hidden = false;
    const a = ta.selectionStart, b = ta.selectionEnd;
    $("paletteScope").textContent = a !== b ? `Works on the selection (${(b - a).toLocaleString()} characters). Inserts go at the cursor.`
      : "Works on the whole text. Select part of it first to change just that part.";
    $("paletteSearch").value = "";
    fillPalette("");
    $("paletteSearch").focus();
  }
  function closePalette(back = true) {
    $("palette").hidden = true;
    if (back) ta.focus();
  }
  function fillPalette(q) {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    palItems = TOOLS.filter((t) => words.every((w) => `${t[0]} ${t[2]} ${t[1]}`.toLowerCase().includes(w)));
    palIndex = 0;
    const list = $("paletteList");
    list.textContent = "";
    if (!palItems.length) { list.innerHTML = '<div class="none">No tool by that name.</div>'; return; }
    let group = "";
    palItems.forEach((t, i) => {
      if (t[0] !== group) {
        group = t[0];
        const g = document.createElement("div");
        g.className = "group";
        g.textContent = group;
        list.append(g);
      }
      const b = document.createElement("button");
      b.setAttribute("role", "option");
      b.dataset.i = i;
      b.textContent = t[2];
      if (t[4] && t[4].insert) { const s = document.createElement("span"); s.textContent = "inserts"; b.append(s); }
      b.addEventListener("click", () => { closePalette(false); runTool(t); });
      b.addEventListener("mousemove", () => markPalette(i));
      list.append(b);
    });
    markPalette(0);
  }
  function markPalette(i) {
    palIndex = i;
    $("paletteList").querySelectorAll("button").forEach((b) => {
      const on = +b.dataset.i === i;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", String(on));
      if (on) b.scrollIntoView({ block: "nearest" });
    });
  }
  $("paletteSearch").addEventListener("input", (e) => fillPalette(e.target.value));
  $("paletteSearch").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (palItems.length) markPalette((palIndex + 1) % palItems.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (palItems.length) markPalette((palIndex - 1 + palItems.length) % palItems.length); }
    else if (e.key === "Enter") { e.preventDefault(); const t = palItems[palIndex]; if (t) { closePalette(false); runTool(t); } }
    else if (e.key === "Escape") { e.preventDefault(); closePalette(); }
  });
  document.addEventListener("mousedown", (e) => {
    if (!$("palette").hidden && !$("palette").contains(e.target) && e.target !== $("plugins") && !$("plugins").contains(e.target)) closePalette(false);
    if (!$("saveMenu").hidden && !$("saveMenu").contains(e.target) && !$("saveMore").contains(e.target)) $("saveMenu").hidden = true;
  });

  // ---- dialogs (password, confirm) -------------------------------------------------------------------------
  function dialog({ title, text, fields = [], ok = "OK", validate }) {
    return new Promise((resolve) => {
      const m = $("modal");
      $("modalTitle").textContent = title;
      $("modalText").textContent = text || "";
      $("modalError").textContent = "";
      const box = $("modalFields");
      box.textContent = "";
      const inputs = fields.map((f) => {
        const label = document.createElement("label");
        label.textContent = f.label;
        const input = document.createElement("input");
        input.type = f.type || "text";
        input.autocomplete = "new-password";
        label.append(input);
        box.append(label);
        return input;
      });
      if (fields.some((f) => f.type === "password")) {
        const show = document.createElement("label");
        show.style.cssText = "display:flex;gap:8px;align-items:center;cursor:pointer";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.style.height = "auto";
        cb.addEventListener("change", () => inputs.forEach((i) => { i.type = cb.checked ? "text" : "password"; }));
        show.append(cb, "Show the password");
        box.append(show);
      }
      $("modalOk").textContent = ok;
      m.hidden = false;
      (inputs[0] || $("modalOk")).focus();
      const done = (value) => {
        m.hidden = true;
        $("modalOk").onclick = $("modalCancel").onclick = m.onkeydown = null;
        ta.focus();
        resolve(value);
      };
      $("modalOk").onclick = () => {
        const values = inputs.map((i) => i.value);
        const problem = validate ? validate(values) : "";
        if (problem) { $("modalError").textContent = problem; return; }
        done(fields.length ? values : true);
      };
      $("modalCancel").onclick = () => done(null);
      m.onkeydown = (e) => {
        if (e.key === "Escape") { e.preventDefault(); done(null); }
        if (e.key === "Enter") { e.preventDefault(); $("modalOk").onclick(); }
      };
    });
  }
  async function askPassword(mode) {
    const v = await dialog(mode === "new" ? {
      title: "Encrypt with a password",
      text: "AES-256. Only this password opens it again: YDM doesn't keep it anywhere.",
      fields: [{ label: "Password", type: "password" }, { label: "Again", type: "password" }],
      ok: "Encrypt",
      validate: ([a, b]) => (a.length < 4 ? "Use at least 4 characters (longer is safer)." : a !== b ? "The two passwords aren't the same." : ""),
    } : {
      title: "Decrypt", text: "The password the text was encrypted with.",
      fields: [{ label: "Password", type: "password" }], ok: "Decrypt",
      validate: ([a]) => (a ? "" : "Type the password."),
    });
    return v ? v[0] : null;
  }
  const confirmBox = (title, text, ok) => dialog({ title, text, ok }).then((v) => v === true);

  // ---- find and replace ---------------------------------------------------------------------------------------
  function finder() {
    const q = $("findText").value;
    if (!q) return null;
    const regex = $("useRegex").getAttribute("aria-pressed") === "true";
    const cs = $("matchCase").getAttribute("aria-pressed") === "true";
    try { return new RegExp(regex ? q : q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), cs ? "gm" : "gim"); }
    catch { return undefined; }                       // a regex still being typed
  }
  function matches() {
    const re = finder();
    if (!re) return [];
    const out = [];
    let m;
    while ((m = re.exec(ta.value)) && out.length < 10000) {
      if (m[0] === "") { re.lastIndex++; continue; }
      out.push([m.index, m.index + m[0].length]);
    }
    return out;
  }
  function showCount(list, at) {
    const re = finder();
    $("findText").classList.toggle("none", re === undefined || (!!$("findText").value && !list.length));
    $("findCount").textContent = re === undefined ? "Bad pattern" : !$("findText").value ? "" :
      !list.length ? "No results" : `${at >= 0 ? at + 1 : "?"} of ${list.length.toLocaleString()}${list.length >= 10000 ? "+" : ""}`;
  }
  function findStep(dir) {
    const list = matches();
    if (!list.length) return showCount(list, -1);
    const from = dir > 0 ? ta.selectionEnd : ta.selectionStart;
    let i = dir > 0 ? list.findIndex(([s]) => s >= from) : list.map(([s]) => s < from).lastIndexOf(true);
    if (i < 0) i = dir > 0 ? 0 : list.length - 1;
    const [s, e] = list[i];
    ta.setSelectionRange(s, e);
    const line = lineOf(s);
    if ((line - 1) * lineHeight() < ta.scrollTop || (line + 1) * lineHeight() > ta.scrollTop + ta.clientHeight) {
      ta.scrollTop = Math.max(0, (line - 1) * lineHeight() - ta.clientHeight / 3);
    }
    showCount(list, i);
    position();
  }
  function replaceOne() {
    const re = finder();
    if (!re) return;
    const a = ta.selectionStart, b = ta.selectionEnd;
    const one = new RegExp(re.source, re.flags.replace("g", ""));
    const sel = ta.value.slice(a, b);
    const m = one.exec(sel);
    if (a !== b && m && m.index === 0 && m[0] === sel) {
      const out = sel.replace(one, $("useRegex").getAttribute("aria-pressed") === "true" ? $("replaceText").value : () => $("replaceText").value);
      replaceRange(a, b, out, false);
      $("findText").focus();
    }
    findStep(1);
  }
  function replaceAll() {
    const re = finder();
    if (!re) return;
    const n = matches().length;
    if (!n) { showCount([], -1); return; }
    const rep = $("replaceText").value;
    const out = ta.value.replace(re, $("useRegex").getAttribute("aria-pressed") === "true" ? rep : () => rep);
    replaceRange(0, ta.value.length, out, false);
    toast(`Replaced ${n.toLocaleString()} time${n === 1 ? "" : "s"}`);
    showCount(matches(), -1);
    $("findText").focus();
  }
  function openFind(replace = false) {
    $("findBar").hidden = false;
    const sel = ta.value.slice(ta.selectionStart, ta.selectionEnd);
    if (sel && !sel.includes("\n") && sel.length < 200) $("findText").value = sel;
    (replace ? $("replaceText") : $("findText")).focus();
    $("findText").select();
    showCount(matches(), -1);
  }
  $("findText").addEventListener("input", () => {
    const list = matches();
    if (list.length) { ta.setSelectionRange(ta.selectionStart, ta.selectionStart); findStep(1); } else showCount(list, -1);
  });
  for (const id of ["findText", "replaceText"]) {
    $(id).addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); if (id === "replaceText") replaceOne(); else findStep(e.shiftKey ? -1 : 1); }
      if (e.key === "Escape") { e.preventDefault(); $("findBar").hidden = true; ta.focus(); }
    });
  }
  for (const id of ["matchCase", "useRegex"]) {
    $(id).addEventListener("click", () => {
      $(id).setAttribute("aria-pressed", String($(id).getAttribute("aria-pressed") !== "true"));
      showCount(matches(), -1);
    });
  }

  // ---- saving, copying, opening ---------------------------------------------------------------------------------
  const defaultType = () => ({ json: "json", yaml: "yaml", xml: "xml" }[kind] || "txt");
  function defaultName() {
    const base = (rec && rec.title ? rec.title : "text").replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 60);
    const d = new Date(), p = (n) => String(n).padStart(2, "0");
    return `${base || "text"} - ${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}.${p(d.getMinutes())}`;
  }
  async function save(type = saveType || defaultType()) {
    const as = { json: "json", yaml: "yaml", yml: "yaml", xml: "xml" }[type];
    if (as) {
      const v = TextKit.validate(ta.value, as);
      if (!v.ok && !(await confirmBox(`This isn't valid ${as.toUpperCase()}`, `${v.message}. Save it anyway?`, "Save anyway"))) return;
    }
    const known = new RegExp(`\\.(${TYPES.map((t) => t[0]).join("|")})$`, "i");
    const name = ($("fileName").value.trim() || defaultName()).replace(/[\\/:*?"<>|]+/g, " ").replace(known, "").trim() || "text";
    const text = type === "ps1" || type === "bat" ? ta.value.replace(/\r?\n/g, "\r\n") : ta.value;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    a.download = `${name}.${type}`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
    toast(`Saved ${name}.${type}`);
  }
  function fillSaveTypes() {
    const box = $("saveTypes");
    box.textContent = "";
    for (const [ext, label] of TYPES) {
      const b = document.createElement("button");
      b.setAttribute("role", "menuitemradio");
      b.setAttribute("aria-checked", String(ext === (saveType || defaultType())));
      b.innerHTML = `${esc(label)} <span>.${ext}</span>`;
      b.addEventListener("click", () => {
        saveType = ext;
        $("saveMenu").hidden = true;
        recheck();
        keep();
        save(ext);
      });
      box.append(b);
    }
  }
  async function copyAll() {
    try {
      await navigator.clipboard.writeText(ta.value);
      toast(`Copied ${ta.value.length.toLocaleString()} characters`);
    } catch (e) {
      toast(`Couldn't copy: ${e.message}`, true);
    }
  }
  async function openFile(file) {
    if (!file) return;
    if (file.size > 25 * 1024 * 1024) { toast("That file is too big for the editor (over 25 MB).", true); return; }
    const text = await file.text();
    if (/\u0000/.test(text.slice(0, 4096))) { toast("That looks like a binary file, not text.", true); return; }
    replaceRange(0, ta.value.length, text, false);
    ta.setSelectionRange(0, 0);
    ta.scrollTop = 0;
    const m = /^(.*?)(?:\.([^.]+))?$/.exec(file.name);
    $("fileName").value = m[1];
    if (m[2] && TYPES.some((t) => t[0] === m[2].toLowerCase())) saveType = m[2].toLowerCase();
    rec.title = file.name;
    $("title").textContent = file.name;
    setLang("auto");
    toast(`Opened ${file.name}`);
  }
  $("fileInput").addEventListener("change", (e) => { openFile(e.target.files[0]); e.target.value = ""; });
  editor.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); $("drop").hidden = false; } });
  editor.addEventListener("dragleave", (e) => { if (!editor.contains(e.relatedTarget)) $("drop").hidden = true; });
  editor.addEventListener("drop", (e) => {
    $("drop").hidden = true;
    if (e.dataTransfer.files.length) { e.preventDefault(); openFile(e.dataTransfer.files[0]); }
  });

  // ---- view -------------------------------------------------------------------------------------------------
  function applyView() {
    document.documentElement.style.setProperty("--fs", `${fontSize}px`);
    document.documentElement.style.setProperty("--lh", `${lineHeight()}px`);
    editor.classList.toggle("wrap", wrap);
    ta.setAttribute("wrap", wrap ? "soft" : "off");
    $("wrap").setAttribute("aria-pressed", String(wrap));
    paint();
  }
  const zoom = (d) => { fontSize = Math.min(24, Math.max(10, fontSize + d)); setPref("font", fontSize); applyView(); };

  // ---- buttons and keys ---------------------------------------------------------------------------------------
  function build() {
    $("open").innerHTML = `${svg(ICON.open)}<span class="long">Open</span>`;
    $("copy").innerHTML = `${svg(ICON.copy)}Copy`;
    $("saveMore").innerHTML = svg(ICON.caret);
    $("plugins").innerHTML = `${svg(ICON.plugins)}Plugins`;
    $("format").innerHTML = `${svg(ICON.format)}<span class="long">Format</span>`;
    $("secrets").innerHTML = `${svg(ICON.secrets)}<span class="long">Hide secrets</span>`;
    for (const [id, icon] of [["find", "find"], ["wrap", "wrap"], ["smaller", "smaller"], ["bigger", "bigger"], ["undo", "undo"],
                              ["redo", "redo"], ["findPrev", "up"], ["findNext", "down"], ["findClose", "close"]]) $(id).innerHTML = svg(ICON[icon]);
    $("open").addEventListener("click", () => $("fileInput").click());
    $("copy").addEventListener("click", copyAll);
    $("save").addEventListener("click", () => save());
    $("saveMore").addEventListener("click", () => {
      const m = $("saveMenu");
      m.hidden = !m.hidden;
      if (!m.hidden) { if (!$("fileName").value) $("fileName").value = defaultName(); fillSaveTypes(); }
    });
    $("fileName").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("saveMenu").hidden = true; save(); } });
    $("plugins").addEventListener("click", () => ($("palette").hidden ? openPalette() : closePalette()));
    $("format").addEventListener("click", format);
    $("secrets").addEventListener("click", () => runTool(byId("secrets")));
    $("lock").addEventListener("click", () => runTool(byId(ta.value.trimStart().startsWith(PREFIX) ? "decrypt" : "encrypt")));
    $("find").addEventListener("click", () => ($("findBar").hidden ? openFind() : ($("findBar").hidden = true)));
    $("findNext").addEventListener("click", () => findStep(1));
    $("findPrev").addEventListener("click", () => findStep(-1));
    $("replaceOne").addEventListener("click", replaceOne);
    $("replaceAll").addEventListener("click", replaceAll);
    $("findClose").addEventListener("click", () => { $("findBar").hidden = true; ta.focus(); });
    $("wrap").addEventListener("click", () => { wrap = !wrap; setPref("wrap", wrap); applyView(); });
    $("smaller").addEventListener("click", () => zoom(-1));
    $("bigger").addEventListener("click", () => zoom(1));
    $("undo").addEventListener("click", () => { ta.focus(); document.execCommand("undo"); });
    $("redo").addEventListener("click", () => { ta.focus(); document.execCommand("redo"); });
    $("lang").addEventListener("change", () => { recheck(); keep(); });
  }
  function format() {
    const id = { json: "json-pretty", yaml: "yaml-format", xml: "xml-pretty" }[kind];
    if (!id) { toast("Format works on JSON, YAML and XML: pick one in the list on the left if it wasn't recognised.", true); return; }
    runTool(byId(id));
  }
  document.addEventListener("keydown", (e) => {
    const k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey;
    if (!$("modal").hidden) return;
    if (mod && e.shiftKey && k === "p") { e.preventDefault(); $("palette").hidden ? openPalette() : closePalette(); }
    else if (mod && e.shiftKey && k === "c") { e.preventDefault(); copyAll(); }
    else if (mod && k === "s") { e.preventDefault(); save(); }
    else if (mod && k === "f") { e.preventDefault(); openFind(false); }
    else if (mod && k === "h") { e.preventDefault(); openFind(true); }
    else if (mod && (k === "=" || k === "+")) { e.preventDefault(); zoom(1); }
    else if (mod && k === "-") { e.preventDefault(); zoom(-1); }
    else if (e.altKey && !mod && k === "z") { e.preventDefault(); $("wrap").click(); }
    else if (e.altKey && e.shiftKey && k === "f") { e.preventDefault(); format(); }
    else if (k === "escape" && !$("palette").hidden) closePalette();
  });

  // ---- start ----------------------------------------------------------------------------------------------
  async function start() {
    build();
    applyView();
    let id = location.hash.slice(1);
    rec = id ? await TextDB.get(id).catch(() => null) : null;
    if (!rec) {
      if (id) toast("That text isn't kept any more: here's an empty page.", true);
      id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      rec = { id, created: Date.now(), updated: Date.now(), title: "", url: "", text: "", lang: "auto" };
      history.replaceState(null, "", `#${id}`);
    }
    ta.value = rec.text || "";
    $("lang").value = rec.lang || "auto";
    saveType = rec.saveType || "";
    const host = hostOf(rec.url);
    $("title").textContent = rec.title || (host ? `Text from ${host}` : "Text");
    $("title").title = rec.url || "";
    $("meta").textContent = [host ? `Copied from ${host}` : "", new Date(rec.created).toLocaleString()].filter(Boolean).join("  ·  ");
    document.title = `Text · ${rec.title || host || "YDM"}`;
    recheck();
    position();
    ta.focus();
    ta.setSelectionRange(0, 0);
  }

  // the test harness looks inside through this; nothing else uses it
  window.__ydmText = { get state() { return { kind, check, text: ta.value, saveType, fontSize, wrap, id: rec && rec.id }; },
                       run: (id) => runTool(byId(id)), recheck };
  start();
})();
