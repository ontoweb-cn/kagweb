import assert from "node:assert/strict";
import test from "node:test";
import type { StreamEvent } from "../features/chat/model/protocol";
import { buildTurnSummary, collectTurnSummaryCounts } from "../lib/trace-summary";

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

function fakeT(key: string, params?: Record<string, unknown>): string {
  return params ? key.replace("{{n}}", String(params.n)) : key;
}

test("turn summary counts rounds, tool calls and sources", () => {
  const counts = collectTurnSummaryCounts([
    event("thinking", "r1", { call_kind: "agent_loop_round" }, "hmm"),
    event("tool_call", "t1", { trace_group: "tool_call", tool_name: "rag" }),
    event("tool_result", "t1", {}, "hits"),
    event("thinking", "r2", { call_kind: "agent_loop_round" }, "more"),
    event("tool_call", "t2", { trace_group: "tool_call", tool_name: "web_search" }),
    event("tool_result", "t2", {}, "results"),
    event("sources", null, { sources: [{ url: "a" }, { url: "b" }, { url: "c" }] }),
  ]);
  assert.deepEqual(counts, { rounds: 2, toolCalls: 2, sources: 3 });
});

test("turn summary falls back to retrieve-group count without a SOURCES event", () => {
  const counts = collectTurnSummaryCounts([
    event("progress", "seed", { trace_role: "retrieve" }, "querying"),
    event("tool_result", "seed", { trace_role: "retrieve" }, "chunks"),
  ]);
  assert.deepEqual(counts, { rounds: 0, toolCalls: 0, sources: 1 });
});

test("turn summary is null when the trace has no renderable groups", () => {
  assert.equal(collectTurnSummaryCounts([]), null);
  assert.equal(
    collectTurnSummaryCounts([
      event("content", "final", { call_kind: "llm_final_response" }, "answer"),
    ]),
    null,
  );
});

test("buildTurnSummary omits zero segments and joins the rest", () => {
  assert.equal(
    buildTurnSummary(
      [event("thinking", "r1", { call_kind: "agent_loop_round" }, "hmm")],
      fakeT,
    ),
    "1 rounds",
  );
  assert.equal(
    buildTurnSummary(
      [
        event("thinking", "r1", { call_kind: "agent_loop_round" }, "hmm"),
        event("tool_call", "t1", { trace_group: "tool_call", tool_name: "rag" }),
        event("tool_result", "t1", {}, "hits"),
        event("sources", null, { sources: [{ url: "a" }] }),
      ],
      fakeT,
    ),
    "1 rounds · 1 tool calls · 1 sources",
  );
  // Only a final response: no summary line at all.
  assert.equal(
    buildTurnSummary(
      [event("content", "final", { call_kind: "llm_final_response" }, "answer")],
      fakeT,
    ),
    null,
  );
});
