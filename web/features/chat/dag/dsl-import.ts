/**
 * DSL import — reconstruct a SessionDag IR from an exported DSL document
 * (module 11). The imported document drives rendering only: no session
 * state, no persistence, no network. Call trees are materialized fully —
 * an import is a snapshot, so interactive re-expansion is unnecessary.
 */
import { DAG_NODE_LIMIT, type DagEdge, type DagNode, type SessionDag } from "./model";
import type { DslCallEntry, DslDocument, DslTraceEntry } from "./dsl";

function nodeKey(entry: { node: string }, used: Set<string>): string {
  // DSL ids (msg:N / turn:N.M / b1:turn:N) are scrubbed of selector-hostile
  // characters and namespaced apart from live ids. Non-normalized exports
  // may repeat raw call ids across the main trace and branch sub-traces —
  // suffix duplicates so every entry gets a distinct IR key and cytoscape
  // never silently drops a node (#75).
  const base = `dsl:${entry.node.replace(/[^A-Za-z0-9_.:-]/g, "_")}`;
  if (!used.has(base)) {
    used.add(base);
    return base;
  }
  let suffix = 2;
  let candidate = `${base}~${suffix}`;
  while (used.has(candidate)) {
    suffix += 1;
    candidate = `${base}~${suffix}`;
  }
  used.add(candidate);
  return candidate;
}

function traceEntryToNode(
  entry: DslTraceEntry,
  seq: number,
  parentId: string | null,
  calls: DslCallEntry[] | undefined,
  /** 0-based position in this chain — mirrors the live IR's visible-path
   * index so Q/A labels agree between the session view and the import. */
  messageIndex: number,
  used: Set<string>,
): DagNode {
  return {
    id: nodeKey(entry, used),
    kind: entry.kind,
    parentId,
    seq,
    meta: {
      childCount: countCalls(calls),
      messageIndex,
      capability: entry.capability,
      textPreview: entry.text_preview,
      // The DSL records the fork's own {index, total} (total includes the
      // selected branch) — map it verbatim instead of guessing (#75).
      branchInfo: entry.branch ? { index: entry.branch.index, total: entry.branch.total } : undefined,
    },
  };
}

function countCalls(calls: DslCallEntry[] | undefined): number {
  if (!calls?.length) return 0;
  return calls.reduce((sum, call) => sum + 1 + countCalls(call.calls), 0);
}

function callEntryToNode(
  entry: DslCallEntry,
  seq: number,
  parentId: string,
  used: Set<string>,
): DagNode {
  return {
    id: nodeKey(entry, used),
    kind: entry.kind,
    parentId,
    seq,
    meta: {
      childCount: countCalls(entry.calls),
      toolName: entry.tool,
      provider: entry.provider,
      callState: entry.state,
      query: entry.query,
      subagentName: entry.subagent_name,
      consultIndex: entry.consult_index,
      roundIndex: entry.round_index,
      durationMs: entry.duration_ms,
      error: entry.error,
    },
  };
}

/** Shared node budget across a single dslToDag walk (#77). Message-layer
 * nodes never consume budget (the session skeleton always renders); only
 * call-tree nodes do. Mutated in place by materializeCallNodes. */
interface NodeBudget {
  remaining: number;
  dropped: number;
}

function materializeCallNodes(
  calls: DslCallEntry[] | undefined,
  parentNodeId: string,
  nodes: DagNode[],
  edges: DagEdge[],
  used: Set<string>,
  budget: NodeBudget,
): void {
  let prev = parentNodeId;
  let seq = nodes.length;
  for (const call of calls ?? []) {
    if (budget.remaining <= 0) {
      // Count the skipped subtree: this call and everything nested below.
      budget.dropped += 1 + countCalls(call.calls);
      continue;
    }
    budget.remaining -= 1;
    const node = callEntryToNode(call, seq, prev, used);
    nodes.push(node);
    edges.push({ id: `e:${prev}->${node.id}`, source: prev, target: node.id, kind: "drilldown" });
    materializeCallNodes(call.calls, node.id, nodes, edges, used, budget);
    prev = node.id;
    seq += 1;
  }
}

/** Build a fully-materialized DAG IR from a parsed DSL document. */
export function dslToDag(doc: DslDocument): SessionDag {
  const nodes: DagNode[] = [
    {
      id: "root",
      kind: "root",
      parentId: null,
      seq: 0,
      meta: { childCount: 0, messageIndex: -1 },
    },
  ];
  const edges: DagEdge[] = [];
  // Call trees are fully materialized, so nothing is expandable: populating
  // `expandable` would route taps to the (no-op) toggle handler and make
  // these nodes impossible to select for the detail grid (#75).
  const expandable = new Set<string>();
  const used = new Set<string>();
  // Node budget (#77): once exhausted, remaining call trees are skipped but
  // the message chain keeps rendering — a partial view beats a frozen canvas.
  const budget: NodeBudget = { remaining: DAG_NODE_LIMIT, dropped: 0 };
  let prev = "root";

  const walkTrace = (entries: DslTraceEntry[], startIndex: number) => {
    for (let position = 0; position < entries.length; position += 1) {
      const entry = entries[position];
      // Chain-relative numbering (not nodes.length, which counts the root
      // and materialized call nodes) keeps Q/A labels aligned with the
      // live view (#75).
      const messageIndex = startIndex + position;
      const node = traceEntryToNode(entry, nodes.length, prev, entry.calls, messageIndex, used);
      nodes.push(node);
      edges.push({ id: `e:${prev}->${node.id}`, source: prev, target: node.id, kind: "conversation" });
      materializeCallNodes(entry.calls, node.id, nodes, edges, used, budget);
      // Branch sub-chains hang off the fork entry with sibling edges; the
      // branch's first entry replaces the fork at the same path position,
      // so its chain numbering continues from the fork's messageIndex.
      for (const branch of entry.branches ?? []) {
        let branchPrev = node.id;
        for (let offset = 0; offset < branch.trace.length; offset += 1) {
          const branchEntry = branch.trace[offset];
          const branchNode = traceEntryToNode(
            branchEntry,
            nodes.length,
            branchPrev,
            branchEntry.calls,
            messageIndex + offset,
            used,
          );
          nodes.push(branchNode);
          edges.push({
            id: `e:${branchPrev}->${branchNode.id}`,
            source: branchPrev,
            target: branchNode.id,
            kind: branchPrev === node.id ? "sibling" : "conversation",
          });
          materializeCallNodes(branchEntry.calls, branchNode.id, nodes, edges, used, budget);
          branchPrev = branchNode.id;
        }
      }
      prev = node.id;
    }
  };

  walkTrace(doc.trace, 0);
  return {
    nodes,
    edges,
    byId: new Map(nodes.map((n) => [n.id, n])),
    expandable,
    truncated: budget.dropped > 0 ? { limit: DAG_NODE_LIMIT, dropped: budget.dropped } : null,
  };
}
