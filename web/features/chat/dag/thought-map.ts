/**
 * DAG → thought-map projection: the layout the SVG exporter needs, kept out
 * of the panel so it is testable (and reusable by a future CLI/DSL export
 * path). Pure: no React, no DOM.
 */
import type { ThoughtMapInput, ThoughtMapNode } from "@/lib/thought-map-export";
import type { SessionDag } from "./model";

export function buildThoughtMapInput(dag: SessionDag, title?: string): ThoughtMapInput {
  // Layer levels by longest path from the roots (nodes without incoming
  // conversation/drilldown edges); rows enumerate within a level. Sibling
  // edges (DSL imports) are branch hints, not structure.
  const links = dag.edges.filter((edge) => edge.kind !== "sibling");
  const incoming = new Map<string, number>();
  for (const edge of links) {
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
  }
  const childrenOf = new Map<string, string[]>();
  for (const edge of links) {
    const children = childrenOf.get(edge.source);
    if (children) children.push(edge.target);
    else childrenOf.set(edge.source, [edge.target]);
  }

  const level = new Map<string, number>();
  const queue: string[] = [];
  for (const node of dag.nodes) {
    if (!incoming.get(node.id)) {
      level.set(node.id, 0);
      queue.push(node.id);
    }
  }
  // Longest-path relaxation: a node reached by a later, longer parent path
  // must be re-relaxed, so re-enqueueing is intentional (the graph is a
  // forest, so each node is popped once per parent).
  for (let head = 0; head < queue.length; head += 1) {
    const id = queue[head];
    for (const child of childrenOf.get(id) ?? []) {
      level.set(child, Math.max(level.get(child) ?? 0, (level.get(id) ?? 0) + 1));
      queue.push(child);
    }
  }

  const rows = new Map<string, number>();
  const perLevel = new Map<number, number>();
  const nodes: ThoughtMapNode[] = dag.nodes.map((node, order) => {
    const lvl = level.get(node.id) ?? 0;
    const row = perLevel.get(lvl) ?? 0;
    perLevel.set(lvl, row + 1);
    rows.set(node.id, row);
    return {
      id: node.id,
      level: lvl,
      row,
      order,
      kind:
        node.kind === "user"
          ? "user"
          : node.kind === "assistant"
            ? "assistant"
            : "other",
      insightType: node.meta.turnInsight?.type,
    };
  });

  return {
    nodes,
    edges: links.map((edge) => ({
      source: edge.source,
      target: edge.target,
      dashed: edge.kind === "drilldown",
    })),
    title,
  };
}
