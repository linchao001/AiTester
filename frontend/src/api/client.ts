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
}

export interface ProviderInfo {
  id: string;
  name: string;
  base_url: string;
  has_key: boolean;
  key_masked: string;
  models: ModelInfo[];
}

export interface ModelsResponse {
  default_uid: string;
  providers: ProviderInfo[];
}

export interface ToolInfo {
  id: string;
  group: string;
  icon: string;
  label: string;
  os: string;
  desc: string;
  enabled: boolean;
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

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    let detail = `请求失败: HTTP ${resp.status}`;
    try {
      const body = (await resp.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // 响应体不是 JSON 时保留默认错误文案
    }
    throw new Error(detail);
  }
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
