export interface HealthResponse {
  ok: boolean;
  service: string;
}

export async function getHealth(): Promise<HealthResponse> {
  const resp = await fetch("/api/health");
  if (!resp.ok) {
    throw new Error(`health 请求失败: HTTP ${resp.status}`);
  }
  return (await resp.json()) as HealthResponse;
}
