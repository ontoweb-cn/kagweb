/**
 * Thought-map export: the *shape* of a session as an SVG — nodes as
 * dots/squares, edges as lines, badges by epistemic type, time-ink gradient by
 * message order. Zero conversation text: the input type carries no text
 * fields and the serializer never receives messages, so a leak is
 * unrepresentable by construction.
 */
import { nodeInkColor, type InsightType } from "@/lib/turn-insight";

export interface ThoughtMapNode {
  id: string;
  /** Topological level (column) and row within the level — the exporter
      lays nodes out on a simple grid; it never sees a live canvas. */
  level: number;
  row: number;
  /** Position in message order, for the time-ink gradient. Falls back to
      `row` when a caller has no ordering to offer. */
  order?: number;
  kind: "user" | "assistant" | "other";
  /** The judge's badge type; `InsightType` rather than `string` so no
      free-form text can reach the caption. */
  insightType?: InsightType;
}

export interface ThoughtMapEdge {
  source: string;
  target: string;
  dashed?: boolean;
}

export interface ThoughtMapInput {
  nodes: ThoughtMapNode[];
  edges: ThoughtMapEdge[];
  /** Session title for the caption — user-authored text is allowed. */
  title?: string;
}

const W = 96; // column pitch
const H = 56; // row pitch
const M = 48; // margin

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function buildThoughtMapSvg(input: ThoughtMapInput): string {
  const pos = new Map<string, { x: number; y: number }>();
  let maxLevel = 0;
  let maxRow = 0;
  for (const n of input.nodes) {
    maxLevel = Math.max(maxLevel, n.level);
    maxRow = Math.max(maxRow, n.row);
    pos.set(n.id, { x: M + n.level * W, y: M + n.row * H });
  }
  // Sized from the deepest row, not the node count: a level holding k nodes
  // reaches y = M + (k-1)*H, which a count-based height clips.
  const width = M * 2 + Math.max(1, maxLevel + 1) * W;
  const height = M * 2 + Math.max(1, maxRow + 1) * H;

  // Time ink follows message order, not the row within a level — a linear
  // session has one node per level, so a row-based gradient would be flat.
  const ink = (n: ThoughtMapNode): number =>
    n.kind === "assistant"
      ? 0.45 + 0.55 * Math.min(1, (n.order ?? n.row) / 12)
      : 1;

  const edges = input.edges
    .filter((e) => pos.has(e.source) && pos.has(e.target))
    .map((e) => {
      const a = pos.get(e.source)!;
      const b = pos.get(e.target)!;
      const dash = e.dashed ? ' stroke-dasharray="4 4"' : "";
      return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#94a3b8" stroke-width="1.5"${dash} opacity="0.7"/>`;
    })
    .join("\n  ");

  const nodes = input.nodes
    .map((n) => {
      const p = pos.get(n.id)!;
      // Shared ink helper: the badge palette when there is a badge, else the
      // neutral kind colour — the same rule the DAG canvas applies.
      const color = nodeInkColor(n.insightType, n.kind);
      if (n.kind === "user") {
        return `<rect x="${p.x - 5}" y="${p.y - 5}" width="10" height="10" rx="2" fill="${color}" opacity="${ink(n)}"/>`;
      }
      return `<circle cx="${p.x}" cy="${p.y}" r="8" fill="${color}" opacity="${ink(n)}"/>`;
    })
    .join("\n  ");

  const counts = input.nodes.reduce<Record<string, number>>((acc, n) => {
    const key = n.insightType ?? n.kind;
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
  const legend = Object.entries(counts)
    .map(([k, v]) => `${esc(k)}×${v}`)
    .join(" · ");
  const caption = esc(input.title ? `${input.title} — ${legend}` : legend);

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="Inter, system-ui, sans-serif">
  <rect width="100%" height="100%" fill="#f8fafc"/>
  ${edges}
  ${nodes}
  <text x="${M - 16}" y="${height - M + 18}" font-size="12" fill="#475569">${caption}</text>
</svg>`;
}

export function downloadThoughtMapSvg(svg: string, filename: string): void {
  const blob = new Blob([svg], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
