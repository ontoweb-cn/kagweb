/**
 * Session DSL — JSON serialization of a session's reasoning chain.
 *
 * The DSL is the "store / diff / query / share" projection of the DAG IR:
 * a whitelist-field, tree-shaped document (messages → nested calls) that can
 * be archived, diffed between runs (normalizeIds + stable), and grepped.
 * It is NOT a lossless IR encoding — round-tripping is defined at the
 * normalized-tree level only (impl-review #19).
 *
 * Security: only the whitelisted fields below ever leave the IR. Raw event
 * metadata (args, tool_metadata, …) is never passed through (#14), and user
 * text is clipped to a preview (includeText turns even that off).
 */
import { parentKey, type VisiblePathResult } from "@/lib/message-branches";
import {
  computeSessionDag,
  visibleMessagesForDag,
  type DagMessage,
  type SessionDagInput,
} from "./aggregate";
import { DSL_MAX_DEPTH, type DagNode, type SessionDag } from "./model";

export const DSL_VERSION = 1;
const GENERATOR = "kagweb/session-dsl";
const PREVIEW_LIMIT = 140;

export interface SerializeDslOptions {
  /** Drop volatile fields (duration_ms, error, session block, exported_at)
   * so snapshots stay stable across identical runs. Default false. */
  stable?: boolean;
  /** Renumber node ids positionally (turn:N / turn:N.M) so two runs of the
   * same flow diff structurally instead of by uuid. Default false. */
  normalizeIds?: boolean;
  /** Include text_preview fields. Default true; disable for sensitive
   * sessions (#21). */
  includeText?: boolean;
  /** Session id for the (non-stable) session block. */
  sessionId?: string;
}

export interface DslCallEntry {
  node: string;
  kind: "round" | "tool_call" | "retrieve" | "subagent";
  tool?: string;
  provider?: string;
  state?: string;
  query?: string;
  subagent_name?: string;
  consult_index?: number;
  round_index?: number;
  duration_ms?: number;
  error?: string;
  calls?: DslCallEntry[];
}

export interface DslBranch {
  selected: boolean;
  trace: DslTraceEntry[];
}

export interface DslTraceEntry {
  node: string;
  kind: "user" | "assistant";
  text_preview?: string;
  capability?: string;
  /** 1-based position of the selected branch when this message forks. */
  branch?: { index: number; total: number };
  calls?: DslCallEntry[];
  /** Alternative continuations at this fork point (non-selected siblings). */
  branches?: DslBranch[];
}

export interface DslDocument {
  version: number;
  generator: string;
  session?: {
    id: string;
    exported_at: string;
    turn_count: number;
  };
  trace: DslTraceEntry[];
}

interface Writer {
  opts: Required<Pick<SerializeDslOptions, "includeText">> &
    Pick<SerializeDslOptions, "stable" | "normalizeIds">;
}

/** DFS position counters — the basis for normalizeIds renumbering. */
interface Counters {
  message: number;
  call: number;
}

/** Per-conversion context: precomputed child index (D3) and the id prefix
 * for the current branch namespace (D2). */
interface TreeContext {
  childrenByParent: Map<string, DagNode[]>;
  idPrefix: string;
}

/** Index drilldown edges by parent once per DAG — O(E) instead of an
 * O(E) scan per node. */
function buildChildrenIndex(dag: SessionDag): Map<string, DagNode[]> {
  const index = new Map<string, DagNode[]>();
  for (const edge of dag.edges) {
    if (edge.kind !== "drilldown") continue;
    const child = dag.byId.get(edge.target);
    if (!child) continue;
    const list = index.get(edge.source);
    if (list) list.push(child);
    else index.set(edge.source, [child]);
  }
  for (const list of index.values()) list.sort((a, b) => a.seq - b.seq);
  return index;
}

function messageCalls(
  ctx: TreeContext,
  msgKey: string,
  writer: Writer,
  counters: Counters,
): DslCallEntry[] {
  // Children in IR seq order, recursing depth-first.
  return (ctx.childrenByParent.get(msgKey) ?? []).map((child) =>
    convertCall(ctx, child, writer, counters),
  );
}

function convertCall(
  ctx: TreeContext,
  node: DagNode,
  writer: Writer,
  counters: Counters,
): DslCallEntry {
  counters.call += 1;
  const meta = node.meta;
  const entry: DslCallEntry = {
    node: writer.opts.normalizeIds
      ? `${ctx.idPrefix}turn:${counters.message}.${counters.call}`
      : node.id,
    kind: node.kind as DslCallEntry["kind"],
  };
  if (meta.toolName) entry.tool = meta.toolName;
  if (meta.provider) entry.provider = meta.provider;
  if (meta.callState) entry.state = meta.callState;
  if (meta.query) entry.query = meta.query;
  if (meta.subagentName) entry.subagent_name = meta.subagentName;
  if (meta.consultIndex != null) entry.consult_index = meta.consultIndex;
  if (meta.roundIndex != null) entry.round_index = meta.roundIndex;
  if (!writer.opts.stable && meta.durationMs != null) {
    entry.duration_ms = meta.durationMs;
  }
  if (!writer.opts.stable && meta.error) entry.error = meta.error;
  const calls = messageCalls(ctx, node.id, writer, counters);
  if (calls.length) entry.calls = calls;
  return entry;
}

function convertTrace(
  input: SessionDagInput,
  allowBranches: boolean,
  startIndex: number,
  writer: Writer,
  reuse?: { visible: VisiblePathResult<DagMessage>; idPrefix: string },
): DslTraceEntry[] {
  const dag = computeSessionDag(input, null, { expandAll: true });
  // Merged visible path (deep_research pairs collapsed) — same view the DAG
  // derives, so nodeByIndex (merged-list indexes) matches this walk (#63).
  const visible = reuse?.visible ?? visibleMessagesForDag(input);
  const ctx: TreeContext = {
    childrenByParent: buildChildrenIndex(dag),
    idPrefix: reuse?.idPrefix ?? "",
  };
  // messageIndex → node lookup (the IR indexes by visible-path position).
  const nodeByIndex = new Map<number, DagNode>();
  for (const node of dag.nodes) {
    if (node.meta.messageIndex != null) nodeByIndex.set(node.meta.messageIndex, node);
  }
  const entries: DslTraceEntry[] = [];
  const counters: Counters = { message: 0, call: 0 };

  for (let index = startIndex; index < visible.messages.length; index += 1) {
    const message = visible.messages[index];
    if (message.role === "system") continue;
    if (message.id != null && message.id < 0) continue; // optimistic (#22)
    const node = nodeByIndex.get(index);
    if (!node || node.kind !== message.role) continue;
    counters.message += 1;
    counters.call = 0;

    const entry: DslTraceEntry = {
      node: writer.opts.normalizeIds
        ? `${ctx.idPrefix}turn:${counters.message}`
        : node.id,
      kind: message.role,
    };
    if (writer.opts.includeText && node.meta.textPreview) {
      entry.text_preview = node.meta.textPreview.slice(0, PREVIEW_LIMIT);
    }
    if (node.meta.capability) entry.capability = node.meta.capability;
    const siblingInfo =
      message.id != null
        ? visible.siblingsByMessageId.get(message.id)
        : undefined;
    if (siblingInfo && siblingInfo.total > 1) {
      entry.branch = { index: siblingInfo.index, total: siblingInfo.total };
      // Branches are collected one level deep only: a branch's own trace
      // walks its visible path but never re-expands nested forks. Without
      // this bound, M forks × K siblings each would serialize K^M paths,
      // each with a full-session DAG computation (review D1).
      if (allowBranches) {
        const branches = collectBranches(
          input,
          message.id,
          siblingInfo.siblingIds,
          siblingInfo.parentId,
          writer,
        );
        if (branches.length) entry.branches = branches;
      }
    }

    if (message.role === "assistant") {
      const calls = messageCalls(ctx, node.id, writer, counters);
      if (calls.length) entry.calls = calls;
    }
    entries.push(entry);
  }
  return entries;
}

function collectBranches(
  input: SessionDagInput,
  chosenId: number | undefined,
  siblingIds: number[],
  parentId: number | null,
  writer: Writer,
): DslBranch[] {
  const key = parentKey(parentId);
  const branches: DslBranch[] = [];
  for (const [position, siblingId] of siblingIds.entries()) {
    if (siblingId === chosenId) continue;
    // Force this sibling selected and re-walk the path from the session root
    // (with deep_research pairs merged on the branch's own path as well).
    const override = { ...(input.selectedBranches ?? {}), [key]: siblingId };
    const subVisible = visibleMessagesForDag({
      messages: input.messages,
      selectedBranches: override,
    });
    const start = subVisible.messages.findIndex((m) => m.id === siblingId);
    if (start < 0) continue;
    // The sibling's own positional number keeps branch prefixes stable when
    // the selected branch changes; the visible path is reused (not recomputed)
    // by the recursive walk (review D1).
    const trace = convertTrace(
      { messages: input.messages, selectedBranches: override },
      false,
      start,
      writer,
      { visible: subVisible, idPrefix: `b${position + 1}:` },
    );
    branches.push({ selected: false, trace });
  }
  return branches;
}

/** Build the DSL document object (module 9's Mermaid codegen consumes it
 * without a JSON round-trip). */
export function buildSessionDslDocument(
  input: SessionDagInput,
  opts?: SerializeDslOptions,
): DslDocument {
  const writer: Writer = {
    opts: {
      includeText: opts?.includeText ?? true,
      stable: opts?.stable,
      normalizeIds: opts?.normalizeIds,
    },
  };
  const trace = convertTrace(input, true, 0, writer);
  const doc: DslDocument = {
    version: DSL_VERSION,
    generator: GENERATOR,
    trace,
  };
  if (!opts?.stable) {
    doc.session = {
      id: opts?.sessionId ?? "",
      exported_at: new Date().toISOString(),
      turn_count: trace.length,
    };
  }
  return doc;
}

export function serializeSessionDsl(
  input: SessionDagInput,
  opts?: SerializeDslOptions,
): string {
  return JSON.stringify(buildSessionDslDocument(input, opts), null, 2);
}

/**
 * Parse a DSL document back into its tree shape. Structural validation:
 * version, entry kinds, and array shapes are enforced so a corrupt or
 * hand-edited file fails HERE with a clean message — `dslToDag` runs later
 * inside a render-phase useMemo with no error boundary beneath it, where a
 * type-confused field would take down the whole page (code review #75).
 *
 * Node-id uniqueness is deliberately NOT enforced: non-normalized exports
 * legitimately repeat raw call ids across the main trace and branch
 * sub-traces (same underlying call events, per-message counters). Import
 * handles duplicates by suffixing its internal keys (dsl-import.ts).
 */
const TRACE_KINDS = new Set(["user", "assistant"]);
const CALL_KINDS = new Set(["round", "tool_call", "retrieve", "subagent"]);

function validateCallEntry(call: unknown, path: string, depth: number): void {
  if (depth > DSL_MAX_DEPTH) {
    throw new Error(`Session DSL nests deeper than ${DSL_MAX_DEPTH} levels at ${path}`);
  }
  if (call == null || typeof call !== "object" || Array.isArray(call)) {
    throw new Error(`${path} must be an object`);
  }
  const entry = call as Record<string, unknown>;
  if (typeof entry.node !== "string" || entry.node.length === 0) {
    throw new Error(`${path}.node must be a non-empty string`);
  }
  if (!CALL_KINDS.has(entry.kind as string)) {
    throw new Error(`${path}.kind must be one of round|tool_call|retrieve|subagent`);
  }
  if (entry.calls !== undefined && !Array.isArray(entry.calls)) {
    throw new Error(`${path}.calls must be an array`);
  }
  (entry.calls as unknown[] | undefined)?.forEach((child, i) =>
    validateCallEntry(child, `${path}.calls[${i}]`, depth + 1),
  );
}

function validateTraceEntry(entry: unknown, path: string, depth: number): void {
  if (depth > DSL_MAX_DEPTH) {
    throw new Error(`Session DSL nests deeper than ${DSL_MAX_DEPTH} levels at ${path}`);
  }
  if (entry == null || typeof entry !== "object" || Array.isArray(entry)) {
    throw new Error(`${path} must be an object`);
  }
  const record = entry as Record<string, unknown>;
  if (typeof record.node !== "string" || record.node.length === 0) {
    throw new Error(`${path}.node must be a non-empty string`);
  }
  if (!TRACE_KINDS.has(record.kind as string)) {
    throw new Error(`${path}.kind must be "user" or "assistant"`);
  }
  if (record.calls !== undefined && !Array.isArray(record.calls)) {
    throw new Error(`${path}.calls must be an array`);
  }
  (record.calls as unknown[] | undefined)?.forEach((child, i) =>
    validateCallEntry(child, `${path}.calls[${i}]`, depth + 1),
  );
  if (record.branch !== undefined) {
    const branch = record.branch as Record<string, unknown> | null;
    if (
      branch == null ||
      typeof branch !== "object" ||
      typeof branch.index !== "number" ||
      typeof branch.total !== "number"
    ) {
      throw new Error(`${path}.branch must be { index: number, total: number }`);
    }
  }
  if (record.branches !== undefined) {
    if (!Array.isArray(record.branches)) {
      throw new Error(`${path}.branches must be an array`);
    }
    (record.branches as unknown[]).forEach((branch, i) => {
      const b = branch as Record<string, unknown> | null;
      if (b == null || typeof b !== "object" || !Array.isArray(b.trace)) {
        throw new Error(`${path}.branches[${i}].trace must be an array`);
      }
      (b.trace as unknown[]).forEach((child, j) =>
        validateTraceEntry(child, `${path}.branches[${i}].trace[${j}]`, depth + 1),
      );
    });
  }
}

export function parseSessionDsl(text: string): DslDocument {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    throw new Error(`Session DSL is not valid JSON: ${(err as Error).message}`);
  }
  const doc = parsed as DslDocument;
  if (doc == null || typeof doc !== "object") {
    throw new Error("Session DSL must be a JSON object");
  }
  if (doc.version !== DSL_VERSION) {
    throw new Error(`Unsupported Session DSL version: ${String(doc.version)}`);
  }
  if (!Array.isArray(doc.trace)) {
    throw new Error("Session DSL is missing the trace array");
  }
  doc.trace.forEach((entry, i) => validateTraceEntry(entry, `trace[${i}]`, 1));
  return doc;
}
