import assert from "node:assert/strict";
import test from "node:test";
import type { StreamEvent } from "../features/chat/model/protocol";
import {
  classifyTraceGroup,
  detectStreamingMode,
  groupTraceEvents,
  hasRenderableCallTrace,
  isNarrationRound,
  isTracePending,
  selectTraceDisplayItems,
} from "../features/chat/trace/selectors";

function event(
  type: StreamEvent["type"],
  callId: string,
  metadata: Record<string, unknown> = {},
  content = "",
): StreamEvent {
  return {
    type,
    source: "chat",
    stage: "exploring",
    content,
    metadata: { call_id: callId, ...metadata },
    timestamp: 1,
  };
}

test("trace groups preserve first-seen call order and event order", () => {
  const groups = groupTraceEvents([
    event("thinking", "a", {}, "one"),
    event("tool_call", "b", { tool_name: "rag" }),
    event("progress", "a", {}, "two"),
  ]);
  assert.deepEqual(
    groups.map((group) => group.callId),
    ["a", "b"],
  );
  assert.deepEqual(
    groups[0].events.map((item) => item.content),
    ["one", "two"],
  );
});

test("classifyTraceGroup applies the skip rules and the tool→retrieve→round precedence", () => {
  // The one rule the inline trace, the session DAG and the DSL export share.
  assert.equal(
    classifyTraceGroup([
      event("content", "f", { call_kind: "llm_final_response" }, "Answer"),
    ]),
    null,
  );
  assert.equal(
    classifyTraceGroup([
      event("thinking", "a", { absorbed_into_final: true }, "Draft"),
    ]),
    null,
  );
  assert.equal(
    classifyTraceGroup([event("thinking", "e", {}, "")]),
    null,
  );

  // Order matters: a retrieval event reusing a tool's call_id stays in that
  // tool's group rather than becoming its own retrieve node.
  assert.equal(
    classifyTraceGroup([
      event("tool_call", "t1", { trace_group: "tool_call", tool_name: "rag" }, "rag"),
      event("progress", "t1", { trace_role: "retrieve", query: "q" }, "searching"),
    ])?.kind,
    "tool_call",
  );
  assert.equal(
    classifyTraceGroup([
      event("progress", "r1", { trace_role: "retrieve", query: "q" }, "searching"),
    ])?.kind,
    "retrieve",
  );
  assert.equal(
    classifyTraceGroup([
      event("thinking", "r2", { call_kind: "agent_loop_round" }, "pondering"),
    ])?.kind,
    "round",
  );
  // An untagged tool group — a turn persisted before the trace contract, or a
  // backend that does not tag its events — is still a tool call.
  assert.equal(
    classifyTraceGroup([
      event("tool_call", "legacy", { call_state: "running" }, "Bash"),
      event("tool_result", "legacy", { call_state: "complete" }, "files"),
    ])?.kind,
    "tool_call",
  );
});

test("classifyTraceGroup collects distinct subagent markers for tool groups only", () => {
  const classified = classifyTraceGroup([
    event("tool_call", "t1", { trace_group: "tool_call", tool_name: "consult_subagent" }, "go"),
    event("progress", "t1", { subagent_name: "math", consult_index: 1 }, "working"),
    event("progress", "t1", { subagent_name: "math", consult_index: 1 }, "still working"),
    event("progress", "t1", { subagent_name: "writer" }, "working"),
    event("tool_result", "t1", { trace_group: "tool_call" }, "ok"),
  ]);

  assert.deepEqual(classified?.subagents, [
    { name: "math", consultIndex: 1 },
    { name: "writer", consultIndex: undefined },
  ]);

  assert.deepEqual(
    classifyTraceGroup([
      event(
        "thinking",
        "r1",
        { call_kind: "agent_loop_round", subagent_name: "math" },
        "pondering",
      ),
    ])?.subagents,
    [],
  );
});

test("pending state ends only when its own call reports a terminal marker", () => {
  const running = [event("progress", "a", { call_state: "running" })];
  assert.equal(isTracePending(running), true);
  assert.equal(
    isTracePending([
      ...running,
      event("progress", "a", { call_state: "complete" }),
    ]),
    false,
  );
});

test("final answer and absorbed groups stay out of progressive trace disclosure", () => {
  const events = [
    event("content", "final", { call_kind: "llm_final_response" }, "Answer"),
    event("thinking", "absorbed", { absorbed_into_final: true }, "Draft"),
  ];
  assert.deepEqual(selectTraceDisplayItems(groupTraceEvents(events)), []);
  assert.equal(hasRenderableCallTrace(events), false);
});

test("narration before a tool remains visible while a finish answer does not", () => {
  const narration = [
    event(
      "content",
      "round-1",
      { call_kind: "agent_loop_round" },
      "I'll search.",
    ),
    event("progress", "round-1", {
      trace_kind: "call_status",
      call_state: "complete",
      call_role: "narration",
    }),
  ];
  const finish = [
    event(
      "content",
      "round-2",
      { call_kind: "agent_loop_round" },
      "Final answer",
    ),
    event("progress", "round-2", {
      trace_kind: "call_status",
      call_state: "complete",
      call_role: "finish",
    }),
  ];
  assert.equal(isNarrationRound(narration), true);
  assert.equal(hasRenderableCallTrace(narration), true);
  assert.equal(hasRenderableCallTrace(finish), false);
});

test("adjacent react rounds and their trailing trace collapse into one display step", () => {
  const events = [
    event(
      "thinking",
      "round-1",
      { trace_group: "react_round", step_id: "step-1" },
      "Plan",
    ),
    event(
      "tool_call",
      "round-2",
      { trace_group: "react_round", step_id: "step-1" },
      "Search",
    ),
    event("thinking", "standalone", {}, "Reflect"),
  ];

  const items = selectTraceDisplayItems(groupTraceEvents(events));

  assert.equal(items.length, 1);
  assert.equal(items[0].kind, "step");
  if (items[0].kind === "step") {
    assert.equal(items[0].stepId, "step-1");
    assert.deepEqual(
      items[0].traces.map((trace) => trace.callId),
      ["round-1", "round-2", "standalone"],
    );
  }
});

test("streaming mode follows the latest meaningful event", () => {
  assert.equal(
    detectStreamingMode([event("tool_call", "a")], false, true),
    "exploring",
  );
  assert.equal(
    detectStreamingMode(
      [event("thinking", "a", { call_kind: "tool_result_reflection" })],
      false,
      true,
    ),
    "reflecting",
  );
  assert.equal(detectStreamingMode([], true, false), "responded");
});

test("an agent-loop round and its tool call drive the live status label", () => {
  // The external loop's events are what the header used to fall through on,
  // leaving it on the "reasoning" default while tools were running.
  const chunk = event(
    "thinking",
    "chat-round-1",
    { call_kind: "agent_loop_round", trace_kind: "llm_chunk" },
    "pondering",
  );
  assert.equal(detectStreamingMode([chunk], false, true), "exploring");

  const call = {
    ...event(
      "tool_call",
      "chat-tool-1",
      { call_kind: "tool_planning", trace_group: "tool_call" },
      "exec",
    ),
    // The agent-loop family streams under the chat stage, not "exploring".
    stage: "responding",
  };
  assert.equal(detectStreamingMode([chunk, call], true, true), "tool_using");
});

test("an agent-loop round settles on its own marker and stays renderable", () => {
  // `call_role: "round"` is the intermediate close: it must settle the row
  // without being read as narration (which would strip the prose from the
  // answer) and without collapsing the trace as a final answer would.
  const events = [
    event("progress", "chat-round-1", { call_state: "running" }),
    event(
      "thinking",
      "chat-round-1",
      { call_kind: "agent_loop_round", trace_kind: "llm_chunk" },
      "pondering",
    ),
    event(
      "content",
      "chat-round-1",
      { call_kind: "agent_loop_round", trace_kind: "llm_chunk" },
      "Let me look.",
    ),
    event("progress", "chat-round-1", {
      trace_kind: "call_status",
      call_state: "complete",
      call_role: "round",
    }),
  ];

  assert.equal(isTracePending(events), false);
  assert.equal(isNarrationRound(events), false);
  assert.equal(hasRenderableCallTrace(events), true);
  const items = selectTraceDisplayItems(groupTraceEvents(events));
  assert.deepEqual(
    items.map((item) => item.kind),
    ["trace"],
  );
});
