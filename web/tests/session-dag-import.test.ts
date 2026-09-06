import assert from "node:assert/strict";
import test from "node:test";
import { buildSessionDslDocument, parseSessionDsl } from "../features/chat/dag/dsl";
import { dslToDag } from "../features/chat/dag/dsl-import";
import { assistantMsg, ev, userMsg } from "./session-dag-fixtures";

function dslFromMessages() {
  const messages = [
    { ...userMsg(1, "what is fourier?"), parentMessageId: null },
    { ...assistantMsg(2, [
      ev("thinking", { call_id: "r1", call_kind: "agent_loop_round", trace_group: "stage" }, "plan", 1),
      ev("tool_call", { call_id: "t1", trace_group: "tool_call", tool_name: "rag" }, "rag", 2),
      ev("progress", { call_id: "t1", trace_role: "retrieve", query: "fourier" }, "searching", 3),
      ev("tool_result", { call_id: "t1", trace_group: "tool_call" }, "ok", 4),
    ]), parentMessageId: 1, capability: "chat" },
    // fork: edited question wins the visible path; branch 1 covers msg 1→2
    { ...userMsg(3, "edited"), parentMessageId: null },
    { ...assistantMsg(4, [
      ev("thinking", { call_id: "r2", call_kind: "agent_loop_round", trace_group: "stage" }, "replan", 5),
    ]), parentMessageId: 3, capability: "chat" },
  ];
  // normalizeIds gives branch entries the b1: namespace, mirroring the
  // recommended export mode for diff/share.
  return buildSessionDslDocument({ messages }, { stable: true, normalizeIds: true });
}

test("dslToDag reconstructs a renderable IR from an exported document", () => {
  const doc = dslFromMessages();
  const dag = dslToDag(doc);

  // root + 2 main-chain messages + branch chain (2) + call nodes (2 rounds + 1 tool)
  assert.ok(dag.nodes.length >= 8);
  assert.equal(dag.nodes[0].kind, "root");

  // main chain connected via conversation edges from root
  const conversation = dag.edges.filter((e) => e.kind === "conversation");
  assert.ok(conversation.length >= 3); // root→u3, u3→a4, b1:u1→b1:a2
  // call trees via drilldown edges
  assert.ok(dag.edges.some((e) => e.kind === "drilldown"));
  // branch sub-chain linked from the fork node via a sibling edge
  const sibling = dag.edges.find((e) => e.kind === "sibling");
  assert.ok(sibling, "branch chain attaches with a sibling edge");
  assert.equal(dag.byId.get(sibling!.target)?.kind, "user");

  // every node id is namespaced (dsl:) and referenced edges resolve
  for (const edge of dag.edges) {
    assert.ok(dag.byId.has(edge.source), `edge source ${edge.source}`);
    assert.ok(dag.byId.has(edge.target), `edge target ${edge.target}`);
  }
  for (const node of dag.nodes) {
    assert.ok(node.id === "root" || node.id.startsWith("dsl:"));
  }
});

test("imported call nodes carry reverse-mapped meta for the detail grid", () => {
  const doc = dslFromMessages();
  const dag = dslToDag(doc);
  const tool = dag.nodes.find((n) => n.kind === "tool_call");
  assert.ok(tool);
  assert.equal(tool.meta.toolName, "rag");
  const round = dag.nodes.find((n) => n.kind === "round");
  assert.ok(round);
  assert.ok(round.meta.roundIndex != null);
  const user = dag.nodes.find((n) => n.kind === "user");
  assert.ok(user);
  assert.equal(user.meta.textPreview, "edited");
});

test("nothing is expandable — call trees are fully materialized (#75)", () => {
  const dag = dslToDag(dslFromMessages());
  // Taps must fall through to select (detail grid), not the no-op toggle:
  // an import is a snapshot, so there is nothing left to expand.
  assert.equal(dag.expandable.size, 0);
});

test("messageIndex is chain-relative so Q/A labels match the live view (#75)", () => {
  const doc = dslFromMessages();
  const dag = dslToDag(doc);
  // Main chain: user (Q1) at index 0, assistant at index 1 — NOT inflated
  // by the root node or the materialized call nodes.
  const mainUser = dag.nodes.find((n) => n.kind === "user" && n.meta.textPreview === "edited");
  assert.equal(mainUser?.meta.messageIndex, 0);
  const mainAssistant = dag.nodes.find(
    (n) => n.kind === "assistant" && !n.id.startsWith("dsl:b1:"),
  );
  assert.ok(mainAssistant);
  assert.equal(mainAssistant.meta.messageIndex, 1);
  // Branch chain continues from the fork's position: b1:u1 sits where the
  // selected fork entry is (index 0), b1:a2 at index 1.
  const branchUser = dag.nodes.find((n) => n.id.startsWith("dsl:b1:") && n.kind === "user");
  assert.ok(branchUser);
  assert.equal(branchUser.meta.messageIndex, 0);
});

test("fork entries map their DSL branch field verbatim (#75)", () => {
  const doc = dslFromMessages();
  const dag = dslToDag(doc);
  // The exported fork entry carries branch: { index, total } with total
  // including the selected branch — imported nodes must keep it (the old
  // code dropped the fork's own info and under-counted totals).
  const forkEntry = doc.trace[0]; // user "edited" — the fork point
  assert.ok(forkEntry.branch); // export-side sanity
  const forkNode = dag.byId.get(`dsl:${forkEntry.node}`);
  assert.ok(forkNode);
  assert.deepEqual(forkNode.meta.branchInfo, {
    index: forkEntry.branch!.index,
    total: forkEntry.branch!.total,
  });
});

test("duplicate DSL node ids get suffixed keys — no node is dropped (#75)", () => {
  // Non-normalized exports repeat raw call ids across main/branch traces;
  // the IR must keep both entries distinct instead of letting cytoscape
  // silently deduplicate them.
  const doc: Parameters<typeof dslToDag>[0] = {
    version: 1,
    generator: "kagweb/session-dsl",
    trace: [
      {
        node: "turn:1",
        kind: "user",
        branches: [
          {
            selected: false,
            trace: [
              {
                node: "turn:1",
                kind: "assistant",
                calls: [{ node: "call:x:round:0", kind: "round", round_index: 0 }],
              },
            ],
          },
        ],
        calls: [{ node: "call:x:round:0", kind: "round", round_index: 0 }],
      },
    ],
  };
  const dag = dslToDag(doc);
  const ids = dag.nodes.map((n) => n.id);
  // Same DSL node id twice (main + branch) plus the same call id twice —
  // all four entries survive with distinct keys.
  assert.equal(ids.length, new Set(ids).size);
  assert.equal(ids.filter((id) => id.startsWith("dsl:call:x:round:0")).length, 2);
});

test("round-trip: export → parse → dslToDag keeps all call nodes materialized", () => {
  const text = JSON.stringify(dslFromMessages());
  const doc = parseSessionDsl(text);
  const dag = dslToDag(doc);
  const kinds = dag.nodes.map((n) => n.kind);
  assert.ok(kinds.includes("round"));
  assert.ok(kinds.includes("tool_call"));
  assert.ok(kinds.includes("retrieve") === false || kinds.includes("tool_call")); // retrieve merged under tool
});

test("empty document yields just the root", () => {
  const dag = dslToDag({ version: 1, generator: "kagweb/session-dsl", trace: [] });
  assert.deepEqual(
    dag.nodes.map((n) => n.kind),
    ["root"],
  );
  assert.equal(dag.edges.length, 0);
});

// ─── Module 17: scale guardrails (#77) ─────────────────────────────────────

test("parseSessionDsl rejects nesting deeper than the depth budget", () => {
  // Build a hostile 100-deep calls chain — hostile to the recursive
  // importer, not something a real export produces.
  let entry: Record<string, unknown> = { node: "deep", kind: "round", round_index: 0 };
  for (let i = 0; i < 100; i += 1) {
    entry = { node: `d${i}`, kind: "round", round_index: 0, calls: [entry] };
  }
  const doc = JSON.stringify({
    version: 1,
    generator: "kagweb/session-dsl",
    trace: [{ node: "t1", kind: "user" }, { node: "t2", kind: "assistant", calls: [entry] }],
  });
  assert.throws(() => parseSessionDsl(doc), /nests deeper than 64 levels/);
});

test("dslToDag truncates call trees at the node budget, keeping the message skeleton (#77)", () => {
  // 500 messages, 250 assistants × 10 calls each = 2500 call nodes > DAG_NODE_LIMIT.
  const trace: Array<{
    node: string;
    kind: "user" | "assistant";
    calls?: { node: string; kind: "round"; round_index: number }[];
  }> = Array.from({ length: 500 }, (_, i) => ({
    node: `turn:${i + 1}`,
    kind: i % 2 === 0 ? ("user" as const) : ("assistant" as const),
    calls:
      i % 2 === 0
        ? undefined
        : Array.from({ length: 10 }, (_, c) => ({
            node: `call:turn${i + 1}:round:${c}`,
            kind: "round" as const,
            round_index: c,
          })),
  }));
  const dag = dslToDag({ version: 1, generator: "kagweb/session-dsl", trace });
  // Message layer survives intact: root + 500 entries.
  assert.equal(dag.nodes.filter((n) => n.kind === "user" || n.kind === "assistant").length, 500);
  // Call nodes capped, remainder reported as dropped.
  assert.ok(dag.nodes.length <= 1 + 500 + 2000);
  assert.ok(dag.truncated);
  assert.ok(dag.truncated.dropped > 0);
  assert.equal(dag.truncated.limit, 2000);
});

test("dslToDag on a budget-sized document completes fast and untruncated", () => {
  // ~1500 call nodes — inside the budget, must parse + import quickly and
  // report no truncation (loose time bound guards against accidental O(n²)).
  const trace: Array<{ node: string; kind: string; calls?: unknown[] }> = [];
  for (let i = 0; i < 150; i += 1) {
    trace.push({ node: `u${i}`, kind: "user" });
    trace.push({
      node: `a${i}`,
      kind: "assistant",
      calls: Array.from({ length: 10 }, (_, c) => ({
        node: `call:a${i}:r${c}`,
        kind: "round",
        round_index: c,
      })),
    });
  }
  const text = JSON.stringify({ version: 1, generator: "kagweb/session-dsl", trace });
  const started = process.hrtime.bigint();
  const dag = dslToDag(parseSessionDsl(text));
  const ms = Number(process.hrtime.bigint() - started) / 1e6;
  assert.equal(dag.truncated, null);
  assert.equal(dag.nodes.filter((n) => n.kind === "round").length, 1500);
  assert.ok(ms < 2000, `dslToDag took ${ms.toFixed(0)}ms for 1500 call nodes`);
});
