/**
 * Turn summary line — the observation-grade digest shown once a turn
 * settles, plus the per-round facts behind the tier-1 disclosure lines.
 *
 * Layered-materials contract: the summary counts *system observations only*
 * (kernel-classified call groups and the turn-end SOURCES event). Narration
 * and thinking text are model self-report and deliberately excluded here; the
 * per-round intent preview is the one self-report that may appear, and the
 * render layer must mark it as such.
 */
import type { StreamEvent } from "@/features/chat/model/protocol";
import {
  classifyTraceGroup,
  getTraceMeta,
  groupTraceEvents,
  isNarrationRound,
  type TraceGroupClass,
} from "@/features/chat/trace/selectors";
import { clipPreview } from "@/lib/trace-text";

/**
 * Groups plus their classification, computed once per ``events`` array
 * identity. Both the settle counts and the tier-1 facts read this, and the
 * events array is stable within a render pass (the caller memoizes it), so
 * one classification serves every consumer instead of three.
 */
interface ClassifiedGroup {
  kind: TraceGroupClass["kind"];
  events: StreamEvent[];
}

const classifiedCache = new WeakMap<StreamEvent[], ClassifiedGroup[]>();

function classifiedGroups(events: StreamEvent[]): ClassifiedGroup[] {
  const cached = classifiedCache.get(events);
  if (cached) return cached;
  const result: ClassifiedGroup[] = [];
  for (const { events: groupEvents } of groupTraceEvents(events)) {
    const group = classifyTraceGroup(groupEvents);
    if (group) result.push({ kind: group.kind, events: groupEvents });
  }
  classifiedCache.set(events, result);
  return result;
}

/** Minimal `t` shape so the builder stays testable without i18next. */
export type TraceSummaryT = (key: string, params?: Record<string, unknown>) => string;

export interface TurnSummaryCounts {
  rounds: number;
  toolCalls: number;
  sources: number;
}

/**
 * Count the observation-grade facts of a turn: rounds, tool calls and
 * citation sources. Returns null when the trace has no renderable groups —
 * the caller then shows no summary line at all.
 */
export function collectTurnSummaryCounts(events: StreamEvent[]): TurnSummaryCounts | null {
  let rounds = 0;
  let toolCalls = 0;
  let retrieveGroups = 0;
  const classified = classifiedGroups(events);
  for (const group of classified) {
    if (group.kind === "round") rounds += 1;
    else if (group.kind === "tool_call") toolCalls += 1;
    else retrieveGroups += 1;
  }
  if (!classified.length) return null;

  // The turn-end SOURCES event is the authoritative citation count; the
  // retrieve-group count is only the fallback for capabilities that never
  // emit it. KAGWeb's agent-loop bridge emits no SOURCES event today, so this
  // normally stays at the retrieve-group count (often zero) — the summary
  // omits zero parts rather than inventing a number.
  let sources = retrieveGroups;
  for (const event of events) {
    if (event.type !== "sources") continue;
    const raw = getTraceMeta(event).sources;
    if (Array.isArray(raw) && raw.length) {
      sources = raw.length;
      break;
    }
  }
  return { rounds, toolCalls, sources };
}

/** Build the one-line settle summary, e.g. "5 rounds · 3 tool calls · 2 sources". */
export function buildTurnSummary(events: StreamEvent[], t: TraceSummaryT): string | null {
  const counts = collectTurnSummaryCounts(events);
  if (!counts) return null;
  const parts: string[] = [];
  if (counts.rounds > 0) parts.push(t("{{n}} rounds", { n: counts.rounds }));
  if (counts.toolCalls > 0) parts.push(t("{{n}} tool calls", { n: counts.toolCalls }));
  if (counts.sources > 0) parts.push(t("{{n}} sources", { n: counts.sources }));
  return parts.length ? parts.join(" · ") : null;
}

/**
 * Per-round facts for the tier-1 disclosure lines.
 *
 * Layered-materials contract: `tools` are system observations
 * (kernel-classified tool groups attributed to their preceding round, same
 * pointer walk as the DAG's materializeCalls); `intentText` is the round's own
 * narration/thinking preview — model self-report that the render layer MUST
 * mark as such. Groups before the first round (KB prefetch seeds) collect
 * under `roundIndex: -1`.
 */
export interface RoundFacts {
  roundIndex: number;
  tools: Array<{ name: string; args?: Record<string, unknown> }>;
  /** Self-report preview of the round's narration/thinking (140 chars). */
  intentText?: string;
  hasError: boolean;
}

function clipRoundIntent(text: string): string | undefined {
  return clipPreview(text) || undefined;
}

function roundIntentText(events: StreamEvent[]): string | undefined {
  // The round's self-report: thinking chunks first, then — only for a
  // narration round — the round's own prose. An agent-loop round whose
  // content is the answer (`isChatLoopAnswerContent`) must never be quoted
  // here: it is already rendered below, and labelling it "model self-report"
  // would be both duplication and a lie.
  const parts: string[] = [];
  for (const event of events) {
    if (event.type === "thinking" && event.content.trim()) parts.push(event.content.trim());
  }
  if (!parts.length && isNarrationRound(events)) {
    for (const event of events) {
      if (event.type === "content" && event.content.trim()) parts.push(event.content.trim());
    }
  }
  return clipRoundIntent(parts.join(" "));
}

function toolNameOf(events: StreamEvent[]): string {
  for (const event of events) {
    if (event.type !== "tool_call") continue;
    const meta = getTraceMeta(event);
    const name = String(meta.tool_name || event.content || "").trim();
    if (name) return name;
  }
  return "retrieval";
}

function argsOf(events: StreamEvent[]): Record<string, unknown> | undefined {
  for (const event of events) {
    if (event.type !== "tool_call") continue;
    const args = (event.metadata as Record<string, unknown> | undefined)?.args;
    if (args && typeof args === "object") return args as Record<string, unknown>;
  }
  return undefined;
}

function groupHasError(events: StreamEvent[]): boolean {
  return events.some(
    (event) => event.type === "error" && event.content.trim().length > 0,
  );
}

/** The query a standalone retrieve group (KB prefetch seed) ran with. */
function retrieveQueryOf(events: StreamEvent[]): string | undefined {
  for (const event of events) {
    const query = getTraceMeta(event).query;
    if (typeof query === "string" && query.trim()) return clipRoundIntent(query);
  }
  return undefined;
}

export function collectRoundFacts(events: StreamEvent[]): RoundFacts[] {
  const classified = classifiedGroups(events);

  const preRound: RoundFacts = { roundIndex: -1, tools: [], hasError: false };
  const rounds: RoundFacts[] = [];
  let current: RoundFacts | null = null;
  const owner = () => current ?? preRound;

  for (const group of classified) {
    if (group.kind === "round") {
      current = {
        roundIndex: rounds.length,
        tools: [],
        intentText: roundIntentText(group.events),
        hasError: false,
      };
      rounds.push(current);
    } else if (group.kind === "retrieve") {
      // Standalone retrieve groups are KB prefetch seeds: their query (not a
      // tool verb) is the informative bit for the tier-one line.
      const query = retrieveQueryOf(group.events);
      owner().tools.push({ name: "retrieval", args: query ? { query } : undefined });
    } else {
      owner().tools.push({ name: toolNameOf(group.events), args: argsOf(group.events) });
    }
    if (groupHasError(group.events)) owner().hasError = true;
  }

  const rows: RoundFacts[] = [];
  if (preRound.tools.length || preRound.hasError) rows.push(preRound);
  rows.push(...rounds);
  return rows;
}
