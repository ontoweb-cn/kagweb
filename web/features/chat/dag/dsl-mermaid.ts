/**
 * DSL → Mermaid codegen.
 *
 * Renders a Session DSL document as a `flowchart TD` diagram for CI
 * reports and offline sharing (rendered by the existing
 * `components/Mermaid.tsx` pipeline, `securityLevel: "strict"`). Pure text
 * generation — no DOM, no rendering — so it runs in node tests as well.
 */
import type {
  DslBranch,
  DslCallEntry,
  DslDocument,
  DslTraceEntry,
} from "./dsl";

/** Node labels stay short for diagram readability (previews run to 140). */
const LABEL_LIMIT = 48;

/** DSL ids contain `:` (msg:1, turn:2.1, b1:turn:1) which mermaid ids do not
 * accept — sanitize and dedupe against already-registered nodes (#49). */
function sanitizeId(id: string, used: Set<string>): string {
  let base = id.replace(/[^A-Za-z0-9_-]/g, "_");
  if (!base) base = "n";
  let candidate = base;
  let suffix = 1;
  while (used.has(candidate)) {
    candidate = `${base}_${suffix}`;
    suffix += 1;
  }
  used.add(candidate);
  return candidate;
}

/** `"` ends the quoted label, `#` prefixes mermaid entity codes, newlines
 * break the syntax — scrub all three (#50). */
function sanitizeLabel(text: string | undefined): string {
  if (!text) return "";
  const flat = text.replace(/["#\r\n]/g, " ").trim();
  return flat.length > LABEL_LIMIT ? `${flat.slice(0, LABEL_LIMIT)}...` : flat;
}

function labelForTrace(entry: DslTraceEntry): string {
  if (entry.kind === "user") return `User: ${sanitizeLabel(entry.text_preview)}`;
  const capability = entry.capability ? ` (${sanitizeLabel(entry.capability)})` : "";
  return `Assistant${capability}`;
}

function labelForCall(entry: DslCallEntry): string {
  switch (entry.kind) {
    case "round":
      return `Round${entry.round_index != null ? ` ${entry.round_index + 1}` : ""}`;
    case "retrieve":
      return `Retrieve: ${sanitizeLabel(entry.query)}`;
    case "subagent":
      return `Subagent: ${sanitizeLabel(entry.subagent_name)}`;
    default:
      return `Tool: ${sanitizeLabel(entry.tool)}`;
  }
}

interface RenderContext {
  lines: string[];
  used: Set<string>;
  idByNode: Map<string, string>;
}

function registerNode(ctx: RenderContext, nodeId: string, label: string): string {
  const mermaidId = sanitizeId(nodeId, ctx.used);
  ctx.idByNode.set(nodeId, mermaidId);
  ctx.lines.push(`    ${mermaidId}["${label}"]`);
  return mermaidId;
}

function renderCalls(
  ctx: RenderContext,
  calls: DslCallEntry[],
  parentNode: string,
): void {
  let parent = parentNode;
  for (const call of calls) {
    const id = registerNode(ctx, call.node, labelForCall(call));
    ctx.lines.push(`    ${parent} --> ${id}`);
    if (call.calls?.length) renderCalls(ctx, call.calls, id);
    parent = id;
  }
}

function renderTraceChain(
  ctx: RenderContext,
  trace: DslTraceEntry[],
  startNode: string | null,
): string | null {
  let prev = startNode;
  for (const entry of trace) {
    const id = registerNode(ctx, entry.node, labelForTrace(entry));
    if (prev) ctx.lines.push(`    ${prev} --> ${id}`);
    if (entry.calls?.length) renderCalls(ctx, entry.calls, id);
    if (entry.branches?.length) {
      for (const [position, branch] of entry.branches.entries()) {
        renderBranch(ctx, branch, position + 1, id);
      }
    }
    prev = id;
  }
  return prev;
}

function renderBranch(
  ctx: RenderContext,
  branch: DslBranch,
  position: number,
  forkNode: string,
): void {
  // Dashed alternative-path edge; the branch's own chain renders inline
  // (single-level expansion is already guaranteed by the DSL itself).
  const first = branch.trace[0];
  if (!first) return;
  const firstId = sanitizeId(first.node, ctx.used);
  ctx.idByNode.set(first.node, firstId);
  ctx.lines.push(`    ${firstId}["${labelForTrace(first)}"]`);
  ctx.lines.push(`    ${forkNode} -. "branch ${position}" .-> ${firstId}`);
  if (first.calls?.length) renderCalls(ctx, first.calls, firstId);
  renderTraceChain(ctx, branch.trace.slice(1), firstId);
}

export function dslToMermaid(doc: DslDocument): string {
  const ctx: RenderContext = {
    lines: ["flowchart TD"],
    used: new Set<string>(),
    idByNode: new Map<string, string>(),
  };
  renderTraceChain(ctx, doc.trace, null);
  return `${ctx.lines.join("\n")}\n`;
}
