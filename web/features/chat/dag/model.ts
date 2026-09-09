/**
 * Session DAG — the intermediate representation (IR) between chat state and
 * the two projections built on it: the cytoscape view (V1) and the DSL
 * exporter (V2).
 *
 * Pure types only: no React, no cytoscape, no i18n imports so the aggregate
 * layer stays testable under node:test without a DOM.
 */
import type { TurnInsight } from "@/lib/turn-insight";

export type DagNodeKind =
  | "root"
  | "user"
  | "assistant"
  | "round"
  | "tool_call"
  | "retrieve"
  | "subagent";

/** Branch summary at a fork point (from `buildVisiblePath`). */
export interface DagBranchInfo {
  /** Total branches at this point, including the selected one. */
  total: number;
  /** 1-based index of the selected branch in creation order. */
  index: number;
}

/**
 * Token counters as the backend reported them. `total` is optional because a
 * cumulative counter pair has no honest sum; `usage_scope` says which reading
 * applies (`"pass"` unless the backend marked it otherwise).
 */
export interface TokenCounts {
  prompt: number;
  completion: number;
  total?: number;
}

export interface DagNodeMeta {
  // —— message layer ——
  /** 0-based position in the visible path. Absent on call-layer nodes. */
  messageIndex?: number;
  messageId?: number;
  messageRole?: "user" | "assistant";
  branchInfo?: DagBranchInfo;
  capability?: string;
  // —— call layer ——
  callId?: string;
  callKind?: string;
  /** running | complete | error — from the last call_status event. */
  callState?: string;
  traceGroup?: string;
  toolName?: string;
  provider?: string;
  // —— call-layer display fields (reserved for the V2 DSL serializer) ——
  query?: string;
  subagentName?: string;
  consultIndex?: number;
  roundIndex?: number;
  durationMs?: number;
  /** Pass-level token counters; present only on the round that carries the
   *  backend's completion marker, and only when the backend reported them. */
  tokens?: TokenCounts;
  usageScope?: string;
  textPreview?: string;
  error?: string;
  /** Judge-written turn badge — colours the plaque/glyph zoom tiers and the
   *  thought-map export. */
  turnInsight?: TurnInsight;
  // —— UI ——
  /** Hidden child count while the assistant node is collapsed. */
  childCount: number;
}

export interface DagNode {
  /**
   * `root` | `msg:{id}` (persisted, positive id) | `msg:i{index}` (optimistic
   * negative id / missing id — keeps ids valid as CSS selectors) |
   * `call:{callId}:{kind}:{n}`.
   */
  id: string;
  kind: DagNodeKind;
  /** Semantic parent: previous message (conversation) or container (drilldown). */
  parentId: string | null;
  /** 0-based order within the parent's children. */
  seq: number;
  meta: DagNodeMeta;
}

export interface DagEdge {
  id: string;
  source: string;
  target: string;
  /** conversation: message-chain · drilldown: call-tree containment ·
   * sibling: imported-DSL branch sub-chain (dashed alternative path). */
  kind: "conversation" | "drilldown" | "sibling";
}

export interface SessionDag {
  nodes: DagNode[];
  edges: DagEdge[];
  byId: Map<string, DagNode>;
  /** Assistant message node ids whose call tree is non-empty. */
  expandable: Set<string>;
  /** Set when the node budget forced the aggregator to drop call-tree
   * nodes: `{ limit, dropped }` powers the panel's truncation banner.
   * Message-layer nodes are never dropped — only their call trees (#77). */
  truncated?: { limit: number; dropped: number } | null;
}

/** Node budget shared by the live aggregator and the DSL importer (#77):
 * past this a dagre layout run costs seconds and streaming rebuilds stack
 * up behind the debounce. Call-tree nodes are dropped first; the message
 * skeleton always renders. */
export const DAG_NODE_LIMIT = 2000;
/** Depth budget for DSL parsing: real call trees nest round → tool →
 * retrieve/subagent, a handful of levels. Deeper nesting in a file is
 * hostile input aimed at the recursive importer (#77). */
export const DSL_MAX_DEPTH = 64;

/** Stable node key for a visible message. Exported so the panel (expanded-set
 * keys) and the aggregate agree on the same mapping. */
export function messageNodeKey(id: number | undefined, index: number): string {
  return id != null && id > 0 ? `msg:${id}` : `msg:i${index}`;
}
