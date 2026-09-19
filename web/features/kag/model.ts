/**
 * KAG 管理面纯模型层（设计 docs/kag-integration-design.md §5.4）。
 *
 * 只做契约归一与校验，不渲染、不依赖 app/components/context——
 * `feature-domain-does-not-render` 分层规则由 depcruise 守护。
 * 上游字段缺失/类型漂移时降级为安全缺省值，列表页始终可渲染。
 */

// —— 项目（OpenSPG /public/v1/project 原始行的宽松归一）——

export interface KagProject {
  projectId: string;
  name: string;
  namespace: string;
  description: string;
  /** LOCAL / PUBLIC_NET / …；上游缺省为空串。 */
  tag: string;
  visibility: string;
  userNo: string;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function text(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function textOrNull(value: unknown): string | null {
  const s = text(value);
  return s === "" || s === "None" ? null : s;
}

function parseProject(raw: unknown): KagProject {
  const row = record(raw);
  return {
    projectId: text(row.id ?? row.projectId),
    name: text(row.name),
    namespace: text(row.namespace),
    description: textOrNull(row.description) ?? "",
    tag: text(row.tag),
    visibility: text(row.visibility),
    userNo: text(row.userNo),
  };
}

export function parseKagProjects(raw: unknown): KagProject[] {
  const payload = record(raw);
  const rows = payload.projects;
  if (!Array.isArray(rows)) return [];
  return rows.map(parseProject);
}

export function parseKagProject(raw: unknown): KagProject | null {
  const payload = record(raw);
  const project = payload.project ?? raw;
  if (!record(project).id && !record(project).projectId) return null;
  return parseProject(project);
}

// —— 项目详情（GET /api/kag/projects/{id}）——

export interface KagProjectDetail {
  project: KagProject;
  schemaSummary: { spgTypeCount: number; spgTypeNames: string[] } | null;
  graphLabels: string[] | null;
}

export function parseKagProjectDetail(raw: unknown): KagProjectDetail | null {
  const payload = record(raw);
  const project = parseKagProject(raw);
  if (!project) return null;
  const summary = record(payload.schema_summary);
  const names = Array.isArray(summary.spg_type_names)
    ? summary.spg_type_names.map(text).filter(Boolean)
    : null;
  // 后端 spg_type_count 是真实数；spg_type_names 截断到 100（kag.py），
  // >100 类型的项目以 names.length 计数会偏小——count 字段优先，names 仅 fallback。
  const reported = Number(summary.spg_type_count);
  const typeCount =
    Number.isInteger(reported) && reported >= 0
      ? reported
      : (names?.length ?? 0);
  const labels = Array.isArray(payload.graph_labels)
    ? payload.graph_labels.map(text).filter(Boolean)
    : null;
  return {
    project,
    schemaSummary:
      names === null ? null : { spgTypeCount: typeCount, spgTypeNames: names },
    graphLabels: labels,
  };
}

// —— Schema（spgTypes 原始行 → 只读树行）——

export type SpgTypeKind =
  | "basic"
  | "index"
  | "standard"
  | "entity"
  | "concept"
  | "event"
  | "unknown";

const SPG_TYPE_KINDS: Record<string, SpgTypeKind> = {
  BASIC_TYPE: "basic",
  INDEX_TYPE: "index",
  STANDARD_TYPE: "standard",
  ENTITY_TYPE: "entity",
  CONCEPT_TYPE: "concept",
  EVENT_TYPE: "event",
};

export interface SpgPropertyRow {
  name: string;
  nameZh: string;
  objectType: string;
  inherited: boolean;
}

export interface SpgTypeRow {
  /** 限定名（namespace + nameEn）；basic 类型无 namespace。 */
  key: string;
  name: string;
  namespace: string;
  nameZh: string;
  desc: string;
  kind: SpgTypeKind;
  parent: string | null;
  properties: SpgPropertyRow[];
  /** 原始读模型 JSON（M3.5 编辑端点要求原样回传——wire 转换在服务端）。 */
  raw: Record<string, unknown>;
}

function qualifiedName(basicInfo: Record<string, unknown>): string {
  const name = record(basicInfo.name);
  const ns = textOrNull(name.namespace);
  const nameEn = text(name.nameEn ?? name.name);
  return ns && nameEn ? `${ns}.${nameEn}` : nameEn;
}

function parseProperty(raw: unknown): SpgPropertyRow {
  const row = record(raw);
  const info = record(row.basicInfo);
  const predicate = record(info.name);
  const objectType = qualifiedName(record(record(row.objectTypeRef).basicInfo));
  return {
    name: text(predicate.name),
    nameZh: text(info.nameZh),
    objectType,
    inherited: row.inherited === true,
  };
}

/** parentTypeIdentifier 的字段直接挂在自身（无 basicInfo.name 包层）。 */
function parentQualifiedName(raw: unknown): string {
  const identifier = record(raw);
  const ns = textOrNull(identifier.namespace);
  const nameEn = text(identifier.nameEn);
  return ns && nameEn ? `${ns}.${nameEn}` : nameEn;
}

function parseSpgType(raw: unknown): SpgTypeRow {
  const row = record(raw);
  const info = record(row.basicInfo);
  const name = record(info.name);
  const kind = SPG_TYPE_KINDS[text(row.spgTypeEnum)] ?? "unknown";
  const key = qualifiedName(info) || text(name.nameEn);
  const parent = parentQualifiedName(
    record(record(row.parentTypeInfo).parentTypeIdentifier),
  );
  const properties = Array.isArray(row.properties)
    ? row.properties.map(parseProperty)
    : [];
  return {
    key: key || text(name.name),
    name: text(name.nameEn ?? name.name),
    namespace: text(name.namespace),
    nameZh: text(info.nameZh),
    desc: textOrNull(info.desc) ?? "",
    kind,
    parent: parent && parent !== key ? parent : null,
    properties,
    raw: row,
  };
}

export function parseSpgSchema(raw: unknown): SpgTypeRow[] {
  const payload = record(raw);
  const types = record(payload.schema).spgTypes ?? payload.spgTypes;
  if (!Array.isArray(types)) return [];
  return types.map(parseSpgType).filter((row) => row.key !== "");
}

/** 只读树节点（实体/概念/事件类型按 parentTypeInfo 层级化，basic 平铺）。 */
export interface SpgTypeNode extends SpgTypeRow {
  children: SpgTypeNode[];
}

export function buildSpgTypeTree(rows: SpgTypeRow[]): SpgTypeNode[] {
  const byKey = new Map<string, SpgTypeNode>();
  for (const row of rows) {
    if (!byKey.has(row.key)) byKey.set(row.key, { ...row, children: [] });
  }
  const roots: SpgTypeNode[] = [];
  for (const node of byKey.values()) {
    const parent = node.parent ? byKey.get(node.parent) : undefined;
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }
  // 根层按 BASIC → STANDARD → 业务类型 排序，子层保持 schema 声明序。
  const order: Record<SpgTypeKind, number> = {
    basic: 0,
    standard: 1,
    entity: 2,
    concept: 3,
    event: 4,
    index: 5,
    unknown: 6,
  };
  roots.sort((a, b) => order[a.kind] - order[b.kind] || a.key.localeCompare(b.key));
  return roots;
}

/** properties 按继承分离：详情树中默认只展示自有属性。 */
export function ownProperties(row: SpgTypeRow): SpgPropertyRow[] {
  return row.properties.filter((property) => !property.inherited);
}

/** Schema 关系行（M3.5 编辑：从 raw.relations 解析，编辑意图走 add/delete 列表）。 */
export interface SpgRelationRow {
  name: string;
  nameZh: string;
  desc: string;
  objectType: string;
  inherited: boolean;
}

export function parseRelations(raw: unknown): SpgRelationRow[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => {
      const row = record(item);
      const info = record(row.basicInfo);
      const predicate = record(info.name);
      const objectType = qualifiedName(record(record(row.objectTypeRef).basicInfo));
      return {
        name: text(predicate.name),
        nameZh: text(info.nameZh),
        desc: textOrNull(info.desc) ?? "",
        objectType,
        inherited: row.inherited === true,
      };
    })
    .filter((rel) => rel.name !== "");
}

// —— 图浏览（M3.4：reason DSL rows → 节点/边，2 列 id 查询即边）——

export interface GraphExplorerEdge {
  source: string;
  target: string;
}

export interface GraphExplorerData {
  nodes: string[];
  edges: GraphExplorerEdge[];
}

/** 2 列 id 的 rows → 去重节点 + 边（M3.3 实测：结果在 resultTableResult.rows）。 */
export function rowsToGraph(rows: unknown, limit = 200): GraphExplorerData {
  const nodes: string[] = [];
  const seen = new Set<string>();
  const edges: GraphExplorerEdge[] = [];
  const edgeKeys = new Set<string>();
  if (!Array.isArray(rows)) return { nodes, edges };
  for (const row of rows.slice(0, limit)) {
    if (!Array.isArray(row) || row.length < 2) continue;
    const source = text(row[0]);
    const target = text(row[1]);
    if (!source || !target) continue;
    for (const id of [source, target]) {
      if (!seen.has(id)) {
        seen.add(id);
        if (nodes.length < limit) nodes.push(id);
      }
    }
    const key = `${source}\u0000${target}`;
    if (!edgeKeys.has(key) && edges.length < limit) {
      edgeKeys.add(key);
      edges.push({ source, target });
    }
  }
  return { nodes, edges };
}

/** 图浏览查询结果（GET 无 body，POST 返回结构）。 */
export interface KagGraphQueryResult {
  status: string;
  header: string[];
  rows: unknown[];
  rowCount: number;
  truncated: boolean;
  error: string;
}

export function parseGraphQueryResult(raw: unknown): KagGraphQueryResult {
  const payload = record(raw);
  return {
    status: text(payload.status) || "UNKNOWN",
    header: Array.isArray(payload.header) ? payload.header.map((h) => text(h)) : [],
    rows: Array.isArray(payload.rows) ? payload.rows : [],
    rowCount: Number(payload.row_count ?? 0) || 0,
    truncated: payload.truncated === true,
    error: text(payload.error),
  };
}

// —— 推理任务（GET /api/kag/tasks，A.3 契约）——

export interface KagTaskRow {
  taskId: string;
  sessionId: string;
  projectId: string;
  namespace: string;
  question: string;
  answerDigest: string;
  costMs: number;
  references: string[];
  createdAt: string;
}

export function parseKagTasks(raw: unknown): KagTaskRow[] {
  const payload = record(raw);
  const rows = payload.tasks;
  if (!Array.isArray(rows)) return [];
  return rows.map((rawRow) => {
    const row = record(rawRow);
    const references = Array.isArray(row.references)
      ? row.references.map(text).filter(Boolean)
      : [];
    const costMs = Number(row.cost_ms);
    return {
      taskId: text(row.task_id),
      sessionId: text(row.session_id),
      projectId: text(row.project_id),
      namespace: text(row.namespace),
      question: text(row.question),
      answerDigest: text(row.answer_digest),
      costMs: Number.isFinite(costMs) ? Math.max(0, Math.trunc(costMs)) : 0,
      references,
      createdAt: text(row.created_at),
    };
  });
}

// —— kag settings 域（GET/PUT /api/settings/kag；bridge_api_key write-only）——

export interface KagSettings {
  spgServerUrl: string;
  bridgeCommand: string;
  bridgeArgs: string[];
  kagProjectDir: string;
  namespace: string;
  projectId: string;
  /** 响应恒不回显 key，只回显“已设置”标记。 */
  bridgeApiKeySet: boolean;
}

export function parseKagSettings(raw: unknown): KagSettings {
  const row = record(raw);
  const args = Array.isArray(row.bridge_args) ? row.bridge_args.map(text) : [];
  return {
    spgServerUrl: text(row.spg_server_url),
    bridgeCommand: text(row.bridge_command),
    bridgeArgs: args,
    kagProjectDir: text(row.kag_project_dir),
    namespace: text(row.namespace),
    projectId: text(row.project_id),
    bridgeApiKeySet: row.bridge_api_key_set === true,
  };
}

/** 可编辑草稿；bridgeApiKey 三态：null = 保留已存 key。 */
export interface KagSettingsDraft {
  spgServerUrl: string;
  bridgeCommand: string;
  bridgeArgsText: string;
  kagProjectDir: string;
  namespace: string;
  projectId: string;
  bridgeApiKey: string | null;
}

export function kagSettingsToDraft(settings: KagSettings): KagSettingsDraft {
  return {
    spgServerUrl: settings.spgServerUrl,
    bridgeCommand: settings.bridgeCommand,
    bridgeArgsText: settings.bridgeArgs.join("\n"),
    kagProjectDir: settings.kagProjectDir,
    namespace: settings.namespace,
    projectId: settings.projectId,
    bridgeApiKey: null,
  };
}

/** PUT 请求体：空 key = 保留旧值（后端语义），args 按行拆分去空白。 */
export function kagDraftToRequest(draft: KagSettingsDraft): Record<string, unknown> {
  return {
    spg_server_url: draft.spgServerUrl.trim(),
    bridge_command: draft.bridgeCommand.trim(),
    bridge_args: draft.bridgeArgsText
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean),
    kag_project_dir: draft.kagProjectDir.trim(),
    namespace: draft.namespace.trim(),
    project_id: draft.projectId.trim(),
    // null/空串都表示“保留已存值”（后端：空即沿用旧值）
    bridge_api_key: draft.bridgeApiKey == null ? "" : draft.bridgeApiKey,
  };
}

// —— 创建项目表单（POST /api/kag/projects）——

export interface KagProjectCreateForm {
  name: string;
  namespace: string;
  embeddingModelId: string;
  vectorDimensions: string;
  serviceUserNo: string;
}

export const EMPTY_PROJECT_CREATE_FORM: KagProjectCreateForm = {
  name: "",
  namespace: "",
  embeddingModelId: "",
  vectorDimensions: "",
  serviceUserNo: "",
};

const NAMESPACE_RE = /^[A-Za-z][A-Za-z0-9]{2,63}$/;
const USERNO_RE = /^[A-Za-z0-9_]{6,20}$/;

/** 校验失败返回 i18n 键（英文原文即键），通过则返回空数组。 */
export function validateProjectCreateForm(
  form: KagProjectCreateForm,
): string[] {
  const errors: string[] = [];
  if (!form.name.trim()) errors.push("Project name is required.");
  if (!NAMESPACE_RE.test(form.namespace.trim()))
    errors.push(
      "Namespace must be 3-64 characters, letters and digits only, starting with a letter.",
    );
  if (!form.embeddingModelId)
    errors.push("Select an embedding model for the project vectorizer.");
  if (form.vectorDimensions.trim()) {
    const value = Number(form.vectorDimensions.trim());
    if (!Number.isInteger(value) || value <= 0)
      errors.push("Vector dimensions must be a positive integer.");
  }
  if (form.serviceUserNo.trim() && !USERNO_RE.test(form.serviceUserNo.trim()))
    errors.push(
      "Service account must be 6-20 characters: letters, digits, or underscores.",
    );
  return errors;
}

export function projectCreateRequest(
  form: KagProjectCreateForm,
): Record<string, unknown> {
  const dimensions = Number(form.vectorDimensions.trim());
  const body: Record<string, unknown> = {
    name: form.name.trim(),
    namespace: form.namespace.trim(),
    embedding_model_id: form.embeddingModelId,
    service_user_no: form.serviceUserNo.trim(),
  };
  if (form.vectorDimensions.trim() && Number.isInteger(dimensions) && dimensions > 0)
    body.vector_dimensions = dimensions;
  return body;
}

// —— embedding 模型目录（/api/settings/catalog 的 services.embedding.profiles）——
// web 侧 Catalog 类型未列 embedding 服务（UI 未消费），这里按目录真实形态宽松解析。

export interface KagEmbeddingProfile {
  id: string;
  name: string;
  model: string;
}

export function parseEmbeddingProfiles(raw: unknown): KagEmbeddingProfile[] {
  const catalog = record(record(raw).catalog);
  const embedding = record(record(catalog.services).embedding);
  const profiles = embedding.profiles;
  if (!Array.isArray(profiles)) return [];
  return profiles
    .map((rawProfile) => {
      const profile = record(rawProfile);
      const first = Array.isArray(profile.models)
        ? record(profile.models[0])
        : {};
      return {
        id: text(profile.id),
        name: text(profile.name) || text(profile.id),
        model: text(first.model) || text(profile.model) || text(profile.id),
      };
    })
    .filter((profile) => profile.id !== "");
}
