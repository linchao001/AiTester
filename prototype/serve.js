/* AiTester 原型本地服务：静态页面 + 知识库文件接口（仅监听 127.0.0.1） */
const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = 8899;
const STATIC_ROOT = __dirname;
const KB_ROOT = 'C:\\Users\\qifengshunshi\\.reme\\knowledge_bases\\zhb_kb';
const MAX_TEXT = 2 * 1024 * 1024;

const HIDDEN_DIRS = new Set(['.git', '.idea', '.vscode', '.locks', '__pycache__', 'node_modules', '.obsidian']);
const HIDDEN_FILES = new Set(['.DS_Store', 'thumbs.db']);
const TEXT_EXT = new Set(['.md', '.markdown', '.txt', '.json', '.jsonl', '.py', '.js', '.ts', '.yaml', '.yml',
  '.sql', '.sh', '.bat', '.ini', '.cfg', '.csv', '.html', '.css', '.xml', '.toml', '.gitignore', '.log']);

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon',
};

const isText = f => TEXT_EXT.has(path.extname(f).toLowerCase()) || !path.extname(f);
const relOf = abs => path.relative(KB_ROOT, abs).split(path.sep).join('/');

/* 把请求里的相对路径解析成 KB_ROOT 内的绝对路径，越界或命中隐藏目录返回 null */
function kbPath(rel) {
  if (typeof rel !== 'string') return null;
  const clean = rel.replace(/^[\\/]+/, '');
  if (clean.includes('\0')) return null;
  const abs = path.resolve(KB_ROOT, clean.split('/').join(path.sep));
  if (abs !== KB_ROOT && !abs.startsWith(KB_ROOT + path.sep)) return null;
  const segs = path.relative(KB_ROOT, abs).split(path.sep).filter(Boolean);
  if (segs.some(s => HIDDEN_DIRS.has(s) || HIDDEN_FILES.has(s.toLowerCase()))) return null;
  return abs;
}

function hiddenEntry(name) {
  return HIDDEN_DIRS.has(name) || HIDDEN_FILES.has(name.toLowerCase())
    || name.startsWith('.') || /\.(pyc|pyo|pack|idx|rev|sample)$/.test(name);
}

function statOf(abs) {
  const st = fs.statSync(abs);
  return { size: st.size, mtime: Math.floor(st.mtimeMs) };
}

/* 只解析 frontmatter 顶层 key: value，够原型用 */
function parseFm(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
  if (!m) return null;
  const out = {};
  for (const line of m[1].split(/\r?\n/)) {
    const kv = /^([A-Za-z0-9_\-.]+):\s*(.*)$/.exec(line);
    if (!kv) continue;
    let v = kv[2].trim();
    if (v.length > 1 && v[0] === '"' && v[v.length - 1] === '"') v = v.slice(1, -1);
    out[kv[1]] = v;
  }
  return out;
}

function json(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', c => {
      size += c.length;
      if (size > MAX_TEXT + 64 * 1024) { reject(new Error('too_large')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function walk(abs, out, depth) {
  const entries = fs.readdirSync(abs, { withFileTypes: true });
  for (const e of entries) {
    if (hiddenEntry(e.name)) continue;
    const child = path.join(abs, e.name);
    out.push(child);
    if (e.isDirectory() && depth < 12) walk(child, out, depth + 1);
  }
}

const handlers = {
  'GET /kb/tree'(q, res) {
    const abs = kbPath(q.get('path') || '');
    if (!abs) return json(res, 403, { error: '路径超出知识库范围' });
    let st;
    try { st = fs.statSync(abs); } catch (_) { return json(res, 404, { error: '目录不存在' }); }
    if (!st.isDirectory()) return json(res, 400, { error: '不是目录' });
    const items = fs.readdirSync(abs, { withFileTypes: true })
      .filter(e => !hiddenEntry(e.name))
      .map(e => {
        const child = path.join(abs, e.name);
        const info = { name: e.name, rel: relOf(child), dir: e.isDirectory() };
        try { Object.assign(info, statOf(child)); } catch (_) { info.size = 0; info.mtime = 0; }
        return info;
      })
      .sort((a, b) => (b.dir - a.dir) || a.name.localeCompare(b.name, 'zh'));
    json(res, 200, { root: KB_ROOT, rel: relOf(abs), items });
  },

  'GET /kb/file'(q, res) {
    const abs = kbPath(q.get('path') || '');
    if (!abs) return json(res, 403, { error: '路径超出知识库范围' });
    let st;
    try { st = fs.statSync(abs); } catch (_) { return json(res, 404, { error: '文件不存在' }); }
    if (st.isDirectory()) return json(res, 400, { error: '是目录，不是文件' });
    if (!isText(abs)) return json(res, 415, { error: '暂不支持预览该文件类型', editable: false });
    if (st.size > MAX_TEXT) return json(res, 413, { error: `文件超过 ${Math.floor(MAX_TEXT / 1024 / 1024)}MB，只读不加载`, editable: false });
    json(res, 200, {
      rel: relOf(abs), name: path.basename(abs), ext: path.extname(abs).toLowerCase(),
      content: fs.readFileSync(abs, 'utf8'), size: st.size, mtime: Math.floor(st.mtimeMs), editable: true,
    });
  },

  'GET /kb/search'(q, res) {
    const kw = (q.get('q') || '').trim().toLowerCase();
    if (kw.length < 1) return json(res, 200, { root: KB_ROOT, hits: [] });
    const limit = Math.min(parseInt(q.get('limit') || '120', 10) || 120, 400);
    const all = [];
    walk(KB_ROOT, all, 0);
    const hits = [];
    for (const abs of all) {
      const name = path.basename(abs);
      if (!name.toLowerCase().includes(kw)) continue;
      let st; try { st = fs.statSync(abs); } catch (_) { continue; }
      hits.push({ name, rel: relOf(abs), dir: st.isDirectory(), size: st.size, mtime: Math.floor(st.mtimeMs) });
      if (hits.length >= limit) break;
    }
    hits.sort((a, b) => a.rel.localeCompare(b.rel, 'zh'));
    json(res, 200, { root: KB_ROOT, total: hits.length, truncated: hits.length >= limit, hits });
  },

  'GET /kb/scan'(q, res) {
    const abs = kbPath(q.get('path') || '');
    if (!abs) return json(res, 403, { error: '路径超出知识库范围' });
    if (!fs.existsSync(abs)) return json(res, 404, { error: '目录不存在' });
    const limit = Math.min(parseInt(q.get('limit') || '800', 10) || 800, 3000);
    const onlyMd = q.get('md') !== '0';
    const files = [];
    walk(abs, files, 0);
    const docs = [];
    for (const f of files) {
      let st; try { st = fs.statSync(f); } catch (_) { continue; }
      if (!st.isFile()) continue;
      if (onlyMd && path.extname(f).toLowerCase() !== '.md') continue;
      if (st.size > 512 * 1024) continue;
      const head = fs.readFileSync(f, 'utf8').slice(0, 4000);
      docs.push({ rel: relOf(f), name: path.basename(f), size: st.size, mtime: Math.floor(st.mtimeMs), fm: parseFm(head) });
      if (docs.length >= limit) break;
    }
    json(res, 200, { root: KB_ROOT, scanned: docs.length, truncated: docs.length >= limit, docs });
  },

  async 'PUT /kb/file'(q, res, req) {
    const abs = kbPath(q.get('path') || '');
    if (!abs) return json(res, 403, { error: '路径超出知识库范围' });
    if (!isText(abs)) return json(res, 415, { error: '只允许写入文本文件' });
    let st;
    try { st = fs.statSync(abs); } catch (_) { return json(res, 404, { error: '文件不存在，请用新建接口' }); }
    if (st.isDirectory()) return json(res, 400, { error: '是目录' });
    const baseMtime = parseInt(q.get('mtime') || '', 10);
    if (baseMtime && Math.floor(st.mtimeMs) !== baseMtime) {
      return json(res, 409, { error: '文件在页面打开后被外部修改，未覆盖', mtime: Math.floor(st.mtimeMs) });
    }
    const raw = await readBody(req);
    let content = raw;
    try { content = JSON.parse(raw).content; } catch (_) { /* 允许裸文本 */ }
    if (typeof content !== 'string') return json(res, 400, { error: '缺少 content' });
    if (Buffer.byteLength(content, 'utf8') > MAX_TEXT) return json(res, 413, { error: '内容过大' });
    fs.writeFileSync(abs, content, 'utf8');
    json(res, 200, { rel: relOf(abs), size: Buffer.byteLength(content, 'utf8'), mtime: Math.floor(fs.statSync(abs).mtimeMs) });
  },

  async 'POST /kb/file'(q, res, req) {
    const abs = kbPath(q.get('path') || '');
    if (!abs) return json(res, 403, { error: '路径超出知识库范围' });
    if (!isText(abs)) return json(res, 415, { error: '只允许写入文本文件' });
    if (fs.existsSync(abs)) return json(res, 409, { error: '同名文件已存在，请换个名字' });
    const parent = path.dirname(abs);
    if (!fs.existsSync(parent)) return json(res, 400, { error: '父目录不存在' });
    const raw = await readBody(req);
    let content = raw;
    try { content = JSON.parse(raw).content; } catch (_) { /* 允许裸文本 */ }
    if (typeof content !== 'string') return json(res, 400, { error: '缺少 content' });
    fs.writeFileSync(abs, content, 'utf8');
    json(res, 201, { rel: relOf(abs), size: Buffer.byteLength(content, 'utf8'), mtime: Math.floor(fs.statSync(abs).mtimeMs) });
  },
};

function serveStatic(req, res, pathname) {
  const abs = path.resolve(STATIC_ROOT, '.' + decodeURIComponent(pathname));
  if (abs !== STATIC_ROOT && !abs.startsWith(STATIC_ROOT + path.sep)) { res.writeHead(403); return res.end('forbidden'); }
  const file = fs.existsSync(abs) && fs.statSync(abs).isDirectory() ? path.join(abs, 'index.html') : abs;
  fs.readFile(file, (err, buf) => {
    if (err) { res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }); return res.end('404 not found'); }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    res.end(buf);
  });
}

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const key = `${req.method} ${url.pathname}`;
  const handler = handlers[key];
  if (!handler) {
    if (req.method === 'GET' && !url.pathname.startsWith('/kb/')) return serveStatic(req, res, url.pathname);
    return json(res, 404, { error: `no route: ${key}` });
  }
  try {
    Promise.resolve(handler(url.searchParams, res, req))
      .catch(e => json(res, 500, { error: String(e && e.message || e) }));
  } catch (e) {
    json(res, 500, { error: String(e && e.message || e) });
  }
}).listen(PORT, '127.0.0.1', () => {
  console.log(`AiTester prototype → http://localhost:${PORT}\nKB root      → ${KB_ROOT}`);
});
