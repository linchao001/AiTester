/* KB 页工具函数：逐行转写自 prototype/index.html（:1448 escapeHtml、:1519 wsIcon、:1612-1649 mdRender、
   :2346-2355 一行式族、:2357-2370 splitFm、:2564-2571 kbDiff→kbDiffHtml），语义与原型完全一致。 */

/** 原型 :1448 —— 仅转义 & < >（配合原型轻量 md 渲染的取值域，不做属性级转义）。 */
export function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** 原型 :2346 —— 仅 Markdown 可进编辑器。 */
export const kbIsMd = (ext: string): boolean => ext === ".md" || ext === ".markdown";

/** 原型 :2347 —— rel 的父目录（''=根）。 */
export const kbDirOf = (p: string): string => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");

/** 原型 :2348 —— 目录+文件名拼 rel。 */
export const kbJoin = (d: string, n: string): string => (d ? `${d}/${n}` : n);

/** 原型 :2350 —— 展示/确认文案用：rel 拼成 Windows 风格绝对路径。
    原型读全局 KB.root，这里改为纯函数显式传 root（Task 9/10 调用签名：kbDisp(root, rel)）。 */
export const kbDisp = (root: string, rel: string): string => `${root}\\${rel}`.replace(/[\\/]+/g, "\\");

/** 原型 :2352 —— YYYY-MM-DD。 */
export const kbToday = (): string => new Date().toISOString().slice(0, 10);

/** 原型 :2353 —— UTF-8 字节数。 */
export const kbBytes = (s: string): number => new TextEncoder().encode(s).length;

/** 原型 :2354 —— 体积可读化（B / KB / MB，一位小数）。 */
export const fmtSize = (b: number): string =>
  b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1048576).toFixed(1)} MB`;

/** 原型 :2355 —— 毫秒时间戳 → zh-CN 本地串（斜杠换横线）；0/缺省显示「—」。 */
export const fmtTime = (ms: number): string =>
  ms ? new Date(ms).toLocaleString("zh-CN", { hour12: false }).replace(/\//g, "-") : "—";

/** 原型 :2533 —— 标题→安全文件名 slug（非法字符换下划线、空白连字符、截 60）。 */
export const kbSlug = (s: string): string =>
  s.trim().replace(/[\\/:*?"<>|]/g, "_").replace(/\s+/g, "-").slice(0, 60);

/** 原型 :2357-2370 —— 切 YAML frontmatter（简化解析：仅 key: value 行，去外层引号）。
    返回除 brief 声明的 { fm, body } 外保留原型的 head（原文头部串，Task 9 回写 frontmatter 用）。 */
export interface KbSplitFm {
  fm: Record<string, string> | null;
  body: string;
  head: string;
}
export function splitFm(text: string): KbSplitFm {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(text || "");
  if (!m) return { fm: null, body: text || "", head: "" };
  const fm: Record<string, string> = {};
  m[1].split(/\r?\n/).forEach((line) => {
    const kv = /^([A-Za-z0-9_.\-]+):\s*(.*)$/.exec(line);
    if (!kv) return;
    let v = kv[2].trim();
    if (v.length > 1 && v[0] === '"' && v[v.length - 1] === '"') v = v.slice(1, -1);
    fm[kv[1]] = v;
  });
  return { fm, body: (text || "").slice(m[0].length), head: m[0] };
}

/** 原型 :1519-1522 —— 文件图标（目录行由调用方直接给 📁，这里只处理文件名）。 */
export const wsIcon = (name: string): string =>
  /\.(spec\.[tm]s|ts|js|py|java)$/.test(name) ? "📜"
    : /\.md$/.test(name) ? "📝"
      : /\.xlsx?$/.test(name) ? "📊"
        : "📄";

/** href scheme 白名单：带 scheme: 前缀只放行 http/https/mailto，其余（相对路径/锚链接）原样通过。
    中文注释：view 态经 dangerouslySetInnerHTML 挂载本模块产物，javascript:/data: 之类 scheme 可直接成
    为可点击脚本链接；渲染器现转为生产可用，必须在 href 构造处收口。 */
function hrefSafe(u: string): boolean {
  if (u.startsWith("//")) return false; // 协议相对链接会跳出本站，不属于「相对/锚链接」
  const m = /^[A-Za-z][A-Za-z0-9+.-]*:/.exec(u);
  return !m || /^(https?:|mailto:)/i.test(m[0]);
}

/** 原型 :1612-1618 —— 行内语法（code/b/i/链接）。scheme 不在白名单的链接降级为纯文本（见 hrefSafe 注释）。 */
function mdInline(t: string): string {
  return t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\*([^*]+)\*/g, "<i>$1</i>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_all, text: string, url: string) =>
      hrefSafe(url) ? `<a href="${url}" target="_blank" rel="noopener">${text}</a>` : text);
}

/** 原型 :1619-1649 —— 轻量 Markdown 渲染（先整体转义再按行组装 HTML 串）。
    与原型逐行等价；输出为 HTML 串，React 侧用 dangerouslySetInnerHTML 挂载。 */
export function mdRender(src: string): string {
  // 相对原型补 escape `"`：整体转义串会进入 mdInline 拼进 href="…"，
  // 不转义引号时 URL 里的 " 可截断属性注入事件处理器（carry-over 修复）。
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const lines = esc(src || "").split(/\r?\n/);
  const out: string[] = [];
  let i = 0;
  const isBlock = (L: string) => /^(#{1,4}\s|```|&gt;|\s*[-*]\s|\s*\d+\.\s|\||-{3,}\s*$|\*{3,}\s*$)/.test(L);
  while (i < lines.length) {
    const L = lines[i];
    let m: RegExpMatchArray | null;
    if (/^```/.test(L)) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) { code.push(lines[i]); i++; }
      i++;
      out.push(`<pre><code>${code.join("\n")}</code></pre>`);
      continue;
    }
    if ((m = L.match(/^(#{1,4})\s+(.*)/))) {
      const h = m[1].length;
      out.push(`<h${h}>${mdInline(m[2])}</h${h}>`);
      i++;
      continue;
    }
    if (/^(-{3,}|\*{3,})\s*$/.test(L)) { out.push("<hr>"); i++; continue; }
    if (/^&gt;\s?/.test(L)) {
      const q: string[] = [];
      while (i < lines.length && /^&gt;\s?/.test(lines[i])) { q.push(lines[i].replace(/^&gt;\s?/, "")); i++; }
      out.push(`<blockquote>${mdInline(q.join(" "))}</blockquote>`);
      continue;
    }
    if (/^\s*[-*]\s+/.test(L)) {
      const it: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { it.push(`<li>${mdInline(lines[i].replace(/^\s*[-*]\s+/, ""))}</li>`); i++; }
      out.push(`<ul>${it.join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+\.\s+/.test(L)) {
      const it: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { it.push(`<li>${mdInline(lines[i].replace(/^\s*\d+\.\s+/, ""))}</li>`); i++; }
      out.push(`<ol>${it.join("")}</ol>`);
      continue;
    }
    if (/^\s*\|/.test(L)) {
      const rows: string[] = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) { rows.push(lines[i]); i++; }
      const cells = (r: string) => r.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
      let html = "<table>";
      rows.forEach((r, ri) => {
        if (ri === 1 && /^[\s|:-]+$/.test(r)) return;
        const tag = ri === 0 ? "th" : "td";
        html += `<tr>${cells(r).map((c) => `<${tag}>${mdInline(c)}</${tag}>`).join("")}</tr>`;
      });
      out.push(`${html}</table>`);
      continue;
    }
    if (/^\s*$/.test(L)) { i++; continue; }
    const p = [L];
    i++;
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !isBlock(lines[i])) { p.push(lines[i]); i++; }
    out.push(`<p>${mdInline(p.join("<br>"))}</p>`);
  }
  return out.join("");
}

/** 原型 :2564-2571 kbDiff 的 React 友好版：逻辑逐行照抄，产出 HTML 串（Task 10 草案卡 diff 用）。
    行内容经 escapeHtml，span 结构与原型 .kb-draft .d-diff .add/.del 一致。 */
export function kbDiffHtml(oldT: string, newT: string): string {
  const o = (oldT || "").split(/\r?\n/);
  const n = (newT || "").split(/\r?\n/);
  const oSet = new Set(o);
  const out: string[] = [];
  o.forEach((l) => { if (!n.includes(l)) out.push(`<span class="del">- ${escapeHtml(l)}</span>`); });
  n.forEach((l) => { if (!oSet.has(l)) out.push(`<span class="add">+ ${escapeHtml(l)}</span>`); });
  if (!out.length) out.push("= 无行级变化");
  return out.slice(0, 50).join("\n");
}

/** brief Step 1 —— frontmatter 展示键序（原型 kbPaint :2452 的 keys 数组）。 */
export const KB_FM_KEYS = ["name", "description", "bucket", "status", "confidence",
  "priority", "requirement_id", "updated_by_agent", "updated_at", "signals"] as const;

/** 原型 :2519 —— 目录快捷选择桶。 */
export const KB_BUCKETS = ["_inbox", "business/dbInfo", "business/wiki", "test/test_cases",
  "test/defects", "test/test_design", "tools"];
