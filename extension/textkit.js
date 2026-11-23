// The text editor's tools ("plugins", like Notepad++'s): validators, formatters, converters, encoders,
// hashes, privacy tools (hide secrets, AES encryption), case and line tools, inserts.
// Every tool takes text and returns text (or { text, note }); async tools return a Promise.
// Used by text.js; plain functions, so they can be tested outside the browser (node).

const TextKit = (() => {
  const enc = new TextEncoder();
  const dec = (bytes) => {
    try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
    catch { throw new Error("The result isn't readable text (binary data). Try Hex instead."); }
  };

  // ---- base64 / hex -------------------------------------------------------------------------------------
  function bytesToB64(bytes) {
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  }
  function b64ToBytes(text) {
    let s = text.replace(/\s+/g, "").replace(/-/g, "+").replace(/_/g, "/");
    if (/[^A-Za-z0-9+/=]/.test(s)) throw new Error("This isn't Base64: it has characters Base64 doesn't use.");
    s = s.replace(/=+$/, "");
    if (s.length % 4 === 1) throw new Error("This isn't Base64: its length is off by one character.");
    s += "=".repeat((4 - (s.length % 4)) % 4);
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  const toHex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  function fromHex(text) {
    const s = text.replace(/0x/gi, "").replace(/[\s:,-]+/g, "");
    if (!/^[0-9a-f]*$/i.test(s) || s.length % 2) throw new Error("This isn't hex: use pairs of 0-9 and a-f.");
    const out = new Uint8Array(s.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(s.substr(i * 2, 2), 16);
    return out;
  }

  // ---- hashes -------------------------------------------------------------------------------------------
  function md5(bytes) {                                         // RFC 1321 (not in Web Crypto)
    const K = new Uint32Array(64), S = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
    for (let i = 0; i < 64; i++) K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32);
    const len = bytes.length;
    const total = ((len + 8) >> 6) + 1;
    const words = new Uint32Array(total * 16);
    for (let i = 0; i < len; i++) words[i >> 2] |= bytes[i] << ((i % 4) * 8);
    words[len >> 2] |= 0x80 << ((len % 4) * 8);
    words[total * 16 - 2] = (len * 8) >>> 0;
    words[total * 16 - 1] = Math.floor(len / 0x20000000);
    let a0 = 0x67452301, b0 = 0xefcdab89, c0 = 0x98badcfe, d0 = 0x10325476;
    for (let j = 0; j < words.length; j += 16) {
      let a = a0, b = b0, c = c0, d = d0;
      for (let i = 0; i < 64; i++) {
        let f, g;
        if (i < 16) { f = (b & c) | (~b & d); g = i; }
        else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) % 16; }
        else if (i < 48) { f = b ^ c ^ d; g = (3 * i + 5) % 16; }
        else { f = c ^ (b | ~d); g = (7 * i) % 16; }
        const tmp = d;
        d = c;
        c = b;
        const x = (a + f + K[i] + words[j + g]) >>> 0;
        const r = S[(i >> 4) * 4 + (i % 4)];
        b = (b + ((x << r) | (x >>> (32 - r)))) >>> 0;
        a = tmp;
      }
      a0 = (a0 + a) >>> 0; b0 = (b0 + b) >>> 0; c0 = (c0 + c) >>> 0; d0 = (d0 + d) >>> 0;
    }
    const out = new Uint8Array(16);
    [a0, b0, c0, d0].forEach((v, i) => { for (let k = 0; k < 4; k++) out[i * 4 + k] = (v >>> (k * 8)) & 255; });
    return toHex(out);
  }
  const sha = (name) => async (t) => toHex(new Uint8Array(await crypto.subtle.digest(name, enc.encode(t))));

  // ---- gzip -------------------------------------------------------------------------------------------------
  async function pipe(bytes, stream) {
    const out = new Blob([bytes]).stream().pipeThrough(stream);
    return new Uint8Array(await new Response(out).arrayBuffer());
  }

  // ---- AES-256-GCM with a password (PBKDF2-SHA256) ----------------------------------------------------------
  const PREFIX = "YDM-AES256:";
  const ROUNDS = 600000;
  async function key(password, salt) {
    const base = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: ROUNDS, hash: "SHA-256" }, base,
                                   { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  async function encrypt(text, password) {
    const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
    const data = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await key(password, salt), enc.encode(text)));
    const all = new Uint8Array(28 + data.length);
    all.set(salt, 0); all.set(iv, 16); all.set(data, 28);
    return PREFIX + bytesToB64(all);
  }
  async function decrypt(text, password) {
    const s = text.trim();
    if (!s.startsWith(PREFIX)) throw new Error(`This isn't text encrypted by YDM (it starts with ${PREFIX}).`);
    const all = b64ToBytes(s.slice(PREFIX.length));
    if (all.length < 29) throw new Error("This encrypted text is cut short.");
    try {
      const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: all.slice(16, 28) }, await key(password, all.slice(0, 16)),
                                                all.slice(28));
      return dec(new Uint8Array(plain));
    } catch (e) {
      if (/binary/.test(e.message)) throw e;
      throw new Error("Wrong password, or the encrypted text was changed.");
    }
  }

  // ---- hide secrets ------------------------------------------------------------------------------------------
  const DOTS = "••••••••";
  const SECRET_KEY = "(?:pass(?:word|wd|phrase)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|" +
    "client[_-]?secret|auth(?:orization)?|credentials?|session(?:[_-]?id)?|cookie|bearer|signature|connection[_-]?string)";
  function luhn(num) {
    let sum = 0, alt = false;
    for (let i = num.length - 1; i >= 0; i--) {
      let d = +num[i];
      if (alt) { d *= 2; if (d > 9) d -= 9; }
      sum += d;
      alt = !alt;
    }
    return sum % 10 === 0;
  }
  function hideSecrets(text) {
    let count = 0;
    const hit = (s) => { count++; return s; };
    let t = text;
    // private key blocks
    t = t.replace(/-----BEGIN ([A-Z ]*)PRIVATE KEY-----[\s\S]*?-----END \1PRIVATE KEY-----/g,
                  (_m, k) => hit(`-----BEGIN ${k}PRIVATE KEY-----\n${DOTS}\n-----END ${k}PRIVATE KEY-----`));
    // Authorization: Bearer / Basic …
    t = t.replace(/\b(Bearer|Basic|Token)\s+([A-Za-z0-9._~+/=-]{8,})/g, (_m, kind) => hit(`${kind} ${DOTS}`));
    // user:password@ in addresses
    t = t.replace(/\b([a-z][a-z0-9+.-]*:\/\/[^\s:/@]+):([^\s@/]+)@/gi, (_m, head) => hit(`${head}:${DOTS}@`));
    // key = value / key: value / "key": "value" with a secret-sounding key
    const kv = new RegExp(`((?:["']?[\\w.-]*${SECRET_KEY}[\\w.-]*["']?)\\s*[:=]\\s*)("(?:\\\\.|[^"\\\\])*"|'[^']*'|[^\\s,;}\\]]+)`, "gi");
    t = t.replace(kv, (m, head, val) => {
      if (val.includes(DOTS) || /^(true|false|null|none|""|'')$/i.test(val)) return m;
      const q = /^["']/.test(val) ? val[0] : "";
      return hit(`${head}${q}${DOTS}${q}`);
    });
    // well-known token shapes
    const TOKENS = [
      /\bAKIA[0-9A-Z]{16}\b/g,                                  // AWS access key
      /\bgh[pousr]_[A-Za-z0-9]{30,}\b/g, /\bgithub_pat_[A-Za-z0-9_]{40,}\b/g,
      /\bxox[abprs]-[A-Za-z0-9-]{10,}\b/g,                      // Slack
      /\bAIza[0-9A-Za-z_-]{35}\b/g,                             // Google API
      /\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b/g,           // Stripe
      /\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b/g,               // OpenAI / Anthropic style
      /\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g,   // JWT
    ];
    for (const re of TOKENS) t = t.replace(re, () => hit(DOTS));
    // card numbers (checked with the Luhn sum): only the last four stay
    t = t.replace(/\b(?:\d[ -]?){12,18}\d\b/g, (m) => {
      const digits = m.replace(/\D/g, "");
      if (digits.length < 13 || digits.length > 19 || !luhn(digits)) return m;
      return hit(`•••• •••• •••• ${digits.slice(-4)}`);
    });
    return { text: t, note: count ? `Hid ${count} secret${count === 1 ? "" : "s"}` : "Found no passwords, keys or tokens" };
  }

  // ---- JSON / YAML / XML / CSV ------------------------------------------------------------------------------
  function sortDeep(v) {
    if (Array.isArray(v)) return v.map(sortDeep);
    if (v && typeof v === "object") return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortDeep(v[k])]));
    return v;
  }
  const jsonParse = (t) => { try { return JSON.parse(t); } catch (e) { throw new Error(jsonError(t, e).message); } };
  function yamlParse(t) {
    try { return YAML.parse(t); } catch (e) { throw new Error(e.line ? `Line ${e.line}: ${e.message}` : e.message); }
  }

  function parseCsv(text) {
    const sample = text.split("\n", 5).join("\n");
    const delim = [",", ";", "\t", "|"].map((d) => [d, sample.split(d).length]).sort((a, b) => b[1] - a[1])[0][0];
    const rows = [];
    let row = [], cell = "", q = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) {
        if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
        else if (c === '"') q = false;
        else cell += c;
      } else if (c === '"' && cell === "") q = true;
      else if (c === delim) { row.push(cell); cell = ""; }
      else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(cell); rows.push(row); row = []; cell = "";
      } else cell += c;
    }
    if (q) throw new Error("A quoted CSV cell is never closed.");
    if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
    return rows.filter((r) => r.length > 1 || r[0] !== "");
  }
  function csvToJson(text) {
    const rows = parseCsv(text);
    if (rows.length < 2) throw new Error("CSV to JSON needs a header row and at least one row of data.");
    const head = rows[0].map((h, i) => h.trim() || `column${i + 1}`);
    return JSON.stringify(rows.slice(1).map((r) => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ""]))), null, 2);
  }
  function jsonToCsv(text) {
    let v = jsonParse(text);
    if (!Array.isArray(v)) v = [v];
    if (!v.every((x) => x && typeof x === "object" && !Array.isArray(x))) throw new Error("JSON to CSV needs a list of objects.");
    const keys = [...new Set(v.flatMap(Object.keys))];
    const cell = (x) => {
      const s = x === null || x === undefined ? "" : typeof x === "object" ? JSON.stringify(x) : String(x);
      return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    return [keys.map(cell).join(","), ...v.map((o) => keys.map((k) => cell(o[k])).join(","))].join("\n") + "\n";
  }

  function xmlDoc(text) {
    const d = new DOMParser().parseFromString(text, "application/xml");
    const err = d.querySelector("parsererror");
    if (err) throw new Error(xmlError(err).message);
    return d;
  }
  function xmlPretty(text) {
    const d = xmlDoc(text);
    const out = [];
    const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const attr = (s) => esc(s).replace(/"/g, "&quot;");
    function walk(n, depth) {
      const pad = "  ".repeat(depth);
      if (n.nodeType === 1) {
        const at = Array.from(n.attributes, (a) => ` ${a.name}="${attr(a.value)}"`).join("");
        const kids = Array.from(n.childNodes).filter((c) => !(c.nodeType === 3 && !c.nodeValue.trim()));
        if (!kids.length) return out.push(`${pad}<${n.nodeName}${at}/>`);
        if (kids.length === 1 && kids[0].nodeType === 3) return out.push(`${pad}<${n.nodeName}${at}>${esc(kids[0].nodeValue.trim())}</${n.nodeName}>`);
        out.push(`${pad}<${n.nodeName}${at}>`);
        kids.forEach((c) => walk(c, depth + 1));
        out.push(`${pad}</${n.nodeName}>`);
      } else if (n.nodeType === 3) out.push(pad + esc(n.nodeValue.trim()));
      else if (n.nodeType === 4) out.push(`${pad}<![CDATA[${n.nodeValue}]]>`);
      else if (n.nodeType === 8) out.push(`${pad}<!--${n.nodeValue}-->`);
      else if (n.nodeType === 7) out.push(`${pad}<?${n.target} ${n.data}?>`);
    }
    const decl = /^\s*<\?xml[^>]*\?>/.exec(text);
    if (decl) out.push(decl[0].trim());
    Array.from(d.childNodes).forEach((c) => walk(c, 0));
    return out.join("\n") + "\n";
  }
  const xmlMinify = (text) => { xmlDoc(text); return text.replace(/>\s+</g, "><").trim(); };

  // ---- HTML entities ----------------------------------------------------------------------------------------
  const NAMED = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: "\u00a0", copy: "©", reg: "®", trade: "™",
                  hellip: "…", mdash: "—", ndash: "–", lsquo: "‘", rsquo: "’", ldquo: "“", rdquo: "”", euro: "€",
                  pound: "£", yen: "¥", cent: "¢", sect: "§", deg: "°", plusmn: "±", times: "×", divide: "÷",
                  laquo: "«", raquo: "»", middot: "·", bull: "•", para: "¶", iexcl: "¡", iquest: "¿" };
  const htmlEncode = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  const htmlDecode = (t) => t.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === "#") return String.fromCodePoint(e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
    return NAMED[e.toLowerCase()] ?? m;
  });

  // ---- JWT --------------------------------------------------------------------------------------------------
  function jwt(text) {
    const parts = text.trim().replace(/^Bearer\s+/i, "").split(".");
    if (parts.length < 2) throw new Error("A JWT has three parts separated by dots (header.payload.signature).");
    const part = (p, name) => { try { return JSON.parse(dec(b64ToBytes(p))); } catch { throw new Error(`The JWT's ${name} isn't readable.`); } };
    const header = part(parts[0], "header"), payload = part(parts[1], "payload");
    const when = (s) => {
      const d = new Date(s * 1000);
      const diff = Math.round((d - Date.now()) / 60000);
      const rel = Math.abs(diff) < 60 ? `${Math.abs(diff)} min` : Math.abs(diff) < 2880 ? `${Math.round(Math.abs(diff) / 60)} h`
        : `${Math.round(Math.abs(diff) / 1440)} days`;
      return `${d.toISOString().replace(".000", "")} (${diff >= 0 ? "in " + rel : rel + " ago"})`;
    };
    const lines = ["Header", JSON.stringify(header, null, 2), "", "Payload", JSON.stringify(payload, null, 2), ""];
    for (const [k, label] of [["iat", "Issued"], ["nbf", "Valid from"], ["exp", "Expires"]]) {
      if (typeof payload[k] === "number") lines.push(`${label.padEnd(11)} ${when(payload[k])}`);
    }
    lines.push("Signature   not checked (that needs the key)");
    return lines.join("\n") + "\n";
  }

  // ---- case and words ---------------------------------------------------------------------------------------
  const words = (t) => t.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  const perLine = (fn) => (t) => t.split("\n").map(fn).join("\n");
  const cap = (w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();

  // ---- lines --------------------------------------------------------------------------------------------------
  const lines = (fn) => (t) => {
    const end = t.endsWith("\n");
    const out = fn(end ? t.slice(0, -1).split("\n") : t.split("\n"));
    return out.join("\n") + (end ? "\n" : "");
  };
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

  // ---- inserts ------------------------------------------------------------------------------------------------
  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
    const h = toHex(b);
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }
  function password(n = 20) {
    const sets = ["ABCDEFGHJKLMNPQRSTUVWXYZ", "abcdefghijkmnpqrstuvwxyz", "23456789", "!@#$%^&*-_=+?"];
    const all = sets.join("");
    const pick = (s) => s[crypto.getRandomValues(new Uint32Array(1))[0] % s.length];
    const out = sets.map(pick);                                  // at least one of each kind
    while (out.length < n) out.push(pick(all));
    for (let i = out.length - 1; i > 0; i--) {                   // then shuffle
      const j = crypto.getRandomValues(new Uint32Array(1))[0] % (i + 1);
      [out[i], out[j]] = [out[j], out[i]];
    }
    return out.join("");
  }
  function timeConvert(t) {
    const s = t.trim();
    if (/^\d{9,13}(\.\d+)?$/.test(s)) {
      const ms = s.split(".")[0].length > 11 ? Number(s) : Number(s) * 1000;
      const d = new Date(ms);
      return `${d.toISOString()}  (local: ${d.toLocaleString()})`;
    }
    const d = new Date(s);
    if (Number.isNaN(d.getTime())) throw new Error("Select a Unix time (like 1700000000) or a date (like 2024-05-01T10:00).");
    return String(Math.floor(d.getTime() / 1000));
  }

  // ---- more tools ---------------------------------------------------------------------------------------------
  const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  function base32(bytes) {
    let bits = 0, value = 0, out = "";
    for (const b of bytes) {
      value = (value << 8) | b;
      bits += 8;
      while (bits >= 5) { out += B32[(value >>> (bits - 5)) & 31]; bits -= 5; }
    }
    if (bits > 0) out += B32[(value << (5 - bits)) & 31];
    return out + "=".repeat((8 - (out.length % 8)) % 8);
  }
  function unbase32(text) {
    const s = text.toUpperCase().replace(/[\s=-]+/g, "");
    if (/[^A-Z2-7]/.test(s)) throw new Error("This isn't Base32: it uses only A–Z and 2–7.");
    let bits = 0, value = 0;
    const out = [];
    for (const c of s) {
      value = (value << 5) | B32.indexOf(c);
      bits += 5;
      if (bits >= 8) { out.push((value >>> (bits - 8)) & 255); bits -= 8; }
    }
    return new Uint8Array(out);
  }
  async function hmac(text, secret) {
    const k = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    return toHex(new Uint8Array(await crypto.subtle.sign("HMAC", k, enc.encode(text))));
  }
  const extract = (re, what) => (t) => {
    const found = [...new Set(t.match(re) || [])];
    if (!found.length) throw new Error(`Found no ${what}.`);
    return { text: found.join("\n") + "\n", note: `Found ${found.length} ${what}` };
  };
  function numbers(base, prefix) {                  // every whole number in the text, in another base
    return (t) => {
      let n = 0;
      // whole numbers only: 3.5 and 1,000.25 stay as they are
      const out = t.replace(/(?<![\d.,])\b(0x[0-9a-f]+|0b[01]+|0o[0-7]+|\d+)\b(?![.,]\d)/gi, (m) => {
        const v = BigInt(m.toLowerCase());               // BigInt reads 0x / 0b / 0o itself
        n++;
        return prefix + v.toString(base);
      });
      if (!n) throw new Error("Found no numbers to convert.");
      return out;
    };
  }
  function slug(t) {
    return t.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  }
  function urlParts(t) {
    let u;
    try { u = new URL(t.trim()); } catch { throw new Error("Select a full address, like https://example.com/path?x=1"); }
    const query = {};
    for (const [k, v] of u.searchParams) query[k] = k in query ? [].concat(query[k], v) : v;
    return JSON.stringify({ protocol: u.protocol.replace(":", ""), user: u.username || undefined, host: u.hostname,
                            port: u.port || undefined, path: decodeURIComponent(u.pathname), query,
                            fragment: u.hash ? decodeURIComponent(u.hash.slice(1)) : undefined }, null, 2) + "\n";
  }
  function queryToJson(t) {
    const s = t.trim().replace(/^[^?]*\?/, "").replace(/#.*$/, "");
    const out = {};
    for (const [k, v] of new URLSearchParams(s)) out[k] = k in out ? [].concat(out[k], v) : v;
    if (!Object.keys(out).length) throw new Error("Select a query string, like a=1&b=two");
    return JSON.stringify(out, null, 2) + "\n";
  }
  function jsonToQuery(t) {
    const v = jsonParse(t);
    if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("JSON → query string needs an object.");
    const p = new URLSearchParams();
    for (const [k, x] of Object.entries(v)) for (const one of [].concat(x)) p.append(k, typeof one === "object" ? JSON.stringify(one) : String(one));
    return p.toString();
  }
  function envToJson(t) {
    const out = {};
    for (const line of t.split(/\r?\n/)) {
      const m = /^\s*(?:export\s+)?([A-Za-z_][\w.-]*)\s*=\s*(.*?)\s*$/.exec(line);
      if (!m || line.trim().startsWith("#")) continue;
      let v = m[2];
      if (/^"(.*)"$/.test(v)) v = v.slice(1, -1).replace(/\\n/g, "\n").replace(/\\"/g, '"');
      else if (/^'(.*)'$/.test(v)) v = v.slice(1, -1);
      else v = v.replace(/\s+#.*$/, "");
      out[m[1]] = v;
    }
    if (!Object.keys(out).length) throw new Error("Found no KEY=value lines.");
    return JSON.stringify(out, null, 2) + "\n";
  }
  function jsonToEnv(t) {
    const v = jsonParse(t);
    if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("JSON → .env needs an object.");
    return Object.entries(v).map(([k, x]) => {
      const s = typeof x === "object" && x !== null ? JSON.stringify(x) : String(x ?? "");
      return `${k.replace(/[^\w.-]/g, "_")}=${/[\s#"'$\\]/.test(s) || s === "" ? JSON.stringify(s) : s}`;
    }).join("\n") + "\n";
  }
  function mdTable(rows) {
    const cell = (x) => String(x ?? "").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
    const w = rows[0].map((_c, i) => Math.max(3, ...rows.map((r) => cell(r[i]).length)));
    const line = (r) => `| ${w.map((n, i) => cell(r[i]).padEnd(n)).join(" | ")} |`;
    return [line(rows[0]), `| ${w.map((n) => "-".repeat(n)).join(" | ")} |`, ...rows.slice(1).map(line)].join("\n") + "\n";
  }
  function toMdTable(t) {
    const s = t.trim();
    if (s.startsWith("[") || s.startsWith("{")) {
      let v = jsonParse(s);
      if (!Array.isArray(v)) v = [v];
      const keys = [...new Set(v.flatMap((o) => (o && typeof o === "object" ? Object.keys(o) : [])))];
      if (!keys.length) throw new Error("Markdown table from JSON needs a list of objects.");
      return mdTable([keys, ...v.map((o) => keys.map((k) => (typeof o[k] === "object" && o[k] !== null ? JSON.stringify(o[k]) : o[k])))]);
    }
    const rows = parseCsv(s);
    if (rows.length < 1) throw new Error("Select CSV (or JSON) to turn into a table.");
    const width = Math.max(...rows.map((r) => r.length));
    return mdTable(rows.map((r) => Array.from({ length: width }, (_x, i) => r[i] ?? "")));
  }
  function tidyMdTable(t) {
    const lines = t.trim().split(/\r?\n/).filter((l) => l.trim());
    if (lines.length < 2 || !lines.every((l) => l.trim().startsWith("|"))) throw new Error("Select a Markdown table (lines starting with |).");
    const rows = lines.filter((l, i) => !(i === 1 && /^\|?[\s:|-]+\|?$/.test(l.trim())))
      .map((l) => l.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((c) => c.trim()));
    return mdTable(rows.map((r) => r.map((c) => c.replace(/\\\|/g, "|"))));
  }
  const SQL_BREAK = ["SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING", "LIMIT", "OFFSET", "UNION ALL", "UNION",
    "INSERT INTO", "VALUES", "UPDATE", "SET", "DELETE FROM", "LEFT OUTER JOIN", "RIGHT OUTER JOIN", "FULL OUTER JOIN",
    "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "CROSS JOIN", "FULL JOIN", "JOIN", "ON", "RETURNING", "WITH"];
  const SQL_WORDS = /\b(select|from|where|group by|order by|having|limit|offset|union all|union|insert into|values|update|set|delete from|left outer join|right outer join|full outer join|left join|right join|inner join|cross join|full join|join|on|returning|with|and|or|not|in|is|null|as|asc|desc|distinct|case|when|then|else|end|like|between|exists|count|sum|avg|min|max|create table|primary key|foreign key|references|default|into)\b/gi;
  function sqlFormat(t) {
    const parts = t.split(/('(?:''|[^'])*'|"(?:""|[^"])*"|--[^\n]*|\/\*[\s\S]*?\*\/)/);   // strings and comments stay as they are
    let out = parts.map((p, i) => (i % 2 ? p : p.replace(/\s+/g, " ").replace(SQL_WORDS, (w) => w.toUpperCase()))).join("");
    // every clause on its own line, in one pass, longest first (so LEFT JOIN isn't split at JOIN)
    const clauses = [...SQL_BREAK].sort((a, b) => b.length - a.length).map((k) => k.replace(/ /g, "\\s+")).join("|");
    out = out.replace(new RegExp(`\\s*\\b(${clauses})\\b`, "g"), (_m, k) => `\n${k.replace(/\s+/g, " ")}`);
    out = out.replace(/\s+\b(AND|OR)\b/g, "\n  $1").replace(/,\s*(?=[^\n])/g, ",\n  ").replace(/^\s+/, "");
    out = out.replace(/\n(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|SET|VALUES|ON|RETURNING)\b/g, "\n$1")
      .replace(/(SELECT|GROUP BY|ORDER BY|SET|RETURNING)\s+/g, "$1\n  ").replace(/;\s*/g, ";\n\n");
    return out.replace(/\n{3,}/g, "\n\n").trim() + "\n";
  }
  function tsTypes(t) {
    const v = jsonParse(t);
    const named = [];
    const pascal = (k) => (k.replace(/(^|[^a-zA-Z0-9]+)([a-zA-Z0-9])/g, (_m, _a, c) => c.toUpperCase()) || "Item");
    function type(x, name) {
      if (x === null) return "null";
      if (Array.isArray(x)) {
        if (!x.length) return "unknown[]";
        const ts = [...new Set(x.map((y) => type(y, name.replace(/s$/, "") || "Item")))];
        return ts.length === 1 ? `${ts[0]}[]` : `(${ts.join(" | ")})[]`;
      }
      if (typeof x === "object") {
        const n = pascal(name);
        if (!named.some(([k]) => k === n)) {
          const body = Object.entries(x).map(([k, y]) => `  ${/^[A-Za-z_$][\w$]*$/.test(k) ? k : JSON.stringify(k)}: ${type(y, k)};`);
          named.push([n, `export interface ${n} {\n${body.join("\n")}\n}`]);
        }
        return n;
      }
      return typeof x;
    }
    const root = type(v, "Root");
    const out = named.reverse().map(([, s]) => s);
    if (!/^[A-Z]/.test(root) || Array.isArray(v)) out.push(`export type Root = ${root};`);
    return out.join("\n\n") + "\n";
  }
  const LOREM = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore " +
    "magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.";

  // ---- compare two texts, line by line ------------------------------------------------------------------------
  // [op, line, old number, new number] with op " " (same), "-" (only in the old), "+" (only in the new). The
  // common start and end are matched first, the middle by longest common subsequence (up to 3000 × 3000 lines).
  function lineDiff(a, b) {
    const A = a.replace(/\r\n?/g, "\n").split("\n"), B = b.replace(/\r\n?/g, "\n").split("\n");
    let s = 0;
    while (s < A.length && s < B.length && A[s] === B[s]) s++;
    let e = 0;
    while (e < A.length - s && e < B.length - s && A[A.length - 1 - e] === B[B.length - 1 - e]) e++;
    const a2 = A.slice(s, A.length - e), b2 = B.slice(s, B.length - e);
    if (a2.length > 3000 || b2.length > 3000) throw new Error("The texts differ in over 3000 lines: too much to compare line by line.");
    const n = a2.length, m = b2.length, W = m + 1;
    const L = new Uint16Array((n + 1) * W);
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        L[i * W + j] = a2[i] === b2[j] ? L[(i + 1) * W + j + 1] + 1 : Math.max(L[(i + 1) * W + j], L[i * W + j + 1]);
      }
    }
    const out = [];
    for (let k = 0; k < s; k++) out.push([" ", A[k], k + 1, k + 1]);
    let i = 0, j = 0;
    while (i < n || j < m) {
      if (i < n && j < m && a2[i] === b2[j]) { out.push([" ", a2[i], s + i + 1, s + j + 1]); i++; j++; }
      else if (j < m && (i === n || L[i * W + j + 1] >= L[(i + 1) * W + j])) { out.push(["+", b2[j], null, s + j + 1]); j++; }
      else { out.push(["-", a2[i], s + i + 1, null]); i++; }
    }
    for (let k = 0; k < e; k++) out.push([" ", A[A.length - e + k], A.length - e + k + 1, B.length - e + k + 1]);
    return out;
  }

  // ---- the menu ---------------------------------------------------------------------------------------------
  // [group, id, label, fn, { insert: true } — puts its result at the cursor instead of replacing the text]
  const TOOLS = [
    ["Format", "json-pretty", "JSON: format", (t) => JSON.stringify(jsonParse(t), null, 2) + "\n"],
    ["Format", "json-min", "JSON: minify", (t) => JSON.stringify(jsonParse(t))],
    ["Format", "json-sort", "JSON: sort keys", (t) => JSON.stringify(sortDeep(jsonParse(t)), null, 2) + "\n"],
    ["Format", "yaml-format", "YAML: format", (t) => {
      const docs = YAML.parseAll(t);
      return docs.map((d) => YAML.stringify(d)).join("---\n");
    }],
    ["Format", "xml-pretty", "XML: format", xmlPretty],
    ["Format", "xml-min", "XML: minify", xmlMinify],
    ["Convert", "json-yaml", "JSON → YAML", (t) => YAML.stringify(jsonParse(t))],
    ["Convert", "yaml-json", "YAML → JSON", (t) => JSON.stringify(yamlParse(t), null, 2) + "\n"],
    ["Convert", "csv-json", "CSV → JSON", csvToJson],
    ["Convert", "json-csv", "JSON → CSV", jsonToCsv],
    ["Convert", "json-escape", "Text → JSON string", (t) => JSON.stringify(t)],
    ["Convert", "json-unescape", "JSON string → text", (t) => {
      const v = jsonParse(t.trim().startsWith('"') ? t.trim() : `"${t}"`);
      if (typeof v !== "string") throw new Error("Select a JSON string (in double quotes).");
      return v;
    }],
    ["Convert", "time", "Unix time ⇄ date", timeConvert],
    ["Convert", "url-parts", "URL → its parts (JSON)", urlParts],
    ["Convert", "query-json", "Query string → JSON", queryToJson],
    ["Convert", "json-query", "JSON → query string", jsonToQuery],
    ["Convert", "env-json", ".env → JSON", envToJson],
    ["Convert", "json-env", "JSON → .env", jsonToEnv],
    ["Convert", "md-table", "CSV / JSON → Markdown table", toMdTable],
    ["Convert", "ts-types", "JSON → TypeScript types", tsTypes],
    ["Convert", "to-hex", "Numbers → hex", numbers(16, "0x")],
    ["Convert", "to-dec", "Numbers → decimal", numbers(10, "")],
    ["Convert", "to-bin", "Numbers → binary", numbers(2, "0b")],
    ["Format", "md-tidy", "Markdown table: line up", tidyMdTable],
    ["Format", "sql", "SQL: format", sqlFormat],
    ["Encode", "b64-enc", "Base64 encode", (t) => bytesToB64(enc.encode(t))],
    ["Encode", "b64-dec", "Base64 decode", (t) => dec(b64ToBytes(t))],
    ["Encode", "b64url-enc", "Base64URL encode", (t) => bytesToB64(enc.encode(t)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")],
    ["Encode", "b64url-dec", "Base64URL decode", (t) => dec(b64ToBytes(t))],
    ["Encode", "url-enc", "URL encode", (t) => encodeURIComponent(t)],
    ["Encode", "url-dec", "URL decode", (t) => {
      try { return decodeURIComponent(t.replace(/\+/g, " ")); } catch { throw new Error("This has a broken %-escape."); }
    }],
    ["Encode", "html-enc", "HTML entities encode", htmlEncode],
    ["Encode", "html-dec", "HTML entities decode", htmlDecode],
    ["Encode", "hex-enc", "Hex encode", (t) => toHex(enc.encode(t))],
    ["Encode", "hex-dec", "Hex decode", (t) => dec(fromHex(t))],
    ["Encode", "uni-esc", "Unicode escape (\\u…)", (t) => t.replace(/[^\x20-\x7e\n\t]/g, (c) => {
      const code = c.charCodeAt(0);
      return `\\u${code.toString(16).padStart(4, "0")}`;
    })],
    ["Encode", "uni-unesc", "Unicode unescape", (t) => t.replace(/\\u\{([0-9a-f]+)\}|\\u([0-9a-f]{4})|\\x([0-9a-f]{2})/gi,
      (_m, a, b, c) => (a ? String.fromCodePoint(parseInt(a, 16)) : String.fromCharCode(parseInt(b || c, 16))))],
    ["Encode", "gzip", "Gzip → Base64", async (t) => bytesToB64(await pipe(enc.encode(t), new CompressionStream("gzip")))],
    ["Encode", "gunzip", "Base64 → Gunzip", async (t) => {
      try { return dec(await pipe(b64ToBytes(t), new DecompressionStream("gzip"))); }
      catch (e) { throw new Error(/binary|Base64/.test(e.message) ? e.message : "This isn't gzip data in Base64."); }
    }],
    ["Encode", "jwt", "JWT decode", jwt],
    ["Encode", "b32-enc", "Base32 encode", (t) => base32(enc.encode(t))],
    ["Encode", "b32-dec", "Base32 decode", (t) => dec(unbase32(t))],
    ["Encode", "rot13", "ROT13", (t) => t.replace(/[a-z]/gi, (c) => String.fromCharCode((c <= "Z" ? 65 : 97) + ((c.charCodeAt(0) - (c <= "Z" ? 65 : 97) + 13) % 26)))],
    ["Encode", "regex-esc", "Escape for a regular expression", (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")],
    ["Hash", "md5", "MD5", (t) => md5(enc.encode(t))],
    ["Hash", "sha1", "SHA-1", sha("SHA-1")],
    ["Hash", "sha256", "SHA-256", sha("SHA-256")],
    ["Hash", "sha512", "SHA-512", sha("SHA-512")],
    ["Hash", "hmac", "HMAC-SHA256 (with a key)", null, { secret: true }],
    ["Privacy", "secrets", "Hide passwords, keys and tokens", hideSecrets],
    ["Privacy", "mask", "Mask (••••)", (t) => t.replace(/[^\s]/g, "•")],
    ["Privacy", "encrypt", "Encrypt with a password (AES-256)", null, { password: "new" }],
    ["Privacy", "decrypt", "Decrypt", null, { password: "old" }],
    ["Case", "upper", "UPPERCASE", (t) => t.toUpperCase()],
    ["Case", "lower", "lowercase", (t) => t.toLowerCase()],
    ["Case", "title", "Title Case", perLine((l) => l.replace(/\p{L}[\p{L}'’]*/gu, cap))],
    ["Case", "sentence", "Sentence case", (t) => t.toLowerCase().replace(/(^\s*|[.!?]\s+)(\p{L})/gu, (_m, a, b) => a + b.toUpperCase())],
    ["Case", "camel", "camelCase", perLine((l) => words(l).map((w, i) => (i ? cap(w) : w.toLowerCase())).join(""))],
    ["Case", "pascal", "PascalCase", perLine((l) => words(l).map(cap).join(""))],
    ["Case", "snake", "snake_case", perLine((l) => words(l).map((w) => w.toLowerCase()).join("_"))],
    ["Case", "kebab", "kebab-case", perLine((l) => words(l).map((w) => w.toLowerCase()).join("-"))],
    ["Case", "constant", "CONSTANT_CASE", perLine((l) => words(l).map((w) => w.toUpperCase()).join("_"))],
    ["Lines", "sort", "Sort A → Z", lines((ls) => ls.sort(collator.compare))],
    ["Lines", "sort-desc", "Sort Z → A", lines((ls) => ls.sort(collator.compare).reverse())],
    ["Lines", "sort-len", "Sort by length", lines((ls) => ls.sort((a, b) => a.length - b.length || collator.compare(a, b)))],
    ["Lines", "unique", "Remove duplicate lines", lines((ls) => [...new Set(ls)])],
    ["Lines", "reverse", "Reverse lines", lines((ls) => ls.reverse())],
    ["Lines", "shuffle", "Shuffle lines", lines((ls) => {
      for (let i = ls.length - 1; i > 0; i--) { const j = crypto.getRandomValues(new Uint32Array(1))[0] % (i + 1); [ls[i], ls[j]] = [ls[j], ls[i]]; }
      return ls;
    })],
    ["Lines", "no-empty", "Remove empty lines", lines((ls) => ls.filter((l) => l.trim() !== ""))],
    ["Lines", "trim", "Trim spaces", lines((ls) => ls.map((l) => l.trim()))],
    ["Lines", "join", "Join lines with commas", (t) => t.split(/\r?\n/).map((l) => l.trim()).filter(Boolean).join(", ")],
    ["Lines", "split", "Split commas into lines", (t) => t.split(/\s*[,;]\s*/).filter(Boolean).join("\n")],
    ["Lines", "number", "Number the lines", lines((ls) => ls.map((l, i) => `${String(i + 1).padStart(String(ls.length).length, " ")}  ${l}`))],
    ["Lines", "tabs", "Tabs → spaces", (t) => t.replace(/\t/g, "    ")],
    ["Lines", "trailing", "Remove spaces at line ends", (t) => t.replace(/[ \t]+$/gm, "")],
    ["Lines", "squash", "Collapse repeated spaces", (t) => t.replace(/([^\s]) {2,}/g, "$1 ")],
    ["Lines", "unwrap", "Remove line breaks", (t) => t.replace(/\s*\r?\n\s*/g, " ").trim()],
    ["Lines", "lf", "Line endings: LF (Linux, Mac)", (t) => t.replace(/\r\n?/g, "\n")],
    ["Lines", "crlf", "Line endings: CRLF (Windows)", (t) => t.replace(/\r?\n/g, "\r\n")],
    ["Text", "reverse-text", "Reverse the text", (t) => Array.from(t).reverse().join("")],
    ["Text", "slug", "Slug (url-friendly-name)", perLine(slug)],
    ["Extract", "emails", "Email addresses", extract(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, "email addresses")],
    ["Extract", "urls", "Links", extract(/\bhttps?:\/\/[^\s<>"'()]+[^\s<>"'().,;:!?]/g, "links")],
    ["Extract", "ips", "IP addresses", extract(/\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b/g, "IP addresses")],
    ["Extract", "nums", "Numbers", extract(/-?\b\d+(?:[.,]\d+)*\b/g, "numbers")],
    ["Insert", "uuid", "UUID", () => uuid(), { insert: true }],
    ["Insert", "now", "Date and time (ISO)", () => new Date().toISOString(), { insert: true }],
    ["Insert", "unix", "Unix time", () => String(Math.floor(Date.now() / 1000)), { insert: true }],
    ["Insert", "password", "Strong password (20 characters)", () => password(20), { insert: true }],
    ["Insert", "lorem", "Placeholder text (lorem ipsum)", () => LOREM, { insert: true }],
  ];

  // ---- validation -------------------------------------------------------------------------------------------
  function posToLineCol(text, pos) {
    const before = text.slice(0, pos);
    const line = before.split("\n").length;
    return { line, col: pos - before.lastIndexOf("\n") };
  }
  function jsonError(text, e) {
    const m = e.message;
    let where = /line (\d+) column (\d+)/i.exec(m);
    let line, col;
    if (where) { line = +where[1]; col = +where[2]; }
    else if ((where = /position (\d+)/i.exec(m))) ({ line, col } = posToLineCol(text, +where[1]));
    else if (/end of (JSON )?(data|input)/i.test(m)) ({ line, col } = posToLineCol(text, text.length));
    const msg = m.replace(/^JSON\.parse: /, "").replace(/ in JSON at position \d+.*$/, "").replace(/ at line \d+ column \d+ of the JSON data$/, "")
      .replace(/^./, (c) => c.toUpperCase());
    const err = new Error(line ? `Line ${line}, column ${col}: ${msg}` : msg);
    err.line = line;
    err.col = col;
    return err;
  }
  function xmlError(node) {
    const t = node.textContent || "";
    const m = /line (\d+)(?: at column |, Column: |, column )(\d+)/i.exec(t) || /Line Number (\d+), Column (\d+)/i.exec(t);
    const msg = (t.split("\n").find((l) => /error|expected|unclosed|mismatch|invalid/i.test(l)) || t.split("\n")[0] || "Not valid XML")
      .replace(/^.*?error on line \d+ at column \d+:\s*/i, "").replace(/^XML Parsing Error:\s*/i, "").trim();
    const err = new Error(m ? `Line ${m[1]}, column ${m[2]}: ${msg}` : msg);
    if (m) { err.line = +m[1]; err.col = +m[2]; }
    return err;
  }
  function detect(text) {
    const t = text.trimStart();
    if (!t) return "text";
    if (t[0] === "{" || t[0] === "[") return "json";
    if (/^<!doctype html|^<html[\s>]/i.test(t)) return "html";
    if (t[0] === "<") return "xml";
    const ls = text.split("\n").filter((l) => l.trim() && !l.trim().startsWith("#")).slice(0, 30);
    const yamlish = ls.filter((l) => /^\s*(- )?[\w"'.\/-][^:]*:(\s|$)/.test(l) || /^\s*- /.test(l) || /^---/.test(l)).length;
    // Markdown's own signs (a list alone could be YAML too): headings, code fences, quotes, links, bold, tables
    const all = text.split("\n");
    const strong = all.filter((l) => /^#{1,6}\s+\S|^```|^>\s|\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*|^\|.*\|\s*$/.test(l)).length;
    const lists = all.filter((l) => /^\s*([-*+]|\d+\.)\s/.test(l)).length;
    if (ls.length && yamlish / ls.length >= 0.6) {
      if (!strong) return "yaml";
      try { YAML.parseAll(text); return "yaml"; } catch { return "markdown"; }   // reads as YAML: it's YAML
    }
    return strong >= 2 || (strong >= 1 && lists >= 1) ? "markdown" : "text";
  }
  function validate(text, lang) {
    const kind = lang === "auto" ? detect(text) : lang;
    if (!text.trim() || !["json", "yaml", "xml"].includes(kind)) return { kind, ok: true, message: "" };
    try {
      if (kind === "json") JSON.parse(text);
      else if (kind === "yaml") YAML.parseAll(text);
      else if (kind === "xml") xmlDoc(text);
      return { kind, ok: true, message: `Valid ${kind.toUpperCase()}` };
    } catch (e) {
      const err = kind === "json" ? jsonError(text, e) : e;
      const line = err.line, col = err.col;
      const message = kind === "yaml" && line ? `Line ${line}, column ${col}: ${err.message}` : err.message;
      return { kind, ok: false, message, line, col };
    }
  }

  return { TOOLS, validate, detect, encrypt, decrypt, hmac, lineDiff, hideSecrets, md5, b64ToBytes, bytesToB64, parseCsv, password, uuid };
})();

if (typeof module !== "undefined") module.exports = TextKit;
