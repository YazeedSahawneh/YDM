// A YAML reader and writer for the text editor: the YAML people actually write (Kubernetes, Docker Compose,
// GitHub Actions, Ansible, app configs), written for YDM, no outside code.
// Reads: block mappings and sequences, plain / 'single' / "double" quoted scalars (also over several lines),
// literal | and folded > blocks with - / + chomping and indentation indicators, flow [ ] and { } (over
// several lines too), comments, several documents (--- / ...), anchors & and aliases *, the << merge key,
// the core tags (!!str !!int !!float !!bool !!null, others are ignored) and YAML 1.2 core types.
// Errors carry the line and column: YAML.parse throws YAMLError(message, line, col).
// Writes (YAML.stringify): readable block style, strings quoted only when they must be, | for multi-line text.

const YAML = (() => {
  class YAMLError extends Error {
    constructor(message, line, col) {
      super(message);
      this.line = line;
      this.col = col;
    }
  }

  const fail = (msg, line, col = 1) => { throw new YAMLError(msg, line, col); };

  // ---------------------------------------------------------------------------------------------- scalars
  function resolve(text, tag) {
    if (tag === "!!str") return text;
    if (tag === "!!null") return null;
    const t = text;
    if (tag === "!!bool") return /^(true|True|TRUE)$/.test(t);
    if (tag === "!!int") return parseInt(t, /^0x/i.test(t) ? 16 : /^0o/i.test(t) ? 8 : 10);
    if (tag === "!!float") return parseFloat(t);
    if (t === "" || t === "~" || /^(null|Null|NULL)$/.test(t)) return null;
    if (/^(true|True|TRUE)$/.test(t)) return true;
    if (/^(false|False|FALSE)$/.test(t)) return false;
    if (/^[-+]?(0|[1-9][0-9_]*)$/.test(t)) return Number(t.replace(/_/g, ""));
    if (/^0o[0-7]+$/i.test(t)) return parseInt(t.slice(2), 8);
    if (/^0x[0-9a-f]+$/i.test(t)) return parseInt(t.slice(2), 16);
    if (/^[-+]?(\.[0-9]+|[0-9][0-9_]*(\.[0-9_]*)?)([eE][-+]?[0-9]+)?$/.test(t)) return Number(t.replace(/_/g, ""));
    if (/^[-+]?\.(inf|Inf|INF)$/.test(t)) return t[0] === "-" ? -Infinity : Infinity;
    if (/^\.(nan|NaN|NAN)$/.test(t)) return NaN;
    return t;
  }

  const ESC = { 0: "\0", a: "\x07", b: "\b", t: "\t", "\t": "\t", n: "\n", v: "\v", f: "\f", r: "\r", e: "\x1b",
                " ": " ", '"': '"', "/": "/", "\\": "\\", N: "\x85", _: "\xa0", L: " ", P: " " };

  // Quoted text over several lines folds: a line break and the spaces around it become one space, blank
  // lines become newlines, and in "double quotes" a \ at the end of a line joins the lines directly.
  function decodeQuoted(raw, q, line, col) {
    let out = "";
    for (let i = 0; i < raw.length; i++) {
      const c = raw[i];
      if (c === "\n") {
        out = out.replace(/[ \t]+$/, "");
        let j = i + 1, blanks = 0;
        for (;;) {
          while (raw[j] === " " || raw[j] === "\t") j++;
          if (raw[j] === "\n") { blanks++; j++; continue; }
          break;
        }
        out += blanks ? "\n".repeat(blanks) : " ";
        i = j - 1;
        continue;
      }
      if (q === '"' && c === "\\") {
        const e = raw[i + 1];
        if (e === "\n") {
          let j = i + 2;
          while (raw[j] === " " || raw[j] === "\t") j++;
          i = j - 1;
          continue;
        }
        if (e === "x" || e === "u" || e === "U") {
          const n = { x: 2, u: 4, U: 8 }[e];
          const hex = raw.slice(i + 2, i + 2 + n);
          if (!new RegExp(`^[0-9a-fA-F]{${n}}$`).test(hex)) fail(`A bad \\${e} escape in quoted text`, line, col);
          out += String.fromCodePoint(parseInt(hex, 16));
          i += 1 + n;
          continue;
        }
        if (e === undefined || !(e in ESC)) fail(`Unknown escape \\${e || ""} in quoted text`, line, col);
        out += ESC[e];
        i++;
        continue;
      }
      if (q === "'" && c === "'" && raw[i + 1] === "'") { out += "'"; i++; continue; }
      out += c;
    }
    return out;
  }

  // ---------------------------------------------------------------------------------------------- lines
  // Each line: { no, raw, indent, text } where text has the indentation and any comment removed.
  function stripComment(s) {
    let q = null;
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (q) {
        if (c === "\\" && q === '"') { i++; continue; }
        if (c === q) {
          if (q === "'" && s[i + 1] === "'") { i++; continue; }
          q = null;
        }
        continue;
      }
      if (c === '"' || c === "'") {
        const prev = i === 0 ? " " : s[i - 1];
        if (/[\s\[{,:\-?]/.test(prev) || i === 0) q = c;     // a quote only opens at the start of a scalar
        continue;
      }
      if (c === "#" && (i === 0 || /\s/.test(s[i - 1]))) return s.slice(0, i).replace(/\s+$/, "");
    }
    return s.replace(/\s+$/, "");
  }

  function splitLines(src) {
    return src.replace(/\r\n?/g, "\n").split("\n").map((raw, i) => {
      const m = /^( *)(\t*)/.exec(raw);
      return { no: i + 1, raw, indent: m[1].length, tab: m[2].length > 0 && raw.trim() !== "" };
    });
  }

  // ---------------------------------------------------------------------------------------------- parser
  function Parser(lines, from, to) {
    this.lines = lines;
    this.i = from;
    this.end = to;
    this.anchors = new Map();
  }

  Parser.prototype.peek = function () {
    // the next line with content (comments and blank lines skipped); text cached on the line
    while (this.i < this.end) {
      const l = this.lines[this.i];
      if (l.text === undefined) {
        if (l.tab) fail("Tabs can't be used for indentation in YAML: use spaces", l.no, l.indent + 1);
        l.text = stripComment(l.raw.slice(l.indent));
      }
      if (l.text !== "") return l;
      this.i++;
    }
    return null;
  };

  // value at indentation `ind` (a child must be indented more than its parent; `parentInd` is the parent's)
  Parser.prototype.block = function (parentInd, allowSeqAtParent) {
    const l = this.peek();
    if (!l) return null;
    if (l.indent <= parentInd && !(allowSeqAtParent && l.indent === parentInd && isSeqItem(l.text))) return null;
    return this.node(l.indent);
  };

  Parser.prototype.node = function (ind) {
    const l = this.peek();
    let props = takeProps(l.text);
    if (props.rest === "" && (props.anchor || props.tag)) {        // "&anchor" alone: the node is below
      this.i++;
      const v = this.block(l.indent - 1, true);
      return this.finish(v, props, l);
    }
    if (isSeqItem(props.rest)) return this.finish(this.seq(l.indent), props, l);
    if (isKey(props.rest)) {
      l.text = props.rest;                                    // "&anchor key: v": the anchor names the mapping
      return this.finish(this.map(l.indent), props, l);
    }
    // a scalar or flow node standing on its own line(s)
    return this.inline(l, props.rest, l.indent + (l.text.length - props.rest.length), l.indent - 1, props);
  };

  Parser.prototype.finish = function (v, props, l) {
    if (props.tag && props.tag !== "!!map" && props.tag !== "!!seq" && typeof v === "string") v = resolve(v, props.tag);
    if (props.anchor) this.anchors.set(props.anchor, v);
    return v;
  };

  Parser.prototype.seq = function (ind) {
    const out = [];
    for (;;) {
      const l = this.peek();
      if (!l || l.indent < ind) break;
      if (l.indent > ind) fail("This line is indented more than the list it belongs to", l.no, l.indent + 1);
      if (!isSeqItem(l.text)) {
        if (isKey(l.text)) break;                                  // a mapping key at the list's own column
        fail("Expected a list item (\"- \") here", l.no, l.indent + 1);
      }
      const after = l.text.slice(1);
      const gap = after.length - after.trimStart().length;
      const content = after.trimStart();
      if (content === "") {
        this.i++;
        out.push(this.block(ind, false));
        continue;
      }
      // "- key: value" or "- - x": the content starts a node at its own column, on this same line
      l.indent = ind + 1 + gap;
      l.text = content;
      out.push(this.node(l.indent));
    }
    return out;
  };

  Parser.prototype.map = function (ind) {
    const out = {};
    const seen = new Set();
    for (;;) {
      const l = this.peek();
      if (!l || l.indent < ind) break;
      if (l.indent > ind) fail("This line is indented more than the keys above it", l.no, l.indent + 1);
      if (isSeqItem(l.text) && !isKey(l.text)) break;
      if (!isKey(l.text)) fail("Expected \"key: value\" here", l.no, l.indent + 1);
      const { key, rest, keyEnd } = splitKey(l.text, l);
      if (key === "<<") {
        this.i++;
        const v = this.valueAfterKey(l, rest, ind, keyEnd);
        for (const m of Array.isArray(v) ? v : [v]) {
          if (!m || typeof m !== "object" || Array.isArray(m)) fail("<< can only merge mappings", l.no, l.indent + 1);
          for (const [k, x] of Object.entries(m)) if (!seen.has(k)) { out[k] = x; }
        }
        continue;
      }
      if (seen.has(key)) fail(`The key "${key}" appears twice in this mapping`, l.no, l.indent + 1);
      seen.add(key);
      this.i++;
      out[key] = this.valueAfterKey(l, rest, ind, keyEnd);
    }
    return out;
  };

  Parser.prototype.valueAfterKey = function (l, rest, ind, keyEnd) {
    const props = takeProps(rest);
    const col = l.indent + keyEnd + (rest.length - props.rest.length) + 1;
    if (props.rest === "") {
      const v = this.block(ind, true);
      return this.finish(v, props, l);
    }
    this.i--;                                                     // inline() reads continuation lines from here
    return this.inline(l, props.rest, col - 1, ind, props);
  };

  // A value written on the line `l` starting at column `col` (0-based), possibly continuing below.
  Parser.prototype.inline = function (l, text, col, ownerInd, props) {
    this.i++;
    let v;
    const c = text[0];
    if (c === "|" || c === ">") v = this.blockScalar(l, text, ownerInd);
    else if (c === "*") {
      const name = text.slice(1).trim();
      if (!/^[^\s,\[\]{}]+$/.test(name)) fail("A bad alias name", l.no, col + 1);
      if (!this.anchors.has(name)) fail(`Unknown alias *${name}: define &${name} first`, l.no, col + 1);
      v = this.anchors.get(name);
      return v;
    } else if (c === "[" || c === "{") {
      const src = this.gatherFlow(l, text, col);
      const f = new Flow(src.text, src.map, this.anchors);
      v = f.value();
      f.end();
    } else if (c === '"' || c === "'") {
      v = this.quoted(l, text, col, ownerInd);
    } else {
      const raw = this.plainContinued(text, ownerInd);
      v = props && props.tag ? raw : resolve(raw);             // a tag decides the type in finish()
    }
    return this.finish(v, props || {}, l);
  };

  Parser.prototype.plainContinued = function (text, ownerInd) {
    // a plain scalar may go on over more-indented lines: joined with spaces (blank line = newline)
    let out = text;
    let blank = 0;
    while (this.i < this.end) {
      const l = this.lines[this.i];
      const t = l.raw.trim();
      if (t === "") { blank++; this.i++; continue; }
      if (l.indent <= ownerInd || t.startsWith("#")) break;
      const s = stripComment(l.raw.slice(l.indent));
      if (isKey(s) || isSeqItem(s)) break;
      out += blank ? "\n".repeat(blank) : " ";
      out += s;
      blank = 0;
      this.i++;
    }
    return out;
  };

  Parser.prototype.quoted = function (l, text, col) {
    const q = text[0];
    let raw = "", line = l, src = text.slice(1), c0 = col + 1;
    for (;;) {
      let closed = -1;
      for (let i = 0; i < src.length; i++) {
        if (q === '"' && src[i] === "\\") { i++; continue; }
        if (src[i] === q) {
          if (q === "'" && src[i + 1] === "'") { i++; continue; }
          closed = i;
          break;
        }
      }
      if (closed >= 0) {
        raw += src.slice(0, closed);
        const after = stripComment(src.slice(closed + 1)).trim();
        if (after !== "") fail("Unexpected text after the closing quote", line.no, c0 + closed + 2);
        break;
      }
      raw += src + "\n";
      if (this.i >= this.end) fail("This quoted text is never closed", l.no, col + 1);
      line = this.lines[this.i++];
      src = line.raw;
      c0 = 0;
    }
    return decodeQuoted(raw, q, l.no, col + 1);
  };

  Parser.prototype.blockScalar = function (l, head, ownerInd) {
    const m = /^([|>])([1-9]?)([+-]?)([1-9]?)\s*$/.exec(head);
    if (!m) fail("A block scalar header is | or > followed by an optional indentation digit and + or -", l.no);
    const folded = m[1] === ">";
    const chomp = m[3];
    const explicit = Number(m[2] || m[4]) || 0;
    const body = [];
    let ind = explicit ? Math.max(0, ownerInd) + explicit : 0;
    while (this.i < this.end) {
      const x = this.lines[this.i];
      if (x.raw.trim() === "") { body.push(""); this.i++; continue; }
      if (!ind) {
        if (x.indent <= ownerInd) break;
        ind = x.indent;
      }
      if (x.indent < ind) break;
      body.push(x.raw.slice(ind));
      this.i++;
    }
    // blank lines at the end: kept for "+", else they don't belong to the text
    let trail = 0;
    while (body.length && body[body.length - 1] === "") { body.pop(); trail++; }
    let text;
    if (!folded) text = body.join("\n");
    else {
      text = "";
      for (let i = 0; i < body.length; i++) {
        const cur = body[i];
        if (i === 0) { text = cur; continue; }
        const prev = body[i - 1];
        if (cur === "") text += "\n";
        else if (prev === "" || /^\s/.test(cur) || /^\s/.test(prev)) text += (prev === "" ? "" : "\n") + cur;
        else text += " " + cur;
      }
    }
    if (chomp === "-") return text;
    if (chomp === "+") return text + "\n".repeat(trail + (body.length ? 1 : 0));
    return body.length ? text + "\n" : "";
  };

  Parser.prototype.gatherFlow = function (l, text, col) {
    // a flow collection may span lines: collect until the brackets balance (quotes respected)
    let depth = 0, q = null, out = "", map = [];
    let line = l, s = text, c0 = col;
    for (;;) {
      for (let i = 0; i < s.length; i++) {
        const ch = s[i];
        out += ch;
        map.push([line.no, c0 + i + 1]);
        if (q) {
          if (ch === "\\" && q === '"') { out += s[i + 1] || ""; map.push([line.no, c0 + i + 2]); i++; continue; }
          if (ch === q) q = null;
          continue;
        }
        if (ch === '"' || ch === "'") q = ch;
        else if (ch === "#" && /\s/.test(s[i - 1] || " ")) { out = out.slice(0, -1); map.pop(); break; }
        else if (ch === "[" || ch === "{") depth++;
        else if (ch === "]" || ch === "}") {
          depth--;
          if (depth === 0) {
            const after = stripComment(s.slice(i + 1)).trim();
            if (after !== "") fail("Unexpected text after the closing bracket", line.no, c0 + i + 2);
            return { text: out, map };
          }
        }
      }
      if (this.i >= this.end) fail("This [ or { is never closed", l.no, col + 1);
      line = this.lines[this.i++];
      s = line.raw.trimStart();
      c0 = line.raw.length - s.length;
      out += " ";
      map.push([line.no, 1]);
    }
  };

  // -------------------------------------------------------------------------------------------- flow style
  function Flow(text, map, anchors) {
    this.s = text;
    this.p = 0;
    this.map = map;
    this.anchors = anchors;
  }
  Flow.prototype.err = function (msg) {
    const at = this.map[Math.min(this.p, this.map.length - 1)] || [1, 1];
    fail(msg, at[0], at[1]);
  };
  Flow.prototype.ws = function () { while (this.p < this.s.length && /\s/.test(this.s[this.p])) this.p++; };
  Flow.prototype.end = function () { this.ws(); if (this.p < this.s.length) this.err("Unexpected text after the closing bracket"); };
  Flow.prototype.value = function () {
    this.ws();
    const c = this.s[this.p];
    if (c === "[") return this.seq();
    if (c === "{") return this.mapping();
    if (c === '"' || c === "'") return this.quoted();
    if (c === "*") {
      this.p++;
      const m = /^[^\s,\[\]{}]+/.exec(this.s.slice(this.p));
      if (!m) this.err("A bad alias");
      this.p += m[0].length;
      if (!this.anchors.has(m[0])) this.err(`Unknown alias *${m[0]}`);
      return this.anchors.get(m[0]);
    }
    let anchor = null;
    if (c === "&") {
      const m = /^&([^\s,\[\]{}]+)\s*/.exec(this.s.slice(this.p));
      anchor = m[1];
      this.p += m[0].length;
      const v = this.value();
      this.anchors.set(anchor, v);
      return v;
    }
    const m = /^(?:[^,\[\]{}:#]|:(?=[^\s,\[\]{}]))+/.exec(this.s.slice(this.p));
    if (!m) this.err("Expected a value");
    this.p += m[0].length;
    return resolve(m[0].trim());
  };
  Flow.prototype.quoted = function () {
    const q = this.s[this.p++];
    let v = "";
    while (this.p < this.s.length) {
      const c = this.s[this.p];
      if (q === '"' && c === "\\") {
        const e = this.s[this.p + 1];
        if (e === "u" || e === "x" || e === "U") {
          const n = { x: 2, u: 4, U: 8 }[e];
          v += String.fromCodePoint(parseInt(this.s.slice(this.p + 2, this.p + 2 + n), 16));
          this.p += 2 + n;
          continue;
        }
        if (!(e in ESC)) this.err(`Unknown escape \\${e}`);
        v += ESC[e];
        this.p += 2;
        continue;
      }
      if (c === q) {
        if (q === "'" && this.s[this.p + 1] === "'") { v += "'"; this.p += 2; continue; }
        this.p++;
        return v;
      }
      v += c;
      this.p++;
    }
    this.err("This quoted text is never closed");
  };
  Flow.prototype.seq = function () {
    this.p++;
    const out = [];
    for (;;) {
      this.ws();
      if (this.s[this.p] === "]") { this.p++; return out; }
      const v = this.value();
      this.ws();
      if (this.s[this.p] === ":") {                          // [a: 1] is a list holding a one-key mapping
        this.p++;
        out.push({ [String(v)]: this.value() });
        this.ws();
      } else out.push(v);
      if (this.s[this.p] === ",") { this.p++; continue; }
      if (this.s[this.p] === "]") { this.p++; return out; }
      this.err("Expected , or ] in this list");
    }
  };
  Flow.prototype.mapping = function () {
    this.p++;
    const out = {};
    for (;;) {
      this.ws();
      if (this.s[this.p] === "}") { this.p++; return out; }
      const k = this.value();
      this.ws();
      let v = null;
      if (this.s[this.p] === ":") { this.p++; this.ws(); v = this.s[this.p] === "," || this.s[this.p] === "}" ? null : this.value(); }
      const key = String(k);
      if (Object.prototype.hasOwnProperty.call(out, key)) this.err(`The key "${key}" appears twice in this mapping`);
      out[key] = v;
      this.ws();
      if (this.s[this.p] === ",") { this.p++; continue; }
      if (this.s[this.p] === "}") { this.p++; return out; }
      this.err("Expected , or } in this mapping");
    }
  };

  // ---------------------------------------------------------------------------------------------- helpers
  function isSeqItem(t) { return t === "-" || t.startsWith("- "); }

  function isKey(t) {
    // "key: value" or "key:" — the key plain or quoted. Flow / block starts and list items aren't keys.
    if (!t || isSeqItem(t) || /^[\[{|>*%@`]/.test(t)) return false;
    if (/^[&!]/.test(t)) return false;                       // node() takes "&anchor" / "!tag" off first
    if (t[0] === '"' || t[0] === "'") {
      const q = t[0];
      let i = 1;
      for (; i < t.length; i++) {
        if (q === '"' && t[i] === "\\") { i++; continue; }
        if (t[i] === q) { if (q === "'" && t[i + 1] === "'") { i++; continue; } break; }
      }
      return i < t.length && /^\s*:(\s|$)/.test(t.slice(i + 1));
    }
    const m = /:(\s|$)/.exec(t);
    return !!m && m.index > 0;
  }

  function splitKey(t, l) {
    let key, i;
    if (t[0] === '"' || t[0] === "'") {
      const q = t[0];
      let val = "";
      for (i = 1; i < t.length; i++) {
        const c = t[i];
        if (q === '"' && c === "\\") { val += ESC[t[i + 1]] ?? t[i + 1]; i++; continue; }
        if (c === q) { if (q === "'" && t[i + 1] === "'") { val += "'"; i++; continue; } break; }
        val += c;
      }
      key = val;
      i++;                                                       // past the closing quote
      while (t[i] === " ") i++;
      i++;                                                       // past the colon
    } else {
      const m = /^(.*?):(\s|$)/.exec(t);
      if (!m) fail("Expected \"key: value\" here", l.no, l.indent + 1);
      key = m[1].trim();
      i = m[1].length + 1;
      if (/^[\[{]/.test(key)) fail("Complex keys aren't supported", l.no, l.indent + 1);
    }
    const rest = t.slice(i).trim();
    return { key, rest, keyEnd: i + (t.slice(i).length - t.slice(i).trimStart().length) };
  }

  function takeProps(t) {
    // optional "&anchor" and "!tag" before a node, in either order
    let anchor = null, tag = null, rest = t;
    for (let k = 0; k < 2; k++) {
      let m;
      if (!anchor && (m = /^&([^\s,\[\]{}]+)(\s+|$)/.exec(rest))) { anchor = m[1]; rest = rest.slice(m[0].length); }
      else if (!tag && (m = /^(!!?[^\s]*|!<[^>]*>)(\s+|$)/.exec(rest))) { tag = m[1]; rest = rest.slice(m[0].length); }
    }
    return { anchor, tag, rest };
  }

  // ---------------------------------------------------------------------------------------------- documents
  function parseAll(src) {
    const lines = splitLines(src);
    // split into documents at "---" / "..." (directives "%YAML" are skipped)
    const docs = [];
    let start = 0, started = false;
    const push = (from, to) => docs.push([from, to]);
    for (let i = 0; i < lines.length; i++) {
      const r = lines[i].raw;
      if (/^%/.test(r) && !started) { lines[i].text = ""; continue; }
      if (/^---(\s|$)/.test(r)) {
        if (started || hasContent(lines, start, i)) push(start, i);
        const rest = r.slice(3).trim();
        lines[i].raw = rest ? " ".repeat(4) + rest : "";
        lines[i].indent = rest ? 4 : 0;
        if (rest) { lines[i].text = undefined; start = i; } else { lines[i].text = ""; start = i + 1; }
        started = true;
        continue;
      }
      if (/^\.\.\.(\s|$)/.test(r)) { push(start, i); lines[i].text = ""; start = i + 1; started = false; continue; }
    }
    if (hasContent(lines, start, lines.length) || !docs.length) push(start, lines.length);
    return docs.map(([a, b]) => {
      const p = new Parser(lines, a, b);
      const first = p.peek();
      if (!first) return null;
      const v = p.node(first.indent);
      const extra = p.peek();
      if (extra) fail(extra.indent > first.indent ? "This line is indented more than expected" :
                      "Unexpected text: is the indentation right?", extra.no, extra.indent + 1);
      return v;
    });
  }
  function hasContent(lines, a, b) {
    for (let i = a; i < b; i++) {
      if (lines[i].text === "" || /^%/.test(lines[i].raw)) continue;      // directives aren't content
      if (stripComment(lines[i].raw.trim()) !== "") return true;
    }
    return false;
  }

  function parse(src) {
    const docs = parseAll(src);
    return docs.length === 1 ? docs[0] : docs;
  }

  // ---------------------------------------------------------------------------------------------- writer
  const RESERVED = /^(true|false|yes|no|on|off|null|~|y|n)$/i;
  function needsQuotes(s) {
    if (s === "") return true;
    if (RESERVED.test(s)) return true;
    if (resolve(s) !== s) return true;                           // would read back as a number/bool/null
    if (/^[\s]|[\s]$/.test(s)) return true;
    if (/^[-?:,\[\]{}#&*!|>'"%@`]/.test(s)) return true;
    if (/: |:$| #|\t/.test(s)) return true;
    if (/[\u0000-\u0008\u000b-\u001f\u007f]/.test(s)) return true;
    return false;
  }
  function quote(s) {
    if (!/["\\\u0000-\u001f\u007f]/.test(s) && !s.includes("'")) return `'${s}'`;
    return JSON.stringify(s);
  }
  function scalar(v) {
    if (v === null || v === undefined) return "null";
    if (typeof v === "boolean") return v ? "true" : "false";
    if (typeof v === "number") return Number.isNaN(v) ? ".nan" : v === Infinity ? ".inf" : v === -Infinity ? "-.inf" : String(v);
    const s = String(v);
    return needsQuotes(s) ? quote(s) : s;
  }
  function keyText(k) { return needsQuotes(k) || /[:#]/.test(k) ? quote(k) : k; }

  function stringify(value, indent = 2) {
    const pad = (n) => " ".repeat(n);
    const out = [];
    const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
    const multiline = (s) => typeof s === "string" && s.includes("\n") && !/[\u0000-\u0008\u000b-\u001f]/.test(s);
    function block(s, ind) {
      const chomp = s.endsWith("\n") ? (s.endsWith("\n\n") ? "+" : "") : "-";
      const head = /^\s/.test(s) ? `|${indent}${chomp}` : `|${chomp}`;
      const body = (chomp === "+" ? s.slice(0, -1) : s.replace(/\n$/, "")).split("\n")
        .map((l) => (l === "" ? "" : pad(ind) + l));
      return [head, body];
    }
    function write(v, ind, prefix) {
      if (Array.isArray(v)) {
        if (!v.length) return out.push(prefix + "[]");
        if (prefix.trim() !== "") out.push(prefix.replace(/\s+$/, ""));
        for (const x of v) {
          const lead = pad(ind) + "- ";
          if (isObj(x) && Object.keys(x).length) writeMapInline(x, ind + 2, lead);
          else if (Array.isArray(x) && x.length) { out.push(lead.replace(/\s+$/, "")); write(x, ind + 2, ""); }
          else if (multiline(x)) { const [h, body] = block(x, ind + 2); out.push(lead + h, ...body); }
          else out.push(lead + (isObj(x) ? "{}" : Array.isArray(x) ? "[]" : scalar(x)));
        }
        return;
      }
      if (isObj(v)) {
        if (!Object.keys(v).length) return out.push(prefix + "{}");
        if (prefix.trim() !== "") out.push(prefix.replace(/\s+$/, ""));
        for (const [k, x] of Object.entries(v)) writePair(k, x, ind);
        return;
      }
      if (multiline(v)) { const [h, body] = block(v, ind); out.push(prefix + h, ...body); return; }
      out.push(prefix + scalar(v));
    }
    function writePair(k, x, ind) {
      const lead = pad(ind) + keyText(k) + ":";
      if (Array.isArray(x) && x.length) { out.push(lead); write(x, ind + indent, ""); }   // lists indented under their key
      else if (isObj(x) && Object.keys(x).length) { out.push(lead); write(x, ind + indent, ""); }
      else if (multiline(x)) { const [h, body] = block(x, ind + indent); out.push(`${lead} ${h}`, ...body); }
      else out.push(`${lead} ${Array.isArray(x) ? "[]" : isObj(x) ? "{}" : scalar(x)}`);
    }
    function writeMapInline(obj, ind, lead) {
      let first = true;
      for (const [k, x] of Object.entries(obj)) {
        if (first) {
          const before = out.length;
          writePair(k, x, ind);
          out[before] = lead + out[before].slice(ind);
          first = false;
        } else writePair(k, x, ind);
      }
    }
    write(value, 0, "");
    return out.join("\n") + "\n";
  }

  return { parse, parseAll, stringify, YAMLError };
})();

if (typeof module !== "undefined") module.exports = YAML;
