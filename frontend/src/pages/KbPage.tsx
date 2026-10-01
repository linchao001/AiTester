import { useCallback, useEffect, useState } from "react";
import {
  getKbBases,
  getKbStatus,
  kbSave,
  kbSearch,
  type KbResponse,
} from "../api/client";

/** 检索范围桶（与后端 knowledge_search 的 bucket 参数取值一致）。 */
const SEARCH_BUCKETS = ["all", "business", "test", "business/wiki", "test/test_design", "test/defects"];
/** 写入发布桶（save_to_knowledge 仅接受 2 段 bucket，默认 business/wiki）。 */
const SAVE_BUCKETS = ["business/wiki", "test/test_design", "test/defects"];

interface SearchHit {
  title: string;
  source: string;
  score: number | null;
}

function toText(v: unknown): string {
  if (typeof v === "string") return v;
  if (v === null || v === undefined || typeof v === "object") return "";
  return String(v);
}

/** 命中节点标题：实测 reme 0.4.1.8 的 results 项无顶层 title，从 metadata/text 标题行/文件名逐级兜底。 */
function hitTitle(r: Record<string, unknown>, path: string): string {
  const direct = toText(r.title);
  if (direct) return direct;
  const md = typeof r.metadata === "object" && r.metadata !== null ? (r.metadata as Record<string, unknown>) : {};
  const fromMeta = toText(md.title);
  if (fromMeta) return fromMeta;
  const heading = /^#[ \t]+(.+)$/m.exec(toText(r.text));
  if (heading) return heading[1].trim();
  const base = path.split(/[\\/]/).pop() ?? "";
  return base.replace(/\.md$/i, "") || "（无标题节点）";
}

/** 解析 knowledge_search 的 metadata.results：path/start_line/end_line/scores.score（见 reme search_step 实装）。 */
function readHits(metadata: Record<string, unknown> | undefined): SearchHit[] {
  const raw = metadata?.results;
  if (!Array.isArray(raw)) return [];
  const hits: SearchHit[] = [];
  for (const item of raw) {
    if (typeof item !== "object" || item === null) continue;
    const r = item as Record<string, unknown>;
    const path = toText(r.path);
    const scores = typeof r.scores === "object" && r.scores !== null ? (r.scores as Record<string, unknown>) : {};
    const lines =
      typeof r.start_line === "number" ? `:${r.start_line}${typeof r.end_line === "number" ? `-${r.end_line}` : ""}` : "";
    hits.push({
      title: hitTitle(r, path) || "（无标题节点）",
      source: path ? path + lines : "",
      score: typeof scores.score === "number" ? scores.score : null,
    });
  }
  return hits;
}

/** 从 bases 的 metadata 摘出 KB id 与实体路径等键值对；status 是运行时状态（内存占用），answer 原样展示。
    解析不出任何已知字段时返回空数组（上层降级原始 JSON）。 */
function readStatusKvs(bases: KbResponse | null): { k: string; v: string }[] {
  const kvs: { k: string; v: string }[] = [];
  const md = bases?.metadata ?? {};
  const kbId = toText(md.active_knowledge_base_id);
  if (kbId) kvs.push({ k: "当前知识库", v: kbId });
  const dir = toText(md.knowledge_bases_dir);
  if (dir) kvs.push({ k: "基地目录", v: dir });
  if (dir && kbId) {
    const sep = dir.includes("\\") ? "\\" : "/";
    kvs.push({ k: "实体路径", v: `${dir.replace(/[\\/]+$/, "")}${sep}${kbId}` });
  }
  const list = md.knowledge_bases;
  if (Array.isArray(list)) {
    kvs.push({ k: "可用知识库", v: `${list.length} 个` });
    for (const item of list) {
      if (typeof item !== "object" || item === null) continue;
      const b = item as Record<string, unknown>;
      const name = toText(b.name);
      kvs.push({ k: `库 ${toText(b.id)}`, v: `${name || "（未命名）"}${toText(b.domain) ? `（领域：${toText(b.domain)}）` : ""}` });
    }
  }
  return kvs;
}

function KbSectionTitle({ label, sub }: { label: string; sub?: string }) {
  return (
    <div className="sec-title">
      {label}
      {sub && <span className="m-sub">{sub}</span>}
    </div>
  );
}

export default function KbPage() {
  // —— 状态卡 ——
  const [status, setStatus] = useState<KbResponse | null>(null);
  const [bases, setBases] = useState<KbResponse | null>(null);
  const [kbError, setKbError] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);

  // —— 检索区 ——
  const [query, setQuery] = useState("");
  const [limitText, setLimitText] = useState("5");
  const [searchBucket, setSearchBucket] = useState("all");
  const [searching, setSearching] = useState(false);
  const [searchResp, setSearchResp] = useState<KbResponse | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);

  // —— 写入区 ——
  const [saveTitle, setSaveTitle] = useState("");
  const [saveContent, setSaveContent] = useState("");
  const [saveBucket, setSaveBucket] = useState("business/wiki");
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState<{ ok: boolean; msg: string } | null>(null);

  const refreshStatus = useCallback(() => {
    setStatusLoading(true);
    void Promise.allSettled([getKbStatus(), getKbBases()]).then(([s, b]) => {
      setStatusLoading(false);
      setStatus(s.status === "fulfilled" ? s.value : null);
      setBases(b.status === "fulfilled" ? b.value : null);
      // kb_manager 不可用时后端返回 503；提示条展示 detail，不让整页白屏
      setKbError(s.status === "rejected" ? (s.reason as Error).message : null);
    });
  }, []);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  function doSearch(): void {
    if (searching) return;
    const q = query.trim();
    if (!q) {
      setSearchError("请先输入查询内容");
      setSearchResp(null);
      return;
    }
    const parsed = Number(limitText);
    const lim = Number.isFinite(parsed) && parsed >= 1 ? Math.min(Math.trunc(parsed), 20) : 5;
    setLimitText(String(lim));
    setSearching(true);
    setSearchError(null);
    void kbSearch(q, lim, searchBucket)
      .then((resp) => setSearchResp(resp))
      .catch((e: Error) => {
        setSearchResp(null);
        setSearchError(e.message);
      })
      .finally(() => setSearching(false));
  }

  function doSave(): void {
    const title = saveTitle.trim();
    const content = saveContent.trim();
    if (!title || !content) {
      setSaveResult({ ok: false, msg: "写入失败：请先填写标题与正文" });
      return;
    }
    if (!window.confirm("写入后对所有项目与其他智能体可见，确认？")) return;
    setSaving(true);
    setSaveResult(null);
    void kbSave(title, content, saveBucket)
      .then((resp) => {
        if (resp.success) {
          const answer = toText(resp.answer);
          setSaveResult({
            ok: true,
            msg: `写入成功${answer ? `：${answer}` : ""}。可在上方「知识检索」区验证命中。`,
          });
        } else {
          setSaveResult({ ok: false, msg: `写入失败：${toText(resp.answer) || "后端未返回原因"}` });
        }
      })
      .catch((e: Error) => setSaveResult({ ok: false, msg: `写入失败：${e.message}` }))
      .finally(() => setSaving(false));
  }

  const statusKvs = readStatusKvs(bases);
  const hits = searchResp ? readHits(searchResp.metadata) : [];
  const searchAnswer = searchResp ? toText(searchResp.answer) : "";

  return (
    <div className="page">
      <div className="page-head">
        <h1>知识库</h1>
        <span className="num">全局共享 · ReMe</span>
        <div className="spacer"></div>
        <button className="mini-btn" onClick={refreshStatus} disabled={statusLoading}>
          {statusLoading ? "刷新中…" : "刷新状态"}
        </button>
      </div>

      {kbError !== null && (
        <div className="stale-warn" style={{ maxWidth: 1080, margin: "0 auto 14px" }}>
          知识库未启用或不可用：{kbError}
        </div>
      )}

      {/* —— 状态卡 —— */}
      <KbSectionTitle label="知识库状态" sub="来自 /api/kb/status 与 /api/kb/bases" />
      <div className="case-card">
        <div className="c-head">
          <span>当前状态</span>
          <span className="badge">
            {statusLoading
              ? "读取中"
              : status === null
                ? "不可用"
                : status.success
                  ? "正常"
                  : "异常"}
          </span>
        </div>
        <div style={{ padding: "10px 14px 12px" }}>
          {status === null && bases === null && kbError === null && (
            <div className="empty-tip">{statusLoading ? "正在读取知识库状态…" : "暂无状态数据"}</div>
          )}
          {statusKvs.length > 0 && (
            <div className="kb-meta" style={{ border: "none", background: "transparent", padding: "0 0 4px" }}>
              {statusKvs.map((kv) => (
                <span className="kv" key={`${kv.k}-${kv.v}`} title={kv.v}>
                  <b>{kv.k}：</b>
                  {kv.v}
                </span>
              ))}
            </div>
          )}
          {statusKvs.length === 0 && (status !== null || bases !== null) && (
            <pre className="kb-raw">
              {JSON.stringify({ status: status?.metadata, bases: bases?.metadata }, null, 2)}
            </pre>
          )}
          {bases !== null && toText(bases.answer) && (
            <div className="ag-desc">{toText(bases.answer)}</div>
          )}
          {status !== null && toText(status.answer) && (
            <div className="ag-desc" style={{ marginTop: 10, whiteSpace: "pre-wrap" }}>
              运行时状态：{toText(status.answer)}
            </div>
          )}
        </div>
      </div>

      {/* —— 检索区 —— */}
      <KbSectionTitle label="知识检索" sub="在指定范围桶内做 BM25/向量混合检索" />
      <div className="case-card">
        <div className="c-head">
          <span>检索</span>
          <span className="badge">{searching ? "检索中" : searchResp ? `命中 ${hits.length} 条` : "未检索"}</span>
        </div>
        <div style={{ padding: "12px 14px 14px" }}>
          <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
            <div className="field" style={{ flex: "2 1 260px", marginBottom: 8 }}>
              <label htmlFor="kb-query">查询内容</label>
              <input
                id="kb-query"
                type="text"
                value={query}
                placeholder="输入关键词或问题"
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && doSearch()}
              />
            </div>
            <div className="field" style={{ width: 110, marginBottom: 8 }}>
              <label htmlFor="kb-limit">返回条数</label>
              <input
                id="kb-limit"
                type="number"
                min={1}
                max={20}
                value={limitText}
                onChange={(e) => setLimitText(e.target.value)}
              />
            </div>
            <div className="field" style={{ width: 190, marginBottom: 8 }}>
              <label htmlFor="kb-search-bucket">检索范围</label>
              <select
                id="kb-search-bucket"
                value={searchBucket}
                onChange={(e) => setSearchBucket(e.target.value)}
              >
                {SEARCH_BUCKETS.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
            </div>
            <button className="btn-primary" onClick={doSearch} disabled={searching} style={{ marginBottom: 8 }}>
              {searching ? "检索中…" : "检索"}
            </button>
          </div>
          {searchError !== null && <div className="stale-warn">{searchError}</div>}
          {searchResp !== null && !searchResp.success && (
            <div className="stale-warn">检索失败：{searchAnswer || "后端未返回原因"}</div>
          )}
          {searchResp !== null && searchResp.success && searchAnswer && (
            <div
              className="ag-desc"
              style={{ whiteSpace: "pre-wrap", maxHeight: 240, overflowY: "auto", marginTop: 12 }}
            >
              {searchAnswer}
            </div>
          )}
          {searchResp !== null && searchResp.success && hits.length === 0 && (
            <div className="empty-tip">无命中，可尝试更换范围桶</div>
          )}
          {searchResp !== null && searchResp.success && hits.length > 0 && (
            <table style={{ marginTop: 12 }}>
              <thead>
                <tr>
                  <th>标题</th>
                  <th>出处路径</th>
                  <th>得分</th>
                </tr>
              </thead>
              <tbody>
                {hits.map((h, i) => (
                  <tr key={h.source || `${h.title}-${i}`}>
                    <td>{h.title}</td>
                    <td>
                      <span className="p-dir" title={h.source}>
                        {h.source}
                      </span>
                    </td>
                    <td>{h.score === null ? "-" : h.score.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* —— 写入区 —— */}
      <KbSectionTitle label="知识写入" sub="写入即全局共享，索引自动收敛后（约数十秒）可在检索区验证" />
      <div className="case-card">
        <div className="c-head">
          <span>写入知识库</span>
          <span className="badge">影响所有项目</span>
        </div>
        <div style={{ padding: "12px 14px 14px" }}>
          <div className="field">
            <label htmlFor="kb-save-title">
              标题 <span className="req">*</span>
            </label>
            <input
              id="kb-save-title"
              type="text"
              value={saveTitle}
              placeholder="知识节点标题"
              onChange={(e) => setSaveTitle(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="kb-save-content">
              正文（Markdown） <span className="req">*</span>
            </label>
            <textarea
              id="kb-save-content"
              value={saveContent}
              placeholder="知识节点正文，须为已确认成立的事实或经用户认可的内容"
              onChange={(e) => setSaveContent(e.target.value)}
            />
          </div>
          <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
            <div className="field" style={{ width: 190, marginBottom: 8 }}>
              <label htmlFor="kb-save-bucket">发布桶</label>
              <select
                id="kb-save-bucket"
                value={saveBucket}
                onChange={(e) => setSaveBucket(e.target.value)}
              >
                {SAVE_BUCKETS.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
            </div>
            <button className="btn-primary" onClick={doSave} disabled={saving} style={{ marginBottom: 8 }}>
              {saving ? "写入中…" : "写入知识库"}
            </button>
          </div>
          {saveResult !== null && (
            <div className={saveResult.ok ? "set-tip" : "stale-warn"} style={saveResult.ok ? { color: "var(--ok)" } : undefined}>
              {saveResult.msg}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
