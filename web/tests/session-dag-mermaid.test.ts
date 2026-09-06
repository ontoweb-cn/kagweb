import assert from "node:assert/strict";
import test from "node:test";
import { buildSessionDslDocument, parseSessionDsl, type DslDocument } from "../features/chat/dag/dsl";
import { dslToMermaid } from "../features/chat/dag/dsl-mermaid";
import { assistantMsg, ev, userMsg } from "./session-dag-fixtures";

function branchMessages() {
  return [
    { ...userMsg(1, "original"), parentMessageId: null },
    { ...assistantMsg(2, [
      ev("thinking", { call_id: "r1", call_kind: "agent_loop_round", trace_group: "stage" }, "plan", 1),
      ev("tool_call", { call_id: "t1", trace_group: "tool_call", tool_name: "rag" }, "rag", 2),
      ev("tool_result", { call_id: "t1", trace_group: "tool_call" }, "ok", 3),
    ]), parentMessageId: 1 },
    { ...userMsg(3, "edited"), parentMessageId: null },
    { ...assistantMsg(4, [
      ev("thinking", { call_id: "r2", call_kind: "agent_loop_round", trace_group: "stage" }, "plan", 4),
    ]), parentMessageId: 3 },
  ];
}

test("renders the message chain and nested call tree as a flowchart", () => {
  const doc = parseSessionDsl(
    buildSessionDslDocumentText(),
  );
  const out = dslToMermaid(doc);
  const lines = out.split("\n");

  assert.equal(lines[0], "flowchart TD");
  assert.ok(out.includes('msg_1["User: what is fourier?"]'), "user node with sanitized id");
  assert.ok(out.includes("msg_1 --> msg_2"), "conversation edge");
  assert.ok(out.includes("msg_2 --> call_r1_round_0"), "assistant → round");
  assert.ok(out.includes("call_r1_round_0 --> call_t1_tool_call_1"), "round → tool");
  // chain order: every non-empty line is a node or edge statement
  for (const line of lines.slice(1).filter(Boolean)) {
    assert.match(line, /^\s{4}\S+(\s*-->|\s*-\.|.*\[")/, `statement line: ${line}`);
  }
});

test("renders branches as dashed edges labelled with the branch position", () => {
  const doc = buildSessionDslDocument({ messages: branchMessages() });
  const out = dslToMermaid(doc);
  assert.ok(out.includes('-. "branch 1" .->'), "dashed branch edge with label");
  // branch chain: fork (msg:3) → sibling subtree msg:1 → msg:2
  assert.ok(out.includes("msg_3 -. \"branch 1\" .-> msg_1"));
  assert.ok(out.includes("msg_1 --> msg_2"));
});

test("sanitizes label-breaking characters (#50)", () => {
  const messages = [
    { ...userMsg(1, 'quote " hash # line\nbreak'), parentMessageId: null },
    { ...assistantMsg(2, []), parentMessageId: 1 },
  ];
  const out = dslToMermaid(buildSessionDslDocument({ messages }));
  const label = out.match(/msg_1\["(.*)"\]/)?.[1] ?? "";
  assert.ok(!label.includes('"'), "no quotes inside label");
  assert.ok(!label.includes("#"), "no entity-code prefix inside label");
  assert.ok(!label.includes("\n"), "no newline inside label");
  assert.ok(label.includes("quote"), "readable text preserved");
});

test("truncates labels to the readability limit", () => {
  const long = "x".repeat(200);
  const messages = [
    { ...userMsg(1, long), parentMessageId: null },
    { ...assistantMsg(2, []), parentMessageId: 1 },
  ];
  const out = dslToMermaid(buildSessionDslDocument({ messages }));
  const label = out.match(/msg_1\["(.*)"\]/)?.[1] ?? "";
  // "User: " prefix (6) + 48-char clip + "..." (3)
  assert.ok(label.length <= 57, `label too long: ${label.length}`);
  assert.ok(label.endsWith("..."));
});

test("dedupes sanitized node ids that collide (#49)", () => {
  // msg:1:1 and msg:1_1 both sanitize to msg_1_1 — the second gets a suffix.
  const doc = {
    version: 1,
    generator: "deepmentor/session-dsl",
    trace: [
      { node: "msg:1:1", kind: "user" },
      { node: "msg:1_1", kind: "assistant" },
    ],
  } as DslDocument;
  const out = dslToMermaid(doc);
  assert.ok(out.includes("msg_1_1["), "first id takes the base form");
  assert.ok(out.includes("msg_1_1_1["), "colliding id gets a numeric suffix");
  assert.ok(out.includes("msg_1_1 --> msg_1_1_1"));
});

test("empty documents produce a minimal valid diagram", () => {
  const out = dslToMermaid({ version: 1, generator: "deepmentor/session-dsl", trace: [] });
  assert.equal(out.trim(), "flowchart TD");
});

test("unicode labels survive sanitization (#52)", () => {
  const messages = [
    { ...userMsg(1, "什么是傅里叶变换？"), parentMessageId: null },
    { ...assistantMsg(2, []), parentMessageId: 1 },
  ];
  const out = dslToMermaid(buildSessionDslDocument({ messages }));
  assert.ok(out.includes("什么是傅里叶变换？"), "Chinese label kept verbatim");
});

function buildSessionDslDocumentText(): string {
  const messages = [
    { ...userMsg(1, "what is fourier?"), parentMessageId: null },
    { ...assistantMsg(2, [
      ev("thinking", { call_id: "r1", call_kind: "agent_loop_round", trace_group: "stage" }, "plan", 1),
      ev("tool_call", { call_id: "t1", trace_group: "tool_call", tool_name: "rag" }, "rag", 2),
      ev("progress", { call_id: "t1", trace_role: "retrieve", query: "fourier" }, "searching", 3),
      ev("tool_result", { call_id: "t1", trace_group: "tool_call" }, "ok", 4),
    ]), parentMessageId: 1 },
  ];
  return JSON.stringify(buildSessionDslDocument({ messages }));
}
