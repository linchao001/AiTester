export interface HealthResponse {
  ok: boolean;
  service: string;
  llm_provider: string;
}

export interface ModelInfo {
  id: string;
  enabled: boolean;
  max_output: number;
  context: number;
  name?: string;
  caps?: string[];
  note?: string;
  recommended?: boolean;
}

export interface ProviderUrlOption {
  label: string;
  value: string;
}

export interface ProviderInfo {
  id: string;
  name: string;
  base_url: string;
  has_key: boolean;
  key_masked: string;
  models: ModelInfo[];
  proto?: string;
  key_prefix?: string;
  freeze_url?: boolean;
  base_options?: ProviderUrlOption[];
}

export interface ModelsResponse {
  default_uid: string;
  providers: ProviderInfo[];
}

export interface ProviderTestResponse {
  ok: boolean;
  latency_ms?: number | null;
  reason?: string | null;
}

export interface ToolInfo {
  id: string;
  group: string;
  icon: string;
  label: string;
  os: string;
  desc: string;
  enabled: boolean;
  available: boolean;
  unavailable_reason: string | null;
  carried_by: string[];
}

export interface AgentInfo {
  id: string;
  icon: string;
  name: string;
  desc: string;
  prompt: string;
  default_uid: string;
  effective_uid: string;
  tool_ids: string[];
}

export interface CapabilityResponse {
  tools: ToolInfo[];
  agents: AgentInfo[];
}

/** 带 HTTP 状态码与解析后响应体的 API 错误（Error 子类，旧 catch 路径兼容）。 */
export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(message: string, status: number, data: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    let detail = `请求失败: HTTP ${resp.status}`;
    let body: unknown = null;
    try {
      const parsed = (await resp.json()) as { detail?: unknown };
      body = parsed;
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // 响应体不是 JSON 时保留默认错误文案，body 为 null
    }
    throw new ApiError(detail, resp.status, body);
  }
  // 204 无响应体（DELETE /api/projects/{id}）：先短路，否则 resp.json() 抛「Unexpected end of JSON input」
  if (resp.status === 204) return null as T;
  return (await resp.json()) as T;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/api/health");
}

export function getModels(): Promise<ModelsResponse> {
  return apiFetch<ModelsResponse>("/api/models");
}

export function putApiKey(providerId: string, apiKey: string): Promise<ModelsResponse> {
  return apiFetch<ModelsResponse>(`/api/models/providers/${providerId}/key`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export function testProvider(providerId: string, apiKeyDraft: string): Promise<ProviderTestResponse> {
  return apiFetch<ProviderTestResponse>(`/api/models/providers/${providerId}/test`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ api_key: apiKeyDraft }),
  });
}

export function putModelEnabled(
  providerId: string,
  modelId: string,
  enabled: boolean,
): Promise<ModelsResponse> {
  return apiFetch<ModelsResponse>(
    `/api/models/providers/${providerId}/models/${modelId}/enabled`,
    {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify({ enabled }),
    },
  );
}

export function putDefault(uid: string): Promise<ModelsResponse> {
  return apiFetch<ModelsResponse>("/api/models/default", {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ uid }),
  });
}

export function getCapabilities(): Promise<CapabilityResponse> {
  return apiFetch<CapabilityResponse>("/api/capabilities");
}

export function putAgentDefaultModel(
  agentId: string,
  uid: string,
): Promise<CapabilityResponse> {
  return apiFetch<CapabilityResponse>(`/api/capabilities/agents/${agentId}/default-model`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ uid }),
  });
}

export function putAgentTools(
  agentId: string,
  toolIds: string[],
): Promise<CapabilityResponse> {
  return apiFetch<CapabilityResponse>(`/api/capabilities/agents/${agentId}/tools`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ tool_ids: toolIds }),
  });
}

export function putToolEnabled(
  toolId: string,
  enabled: boolean,
): Promise<CapabilityResponse> {
  return apiFetch<CapabilityResponse>(`/api/capabilities/tools/${toolId}/enabled`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ enabled }),
  });
}

/** /api/kb/* 统一响应壳（后端 KbResponse：answer 多为文本，metadata 形状随 job 而定）。 */
export interface KbResponse {
  success: boolean;
  answer: unknown;
  metadata: Record<string, unknown>;
}

export function getKbStatus(): Promise<KbResponse> {
  return apiFetch<KbResponse>("/api/kb/status");
}

export function getKbBases(): Promise<KbResponse> {
  return apiFetch<KbResponse>("/api/kb/bases");
}

export function kbSearch(query: string, limit: number, bucket: string): Promise<KbResponse> {
  return apiFetch<KbResponse>("/api/kb/search", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ query, limit, bucket }),
  });
}

export function kbSave(title: string, content: string, bucket: string): Promise<KbResponse> {
  return apiFetch<KbResponse>("/api/kb/save", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ title, content, bucket }),
  });
}

export interface KbBrowseItem { name: string; rel: string; dir: boolean; size: number; mtime: number }
export interface KbTreeResponse { root: string; rel: string; items: KbBrowseItem[] }
export interface KbFileResponse { rel: string; name: string; ext: string; content: string; size: number; mtime: number; editable: boolean }
export interface KbSearchHit { name: string; rel: string; dir: boolean; size: number; mtime: number }
export interface KbSearchResponse { root: string; total: number; truncated: boolean; hits: KbSearchHit[] }
export interface KbScanDoc { rel: string; name: string; size: number; mtime: number; fm: Record<string, string> | null }
export interface KbScanResponse { root: string; scanned: number; truncated: boolean; docs: KbScanDoc[] }
export interface KbWriteResponse { rel: string; size: number; mtime: number }

const browseApi = (sub: string, params: Record<string, string | number>) =>
  `/api/kb/browse/${sub}?${new URLSearchParams(
    Object.entries(params).map(([k, v]) => [k, String(v)])).toString()}`;

export function kbTree(path: string): Promise<KbTreeResponse> {
  return apiFetch<KbTreeResponse>(browseApi("tree", { path }));
}
export function kbReadFile(path: string): Promise<KbFileResponse> {
  return apiFetch<KbFileResponse>(browseApi("file", { path }));
}
export function kbSearchFiles(q: string, limit = 120): Promise<KbSearchResponse> {
  return apiFetch<KbSearchResponse>(browseApi("search", { q, limit }));
}
export function kbScanFiles(path: string, limit = 800, md = true): Promise<KbScanResponse> {
  return apiFetch<KbScanResponse>(browseApi("scan", { path, limit, md: md ? "1" : "0" }));
}
export function kbPutFile(path: string, content: string, mtime: number): Promise<KbWriteResponse> {
  return apiFetch<KbWriteResponse>(browseApi("file", { path, mtime }), {
    method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ content }) });
}
export function kbPostFile(path: string, content: string): Promise<KbWriteResponse> {
  return apiFetch<KbWriteResponse>(browseApi("file", { path }), {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ content }) });
}

export interface WsItem { name: string; rel: string; dir: boolean; size: number; mtime: number }
export interface WsTreeResponse { rel: string; items: WsItem[] }
export interface WsFileResponse { rel: string; name: string; ext: string; content: string; size: number; mtime: number; editable: boolean }
export interface WsWriteResponse { rel: string; size: number; mtime: number }

/** 项目工作区接口：形状与 KB browse 同款不同源——不复用 Kb* 类型，将来各自漂移不互累。 */
const wsApi = (pid: string, sub: string, params: Record<string, string | number>) =>
  `/api/projects/${pid}/browse/${sub}?${new URLSearchParams(
    Object.entries(params).map(([k, v]) => [k, String(v)])).toString()}`;

export function wsTree(pid: string, path: string): Promise<WsTreeResponse> {
  return apiFetch<WsTreeResponse>(wsApi(pid, "tree", { path }));
}
export function wsReadFile(pid: string, path: string): Promise<WsFileResponse> {
  return apiFetch<WsFileResponse>(wsApi(pid, "file", { path }));
}
export function wsPutFile(pid: string, path: string, content: string, mtime: number): Promise<WsWriteResponse> {
  return apiFetch<WsWriteResponse>(wsApi(pid, "file", { path, mtime }), {
    method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ content }) });
}

export interface KbDraft {
  op: "create" | "modify";
  path: string;
  abs_display: string;
  summary: string;
  content: string;
  base: string | null;
  mtime: number;
}

export interface ChatStep {
  tool: string;
  ok: boolean;
  round: number;
  detail: string;
}

export interface SendResponse {
  reply: string;
  trace: string[];
  model: string;
  drafts: KbDraft[];
  session_id: string;
  title: string;
  steps: ChatStep[];
}

export function chatSend(
  sessionId: string, message: string, agentId: string, projectId: string,
): Promise<SendResponse> {
  return apiFetch<SendResponse>("/api/chat/send", {
    method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ session_id: sessionId, message, agent_id: agentId, project_id: projectId }) });
}

/** 一条 SSE 帧的落地形态：`event:` 名进 type，`data:` 的单行 JSON 摊平进来（与 router `_frame` 一一对应）。 */
type Frame<T extends string, P> = { type: T } & P;
export type StreamEvent =
  | Frame<"start", { run_id: string; session_id: string }>
  | Frame<"delta", { round: number; text: string }>
  | Frame<"call", { tool: string; round: number; detail: string }>
  | Frame<"step", { tool: string; ok: boolean; round: number; detail: string }>
  | Frame<"draft", { draft: KbDraft }>
  | Frame<"done", { reply: string; steps: ChatStep[]; session_id: string; title: string; stopped: boolean }>
  | Frame<"error", { detail: string }>;

export interface StreamBody {
  session_id: string;
  message: string;
  agent_id: string;
  project_id: string;
}

const STREAM_EVENTS = new Set(["start", "delta", "call", "step", "draft", "done", "error"]);

/** POST + 流解析：EventSource 不能带 JSON body，WebSocket 又是多余的语义，故 fetch + getReader 手解。
 *  守门未过时后端回普通 JSON（400/404/502），照 apiFetch 口径抛 ApiError；守门过后才有事件。 */
export async function chatSendStream(
  body: StreamBody,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch("/api/chat/send/stream", {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body), signal });
  if (!resp.ok) {
    let detail = `请求失败: HTTP ${resp.status}`;
    let data: unknown = null;
    try {
      const parsed = (await resp.json()) as { detail?: unknown };
      data = parsed;
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // 响应体不是 JSON 时保留默认错误文案
    }
    throw new ApiError(detail, resp.status, data);
  }
  if (!resp.body) throw new ApiError("浏览器未提供响应流，无法接收流式回复", resp.status, null);

  const feed = (frame: string) => {
    let name = "";
    let data = "";
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data = line.slice(5).trim();
    }
    if (!STREAM_EVENTS.has(name)) return;   // 未知事件静默忽略（spec 事件表：前端只认这 7 类）
    let payload: object;
    try {
      payload = JSON.parse(data) as object;
    } catch {
      return;                               // 坏帧丢一条，不砸整条流（与后端草案逐条容错同口径）
    }
    onEvent({ ...payload, type: name } as StreamEvent);
  };

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep = buf.indexOf("\n\n");
    while (sep >= 0) {
      feed(buf.slice(0, sep));
      buf = buf.slice(sep + 2);
      sep = buf.indexOf("\n\n");
    }
  }
  buf += decoder.decode();          // 尾包 flush：最后一帧可能不带终止空行
  if (buf.trim()) feed(buf);
}

/** 服务端真停：置取消位。404「这条回答已经结束」是正常竞态，调用方按竞态处理。 */
export function chatStop(runId: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>("/api/chat/stop", {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ run_id: runId }) });
}

export interface ChatSession {
  id: string;
  agent_id: string;
  project_id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  ts: number;
  steps: ChatStep[] | null;
  /** 被停止的 assistant 行：只进 UI 挂「（已停止）」，不进 content。 */
  stopped?: boolean;
}

const sessionsApi = (sub = "") => `/api/chat/sessions${sub}`;

export function getSessions(agentId: string, projectId: string): Promise<{ sessions: ChatSession[] }> {
  return apiFetch<{ sessions: ChatSession[] }>(
    `${sessionsApi()}?${new URLSearchParams({ agent_id: agentId, project_id: projectId }).toString()}`);
}

export function getSessionMessages(sessionId: string): Promise<{ session_id: string; messages: ChatMessage[] }> {
  return apiFetch<{ session_id: string; messages: ChatMessage[] }>(sessionsApi(`/${sessionId}/messages`));
}

/** 204 由 apiFetch 短路成 null（与 deleteProject 同款），失败时抛 ApiError。 */
export function deleteChatSession(sessionId: string): Promise<null> {
  return apiFetch<null>(sessionsApi(`/${sessionId}`), { method: "DELETE" });
}

export interface Project {
  id: string;
  name: string;
  desc: string;
  dir: string;
  agents: string[];
  kb: string;
  session_count: number;
  dir_exists: boolean;
}

export interface ProjectsResponse {
  projects: Project[];
}

export interface ProjectFormValues {
  name: string;
  desc: string;
  dir: string;
  agents: string[];
}

export function getProjects(): Promise<ProjectsResponse> {
  return apiFetch<ProjectsResponse>("/api/projects");
}

export function postProject(values: ProjectFormValues): Promise<Project> {
  return apiFetch<Project>("/api/projects", {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify(values) });
}

export function putProject(id: string, values: ProjectFormValues): Promise<Project> {
  return apiFetch<Project>(`/api/projects/${id}`, {
    method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(values) });
}

export function deleteProject(id: string): Promise<null> {
  return apiFetch<null>(`/api/projects/${id}`, { method: "DELETE" });
}

/* 本机目录选择器：绝对路径只有「拥有桌面的进程」能给，浏览器自己的 showDirectoryPicker
   只暴露末级文件夹名（File System Access API 的隐私限制）。后端与浏览器同机，所以由它弹
   系统「选择文件夹」窗——等价于 Electron / Tauri 里 main 进程弹 dialog 再交给渲染进程。
   path 为空串表示用户取消了弹窗。 */
export interface PickDirResponse { path: string }

export function postPickDir(path: string): Promise<PickDirResponse> {
  return apiFetch<PickDirResponse>("/api/fs/pick-dir", {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ path }) });
}
