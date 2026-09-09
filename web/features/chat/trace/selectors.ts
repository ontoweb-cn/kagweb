import type { StreamEvent } from "@/features/chat/model/protocol";
import { formatProgressLabel, type ToolProvider } from "@/lib/trace-tools";
import type {
  StreamingMode,
  TraceDisplayItem,
  TraceItem,
  TraceMetadata,
} from "./model";

export function getTraceMeta(event: StreamEvent): TraceMetadata {
  return (event.metadata ?? {}) as TraceMetadata;
}

export function getTraceCallKind(events: StreamEvent[]): string {
  for (const event of events) {
    const value = getTraceMeta(event).call_kind;
    if (value) return String(value);
  }
  return "";
}

export function getTraceRole(events: StreamEvent[]): string {
  for (const event of events) {
    const value = getTraceMeta(event).trace_role;
    if (value) return String(value);
  }
  return "";
}

export function getTraceGroup(events: StreamEvent[]): string {
  for (const event of events) {
    const value = getTraceMeta(event).trace_group;
    if (value) return String(value);
  }
  return "";
}

/**
 * The engine a retrieval or search call ran on, if it reported one.
 *
 * Two places to look, because the backend surfaces it two ways: the
 * retrieval pipeline tags its own progress events at the top level, while a
 * tool's `ToolResult.metadata` is nested under `tool_metadata` by
 * `tool_dispatch`. Both are read here so callers do not have to know which
 * kind of row they are holding.
 */
export function getCallProvider(events: StreamEvent[]): string {
  for (const event of events) {
    const meta = getTraceMeta(event);
    const nested = meta.tool_metadata;
    const value =
      (nested && typeof nested.provider === "string" ? nested.provider : "") ||
      meta.provider ||
      "";
    const trimmed = String(value).trim();
    if (trimmed && trimmed !== "none") return trimmed;
  }
  return "";
}

export function getToolProvider(events: StreamEvent[]): ToolProvider | null {
  for (const event of events) {
    const meta = getTraceMeta(event);
    if (meta.tool_source) {
      return {
        source: String(meta.tool_source),
        id: String(meta.tool_provider || ""),
      };
    }
  }
  return null;
}

export function getLatestToolProgress(events: StreamEvent[]): string {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (
      event.type === "progress" &&
      getTraceMeta(event).trace_kind === "tool_progress" &&
      event.content.trim()
    ) {
      return formatProgressLabel(event.content.trim());
    }
  }
  return "";
}

export function isTracePending(events: StreamEvent[]): boolean {
  let running = false;
  let terminal = false;
  for (const event of events) {
    const state = String(getTraceMeta(event).call_state || "");
    if (state === "running") running = true;
    if (state === "complete" || state === "error") terminal = true;
  }
  return running && !terminal;
}

export function groupTraceEvents(events: StreamEvent[]): TraceItem[] {
  const groups = new Map<string, StreamEvent[]>();
  for (const event of events) {
    const callId = String(getTraceMeta(event).call_id || "");
    if (!callId) continue;
    const group = groups.get(callId);
    if (group) group.push(event);
    else groups.set(callId, [event]);
  }
  return [...groups].map(([callId, groupedEvents]) => ({
    callId,
    events: groupedEvents,
  }));
}

export function isChatLoopAnswerContent(event: StreamEvent): boolean {
  return (
    event.type === "content" &&
    getTraceMeta(event).call_kind === "agent_loop_round"
  );
}

const narrationCache = new WeakMap<StreamEvent[], boolean>();

export function isNarrationRound(events: StreamEvent[]): boolean {
  // Asked of the same group by the row body, `hasExpandableContent` and the
  // classification kernel on every render; the array identity is stable within
  // a render pass and events are immutable, so cache on it.
  const cached = narrationCache.get(events);
  if (cached !== undefined) return cached;
  const narration = events.some((event) => {
    const meta = getTraceMeta(event);
    return (
      meta.trace_kind === "call_status" &&
      meta.call_state === "complete" &&
      meta.call_role === "narration" &&
      meta.answer_visible !== true
    );
  });
  narrationCache.set(events, narration);
  return narration;
}

/** Whether a group renders anything at all — the kernel's skip predicate.
 *  Module-private: the rule belongs to `classifyTraceGroup`, and a second
 *  exported entry point is how the classification drifted before. */
function groupHasTraceSubstance(events: StreamEvent[]): boolean {
  const narration = isNarrationRound(events);
  return events.some((event) => {
    if (
      event.type === "tool_call" ||
      event.type === "tool_result" ||
      event.type === "error"
    ) {
      return true;
    }
    if (event.type === "thinking" || event.type === "observation") {
      return Boolean(event.content.trim());
    }
    if (event.type === "progress") {
      return (
        getTraceMeta(event).trace_kind !== "call_status" &&
        Boolean(event.content.trim())
      );
    }
    if (event.type === "content") {
      return (
        (narration || !isChatLoopAnswerContent(event)) &&
        Boolean(event.content.trim())
      );
    }
    return false;
  });
}

/**
 * The one classification rule for a call group, shared by the inline activity
 * trace, the session DAG and the DSL export so the three can never disagree.
 *
 *  - ``null`` → skip the group entirely (``llm_final_response``, absorbed into
 *    the final answer, or no trace substance);
 *  - otherwise the coarse kind, with tool → retrieve → round precedence
 *    (order matters: retrieval events reusing a tool's call_id must stay in
 *    that tool's group, so the tool check comes first) plus the distinct
 *    subagent markers inside a tool group.
 *
 * ``selectTraceDisplayItems`` consumes only the skip decision;
 * ``walkCallGroups`` (session DAG) consumes the full classification, and
 * ``dsl_export._classify_trace_group`` mirrors it for the Python side.
 */
export interface TraceGroupClass {
  kind: "tool_call" | "retrieve" | "round";
  /** Distinct (name, consultIndex) subagent markers inside a tool group. */
  subagents: Array<{ name: string; consultIndex: number | undefined }>;
}

export function classifyTraceGroup(
  events: StreamEvent[],
): TraceGroupClass | null {
  // The same group is classified by the display-item pass, the DAG and then
  // each rendered row; on the streaming hot path that is up to four full
  // scans per group per frame. The group's event array is stable for a render
  // pass and events are treated as immutable everywhere (the reducer appends
  // new arrays), so identity is a safe cache key.
  const cached = classificationCache.get(events);
  if (cached !== undefined) return cached;
  const classified = classifyTraceGroupUncached(events);
  classificationCache.set(events, classified);
  return classified;
}

const classificationCache = new WeakMap<
  StreamEvent[],
  TraceGroupClass | null
>();

function classifyTraceGroupUncached(
  events: StreamEvent[],
): TraceGroupClass | null {
  const kind = getTraceCallKind(events);
  if (kind === "llm_final_response") return null;
  if (events.some((event) => getTraceMeta(event).absorbed_into_final === true)) {
    return null;
  }
  if (!groupHasTraceSubstance(events)) return null;

  const group = getTraceGroup(events);
  const role = getTraceRole(events);
  // A group carrying a tool call is a tool call even when it arrived
  // untagged — a turn persisted before the trace contract, or a backend that
  // does not tag its events. The inline trace renders it as a tool row, so
  // the DAG and the DSL must classify it the same way.
  const untaggedToolCall =
    !kind && !group && events.some((event) => event.type === "tool_call");

  let coarse: TraceGroupClass["kind"];
  if (kind === "tool_planning" || group === "tool_call" || untaggedToolCall) {
    coarse = "tool_call";
  } else if (role === "retrieve") {
    coarse = "retrieve";
  } else {
    coarse = "round";
  }

  const subagents: TraceGroupClass["subagents"] = [];
  if (coarse === "tool_call") {
    const seen = new Set<string>();
    for (const event of events) {
      const meta = getTraceMeta(event);
      // A marker needs a real name, and its index counts only when it is a
      // finite number. `null` / `true` / `"1.5"` arrive from unvalidated JSON
      // (`StreamEvent.metadata` is `Record<string, unknown>`), and the Python
      // mirror (`dsl_export._subagent_markers`) applies exactly this rule —
      // otherwise the DAG would grow a phantom `"null"` subagent node that the
      // CLI/DSL export does not have.
      const name = meta.subagent_name;
      if (typeof name !== "string" || !name) continue;
      const rawIndex = meta.consult_index;
      const consultIndex =
        typeof rawIndex === "number" && Number.isFinite(rawIndex)
          ? rawIndex
          : undefined;
      const key = `${name}:${consultIndex ?? ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      subagents.push({ name, consultIndex });
    }
  }

  return { kind: coarse, subagents };
}

export function selectTraceDisplayItems(
  traceGroups: TraceItem[],
): TraceDisplayItem[] {
  const items: TraceDisplayItem[] = [];
  let currentStep: string | null = null;
  let stepTraces: TraceItem[] = [];
  const flush = () => {
    if (currentStep !== null && stepTraces.length) {
      items.push({ kind: "step", stepId: currentStep, traces: stepTraces });
    }
    currentStep = null;
    stepTraces = [];
  };

  for (const group of traceGroups) {
    if (!classifyTraceGroup(group.events)) continue;
    const meta = getTraceMeta(group.events[0]);
    const groupType = getTraceGroup(group.events);
    const stepId = meta.step_id ? String(meta.step_id) : "";
    const kind = getTraceCallKind(group.events);

    if (groupType === "react_round" && stepId) {
      if (currentStep === stepId) stepTraces.push(group);
      else {
        flush();
        currentStep = stepId;
        stepTraces = [group];
      }
    } else if (currentStep !== null && kind !== "llm_generation") {
      stepTraces.push(group);
    } else {
      flush();
      items.push({ kind: "trace", trace: group });
    }
  }
  flush();
  return items;
}

/** Whether an already-grouped trace renders anything — lets a caller that
 *  needs the groups for other reasons share one pass instead of grouping
 *  again per consumer. */
export function hasRenderableGroups(groups: TraceItem[]): boolean {
  return selectTraceDisplayItems(groups).length > 0;
}

export function hasRenderableCallTrace(events: StreamEvent[]): boolean {
  return hasRenderableGroups(groupTraceEvents(events));
}

export function detectStreamingMode(
  events: StreamEvent[],
  hasFinalContent: boolean,
  isStreaming: boolean,
): StreamingMode {
  if (!isStreaming) return "responded";
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const kind = getTraceMeta(event).call_kind || "";
    if (event.type === "tool_call") {
      if (event.stage === "exploring") return "exploring";
      if (event.stage === "quizzing") return "quizzing";
      return "tool_using";
    }
    if (event.type === "tool_result") continue;
    if (kind === "agent_loop_round") {
      return event.type === "content" ? "responding" : "exploring";
    }
    if (kind === "quiz_question_emitted") return "quizzing";
    if (kind === "tool_result_reflection") return "reflecting";
    if (event.type === "content" && kind === "llm_final_response") {
      if (event.stage === "exploring") return "exploring";
      return "responding";
    }
    if (kind === "llm_planning") return "planning";
    if (event.type === "thinking" && kind === "llm_reasoning") {
      if (event.stage === "exploring") return "exploring";
      if (event.stage === "quizzing") return "quizzing";
      return "reasoning";
    }
  }
  return hasFinalContent ? "responding" : "reasoning";
}
