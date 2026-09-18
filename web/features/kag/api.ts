/**
 * KAG 管理面 transport（设计 §5.4）。
 *
 * 一律走 requestJson（cookie 鉴权、401 跳转、ApiError 归一），错误 scope
 * 归入 "kag"；URL 保持 app 相对路径，由 apiFetch 层统一处理子路径部署。
 */

import { requestJson } from "@/shared/api/client";
import {
  parseEmbeddingProfiles,
  parseKagProject,
  parseKagProjectDetail,
  parseKagProjects,
  parseKagSettings,
  parseKagTasks,
  parseSpgSchema,
  type KagEmbeddingProfile,
  type KagProject,
  type KagProjectDetail,
  type KagSettings,
  type KagTaskRow,
  type SpgTypeRow,
} from "./model";

export interface KagTaskQuery {
  limit?: number;
  sessionId?: string;
  projectId?: string;
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

export async function fetchKagProjects(
  signal?: AbortSignal,
): Promise<KagProject[]> {
  const payload = await requestJson<unknown>("/api/kag/projects", {
    cache: "no-store",
    signal,
    scope: "kag",
  });
  return parseKagProjects(payload);
}

export async function createKagProject(
  body: Record<string, unknown>,
): Promise<KagProject> {
  const payload = await requestJson<unknown>("/api/kag/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    scope: "kag",
  });
  return parseKagProject(payload) ?? parseKagProjects(payload)[0];
}

export async function fetchKagProjectDetail(
  projectId: string,
  signal?: AbortSignal,
): Promise<KagProjectDetail> {
  const payload = await requestJson<unknown>(
    `/api/kag/projects/${encodeURIComponent(projectId)}`,
    { cache: "no-store", signal, scope: "kag" },
  );
  const detail = parseKagProjectDetail(payload);
  if (!detail) throw new Error("Project not found.");
  return detail;
}

export async function fetchKagProjectSchema(
  projectId: string,
  signal?: AbortSignal,
): Promise<SpgTypeRow[]> {
  const payload = await requestJson<unknown>(
    `/api/kag/projects/${encodeURIComponent(projectId)}/schema`,
    { cache: "no-store", signal, scope: "kag" },
  );
  return parseSpgSchema(payload);
}

export async function fetchKagTasks(
  taskQuery: KagTaskQuery = {},
  signal?: AbortSignal,
): Promise<KagTaskRow[]> {
  const payload = await requestJson<unknown>(
    `/api/kag/tasks${query({
      limit: taskQuery.limit,
      session_id: taskQuery.sessionId,
      project_id: taskQuery.projectId,
    })}`,
    { cache: "no-store", signal, scope: "kag" },
  );
  return parseKagTasks(payload);
}

// —— kag settings 域（admin-only；设置区使用）——

export async function fetchKagSettings(signal?: AbortSignal): Promise<KagSettings> {
  const payload = await requestJson<unknown>("/api/settings/kag", {
    cache: "no-store",
    signal,
    scope: "kag",
  });
  return parseKagSettings(payload);
}

export async function saveKagSettings(
  body: Record<string, unknown>,
): Promise<KagSettings> {
  const payload = await requestJson<unknown>("/api/settings/kag", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    scope: "kag",
  });
  return parseKagSettings(payload);
}

// —— embedding 模型选项（创建项目表单；目录端点本身 admin-only）——

export async function fetchKagEmbeddingProfiles(
  signal?: AbortSignal,
): Promise<KagEmbeddingProfile[]> {
  const payload = await requestJson<unknown>("/api/settings/catalog", {
    cache: "no-store",
    signal,
    scope: "kag",
  });
  return parseEmbeddingProfiles(payload);
}
