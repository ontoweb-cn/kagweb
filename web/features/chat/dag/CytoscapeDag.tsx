"use client";

/**
 * Cytoscape canvas rendering the session DAG IR.
 *
 * Rendering concerns only: labels arrive pre-translated from the panel (as a
 * node-id → label map) so this component stays free of i18n; language change
 * is a pure prop update. Theme colors are resolved once from CSS custom
 * properties — cytoscape's style array does not understand `var(--x)`.
 */
import { useEffect, useRef } from "react";
import type { Core, ElementDefinition, LayoutOptions, StylesheetStyle } from "cytoscape";
import { nextZoomTier, type DagZoomTier } from "@/lib/dag-zoom";
import { insightMetaOf } from "@/lib/turn-insight";
import type { SessionDag } from "./model";

/** Available canvas layouts. dagre (layered, edge-aware) is the default;
 * breadthfirst (the original tree layout) stays as the compact fallback. */
export type DagLayoutName = "dagre" | "breadthfirst";

export interface CytoscapeDagProps {
  dag: SessionDag;
  /** Pre-translated labels keyed by node id. */
  labels: Map<string, string>;
  /** Hidden call count per assistant node — rendered as a "+N" suffix when
   * the node is expandable but not yet expanded. */
  childCounts: Map<string, number>;
  onToggleMessage: (nodeId: string) => void;
  selectedNode: string | null;
  onSelectNode: (id: string | null) => void;
  /** Bumped by the parent to request a fit-view. */
  fitToken: number;
  layout: DagLayoutName;
  /** Search hits (#79): non-matching nodes are hidden and the subgraph is
   * fitted. Empty set = no filter active. */
  highlightIds: Set<string>;
}

const APPLY_DEBOUNCE_MS = 500;
/** Never shrink a dense trace into unreadable thumbnail text. Larger graphs
 * stay pannable within the canvas instead of being forced into view. */
const MIN_READABLE_ZOOM = 0.58;
/** Upper bound between rebuilds while updates keep arriving (debounce alone
 * would starve: streaming turns change `dag` more often than the debounce
 * window, resetting the timer indefinitely). */
const APPLY_MAX_WAIT_MS = 2000;

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return value || fallback;
}

function buildStylesheet(): StylesheetStyle[] {
  const primary = cssVar("--primary", "#3b82f6");
  const muted = cssVar("--muted", "#f5f5f5");
  const mutedFg = cssVar("--muted-foreground", "#737373");
  const border = cssVar("--border", "#e5e5e5");
  const card = cssVar("--card", "#ffffff");
  const destructive = cssVar("--destructive", "#ef4444");
  const primaryFg = cssVar("--primary-foreground", "#ffffff");

  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "text-valign": "center",
        "text-halign": "center",
        "text-wrap": "wrap",
        "text-max-width": "156",
        "font-family": "Inter, ui-sans-serif, system-ui, sans-serif",
        "font-size": "11.5",
        "line-height": 1.35,
        color: cssVar("--foreground", "#171717"),
        "background-color": card,
        "border-width": "1",
        "border-color": border,
        shape: "round-rectangle",
        width: "168",
        height: "46",
        "padding-left": "13",
        "padding-right": "13",
        "padding-top": "9",
        "padding-bottom": "9",
      },
    },
    {
      selector: 'node[kind = "root"]',
      style: {
        "background-color": muted,
        "border-color": border,
        color: mutedFg,
        "font-size": "10",
        "font-weight": 500,
        width: "58",
        height: "28",
      },
    },
    {
      selector: 'node[kind = "user"]',
      style: {
        "background-color": muted,
        "border-color": border,
        "font-weight": 500,
      },
    },
    {
      selector: 'node[kind = "assistant"]',
      style: {
        "border-color": primary,
        "border-width": "1.5",
        "font-weight": 500,
        width: "168",
        height: "46",
      },
    },
    {
      selector: 'node[kind = "round"]',
      style: {
        "background-color": muted,
        "border-color": border,
        color: mutedFg,
        width: "92",
        height: "32",
      },
    },
    {
      selector: 'node[kind = "tool_call"], node[kind = "retrieve"], node[kind = "subagent"]',
      style: {
        "background-color": card,
        "border-color": mutedFg,
        "font-size": "10.5",
        width: "140",
        height: "36",
      },
    },
    { selector: 'node[expandable = "true"]', style: { "border-style": "dashed" } },
    { selector: 'node[state = "running"]', style: { "border-color": primary, color: primary } },
    { selector: 'node[state = "error"]', style: { "border-color": destructive, color: destructive } },
    {
      selector: "node:selected",
      style: {
        "border-width": "2",
        "border-color": primary,
        "background-color": primary,
        color: primaryFg,
      },
    },
    // Search hit (#79): the visible subgraph IS the hit set, so the accent
    // is a confirmation, not the only signal.
    { selector: "node.match", style: { "border-color": primary, "border-width": "2" } },
    // Semantic zoom tiers (dag-zoom.ts): classes are applied en masse on
    // tier change — plaque shows the takeaway line, glyph is one dot.
    {
      selector: "node.plaque",
      style: {
        label: "data(plaque)",
        width: "132",
        height: "30",
        "font-size": "10",
        "text-max-width": "120",
        "border-width": "2.5",
        "border-color": "data(badgeColor)",
      },
    },
    {
      selector: "node.glyph",
      style: {
        label: "",
        width: "16",
        height: "16",
        shape: "ellipse",
        "background-color": "data(badgeColor)",
        "border-width": "0",
      },
    },
    {
      selector: 'edge[kind = "conversation"]',
      style: { "line-color": border, "target-arrow-color": border, "target-arrow-shape": "triangle", "arrow-scale": 0.7, width: 1.5 },
    },
    {
      selector: 'edge[kind = "drilldown"]',
      style: {
        "line-color": mutedFg,
        "target-arrow-color": mutedFg,
        "line-style": "dashed",
        "target-arrow-shape": "vee",
        "arrow-scale": 0.6,
        width: 1,
      },
    },
    {
      selector: 'edge[kind = "sibling"]',
      style: {
        "line-color": muted,
        "target-arrow-color": muted,
        "line-style": "dotted",
        "target-arrow-shape": "vee",
        "arrow-scale": 0.6,
        width: 1,
      },
    },
  ];
}

function toElements(
  dag: SessionDag,
  labels: Map<string, string>,
  childCounts: Map<string, number>,
): ElementDefinition[] {
  // Assistant nodes whose call tree is already materialized (has drilldown
  // edges in the IR) lose the "+N" badge.
  const materialized = new Set(
    dag.edges.filter((e) => e.kind === "drilldown").map((e) => e.source),
  );
  const nodes: ElementDefinition[] = dag.nodes.map((node) => {
    const base = labels.get(node.id) ?? node.kind;
    const hidden = childCounts.get(node.id);
    const badge =
      hidden != null && hidden > 0 && !materialized.has(node.id) ? `  ·  ${hidden}` : "";
    const insight = node.meta.turnInsight;
    return {
      group: "nodes" as const,
      data: {
        id: node.id,
        label: `${base}${badge}`,
        kind: node.kind,
        state: node.meta.callState ?? "",
        // Plaque/glyph tiers: the takeaway one-liner and the badge colour.
        plaque: insight?.takeaway ?? base,
        badgeColor: insightMetaOf(insight?.type).color,
        // Cytoscape's `[field = "value"]` compares with strict equality (see
        // its selector `valCmp`), so data must be stringified to match.
        expandable: dag.expandable.has(node.id) ? "true" : "false",
      },
    };
  });
  const edges: ElementDefinition[] = dag.edges.map((edge) => ({
    group: "edges" as const,
    data: { id: edge.id, source: edge.source, target: edge.target, kind: edge.kind },
  }));
  return [...nodes, ...edges];
}

/** @types/cytoscape's LayoutOptions union only covers built-in layouts, so
 * the dagre-specific fields go through BaseLayoutOptions (name: string) with
 * a local cast. */
function buildLayoutOptions(layout: DagLayoutName): LayoutOptions {
  if (layout === "dagre") {
    return {
      name: "dagre",
      rankDir: "TB",
      nodeSep: 28,
      edgeSep: 12,
      rankSep: 44,
      padding: 28,
      fit: false,
      animate: false,
    } as LayoutOptions;
  }
  return {
    name: "breadthfirst",
    directed: true,
    roots: ["root"],
    spacingFactor: 1.35,
    nodeDimensionsIncludeLabels: true,
    padding: 40,
    animate: false,
  };
}

type GraphPosition = { x: number; y: number };

/**
 * Keep ordinary sessions narrow: messages form a vertical reading spine,
 * while each answer's execution steps occupy a single adjacent lane. Dagre
 * remains the fallback for imported branch graphs where a linear timeline
 * would hide the fork structure.
 */
function compactTimelinePositions(dag: SessionDag): Map<string, GraphPosition> | null {
  if (dag.edges.some((edge) => edge.kind === "sibling")) return null;

  const conversationByParent = new Map<string, string>();
  const drilldownByParent = new Map<string, string[]>();
  for (const edge of dag.edges) {
    if (edge.kind === "conversation") {
      if (conversationByParent.has(edge.source)) return null;
      conversationByParent.set(edge.source, edge.target);
    } else if (edge.kind === "drilldown") {
      const children = drilldownByParent.get(edge.source) ?? [];
      children.push(edge.target);
      drilldownByParent.set(edge.source, children);
    }
  }

  const nodeById = dag.byId;
  for (const children of drilldownByParent.values()) {
    children.sort((left, right) => (nodeById.get(left)?.seq ?? 0) - (nodeById.get(right)?.seq ?? 0));
  }

  const spine: string[] = [];
  let current: string | undefined = "root";
  while (current) {
    spine.push(current);
    current = conversationByParent.get(current);
  }
  if (spine.length < 3) return null;

  const positions = new Map<string, GraphPosition>();
  const collectExecution = (parentId: string, result: string[]) => {
    for (const childId of drilldownByParent.get(parentId) ?? []) {
      result.push(childId);
      collectExecution(childId, result);
    }
  };

  let y = 56;
  for (let spineIndex = 0; spineIndex < spine.length; spineIndex += 1) {
    const nodeId = spine[spineIndex];
    const node = nodeById.get(nodeId);
    if (node?.kind === "round") {
      const stages = [nodeId];
      while (spineIndex + 1 < spine.length) {
        const nextId = spine[spineIndex + 1];
        if (nodeById.get(nextId)?.kind !== "round") break;
        stages.push(nextId);
        spineIndex += 1;
      }
      const gap = 118;
      const startX = 150 - ((stages.length - 1) * gap) / 2;
      stages.forEach((stageId, index) => {
        positions.set(stageId, { x: startX + index * gap, y });
      });
      y += 68;
      continue;
    }

    positions.set(nodeId, { x: 150, y });
    y += 76;

    const execution: string[] = [];
    collectExecution(nodeId, execution);
    for (const executionId of execution) {
      positions.set(executionId, { x: 360, y });
      y += 52;
    }
    if (execution.length > 0) y += 16;
  }
  return positions;
}

function panToTimelineStart(cy: Core): void {
  const root = cy.getElementById("root");
  if (root.empty()) return;
  const position = root.position();
  cy.pan({
    x: cy.width() / 2 - position.x * cy.zoom(),
    y: 48 - position.y * cy.zoom(),
  });
}

/** @types/cytoscape does not cover the runtime hide()/show()/class-toggling
 * collection methods — same local-cast approach as buildLayoutOptions. */
interface StyleableCollection {
  addClass(cls: string): void;
  removeClass(cls: string): void;
  show(): void;
  hide(): void;
}

/** Apply the search filter (#79): mark hits, hide misses, fit the subgraph.
 * Called after every rebuild (fresh nodes start visible) and directly on
 * filter changes (no rebuild needed — pan/zoom semantics of a new search
 * intent justify the fit). */
function applyHighlight(cy: Core, highlightIds: ReadonlySet<string>): void {
  const all = cy.nodes() as unknown as StyleableCollection;
  all.removeClass("match");
  all.show();
  if (highlightIds.size === 0) return;
  const matched = cy.nodes().filter((n) => highlightIds.has(n.id()));
  (matched as unknown as StyleableCollection).addClass("match");
  const missed = cy
    .nodes()
    .filter((n) => !highlightIds.has(n.id())) as unknown as StyleableCollection;
  missed.hide();
  // Zero matches: everything is hidden — fitting to the (hidden) graph would
  // drag the viewport across invisible nodes, same trap the ResizeObserver
  // avoids with `:visible`. Keep the viewport; the panel shows "0 matches".
  if (matched.length > 0) cy.fit(matched, 40);
}

function applyToCanvas(
  cy: Core,
  dag: SessionDag,
  labels: Map<string, string>,
  childCounts: Map<string, number>,
  selectedNode: string | null,
  layout: DagLayoutName,
  highlightIds: ReadonlySet<string>,
): boolean {
  cy.elements().remove();
  cy.add(toElements(dag, labels, childCounts));
  cy.nodes().unselect();
  if (selectedNode) cy.getElementById(selectedNode).select();
  cy.layout(buildLayoutOptions(layout)).run();
  const positions = layout === "dagre" ? compactTimelinePositions(dag) : null;
  if (positions) {
    for (const [nodeId, position] of positions) {
      cy.getElementById(nodeId).position(position);
    }
  }
  applyHighlight(cy, highlightIds);
  if (highlightIds.size === 0) {
    cy.fit(undefined, 40);
    if (positions) panToTimelineStart(cy);
  }
  return positions !== null;
}

export default function CytoscapeDag({
  dag,
  labels,
  childCounts,
  onToggleMessage,
  selectedNode,
  onSelectNode,
  fitToken,
  layout,
  highlightIds,
}: CytoscapeDagProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const latestRef = useRef({ dag, labels, childCounts, selectedNode, layout, highlightIds });
  const tierRef = useRef<DagZoomTier>("turn");
  const lastApplyRef = useRef(0);
  const pendingFitRef = useRef(false);

  // Keep the handler-facing snapshot fresh — in an effect (not during render)
  // per React's concurrent-rendering rules.
  useEffect(() => {
    latestRef.current = { dag, labels, childCounts, selectedNode, layout, highlightIds };
  }, [dag, labels, childCounts, selectedNode, layout, highlightIds]);

  // Mount / unmount: create the instance lazily (cytoscape is a browser-only
  // UMD bundle — keep the import out of SSR), keep it alive across updates.
  // cytoscape-dagre's ESM build bundles the dagre algorithm and takes the
  // cytoscape lib as an argument, so it composes with the cytoscape CJS
  // alias without any interop wrangling (#65).
  useEffect(() => {
    let disposed = false;
    let observer: ResizeObserver | null = null;

    (async () => {
      const [cytoscape, registerDagre] = await Promise.all([
        import("cytoscape").then((m) => m.default),
        import("cytoscape-dagre").then((m) => m.default),
      ]);
      if (disposed || !containerRef.current) return;
      registerDagre(cytoscape);
      const cy = cytoscape({
        container: containerRef.current,
        elements: [],
        style: buildStylesheet(),
        layout: { name: "grid" },
        wheelSensitivity: 0.2,
        minZoom: MIN_READABLE_ZOOM,
        // Keep sparse sessions from enlarging until Q/A labels dominate the
        // whole drawer, while MIN_READABLE_ZOOM keeps dense traces legible.
        maxZoom: 1.15,
      });
      cyRef.current = cy;

      cy.on("tap", "node", (event) => {
        const id = event.target.id();
        const node = latestRef.current.dag.byId.get(id);
        if (
          (node?.kind === "assistant" || node?.kind === "round") &&
          latestRef.current.dag.expandable.has(id)
        ) {
          onToggleMessage(id);
        } else {
          onSelectNode(id);
        }
      });
      // Semantic zoom (dag-zoom.ts): on tier change, restyle nodes en masse —
      // nodes carrying an insight become plaques / colored dots. The tier
      // guard keeps this off the high-frequency zoom handler's hot path.
      cy.on("zoom", () => {
        const next = nextZoomTier(tierRef.current, cy.zoom());
        if (next === tierRef.current) return;
        tierRef.current = next;
        cy.batch(() => {
          cy.nodes().forEach((node) => {
            node.removeClass("plaque glyph");
            if (next !== "turn") node.addClass(next);
          });
        });
      });

      cy.on("tap", (event) => {
        if (event.target === cy) onSelectNode(null);
      });

      observer = new ResizeObserver(() => {
        cy.resize();
        // Visible-only: during an active search the hidden majority must
        // not drag the viewport back out to the full graph (#79).
        cy.fit(cy.nodes(":visible"), 40);
        const latest = latestRef.current;
        if (
          latest.highlightIds.size === 0 &&
          latest.layout === "dagre" &&
          compactTimelinePositions(latest.dag)
        ) {
          panToTimelineStart(cy);
        }
      });
      observer.observe(containerRef.current);

      const { dag, labels, childCounts, selectedNode, layout, highlightIds } = latestRef.current;
      applyToCanvas(cy, dag, labels, childCounts, selectedNode, layout, highlightIds);
      lastApplyRef.current = Date.now();
      if (pendingFitRef.current) {
        pendingFitRef.current = false;
        cy.resize();
        cy.fit(undefined, 40);
        if (layout === "dagre" && compactTimelinePositions(dag)) {
          panToTimelineStart(cy);
        }
      }
    })();

    return () => {
      disposed = true;
      observer?.disconnect();
      cyRef.current?.destroy();
      cyRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounced rebuild when the graph shape changes. A max-wait bound keeps
  // continuous streaming updates from resetting the debounce forever.
  // Selection is deliberately NOT a rebuild trigger — see the effect below.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const sinceLast = Date.now() - lastApplyRef.current;
    const delay =
      sinceLast >= APPLY_MAX_WAIT_MS
        ? 0
        : Math.min(APPLY_DEBOUNCE_MS, APPLY_MAX_WAIT_MS - sinceLast);
    const timer = setTimeout(() => {
      const { dag, labels, childCounts, selectedNode, layout, highlightIds } = latestRef.current;
      applyToCanvas(cy, dag, labels, childCounts, selectedNode, layout, highlightIds);
      lastApplyRef.current = Date.now();
    }, delay);
    return () => clearTimeout(timer);
  }, [dag, labels, childCounts]);

  // Layout switch is an immediate full re-apply — the user expects the graph
  // to snap to the new arrangement, not wait out the debounce window. On
  // first render cyRef is still null (cytoscape loads async) and the mount
  // effect applies latestRef.current.layout, so the early return is safe.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const { dag, labels, childCounts, selectedNode, highlightIds } = latestRef.current;
    applyToCanvas(cy, dag, labels, childCounts, selectedNode, layout, highlightIds);
    lastApplyRef.current = Date.now();
  }, [layout]);

  // Search filter (#79): a pure visibility change — no rebuild, no re-layout.
  // The applyToCanvas path replays this after rebuilds, so streaming updates
  // cannot resurrect hidden nodes.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    applyHighlight(cy, highlightIds);
  }, [highlightIds]);

  // Selection is a pure canvas state change — never rebuild, never re-layout,
  // so the user's pan/zoom survives tapping a node to inspect it.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().unselect();
    if (selectedNode) cy.getElementById(selectedNode).select();
  }, [selectedNode]);

  // External fit requests (panel "Fit view" button, panel re-open). If the
  // async cytoscape import has not landed yet, remember the intent and run
  // it right after mount instead of dropping it.
  useEffect(() => {
    if (fitToken <= 0) return;
    const cy = cyRef.current;
    if (!cy) {
      pendingFitRef.current = true;
      return;
    }
    cy.resize();
    cy.fit(undefined, 40);
    const { dag: latestDag, layout: latestLayout } = latestRef.current;
    if (latestLayout === "dagre" && compactTimelinePositions(latestDag)) {
      panToTimelineStart(cy);
    }
  }, [fitToken]);

  return (
    <div
      ref={containerRef}
      className="h-full w-full min-h-0 touch-none bg-[var(--background)]"
      aria-label="Session trace graph"
    />
  );
}
