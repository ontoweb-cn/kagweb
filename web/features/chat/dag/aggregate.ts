/**
 * Session DAG aggregation — builds the graph IR from chat state.
 *
 * Pure function: no React, no cytoscape. The visible conversation spine is
 * derived with `buildVisiblePath` (state.messages is the flat list holding
 * *all* edit branches — only the selected path belongs in the DAG), and each
 * assistant message's call tree is materialized on demand via the
 * `expandedMessages` set.
 *
 * Group classification is not decided here: `walkCallGroups` asks
 * `classifyTraceGroup` (`trace/selectors.ts`), the same kernel the inline
 * activity trace and the DSL export use, so the DAG cannot disagree with
 * either. That kernel owns the rule (and its docstring is the specification).
 */
import type { StreamEvent } from "@/features/chat/model/protocol";
import type { InsightType } from "@/lib/turn-insight";
import {
  classifyTraceGroup,
  getCallProvider,
  getTraceCallKind,
  getTraceGroup,
  getTraceMeta,
  groupTraceEvents,
  type TraceGroupClass,
} from "@/features/chat/trace/selectors";
import { buildVisiblePath } from "@/lib/message-branches";
import { isConfirmedResearchFollowup } from "@/lib/deep-research-report";
import {
  DAG_NODE_LIMIT,
  messageNodeKey,
  type DagBranchInfo,
  type DagEdge,
  type DagNode,
  type DagNodeKind,
  type SessionDag,
} from "./model";

/**
 * Minimal structural slice of a chat message the DAG needs. Declared here
 * (not imported from the React ChatStateAdapter) so this module stays a
 * pure, DOM-free dependency — same pattern as `BranchMessage`.
 */
export interface DagMessage {
  id?: number;
  role: "user" | "assistant" | "system";
  content: string;
  capability?: string;
  events?: StreamEvent[];
  /** Judge-written turn badge (assistant rows, multi-round turns). */
  turnInsight?: { takeaway: string; type: InsightType };
  parentMessageId?: number | null;
}

export interface SessionDagInput {
  messages: DagMessage[];
  selectedBranches?: Record<string, number>;
}

const TEXT_PREVIEW_LIMIT = 140;

/** One materializable group from a message's call tree. */
interface CallGroup extends TraceGroupClass {
  callId: string;
  events: StreamEvent[];
}

/**
 * Single pass over a message's events producing the normalized group list.
 * Both `countCallTree` (collapsed badge) and `materializeCalls` (expanded
 * nodes) consume this, so the count can never drift from the node count.
 */
function walkCallGroups(events: StreamEvent[] | undefined): CallGroup[] {
  if (!events?.length) return [];
  const groups: CallGroup[] = [];
  for (const { callId, events: groupEvents } of groupTraceEvents(events)) {
    const classified = classifyTraceGroup(groupEvents);
    if (!classified) continue;

    groups.push({
      kind: classified.kind,
      callId,
      events: groupEvents,
      subagents: classified.subagents,
    });
  }
  return groups;
}

/** Number of call nodes a message would materialize when expanded. */
export function countCallTree(message: DagMessage): number {
  let count = 0;
  for (const group of walkCallGroups(message.events)) {
    count += 1 + group.subagents.length;
  }
  return count;
}

function lastCallState(events: StreamEvent[]): string | undefined {
  let state: string | undefined;
  for (const event of events) {
    const meta = getTraceMeta(event);
    if (meta.call_state) state = String(meta.call_state);
  }
  return state;
}

function clip(text: string): string {
  const trimmed = text.trim();
  return trimmed.length > TEXT_PREVIEW_LIMIT
    ? `${trimmed.slice(0, TEXT_PREVIEW_LIMIT)}…`
    : trimmed;
}

function extractTextPreview(events: StreamEvent[]): string | undefined {
  for (const event of events) {
    if (event.type === "content" || event.type === "thinking") {
      const text = clip(event.content ?? "");
      if (text) return text;
    }
  }
  return undefined;
}

function extractQuery(events: StreamEvent[]): string | undefined {
  for (const event of events) {
    const query = getTraceMeta(event).query;
    if (typeof query === "string" && query.trim()) return clip(query);
  }
  return undefined;
}

function extractError(events: StreamEvent[]): string | undefined {
  for (const event of events) {
    if (event.type === "error") {
      const text = clip(event.content ?? "");
      if (text) return text;
    }
  }
  return undefined;
}

function extractDurationMs(events: StreamEvent[]): number | undefined {
  // Timestamps are not guaranteed monotonic within a group (events arrive in
  // first-seen order), so span the min/max rather than first/last.
  let min = Infinity;
  let max = -Infinity;
  let seen = false;
  for (const event of events) {
    const ts = event.timestamp;
    if (typeof ts !== "number") continue;
    seen = true;
    if (ts < min) min = ts;
    if (ts > max) max = ts;
  }
  if (!seen) return undefined;
  const duration = max - min;
  return duration > 0 ? duration : undefined;
}

function extractToolName(events: StreamEvent[]): string | undefined {
  for (const event of events) {
    if (event.type === "tool_call") {
      const meta = getTraceMeta(event);
      const name = meta.tool_name || event.content?.trim();
      if (name) return String(name);
    }
  }
  return undefined;
}

function makeCallNode(
  group: CallGroup,
  parentId: string,
  seq: number,
  roundIndex: number,
): DagNode {
  const kind: DagNodeKind = group.kind;
  return {
    id: `call:${group.callId}:${kind}:${seq}`,
    kind,
    parentId,
    seq,
    meta: {
      childCount: 0,
      callId: group.callId,
      callKind: getTraceCallKind(group.events) || undefined,
      callState: lastCallState(group.events),
      traceGroup: getTraceGroup(group.events) || undefined,
      provider: getCallProvider(group.events) || undefined,
      toolName: group.kind === "tool_call" ? extractToolName(group.events) : undefined,
      query: extractQuery(group.events),
      roundIndex: group.kind === "round" ? roundIndex : undefined,
      durationMs: extractDurationMs(group.events),
      textPreview: extractTextPreview(group.events),
      error: extractError(group.events),
    },
  };
}

function materializeCalls(
  message: DagMessage,
  msgKey: string,
  nodes: DagNode[],
  edges: DagEdge[],
): void {
  let seq = 0;
  let nRound = 0;
  let currentRound: string | null = null;
  let currentTool: string | null = null;

  for (const group of walkCallGroups(message.events)) {
    const parentId =
      group.kind === "round"
        ? msgKey
        : group.kind === "tool_call"
          ? (currentRound ?? msgKey)
          : (currentTool ?? currentRound ?? msgKey);
    const node = makeCallNode(group, parentId, seq, nRound);

    nodes.push(node);
    edges.push({ id: `e:${parentId}->${node.id}`, source: parentId, target: node.id, kind: "drilldown" });
    seq += 1;
    if (group.kind === "round") {
      nRound += 1;
      currentRound = node.id;
      currentTool = null;
    } else if (group.kind === "tool_call") {
      currentTool = node.id;
      for (const sub of group.subagents) {
        const subNode: DagNode = {
          id: `call:${group.callId}:subagent:${seq}`,
          kind: "subagent",
          parentId: node.id,
          seq,
          meta: {
            childCount: 0,
            callId: group.callId,
            subagentName: sub.name,
            consultIndex: sub.consultIndex,
            callState: lastCallState(group.events),
            durationMs: extractDurationMs(group.events),
          },
        };
        nodes.push(subNode);
        edges.push({
          id: `e:${node.id}->${subNode.id}`,
          source: node.id,
          target: subNode.id,
          kind: "drilldown",
        });
        seq += 1;
      }
    }
  }
}

export interface ComputeDagOptions {
  /** Materialize the call tree of every expandable message in one pass —
   * used by the DSL exporter, which needs the full tree regardless of the
   * user's panel expansion state (impl-review #33/#34). */
  expandAll?: boolean;
  /** Enforce the DAG_NODE_LIMIT render budget (#77): once the node count
   * approaches the limit, whole call trees are skipped (never partial, so
   * expandable/childCount semantics stay coherent) and the session reports
   * `truncated`. Off by default — the DSL export path shares this function
   * via expandAll and must never truncate (#33). */
  truncate?: boolean;
}

/**
 * Merge deep_research two-turn pairs on the *visible path* (module 12):
 * an outline-preview turn followed by a confirmed research followup gets the
 * followup's events spliced into the parent and the followup dropped —
 * exactly the pairing rules of `ChatMessageList.deepResearchMergeMap`, so
 * the DAG/DSL never disagree with the merged chat bubble. Applying this on
 * the raw (all-branches) list instead would risk pairing a parent and a
 * followup from different branches (impl-review #63).
 */
export function mergeDeepResearchPairs(messages: DagMessage[]): DagMessage[] {
  const followups = new Set<DagMessage>();
  const mergedEvents = new Map<DagMessage, StreamEvent[]>();
  for (let i = 0; i < messages.length; i += 1) {
    const message = messages[i];
    if (message.role !== "assistant" || message.capability !== "deep_research") {
      continue;
    }
    const hasOutline = (message.events ?? []).some(
      (event) =>
        event.type === "result" &&
        Boolean(
          (event.metadata as Record<string, unknown> | undefined)?.outline_preview,
        ),
    );
    if (!hasOutline) continue;
    const followupIndex = messages
      .slice(i + 1)
      .findIndex(
        (m) => m.role === "assistant" && m.capability === "deep_research",
      );
    if (followupIndex === -1) continue;
    const followup = messages[i + 1 + followupIndex];
    if (!isConfirmedResearchFollowup(followup.events ?? [])) continue;
    mergedEvents.set(message, [
      ...(message.events ?? []),
      ...(followup.events ?? []),
    ]);
    followups.add(followup);
  }
  if (!followups.size) return messages;
  return messages
    .filter((message) => !followups.has(message))
    .map((message) =>
      mergedEvents.has(message)
        ? { ...message, events: mergedEvents.get(message) }
        : message,
    );
}

/**
 * Visible-path messages for DAG/DSL derivation: the selected branch path
 * with deep_research pairs pre-merged. Both `computeSessionDag` and the DSL
 * serializer use this so `meta.messageIndex` and the DSL walk always agree
 * on one merged view (impl-review #63).
 */
export function visibleMessagesForDag(input: SessionDagInput) {
  const visible = buildVisiblePath(input.messages, input.selectedBranches);
  return {
    messages: mergeDeepResearchPairs(visible.messages),
    siblingsByMessageId: visible.siblingsByMessageId,
  };
}

/** Fields a trace search matches against (#79) — the same display data the
 * detail grid shows, so a hit is always explainable on tap. */
function searchableText(meta: DagNode["meta"]): string {
  return [meta.toolName, meta.query, meta.subagentName, meta.textPreview, meta.capability]
    .filter((v): v is string => typeof v === "string" && v.length > 0)
    .join("\n");
}

/** Case-insensitive substring search over the DAG's display fields. Returns
 * the matching node ids (empty set = no filter active). Pure: reusable by
 * both the live view and imported snapshots (#79). */
export function searchDagNodes(dag: SessionDag, query: string): Set<string> {
  const needle = query.trim().toLowerCase();
  if (!needle) return new Set();
  const hits = new Set<string>();
  for (const node of dag.nodes) {
    if (searchableText(node.meta).toLowerCase().includes(needle)) hits.add(node.id);
  }
  return hits;
}

export function computeSessionDag(
  input: SessionDagInput,
  expandedMessages?: Set<string> | null,
  options?: ComputeDagOptions,
): SessionDag {
  const visible = visibleMessagesForDag(input);
  const nodes: DagNode[] = [];
  const edges: DagEdge[] = [];
  const expandable = new Set<string>();
  /** Call nodes dropped by the render budget (options.truncate, #77). */
  let droppedCallNodes = 0;
  let prev = "root";

  nodes.push({
    id: "root",
    kind: "root",
    parentId: null,
    seq: 0,
    meta: { childCount: 0, messageIndex: -1 },
  });

  let index = -1;
  for (const message of visible.messages) {
    index += 1;
    if (message.role === "system") continue;

    const key = messageNodeKey(message.id, index);
    const siblingInfo = message.id != null
      ? visible.siblingsByMessageId.get(message.id)
      : undefined;
    const branchInfo: DagBranchInfo | undefined = siblingInfo
      ? { total: siblingInfo.total, index: siblingInfo.index }
      : undefined;

    let childCount = 0;
    if (message.role === "assistant") {
      childCount = countCallTree(message);
      if (childCount > 0) expandable.add(key);
    }

    // Whole-tree skip when the render budget is exhausted (#77): the tree
    // either renders completely or not at all, so a collapsed "+N" badge
    // never lies about a half-materialized tree. Message nodes render
    // regardless — the skeleton survives even a pathological session.
    const wantsTree =
      message.role === "assistant" &&
      childCount > 0 &&
      (options?.expandAll || expandedMessages?.has(key));
    const skipTree = wantsTree && options?.truncate && nodes.length + childCount > DAG_NODE_LIMIT;

    nodes.push({
      id: key,
      kind: message.role,
      parentId: prev,
      seq: index,
      meta: {
        childCount,
        messageIndex: index,
        messageId: message.id,
        messageRole: message.role,
        branchInfo,
        capability: message.capability,
        turnInsight: message.turnInsight,
        textPreview:
          message.role === "user" ? clip(message.content ?? "") || undefined : undefined,
      },
    });
    edges.push({ id: `e:${prev}->${key}`, source: prev, target: key, kind: "conversation" });

    if (skipTree) {
      droppedCallNodes += childCount;
    } else if (wantsTree) {
      materializeCalls(message, key, nodes, edges);
    }
    prev = key;
  }

  return {
    nodes,
    edges,
    byId: new Map(nodes.map((n) => [n.id, n])),
    expandable,
    truncated: droppedCallNodes > 0 ? { limit: DAG_NODE_LIMIT, dropped: droppedCallNodes } : null,
  };
}
