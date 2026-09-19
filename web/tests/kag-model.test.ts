import assert from "node:assert/strict";
import test from "node:test";
import {
  buildSpgTypeTree,
  kagDraftToRequest,
  kagSettingsToDraft,
  ownProperties,
  parseEmbeddingProfiles,
  parseKagProjectDetail,
  parseKagProjects,
  parseKagSettings,
  parseKagTasks,
  parseSpgSchema,
  projectCreateRequest,
  validateProjectCreateForm,
  EMPTY_PROJECT_CREATE_FORM,
} from "../features/kag/model";

// —— 项目列表 ——
// 上游字段形态：M0-4/m0-3 实测（OpenSPG /public/v1/project 原始行）。

test("parseKagProjects normalizes the router envelope", () => {
  const projects = parseKagProjects({
    projects: [
      { id: 1, name: "m0_probe_pubnet", namespace: "m0_probe_pubnet", config: "{}", tag: "PUBLIC_NET" },
      { id: "2", name: "m0ProbeLive", namespace: "m0ProbeLive", description: null, visibility: "PRIVATE", userNo: "kagweb" },
    ],
  });
  assert.deepEqual(projects, [
    {
      projectId: "1",
      name: "m0_probe_pubnet",
      namespace: "m0_probe_pubnet",
      description: "",
      tag: "PUBLIC_NET",
      visibility: "",
      userNo: "",
    },
    {
      projectId: "2",
      name: "m0ProbeLive",
      namespace: "m0ProbeLive",
      description: "",
      tag: "",
      visibility: "PRIVATE",
      userNo: "kagweb",
    },
  ]);
});

test("parseKagProjects degrades a malformed payload to an empty list", () => {
  assert.deepEqual(parseKagProjects(null), []);
  assert.deepEqual(parseKagProjects({}), []);
  assert.deepEqual(parseKagProjects({ projects: "not-a-list" }), []);
});

// —— 项目详情 ——
// schema_summary / graph_labels 失败降级为 null（后端契约），前端不崩。

test("parseKagProjectDetail keeps nullable degradations", () => {
  const detail = parseKagProjectDetail({
    project: { id: 3, name: "m0ProbeLive", namespace: "m0ProbeLive", tag: "LOCAL" },
    schema_summary: { spg_type_count: 2, spg_type_names: ["Entity", "m0ProbeLive.Person"] },
    graph_labels: null,
  });
  assert.equal(detail?.project.projectId, "3");
  assert.deepEqual(detail?.schemaSummary, {
    spgTypeCount: 2,
    spgTypeNames: ["Entity", "m0ProbeLive.Person"],
  });
  assert.equal(detail?.graphLabels, null);
});

test("parseKagProjectDetail accepts label lists and rejects missing projects", () => {
  const detail = parseKagProjectDetail({
    project: { id: 3, name: "x", namespace: "x" },
    schema_summary: null,
    graph_labels: ["Entity", "x.Person"],
  });
  assert.equal(detail?.schemaSummary, null);
  assert.deepEqual(detail?.graphLabels, ["Entity", "x.Person"]);
  assert.equal(parseKagProjectDetail({ project: {} }), null);
});

test("parseKagProjectDetail prefers the backend count over truncated names", () => {
  // 后端 spg_type_names 截断到 100；>100 类型的项目计数以 spg_type_count 为准
  const detail = parseKagProjectDetail({
    project: { id: 1, name: "big", namespace: "big" },
    schema_summary: { spg_type_count: 120, spg_type_names: ["Entity", "big.A"] },
    graph_labels: [],
  });
  assert.equal(detail?.schemaSummary?.spgTypeCount, 120);
  assert.deepEqual(detail?.schemaSummary?.spgTypeNames, ["Entity", "big.A"]);
  // count 字段缺失/非法时退回 names 长度
  const fallback = parseKagProjectDetail({
    project: { id: 1, name: "big", namespace: "big" },
    schema_summary: { spg_type_names: ["Entity", "big.A", "big.B"] },
    graph_labels: [],
  });
  assert.equal(fallback?.schemaSummary?.spgTypeCount, 3);
});

// —— Schema 树 ——
// spgTypes 行形态：M0-3 实测 + knext rest 模型（basicInfo.name.nameEn /
// namespace、parentTypeInfo.parentTypeIdentifier、properties[].inherited）。

const schemaPayload = {
  schema: {
    spgTypes: [
      {
        "@type": "TEXT",
        basicType: "TEXT",
        basicInfo: {
          name: { "@type": "SPG_TYPE", nameEn: "Text", identityType: "SPG_TYPE" },
          nameZh: "文本",
        },
        spgTypeEnum: "BASIC_TYPE",
        properties: [],
      },
      {
        spgTypeEnum: "STANDARD_TYPE",
        basicInfo: {
          name: { "@type": "SPG_TYPE", namespace: "STD", nameEn: "Date" },
          nameZh: "日期",
          desc: "8位数字组成的日期",
        },
        parentTypeInfo: {
          parentTypeIdentifier: { "@type": "SPG_TYPE", nameEn: "Thing" },
        },
        properties: [
          {
            basicInfo: { name: { name: "id", identityType: "PREDICATE" }, nameZh: "标识" },
            objectTypeRef: { basicInfo: { name: { nameEn: "Text" } }, spgTypeEnum: "BASIC_TYPE" },
            inherited: false,
          },
        ],
      },
      {
        spgTypeEnum: "ENTITY_TYPE",
        basicInfo: { name: { namespace: "ns", nameEn: "Person" }, nameZh: "人物" },
        parentTypeInfo: {
          parentTypeIdentifier: { nameEn: "Date", namespace: "STD" },
        },
        properties: [
          {
            basicInfo: { name: { name: "name" } },
            objectTypeRef: { basicInfo: { name: { nameEn: "Text" } } },
            inherited: true,
          },
        ],
      },
    ],
  },
};

test("parseSpgSchema maps the upstream spgType shape onto rows", () => {
  const rows = parseSpgSchema(schemaPayload);
  assert.deepEqual(
    rows.map((row) => [row.key, row.kind, row.parent]),
    [
      ["Text", "basic", null],
      ["STD.Date", "standard", "Thing"],
      ["ns.Person", "entity", "STD.Date"],
    ],
  );
  assert.equal(rows[1].desc, "8位数字组成的日期");
  assert.equal(rows[1].namespace, "STD");
});

test("parseSpgSchema tolerates a raw spgTypes envelope and garbage", () => {
  assert.deepEqual(parseSpgSchema({ spgTypes: [] }), []);
  assert.deepEqual(parseSpgSchema("nope"), []);
});

test("buildSpgTypeTree nests by parent and orders roots basic-first", () => {
  const tree = buildSpgTypeTree(parseSpgSchema(schemaPayload));
  assert.deepEqual(
    tree.map((node) => node.key),
    ["Text", "STD.Date"],
  );
  const date = tree[1];
  assert.deepEqual(date.children.map((node) => node.key), ["ns.Person"]);
});

test("ownProperties separates inherited properties for the tree default", () => {
  const rows = parseSpgSchema(schemaPayload);
  assert.equal(ownProperties(rows[1]).length, 1);
  assert.equal(ownProperties(rows[2]).length, 0);
});

// —— 任务行（A.3 契约宽松归一）——

test("parseKagTasks normalizes bridge rows defensively", () => {
  const tasks = parseKagTasks({
    tasks: [
      {
        task_id: "t1",
        session_id: "s1",
        project_id: "3",
        namespace: "m0ProbeLive",
        question: "Q",
        answer_digest: "A",
        cost_ms: 9712.7,
        references: ["r1", "r2", 3, null],
        created_at: "2026-09-18T11:00:00+00:00",
      },
    ],
    count: 1,
  });
  assert.equal(tasks.length, 1);
  assert.equal(tasks[0].costMs, 9712);
  assert.deepEqual(tasks[0].references, ["r1", "r2", "3"]);
  assert.deepEqual(parseKagTasks({ tasks: [] }), []);
  assert.deepEqual(parseKagTasks({}), []);
});

// —— kag settings 域（bridge_api_key write-only）——

test("parseKagSettings keeps the key-set flag without echoing the key", () => {
  const settings = parseKagSettings({
    version: 1,
    spg_server_url: "http://127.0.0.1:8887",
    bridge_command: "/tmp/kag_m0_venv/bin/python",
    bridge_args: ["-m", "kag_bridge"],
    kag_project_dir: "/tmp/m0_kag_project",
    namespace: "m0ProbeLive",
    project_id: "3",
    bridge_api_key: "",
    bridge_api_key_set: true,
  });
  assert.equal(settings.spgServerUrl, "http://127.0.0.1:8887");
  assert.deepEqual(settings.bridgeArgs, ["-m", "kag_bridge"]);
  assert.equal(settings.bridgeApiKeySet, true);
  // 未提供 service_user_no → 空串
  assert.equal(settings.serviceUserNo, "");
});

test("draft round-trip: empty key keeps the stored value", () => {
  const draft = kagSettingsToDraft({
    spgServerUrl: "http://127.0.0.1:8887",
    bridgeCommand: "python",
    bridgeArgs: ["-m", "kag_bridge"],
    kagProjectDir: "/p",
    namespace: "ns",
    projectId: "3",
    serviceUserNo: "kag-svc",
    bridgeApiKeySet: true,
  });
  // 未触碰 key → null → 请求体空串（后端语义：保留旧值）
  assert.equal(draft.bridgeApiKey, null);
  const body = kagDraftToRequest(draft);
  assert.equal(body.bridge_api_key, "");
  assert.deepEqual(body.bridge_args, ["-m", "kag_bridge"]);
  assert.equal(body.service_user_no, "kag-svc");
  // 显式输入 → 原样下发
  assert.equal(
    kagDraftToRequest({ ...draft, bridgeApiKey: "new-key" }).bridge_api_key,
    "new-key",
  );
  // args 按行拆分去空白、URL 去首尾空白
  const messy = kagDraftToRequest({
    ...draft,
    bridgeArgsText: "  -m \n kag_bridge \n\n",
    spgServerUrl: " http://x ",
  });
  assert.deepEqual(messy.bridge_args, ["-m", "kag_bridge"]);
  assert.equal(messy.spg_server_url, "http://x");
});

// —— 创建表单校验（键 = 英文原文，i18n 直接翻译）——

test("validateProjectCreateForm enforces the OpenSPG contract", () => {
  assert.deepEqual(validateProjectCreateForm(EMPTY_PROJECT_CREATE_FORM), [
    "Project name is required.",
    "Namespace must be 3-64 characters, letters and digits only, starting with a letter.",
    "Select an embedding model for the project vectorizer.",
  ]);
  // 命名空间：字母开头、纯字母数字（Neo4j 库名约束，M0 实测）
  assert.ok(
    validateProjectCreateForm({
      ...EMPTY_PROJECT_CREATE_FORM,
      name: "n",
      namespace: "m0-probe",
      embeddingModelId: "emb",
    })[0].includes("Namespace"),
  );
  assert.ok(
    validateProjectCreateForm({
      ...EMPTY_PROJECT_CREATE_FORM,
      name: "n",
      namespace: "1abc",
      embeddingModelId: "emb",
    })[0].includes("Namespace"),
  );
  // 维度与服务账号
  const problems = validateProjectCreateForm({
    ...EMPTY_PROJECT_CREATE_FORM,
    name: "n",
    namespace: "m0probe",
    embeddingModelId: "emb",
    vectorDimensions: "40.5",
    serviceUserNo: "a",
  });
  assert.deepEqual(problems, [
    "Vector dimensions must be a positive integer.",
    "Service account must be 6-20 characters: letters, digits, or underscores.",
  ]);
  assert.deepEqual(
    validateProjectCreateForm({
      name: "proj",
      namespace: "m0probe",
      embeddingModelId: "emb",
      vectorDimensions: "4096",
      serviceUserNo: "kagweb",
    }),
    [],
  );
});

test("projectCreateRequest omits a blank dimensions override", () => {
  assert.equal(
    projectCreateRequest({
      name: "proj",
      namespace: "m0probe",
      embeddingModelId: "emb",
      vectorDimensions: "",
      serviceUserNo: "",
    }).vector_dimensions,
    undefined,
  );
  assert.equal(
    projectCreateRequest({
      name: "proj",
      namespace: "m0probe",
      embeddingModelId: "emb",
      vectorDimensions: "4096",
      serviceUserNo: "",
    }).vector_dimensions,
    4096,
  );
});

// —— embedding 模型目录（services.embedding.profiles 宽松解析）——

test("parseEmbeddingProfiles reads the catalog's embedding service", () => {
  const profiles = parseEmbeddingProfiles({
    catalog: {
      services: {
        embedding: {
          profiles: [
            { id: "emb1", name: "GPUStack Embedding", model: "Qwen3-Embedding-8B", models: [{ model: "Qwen3-Embedding-8B" }] },
            { name: "no-id-profile" },
          ],
        },
      },
    },
  });
  assert.deepEqual(profiles, [
    { id: "emb1", name: "GPUStack Embedding", model: "Qwen3-Embedding-8B" },
  ]);
  assert.deepEqual(parseEmbeddingProfiles({ catalog: { services: {} } }), []);
  assert.deepEqual(parseEmbeddingProfiles(null), []);
});
