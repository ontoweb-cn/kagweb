import type { StreamEvent } from "../features/chat/model/protocol";
import type { DagMessage } from "../features/chat/dag/aggregate";

/** Minimal stream-event fixture builder for DAG tests. */
export function ev(
  type: StreamEvent["type"],
  metadata: Record<string, unknown> = {},
  content = "",
  timestamp = 1,
  stage: StreamEvent["stage"] = "exploring",
): StreamEvent {
  return {
    type,
    source: "chat",
    stage,
    content,
    metadata,
    timestamp,
  };
}

export function userMsg(
  id: number | null,
  content: string,
  parentMessageId: number | null = null,
): DagMessage {
  return {
    id: id ?? undefined,
    role: "user",
    content,
    parentMessageId,
  };
}

export function assistantMsg(
  id: number | null,
  events: StreamEvent[],
  parentMessageId: number | null = null,
  capability?: string,
): DagMessage {
  return {
    id: id ?? undefined,
    role: "assistant",
    content: "",
    events,
    parentMessageId,
    capability,
  };
}
