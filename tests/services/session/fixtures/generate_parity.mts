/**
 * Session DSL parity fixture generator — run with tsx from the repo root:
 *
 *   npx tsx tests/services/session/fixtures/generate_parity.mts
 *
 * Regenerates `session_dsl_expected.json` (DSL documents) and
 * `session_dsl_expected.mmd` (Mermaid codegen) from the TypeScript
 * implementation. The Python port (`kagweb/services/session/dsl_export.py`)
 * must match these outputs exactly — if you change the web derivation rules,
 * regenerate here AND update the Python port in the same commit (#53/#64).
 */
import { writeFileSync } from "node:fs";
import { buildSessionDslDocument } from "../../../../web/features/chat/dag/dsl";
import { dslToMermaid } from "../../../../web/features/chat/dag/dsl-mermaid";

function ev(
  type: string,
  metadata: Record<string, unknown>,
  content = "",
  timestamp = 1,
  stage = "exploring",
) {
  return { type, source: "chat", stage, content, metadata, timestamp };
}

function storeMsg(
  id: number,
  role: "user" | "assistant",
  content: string,
  parent: number | null,
  capability: string,
  events: unknown[] = [],
) {
  return {
    id,
    session_id: "s1",
    role,
    content,
    capability,
    events,
    attachments: [],
    metadata: {},
    created_at: id,
    parent_message_id: parent,
  };
}

const inputMessages = [
  storeMsg(1, "user", "what is fourier?", null, ""),
  storeMsg(
    2,
    "assistant",
    "",
    1,
    "chat",
    [
      ev("thinking", { call_id: "r1", call_kind: "agent_loop_round", trace_group: "stage" }, "plan", 1),
      ev("tool_call", { call_id: "t1", trace_group: "tool_call", tool_name: "rag" }, "rag", 2),
      ev("progress", { call_id: "t1", trace_role: "retrieve", query: "fourier" }, "searching", 3),
      ev("tool_result", { call_id: "t1", trace_group: "tool_call", tool_metadata: { provider: "lightrag" } }, "ok", 4),
      ev("tool_call", { call_id: "t2", trace_group: "tool_call", tool_name: "consult_subagent" }, "go", 5),
      ev("progress", { call_id: "t2", subagent_name: "math", consult_index: 1 }, "working", 6),
      ev("tool_result", { call_id: "t2", trace_group: "tool_call" }, "ok", 7),
    ],
  ),
  storeMsg(3, "user", "edited question", null, ""),
  storeMsg(
    4,
    "assistant",
    "",
    3,
    "chat",
    [
      ev("thinking", { call_id: "r2", call_kind: "agent_loop_round", trace_group: "stage" }, "replan", 8),
    ],
  ),
  // deep_research two-turn pair (module 12): outline turn + confirmed followup
  storeMsg(5, "user", "research this", 4, ""),
  storeMsg(
    6,
    "assistant",
    "",
    5,
    "deep_research",
    [
      ev("thinking", { call_id: "r3", call_kind: "agent_loop_round", trace_group: "stage" }, "rephrase", 9),
      ev("result", { outline_preview: true }, "outline", 10),
    ],
  ),
  storeMsg(7, "user", "(start research ack)", 6, ""),
  storeMsg(
    8,
    "assistant",
    "",
    7,
    "deep_research",
    [
      ev("thinking", { call_id: "r4", call_kind: "agent_loop_round", trace_group: "stage" }, "search", 11, "researching"),
      ev("content", { call_id: "r4" }, "report body", 12, "reporting"),
    ],
  ),
];

const dagInput = {
  messages: inputMessages.map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content,
    capability: m.capability || undefined,
    events: m.events,
    parentMessageId: m.parent_message_id,
  })),
};

const stableNormalized = buildSessionDslDocument(dagInput, {
  stable: true,
  normalizeIds: true,
});
const raw = buildSessionDslDocument(dagInput, { sessionId: "s1" });

const fixture = {
  input_messages: inputMessages,
  expected: { stable_normalized: stableNormalized, raw },
};

const outDir = new URL(".", import.meta.url).pathname;
writeFileSync(
  `${outDir}session_dsl_expected.json`,
  JSON.stringify(fixture, null, 2) + "\n",
);
writeFileSync(
  `${outDir}session_dsl_expected.mmd`,
  dslToMermaid(stableNormalized),
);
console.log("parity fixtures regenerated (json + mmd)");
