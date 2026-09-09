import type { StreamEvent } from "@/features/chat/model/protocol";

export interface TraceMetadata {
  call_id?: string;
  phase?: string;
  label?: string;
  call_kind?: string;
  trace_role?: string;
  trace_group?: string;
  trace_kind?: string;
  trace_id?: string;
  call_state?: string;
  call_role?: string;
  answer_visible?: boolean;
  absorbed_into_final?: boolean;
  step_id?: string;
  round?: number;
  query?: string;
  tool_name?: string;
  tool_source?: string;
  tool_provider?: string;
  /**
   * The engine a retrieval / search call actually ran on ("perplexity",
   * "lightrag", …). The retrieval pipeline puts it here on its progress
   * events; a tool's own `ToolResult.metadata` arrives under
   * `tool_metadata` instead (see below).
   */
  provider?: string;
  /**
   * A tool's own returned metadata. `tool_dispatch` nests `ToolResult.metadata`
   * under this key rather than merging it into the event metadata, so anything
   * a tool reports about itself is one level down.
   */
  tool_metadata?: Record<string, unknown>;
  progress_fraction?: number;
  elapsed_s?: number;
  /**
   * Backend-authoritative duration of a tool call, on its result event. The
   * client's timestamp span is only a fallback — it does not survive
   * reconnects and replay.
   */
  elapsed_ms?: number;
  /**
   * Token counters. They describe the whole backend pass, not the round that
   * carries them (KAGWeb's agent loop runs inside an external process), and
   * `usage_scope` says how to read them: `"pass"` counters are coherent,
   * `"cumulative"` ones are a running total mixed with a last-message value.
   */
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  usage_scope?: string;
  block_id?: string;
  trace_layer?: string;
  output_mode?: string;
  quality?: string;
  sources?: Array<Record<string, unknown>>;
  question_index?: number;
  total_questions?: number;
  qa_pair?: Record<string, unknown>;
  research_status_key?: string;
  topic_index?: number | string;
  topic_title?: string;
  report_part?: string;
  section_index?: number | string;
  section_count?: number | string;
  section_title?: string;
  subagent_channel?: string;
  subagent_kind?: string;
  subagent_name?: string;
  consult_index?: number;
  subagent_merge_id?: string;
}

export type ResearchStageId =
  | "understand"
  | "decompose"
  | "evidence"
  | "result";

export interface ResearchStageCard {
  id: ResearchStageId;
  title: string;
  hint: string;
  events: StreamEvent[];
}

export interface TraceItem {
  callId: string;
  events: StreamEvent[];
}

export type TraceDisplayItem =
  | { kind: "trace"; trace: TraceItem }
  | { kind: "step"; stepId: string; traces: TraceItem[] };

export type StreamingMode =
  | "reasoning"
  | "tool_using"
  | "responding"
  | "responded"
  | "planning"
  | "exploring"
  | "quizzing"
  | "reflecting";
