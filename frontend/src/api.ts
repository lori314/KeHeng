import type { EvidenceAnalysisCreateResponse, EvidenceAnalysisTaskResponse, EvidenceFirstReport } from "./types";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    if (response.status === 404 && url.includes("/report/v2/company/")) throw new Error("未找到该企业的完整 V2 报告。");
    let detail: unknown;
    try { detail = (await response.json() as { detail?: unknown }).detail; } catch { /* use status below */ }
    if (typeof detail === "object" && detail !== null && "code" in detail) {
      const error = detail as { code?: string; message?: string };
      if (error.code === "provider_not_configured") throw new Error("后端尚未配置完整的模型或检索服务。");
      throw new Error(error.message ?? `请求失败：HTTP ${response.status}`);
    }
    if (typeof detail === "string") throw new Error(detail);
    throw new Error(`请求失败：HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function createEvidenceAnalysis(name: string): Promise<EvidenceAnalysisCreateResponse> {
  return request(`${API_BASE_URL}/evidence-analysis`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enterprise_name: name.trim() }),
  });
}
export function getEvidenceAnalysis(taskId: string): Promise<EvidenceAnalysisTaskResponse> {
  return request(`${API_BASE_URL}/evidence-analysis/${encodeURIComponent(taskId)}`);
}
export function getEvidenceReport(companyId: string): Promise<EvidenceFirstReport> {
  return request(`${API_BASE_URL}/report/v2/company/${encodeURIComponent(companyId)}`);
}
