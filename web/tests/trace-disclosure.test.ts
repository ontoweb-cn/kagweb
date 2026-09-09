import assert from "node:assert/strict";
import test from "node:test";
import type { StreamEvent } from "../features/chat/model/protocol";
import { collectRoundFacts } from "../lib/trace-summary";
import { getTraceMode, setTraceMode, subscribeTraceMode } from "../lib/trace-mode";

function event(
  type: StreamEvent["type"],
  callId: string | null,
  metadata: Record<string, unknown> = {},
  content = "",
): StreamEvent {
  const meta: Record<string, unknown> = { ...metadata };
  if (callId) meta.call_id = callId;
  return {
    type,
    source: "chat",
    stage: "exploring",
    content,
    metadata: meta,
    timestamp: 1,
  };
}

/* ---------------- collectRoundFacts ---------------- */

test("round facts attribute tools to their preceding round", () => {
  const facts = collectRoundFacts([
    event("thinking", "r1", { call_kind: "agent_loop_round" }, "first thought"),
    event("tool_call", "t1", { trace_group: "tool_call", tool_name: "rag", args: { query: "fourier" } }),
    event("tool_result", "t1", {}, "chunks"),
    event("thinking", "r2", { call_kind: "agent_loop_round" }, "second thought"),
    event("tool_call", "t2", { trace_group: "tool_call", tool_name: "web_search" }),
    event("tool_result", "t2", {}, "results"),
  ]);
  assert.equal(facts.length, 2);
  assert.equal(facts[0].roundIndex, 0);
  assert.deepEqual(
    facts[0].tools.map((tool) => tool.name),
    ["rag"],
  );
  assert.equal(facts[0].tools[0].args?.query, "fourier");
  assert.equal(facts[1].roundIndex, 1);
  assert.equal(facts[1].tools[0].name, "web_search");
  assert.equal(facts[0].intentText, "first thought");
});

test("groups before the first round collect under prefetch (roundIndex -1)", () => {
  const facts = collectRoundFacts([
    event("progress", "seed", { trace_role: "retrieve", query: "fourier" }, "querying"),
    event("tool_result", "seed", { trace_role: "retrieve" }, "chunks"),
    event("thinking", "r1", { call_kind: "agent_loop_round" }, "thought"),
  ]);
  assert.equal(facts.length, 2);
  assert.equal(facts[0].roundIndex, -1);
  assert.equal(facts[0].tools.length, 1);
  // The seed's query (not a tool verb) is the informative bit.
  assert.equal(facts[0].tools[0].name, "retrieval");
  assert.equal(facts[0].tools[0].args?.query, "fourier");
  assert.equal(facts[1].roundIndex, 0);
});

test("intent prefers thinking text and falls back to a narration round's content", () => {
  const withThinking = collectRoundFacts([
    event("thinking", "r1", { call_kind: "agent_loop_round" }, "the thought"),
    event("content", "r1", { call_kind: "agent_loop_round" }, "the narration"),
  ]);
  assert.equal(withThinking[0].intentText, "the thought");

  const contentOnly = collectRoundFacts([
    // Without the narration completion marker an agent-loop content event is
    // answer content and the kernel (correctly) drops the group — narration
    // rounds always carry the marker.
    event("progress", "r1", {
      trace_kind: "call_status",
      call_state: "complete",
      call_role: "narration",
    }),
    event("content", "r1", { call_kind: "agent_loop_round" }, "narration only"),
  ]);
  assert.equal(contentOnly[0].intentText, "narration only");
});

test("answer prose never becomes a round's self-report preview", () => {
  // A round whose only substance is an error, with answer-grade content and
  // no narration marker: the preview must stay empty rather than quoting the
  // answer that is already rendered below the trace.
  const facts = collectRoundFacts([
    event("error", "r1", { call_kind: "agent_loop_round" }, "boom"),
    event("content", "r1", { call_kind: "agent_loop_round" }, "the final answer"),
  ]);
  assert.equal(facts.length, 1);
  assert.equal(facts[0].roundIndex, 0);
  assert.equal(facts[0].intentText, undefined);
});

test("long intents clip at 140 chars and errors mark their owner round", () => {
  const long = "x".repeat(200);
  const facts = collectRoundFacts([
    event("thinking", "r1", { call_kind: "agent_loop_round" }, long),
    event("tool_call", "t1", { trace_group: "tool_call", tool_name: "rag" }),
    event("error", "t1", {}, "boom"),
  ]);
  assert.equal(facts[0].intentText?.length, 141); // 140 + ellipsis
  assert.equal(facts[0].hasError, true);
});

test("round facts are empty without renderable groups", () => {
  assert.deepEqual(
    collectRoundFacts([event("content", "f", { call_kind: "llm_final_response" }, "answer")]),
    [],
  );
});

/* ---------------- trace mode ---------------- */

test("trace mode defaults to learner, switches and notifies", () => {
  assert.equal(getTraceMode(), "learner");
  let notified = 0;
  const unsubscribe = subscribeTraceMode(() => {
    notified += 1;
  });
  setTraceMode("expert");
  assert.equal(getTraceMode(), "expert");
  assert.equal(notified, 1);
  setTraceMode("learner");
  assert.equal(getTraceMode(), "learner");
  assert.equal(notified, 2);
  unsubscribe();
  setTraceMode("expert");
  assert.equal(notified, 2);
  setTraceMode("learner");
});
