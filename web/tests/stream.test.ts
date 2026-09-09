import test from "node:test";
import assert from "node:assert/strict";
import {
  collectNarrationCallIds,
  isNarrationMarker,
  recomputeAnswerContent,
} from "../lib/stream";
import type { StreamEvent } from "../features/chat/model/protocol";

function event(
  type: StreamEvent["type"],
  content: string,
  metadata: Record<string, unknown>,
): StreamEvent {
  return {
    type,
    source: "chat",
    stage: "responding",
    content,
    metadata,
    session_id: "session-1",
    turn_id: "turn-1",
    seq: 1,
    timestamp: 0,
  };
}

test("ordinary narration is removed from answer content", () => {
  const events = [
    event("content", "Searching.", {
      call_id: "round-1",
      call_kind: "agent_loop_round",
    }),
    event("progress", "", {
      call_id: "round-1",
      trace_kind: "call_status",
      call_state: "complete",
      call_role: "narration",
    }),
  ];

  assert.deepEqual([...collectNarrationCallIds(events)], ["round-1"]);
  assert.equal(isNarrationMarker(events[1]), true);
  assert.equal(recomputeAnswerContent(events), "");
});

test("clean prose surrounding a DSML call remains answer-visible", () => {
  const events = [
    event("content", "Great job! Choose the next topic.", {
      call_id: "round-dsml",
      call_kind: "agent_loop_round",
    }),
    event("progress", "", {
      call_id: "round-dsml",
      trace_kind: "call_status",
      call_state: "complete",
      call_role: "narration",
      answer_visible: true,
    }),
  ];

  assert.deepEqual([...collectNarrationCallIds(events)], []);
  assert.equal(isNarrationMarker(events[1]), false);
  assert.equal(
    recomputeAnswerContent(events),
    "Great job! Choose the next topic.",
  );
});

test("an agent-loop round marker keeps its prose in the answer", () => {
  // An external loop's prose is part of what it said — a CLI prints it inline —
  // so its round marker must not be read as narration, which would strip it
  // from the message bubble.
  const events = [
    event("content", "Let me look that up.", {
      call_id: "chat-round-1",
      call_kind: "agent_loop_round",
      trace_kind: "llm_chunk",
    }),
    event("progress", "", {
      call_id: "chat-round-1",
      trace_kind: "call_status",
      call_state: "complete",
      call_role: "round",
    }),
  ];

  assert.deepEqual([...collectNarrationCallIds(events)], []);
  assert.equal(isNarrationMarker(events[1]), false);
  assert.equal(recomputeAnswerContent(events), "Let me look that up.");
});

test("token-limit continuation replays the exact visible answer", () => {
  const events = [
    event("content", "Part one. ", {
      call_id: "round-part-1",
      call_kind: "agent_loop_round",
    }),
    event("progress", "", {
      call_id: "round-part-1",
      trace_kind: "call_status",
      call_state: "complete",
      call_role: "narration",
      answer_visible: true,
    }),
    event("content", "Part two.", {
      call_id: "round-part-2",
      call_kind: "agent_loop_round",
    }),
    event("progress", "", {
      call_id: "round-part-2",
      trace_kind: "call_status",
      call_state: "complete",
      call_role: "finish",
    }),
  ];

  assert.equal(recomputeAnswerContent(events), "Part one. Part two.");
});
