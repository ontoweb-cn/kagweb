/**
 * KAG 管理面 transport（设计 §5.4）。
 *
 * 一律走 requestJson（cookie 鉴权、401 跳转、ApiError 归一），错误 scope
 * 归入 "kag"；URL 保持 app 相对路径，由 apiFetch 层统一处理子路径部署。
 */

import { requestJson } from "@/shared/api/client";
import {
  parseEmbeddingProfiles,
  parseGraphQueryResult,
  parseKagBuildDetail,
  parseKagBuilds,
  parseKagProject,
  parseKagProjectDetail,
  parseKagProjects,
  parseKagTasks,
  parseSpgSchema,
  type KagBuildDetail,
  type KagBuildLiveStatus,
  type KagEmbeddingProfile,
  type KagGraphQueryResult,
  type KagProject,
  type KagProjectDetail,
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
  // 类型诚实（评审 F6）：响应缺 project 字段属契约破坏，显式抛错而非返回 undefined
  const project = parseKagProject(payload) ?? parseKagProjects(payload)[0];
  if (!project) throw new Error("Create-project response is missing the project.");
  return project;
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

/** M3.5 Schema 编辑：spg_type 为读模型原样（SpgTypeRow.raw），wire 转换在服务端。 */
export async function alterKagProjectSchema(
  projectId: string,
  body: {
    spg_type: Record<string, unknown>;
    add_relations?: { name: string; name_zh?: string; desc?: string; object_type_name: string }[];
    delete_relations?: string[];
  },
): Promise<unknown> {
  return requestJson<unknown>(
    `/api/kag/projects/${encodeURIComponent(projectId)}/schema/alter`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      scope: "kag",
    },
  );
}

/** M3.4 图浏览：reason DSL 查询（rows ≤200，服务端裁剪）。 */
export async function queryKagGraph(
  projectId: string,
  body: { dsl: string; params?: Record<string, string> },
): Promise<KagGraphQueryResult> {
  const payload = await requestJson<unknown>(
    `/api/kag/projects/${encodeURIComponent(projectId)}/graph/query`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      scope: "kag",
    },
  );
  return parseGraphQueryResult(payload);
}

/** M4-A：触发 KAG_COMMAND 构建（受理层；本地 executor 缺失时执行会失败）。
 * P0a：返回受理记录 taskId（=OpenSPG BuilderJob.id），供提交后回显/跳转。 */
export async function submitKagBuild(
  projectId: string,
  command: string,
): Promise<{ taskId: string }> {
  const payload = await requestJson<unknown>(
    `/api/kag/projects/${encodeURIComponent(projectId)}/build`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command }),
      scope: "kag",
    },
  );
  const record = (payload ?? {}) as Record<string, unknown>;
  const task = (record.task ?? {}) as Record<string, unknown>;
  const buildJob = (record.build_job ?? {}) as Record<string, unknown>;
  return {
    taskId: String(task.task_id ?? buildJob.id ?? ""),
  };
}

/** M4-B：项目成员 + owner（owner_user_no 为 OpenSPG 归因 userNo）。 */
export interface KagProjectMembers {
  owner: string;
  ownerUserNo: string;
  members: string[];
  /** 当前用户是否可编辑成员（admin）。 */
  canEdit: boolean;
}

export async function fetchKagMembers(
  projectId: string,
  signal?: AbortSignal,
): Promise<KagProjectMembers> {
  const payload = await requestJson<unknown>(
    `/api/kag/projects/${encodeURIComponent(projectId)}/members`,
    { cache: "no-store", signal, scope: "kag" },
  );
  const record = (payload ?? {}) as Record<string, unknown>;
  const members = Array.isArray(record.members)
    ? record.members.map((m) => String(m))
    : [];
  return {
    owner: String(record.owner ?? ""),
    ownerUserNo: String(record.owner_user_no ?? ""),
    members,
    canEdit: record.can_edit === true,
  };
}

export async function updateKagMembers(
  projectId: string,
  members: string[],
): Promise<unknown> {
  return requestJson<unknown>(
    `/api/kag/projects/${encodeURIComponent(projectId)}/members`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ members }),
      scope: "kag",
    },
  );
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

/** P0a：项目构建任务列表（本地 build 摘要 × 实时节点级状态）。 */
export async function fetchKagBuilds(
  projectId: string,
  signal?: AbortSignal,
): Promise<(KagTaskRow & { liveStatus: KagBuildLiveStatus })[]> {
  const payload = await requestJson<unknown>(
    `/api/kag/projects/${encodeURIComponent(projectId)}/builds`,
    { cache: "no-store", signal, scope: "kag" },
  );
  return parseKagBuilds(payload);
}

/** P0a：构建任务详情（节点状态 + traceLog，只读）。 */
export async function fetchKagBuildDetail(
  jobId: string,
  signal?: AbortSignal,
): Promise<KagBuildDetail> {
  const payload = await requestJson<unknown>(
    `/api/kag/builds/${encodeURIComponent(jobId)}`,
    { cache: "no-store", signal, scope: "kag" },
  );
  const detail = parseKagBuildDetail(payload);
  if (!detail) throw new Error("Build detail not found.");
  return detail;
}

// —— embedding 模型选项（创建项目表单；目录端点本身 admin-only）——
// （kag settings 域的 GET/PUT 由 features/settings/sections/KagSettingsSection
//  按 Attachments 扩展模式自行 apiFetch——扩展注册需要 pending-restore 特化，
//  不走本模块的通用 transport。）

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
