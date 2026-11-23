// Markdown → HTML for the text editor's preview, written for YDM. Safe by construction: the text is escaped
// first and only this file's own tags come out; links and pictures keep http(s) / mailto addresses only.
// Covers what people write: headings, paragraphs, **bold**, *italic*, ~~strike~~, `code`, fenced code,
// quotes, lists (nested, numbered, - [ ] tasks), tables with alignment, rules, links, pictures, autolinks.

const Markdown = (() => {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const safeUrl = (u) => (/^(https?:|mailto:|#)/i.test(u.trim()) ? u.trim().replace(/"/g, "%22") : "");

  function inline(text) {
    const codes = [];
    let s = esc(text).replace(/`([^`]+)`/g, (_m, c) => `\u0000${codes.push(`<code>${c}</code>`) - 1}\u0000`);
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^)]*&quot;)?\)/g, (m, alt, url) => {
      const u = safeUrl(url.replace(/&amp;/g, "&"));
      return u ? `<img alt="${alt}" src="${esc(u)}">` : m;
    });
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^)]*&quot;)?\)/g, (m, label, url) => {
      const u = safeUrl(url.replace(/&amp;/g, "&"));
      return u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${label}</a>` : m;
    });
    // bare addresses: only URL characters (the text is escaped already: &amp; is a URL's "&", other entities end it)
    s = s.replace(/(^|[\s(])(https?:\/\/(?:[^\s<&"]|&amp;)+?)(?=[.,;:!?)]*(?:\s|$|&(?!amp;)|<))/g, (_m, pre, url) =>
      `${pre}<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
    s = s.replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, (_m, a, b) => `<strong>${a || b}</strong>`)
      .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>")
      .replace(/(^|[^_\w])_([^_\s][^_]*?)_(?!\w)/g, "$1<em>$2</em>")
      .replace(/~~([^~]+)~~/g, "<del>$1</del>");
    return s.replace(/\u0000(\d+)\u0000/g, (_m, i) => codes[+i]);
  }

  function table(lines) {
    const cells = (l) => l.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
    const align = cells(lines[1]).map((c) => (/^:-+:$/.test(c) ? "center" : /-+:$/.test(c) ? "right" : ""));
    const row = (l, tag) => `<tr>${cells(l).map((c, i) => `<${tag}${align[i] ? ` style="text-align:${align[i]}"` : ""}>${inline(c)}</${tag}>`).join("")}</tr>`;
    return `<table><thead>${row(lines[0], "th")}</thead><tbody>${lines.slice(2).map((l) => row(l, "td")).join("")}</tbody></table>`;
  }

  function list(lines, start) {
    // lines of one list (same indentation), with deeper lines belonging to the item above
    const ordered = /^\s*\d+[.)]\s/.test(lines[start]);
    const marker = ordered ? /^\s*\d+[.)]\s/ : /^\s*[-*+]\s/;            // a change of marker starts a new list
    const base = lines[start].length - lines[start].trimStart().length;
    const items = [];
    let i = start;
    for (; i < lines.length; i++) {
      const l = lines[i];
      if (!l.trim()) { if (lines[i + 1] && /^\s*([-*+]|\d+[.)])\s/.test(lines[i + 1])) continue; break; }
      const ind = l.length - l.trimStart().length;
      const m = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(l);
      if (m && ind === base && !marker.test(l)) break;
      if (m && ind === base) items.push([m[2]]);
      else if (ind > base && items.length) items[items.length - 1].push(l.slice(Math.min(ind, base + 2)));
      else break;
    }
    const html = items.map(([first, ...rest]) => {
      const task = /^\[([ xX])\]\s+(.*)$/.exec(first);
      const head = task ? `<input type="checkbox" disabled${task[1] !== " " ? " checked" : ""}> ${inline(task[2])}` : inline(first);
      return `<li>${head}${rest.length ? render(rest.join("\n")) : ""}</li>`;
    }).join("");
    return [`<${ordered ? "ol" : "ul"}>${html}</${ordered ? "ol" : "ul"}>`, i];
  }

  function render(src) {
    const lines = src.replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    for (let i = 0; i < lines.length;) {
      const l = lines[i];
      let m;
      if (!l.trim()) { i++; continue; }
      if ((m = /^\s*(```|~~~)\s*([\w+-]*)/.exec(l))) {                  // fenced code
        const fence = m[1], body = [];
        for (i++; i < lines.length && !lines[i].trim().startsWith(fence); i++) body.push(lines[i]);
        i++;
        out.push(`<pre><code${m[2] ? ` class="lang-${esc(m[2])}"` : ""}>${esc(body.join("\n"))}</code></pre>`);
        continue;
      }
      if ((m = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(l))) { out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); i++; continue; }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)) { out.push("<hr>"); i++; continue; }
      if (/^\s*>/.test(l)) {
        const body = [];
        for (; i < lines.length && /^\s*>/.test(lines[i]); i++) body.push(lines[i].replace(/^\s*>\s?/, ""));
        out.push(`<blockquote>${render(body.join("\n"))}</blockquote>`);
        continue;
      }
      if (/^\s*\|/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(lines[i + 1])) {
        const body = [];
        for (; i < lines.length && /^\s*\|/.test(lines[i]); i++) body.push(lines[i]);
        out.push(table(body));
        continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
        const [html, next] = list(lines, i);
        out.push(html);
        i = next;
        continue;
      }
      const para = [];
      for (; i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*(```|~~~|>|[-*+]\s|\d+[.)]\s|\|))/.test(lines[i]); i++) {
        para.push(lines[i].trim());
      }
      if (!para.length) { para.push(l.trim()); i++; }
      out.push(`<p>${para.map(inline).join(" ")}</p>`);                  // lines of a paragraph join with a space
    }
    return out.join("\n");
  }

  return { render };
})();

if (typeof module !== "undefined") module.exports = Markdown;
