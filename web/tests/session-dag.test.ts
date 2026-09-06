import assert from "node:assert/strict";
import test from "node:test";
import {
  computeSessionDag,
  searchDagNodes,
  countCallTree,
} from "../features/chat/dag/aggregate";
import { messageNodeKey } from "../features/chat/dag/model";
import { assistantMsg, ev, userMsg } from "./session-dag-fixtures";

test("empty input yields only the root node", () => {
  const dag = computeSessionDag({ messages: [] });
  assert.deepEqual(
    dag.nodes.map((n) => n.id),
    ["root"],
  );
  assert.equal(dag.edges.length, 0);
  assert.equal(dag.expandable.size, 0);
});

test("plain user→assistant chain without events is not expandable", () => {
  const dag = computeSessionDag({
    messages: [userMsg(1, "hi"), assistantMsg(2, [], 1)],
  });
  assert.deepEqual(
    dag.nodes.map((n) => n.kind),
    ["root", "user", "assistant"],
  );
  assert.equal(dag.edges.length, 2);
  assert.equal(dag.expandable.size, 0);
  assert.equal(dag.nodes.find((n) => n.kind === "assistant")?.meta.childCount, 0);
});

test("collapsed assistant reports childCount and is expandable", () => {
  const events = [
    ev("thinking", { call_id: "r1", call_kind: "agent_loop_round", trace_group: "stage" }, "plan"),
    ev("tool_call", { call_id: "t1", trace_group: "tool_call", tool_name: "rag" }, "rag"),
    ev("tool_result", { call_id: "t1", trace_group: "tool_call" }, "ok"),
    ev("tool_call", { call_id: "t2", trace_group: "tool_call", tool_name: "web_search" }, "search"),
    ev("tool_result", { call_id: "t2", trace_group: "tool_call" }, "ok"),
  ];
  const dag = computeSessionDag({ messages: [assistantMsg(1, events)] });
  const assistant = dag.nodes.find((n) => n.kind === "assistant");
  assert.ok(assistant);
  assert.equal(assistant.meta.childCount, 3); // round + 2 tools
  assert.ok(dag.expandable.has(assistant.id));
  assert.equal(dag.nodes.length, 2); // root + assistant only (collapsed)
});

test("expanding materializes round → tool hierarchy with drilldown edges", () => {
  const events = [
    ev("thinking", { call_id: "r1", call_kind: "agent_loop_round", trace_group: "stage" }, "plan"),
    ev("tool_call", { call_id: "t1", trace_group: "tool_call", tool_name: "rag" }, "rag"),
    ev("tool_result", { call_id: "t1", trace_group: "tool_call" }, "ok"),
  ];
  const dag = computeSessionDag(
    { messages: [assistantMsg(1, events)] },
    new Set(["msg:1"]),
  );
  const kinds = dag.nodes.map((n) => n.kind);
  assert.deepEqual(kinds, ["root", "assistant", "round", "tool_call"]);
  const round = dag.nodes.find((n) => n.kind === "round");
  const tool = dag.nodes.find((n) => n.kind === "tool_call");
  assert.ok(round && tool);
  assert.equal(tool.parentId, round.id);
  assert.equal(round.parentId, "msg:1");
  assert.equal(dag.edges.filter((e) => e.kind === "drilldown").length, 2);
  // roundIndex is 0-based within the message
  assert.equal(round.meta.roundIndex, 0);
  // tool meta carries the tool name
  assert.equal(tool.meta.toolName, "rag");
});

test("subagent events sharing the tool call_id become subagent child nodes", () => {
  const events = [
    ev("tool_call", { call_id: "t1", trace_group: "tool_call", tool_name: "consult_subagent" }, "go"),
    ev("progress", { call_id: "t1", subagent_name: "math", consult_index: 1 }, "working"),
    ev("progress", { call_id: "t1", subagent_name: "writer", consult_index: 2 }, "working"),
    ev("progress", { call_id: "t1", subagent_name: "math", consult_index: 1 }, "still working"),
    ev("tool_result", { call_id: "t1", trace_group: "tool_call" }, "ok"),
  ];
  const dag = computeSessionDag(
    { messages: [assistantMsg(1, events)] },
    new Set(["msg:1"]),
  );
  const subs = dag.nodes.filter((n) => n.kind === "subagent");
  assert.equal(subs.length, 2); // distinct (name, consultIndex) pairs
  const tool = dag.nodes.find((n) => n.kind === "tool_call");
  assert.ok(subs.every((s) => s.parentId === tool?.id));
  // collapsed count includes subagents
  assert.equal(countCallTree(assistantMsg(1, events)), 3);
});

test("retrieve events reusing the tool call_id do NOT form separate nodes", () => {
  // rag tool internally derives retrieval events on the same call_id — the
  // group merges into the tool node and the query lands in its meta.
  const events = [
    ev("tool_call", { call_id: "t1", trace_group: "tool_call", tool_name: "rag" }, "rag"),
    ev("progress", { call_id: "t1", trace_role: "retrieve", query: "fourier" }, "searching"),
    ev("tool_result", { call_id: "t1", trace_group: "tool_call" }, "ok"),
  ];
  const dag = computeSessionDag(
    { messages: [assistantMsg(1, events)] },
    new Set(["msg:1"]),
  );
  assert.equal(dag.nodes.filter((n) => n.kind === "retrieve").length, 0);
  const tool = dag.nodes.find((n) => n.kind === "tool_call");
  assert.equal(tool?.meta.query, "fourier");
});

test("KB-prefetch seed retrieve (independent call_id) hangs off the message", () => {
  const events = [
    ev("progress", { call_id: "seed1", trace_role: "retrieve", query: "prefetch" }, "seeding"),
    ev("tool_call", { call_id: "t1", trace_group: "tool_call", tool_name: "rag" }, "rag"),
    ev("tool_result", { call_id: "t1", trace_group: "tool_call" }, "ok"),
  ];
  const dag = computeSessionDag(
    { messages: [assistantMsg(1, events)] },
    new Set(["msg:1"]),
  );
  const retrieve = dag.nodes.find((n) => n.kind === "retrieve");
  assert.ok(retrieve);
  assert.equal(retrieve.parentId, "msg:1"); // no round yet → message-level
  assert.equal(retrieve.meta.query, "prefetch");
});

test("llm_final_response and absorbed groups are skipped", () => {
  const events = [
    ev("thinking", { call_id: "r1", call_kind: "agent_loop_round", trace_group: "stage" }, "plan"),
    ev("content", { call_id: "f1", call_kind: "llm_final_response" }, "final"),
    ev("thinking", { call_id: "x1", absorbed_into_final: true }, "absorbed"),
  ];
  const dag = computeSessionDag(
    { messages: [assistantMsg(1, events)] },
    new Set(["msg:1"]),
  );
  assert.equal(dag.nodes.filter((n) => n.kind === "round").length, 1);
  assert.equal(dag.nodes.length, 3); // root + assistant + one round
});

test("interleaved call_ids keep first-seen group order", () => {
  const events = [
    ev("tool_call", { call_id: "b", trace_group: "tool_call", tool_name: "b" }, "b"),
    ev("thinking", { call_id: "a", call_kind: "agent_loop_round", trace_group: "stage" }, "a"),
    ev("tool_result", { call_id: "b", trace_group: "tool_call" }, "done b"),
    ev("tool_call", { call_id: "c", trace_group: "tool_call", tool_name: "c" }, "c"),
  ];
  const dag = computeSessionDag(
    { messages: [assistantMsg(1, events)] },
    new Set(["msg:1"]),
  );
  const kinds = dag.nodes.slice(2).map((n) => n.kind);
  assert.deepEqual(kinds, ["tool_call", "round", "tool_call"]);
});

test("branchy session: only the selected path enters the DAG", () => {
  // 1(user) → 2(assistant); edit of 1 creates 3(user) → 4(assistant).
  const withParents = [
    { ...userMsg(1, "original"), parentMessageId: null },
    { ...assistantMsg(2, []), parentMessageId: 1 },
    { ...userMsg(3, "edited"), parentMessageId: null },
    { ...assistantMsg(4, []), parentMessageId: 3 },
  ];
  // No selection → latest child wins (3, 4).
  const dagLatest = computeSessionDag({ messages: withParents });
  assert.deepEqual(
    dagLatest.nodes.map((n) => n.meta.messageId),
    [undefined, 3, 4],
  );
  // Select branch 1 → visible path is 1, 2.
  const dagSelected = computeSessionDag({
    messages: withParents,
    selectedBranches: { null: 1 },
  });
  assert.deepEqual(
    dagSelected.nodes.map((n) => n.meta.messageId),
    [undefined, 1, 2],
  );
  // Branch info is reported on the fork-point user nodes.
  const fork = dagSelected.nodes.find((n) => n.meta.messageId === 1);
  assert.deepEqual(fork?.meta.branchInfo, { total: 2, index: 1 });
});

test("optimistic negative id gets an index-based CSS-safe node key", () => {
  const dag = computeSessionDag({
    messages: [userMsg(-1725000000000, "in flight")],
  });
  const key = dag.nodes.find((n) => n.kind === "user")?.id;
  assert.equal(key, "msg:i0");
  assert.equal(messageNodeKey(-5, 2), "msg:i2");
  assert.equal(messageNodeKey(7, 2), "msg:7");
});

test("countCallTree equals the number of materialized nodes", () => {
  const events = [
    ev("progress", { call_id: "seed1", trace_role: "retrieve", query: "q" }, "seeding"),
    ev("thinking", { call_id: "r1", call_kind: "agent_loop_round", trace_group: "stage" }, "plan"),
    ev("tool_call", { call_id: "t1", trace_group: "tool_call", tool_name: "consult_subagent" }, "go"),
    ev("progress", { call_id: "t1", subagent_name: "math", consult_index: 1 }, "working"),
    ev("tool_result", { call_id: "t1", trace_group: "tool_call" }, "ok"),
    ev("tool_call", { call_id: "t2", trace_group: "tool_call", tool_name: "rag" }, "rag"),
    ev("tool_result", { call_id: "t2", trace_group: "tool_call" }, "ok"),
  ];
  const message = assistantMsg(1, events);
  const collapsed = computeSessionDag({ messages: [message] });
  const expanded = computeSessionDag(
    { messages: [message] },
    new Set(["msg:1"]),
  );
  const callNodes = expanded.nodes.filter((n) =>
    ["round", "tool_call", "retrieve", "subagent"].includes(n.kind),
  );
  assert.equal(callNodes.length, countCallTree(message));
  assert.equal(
    collapsed.nodes.find((n) => n.kind === "assistant")?.meta.childCount,
    callNodes.length,
  );
});

// --- module 12: deep_research two-turn merge -------------------------------

function researchPair() {
  const outlineEvents = [
    ev("thinking", { call_id: "r1", call_kind: "agent_loop_round", trace_group: "stage" }, "rephrase"),
    ev("result", { outline_preview: true }, "outline"),
  ];
  const followupEvents = [
    ev("thinking", { call_id: "r2", call_kind: "agent_loop_round", trace_group: "stage" }, "search", 2, "researching"),
    ev("content", { call_id: "r2" }, "report body", 3, "reporting"),
  ];
  return [
    { ...userMsg(1, "research this"), parentMessageId: null },
    { ...assistantMsg(2, outlineEvents, 1, "deep_research"), parentMessageId: 1 },
    { ...userMsg(3, "(hidden start-research ack)"), parentMessageId: 2 },
    { ...assistantMsg(4, followupEvents, 3, "deep_research"), parentMessageId: 3 },
    { ...userMsg(5, "thanks"), parentMessageId: 4 },
  ];
}

test("deep_research pair merges: followup node dropped, events spliced (module 12)", () => {
  const dag = computeSessionDag({ messages: researchPair() });
  const kinds = dag.nodes.map((n) => n.kind);
  // followup assistant (msg 4) is gone; only one deep_research assistant remains
  assert.equal(kinds.filter((k) => k === "assistant").length, 1);
  assert.ok(!dag.byId.has(messageNodeKey(4, 3)));
  // merged events: childCount covers both turns' rounds
  const merged = dag.nodes.find((n) => n.kind === "assistant");
  assert.ok(merged && merged.meta.childCount >= 2);
  // chain stays connected: the followup assistant is dropped but the ack
  // user (msg 3) remains, so user5 chains onto it
  const lastUser = dag.nodes.find((n) => n.meta.messageId === 5);
  assert.ok(lastUser);
  assert.equal(lastUser.parentId, messageNodeKey(3, 2));
});

test("deep_research pair without confirmed followup stays two nodes", () => {
  const messages = [
    { ...userMsg(1, "research this"), parentMessageId: null },
    {
      ...assistantMsg(2, [ev("result", { outline_preview: true }, "outline")], 1, "deep_research"),
      parentMessageId: 1,
    },
    { ...userMsg(3, "ack"), parentMessageId: 2 },
    {
      // followup candidate without researching/reporting stage → not a pair
      ...assistantMsg(4, [ev("thinking", { call_id: "x" }, "nope")], 3, "deep_research"),
      parentMessageId: 3,
    },
  ];
  const dag = computeSessionDag({ messages });
  assert.equal(dag.nodes.filter((n) => n.kind === "assistant").length, 2);
});

test("DSL export reflects the merged deep_research view (#64)", async () => {
  const { buildSessionDslDocument } = await import("../features/chat/dag/dsl");
  const doc = buildSessionDslDocument(
    { messages: researchPair() },
    { stable: true, normalizeIds: true },
  );
  // 4 turns: user → merged assistant → ack user → user (followup assistant absent)
  assert.deepEqual(
    doc.trace.map((t) => t.kind),
    ["user", "assistant", "user", "user"],
  );
  // merged call tree carries both turns' rounds under the one assistant
  const calls = doc.trace[1].calls ?? [];
  assert.equal(calls.length, 2); // r1 (outline turn) + r2 (research turn)
  const second = calls[1];
  assert.equal(second.kind, "round");
  // merged events preserved across the splice: the followup's round has content
  assert.ok(JSON.stringify(second).length > 0);
});

// ─── Module 17: render-budget truncation (#77) ─────────────────────────────

test("computeSessionDag with truncate skips whole call trees past the budget (#77)", () => {
  // 500 assistants × 10 tool calls each = 5000 call nodes, far past the
  // DAG_NODE_LIMIT render budget.
  const messages = [];
  for (let i = 0; i < 500; i += 1) {
    messages.push({ ...userMsg(i * 2 + 1, `q${i}`), parentMessageId: i === 0 ? null : i * 2 });
    messages.push(
      {
        ...assistantMsg(i * 2 + 2, [
          ...Array.from({ length: 10 }, (_, c) =>
            ev("tool_call", { call_id: `t${i}-${c}`, trace_group: "tool_call", tool_name: "rag" }, "call", c),
          ),
        ]),
        parentMessageId: i * 2 + 1,
      },
    );
  }
  const expanded = new Set(
    messages
      .filter((m) => m.role === "assistant")
      .map((m) => `msg:${(m as { id: number }).id}`),
  );
  const dag = computeSessionDag(
    { messages },
    expanded,
    { truncate: true },
  );
  // Message layer complete: root + 1000 messages.
  assert.equal(dag.nodes.filter((n) => n.kind === "user" || n.kind === "assistant").length, 1000);
  assert.ok(dag.nodes.length <= 1 + 1000 + 2000);
  assert.ok(dag.truncated);
  assert.equal(dag.truncated.limit, 2000);
  // And without the flag (export path, #33): everything materializes.
  const full = computeSessionDag({ messages }, expanded);
  assert.equal(full.truncated, null);
  assert.equal(full.nodes.filter((n) => n.kind === "tool_call").length, 5000);
});

// ─── Module 19: trace search (#79) ──────────────────────────────────────────

test("searchDagNodes matches display fields case-insensitively (#79)", () => {
  const messages = [
    { ...userMsg(1, "What is Fourier?"), parentMessageId: null },
    {
      ...assistantMsg(2, [
        ev("tool_call", { call_id: "t1", trace_group: "tool_call", tool_name: "rag" }, "search", 2),
        ev("progress", { call_id: "t1", trace_role: "retrieve", query: "fourier transform" }, "hit", 3),
      ]),
      parentMessageId: 1,
    },
  ];
  // Expand the call tree so retrieve/tool nodes (and their query/toolName
  // fields) exist for the search to hit.
  const dag = computeSessionDag({ messages }, null, { expandAll: true });
  // "fourier" hits the user preview AND the retrieve query.
  const hits = searchDagNodes(dag, "FOURIER");
  assert.ok(hits.size >= 2);
  // Tool names match.
  assert.ok(searchDagNodes(dag, "rag").size >= 1);
  // Empty / whitespace query = no filter.
  assert.equal(searchDagNodes(dag, "   ").size, 0);
  // No match → empty set (panel shows "0 matches").
  assert.equal(searchDagNodes(dag, "nonexistent-term").size, 0);
});
