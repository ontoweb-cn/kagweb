"use client";

/**
 * SessionDagPanel — right-side drawer showing the whole conversation as a
 * trace DAG: one node per visible message, drilldown into rounds / tool
 * calls / retrievals / subagents on tap.
 *
 * All i18n label generation lives here (CytoscapeDag stays a pure renderer).
 * The DAG is only aggregated while `open` — the panel is mounted by
 * ChatWorkspace unconditionally, and `messages` churns on every streaming
 * event, so gating the computation here keeps a closed panel at zero cost.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Maximize2, Download, Code2, Upload, ArrowLeft, Network, ListTree, LocateFixed, Search, Map as MapIcon } from "lucide-react";
import dynamic from "next/dynamic";
import { ActivityDetailGrid, type DetailRow } from "@/components/activity";
import Tooltip from "@/components/common/Tooltip";
import { computeSessionDag, searchDagNodes, type DagMessage } from "./aggregate";
import { browserStorage } from "@/shared/storage";
import {
  buildSessionDslDocument,
  parseSessionDsl,
  serializeSessionDsl,
  type DslDocument,
} from "./dsl";
import { dslToMermaid } from "./dsl-mermaid";
import { buildThoughtMapSvg, downloadThoughtMapSvg } from "@/lib/thought-map-export";
import { buildThoughtMapInput } from "./thought-map";
import { dslToDag } from "./dsl-import";
import type { DagNode, SessionDag } from "./model";
import type { DagLayoutName } from "./CytoscapeDag";

const CytoscapeDag = dynamic(() => import("./CytoscapeDag"), { ssr: false });

/** Import guard: DSL documents are snapshots; anything past this size is
 * pathological and would jank the canvas (#61). */
const IMPORT_MAX_BYTES = 10 * 1024 * 1024;
const NO_HIDDEN_CHILDREN = new Map<string, number>();

/** Reasoning stages stay visible between the question and answer. Their
 * execution trees are presented in the detail pane, so the graph stays calm. */
function withCollapsedRounds(dag: SessionDag): SessionDag {
  const children = new Map<string, string[]>();
  for (const edge of dag.edges) {
    if (edge.kind !== "drilldown") continue;
    const ids = children.get(edge.source) ?? [];
    ids.push(edge.target);
    children.set(edge.source, ids);
  }

  const hidden = new Set<string>();
  const hasExecutionDescendant = (nodeId: string): boolean =>
    (children.get(nodeId) ?? []).some((childId) => {
      const child = dag.byId.get(childId);
      return child?.kind !== "round" || hasExecutionDescendant(childId);
    });
  const hideExecutionDescendants = (nodeId: string) => {
    for (const childId of children.get(nodeId) ?? []) {
      const child = dag.byId.get(childId);
      if (child?.kind === "round") {
        hideExecutionDescendants(childId);
        continue;
      }
      hidden.add(childId);
      hideExecutionDescendants(childId);
    }
  };
  for (const node of dag.nodes) {
    if (node.kind !== "round") continue;
    if (!hasExecutionDescendant(node.id)) {
      hidden.add(node.id);
      hideExecutionDescendants(node.id);
    }
    else hideExecutionDescendants(node.id);
  }
  const expandable = new Set(dag.expandable);
  for (const node of dag.nodes) {
    if (node.kind === "round" && hasExecutionDescendant(node.id)) expandable.add(node.id);
  }
  let nodes = dag.nodes.filter((node) => !hidden.has(node.id));
  let edges = dag.edges.filter(
    (edge) => !hidden.has(edge.source) && !hidden.has(edge.target),
  );

  // The protocol correctly owns call rounds under the assistant message.
  // For reading, place those checkpoints before the answer: Q -> stages -> A.
  const parentOverrides = new Map<string, string>();
  for (const assistant of nodes.filter((node) => node.kind === "assistant")) {
    const stages: DagNode[] = [];
    const collectStages = (parentId: string) => {
      for (const childId of children.get(parentId) ?? []) {
        const child = dag.byId.get(childId);
        if (child?.kind !== "round") continue;
        if (!hidden.has(child.id)) stages.push(child);
        collectStages(child.id);
      }
    };
    collectStages(assistant.id);
    stages.sort((left, right) => left.seq - right.seq);
    if (stages.length === 0) continue;
    const incoming = edges.find(
      (edge) => edge.kind === "conversation" && edge.target === assistant.id,
    );
    if (!incoming) continue;
    const stageIds = new Set(stages.map((stage) => stage.id));
    edges = edges.filter(
      (edge) =>
        edge.id !== incoming.id &&
        !(edge.source === assistant.id && stageIds.has(edge.target)) &&
        !(stageIds.has(edge.source) && stageIds.has(edge.target)),
    );
    const path = [incoming.source, ...stages.map((stage) => stage.id), assistant.id];
    for (let index = 0; index < path.length - 1; index += 1) {
      const source = path[index];
      const target = path[index + 1];
      edges.push({ id: `ui:${source}->${target}`, source, target, kind: "conversation" });
      parentOverrides.set(target, source);
    }
  }
  if (parentOverrides.size > 0) {
    nodes = nodes.map((node) =>
      parentOverrides.has(node.id)
        ? { ...node, parentId: parentOverrides.get(node.id)! }
        : node,
    );
  }
  return { ...dag, nodes, edges, byId: new Map(nodes.map((node) => [node.id, node])), expandable };
}

export interface SessionDagPanelProps {
  open: boolean;
  messages: DagMessage[];
  selectedBranches?: Record<string, number>;
  /** Session id for the DSL export header. */
  sessionId?: string;
  /** Bring the live chat message behind a node on screen (module 16). The
   * panel stays renderer-pure: the workspace owns the transcript scroll
   * container and the flash styling. Only live-mode nodes carry a positive
   * messageId; imported snapshots never show the control. */
  onLocateMessage?: (messageId: number) => void;
}

export default function SessionDagPanel({
  open,
  messages,
  selectedBranches,
  sessionId,
  onLocateMessage,
}: SessionDagPanelProps) {
  const { t } = useTranslation();
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [fitToken, setFitToken] = useState(0);
  const [imported, setImported] = useState<{ doc: DslDocument; filename: string } | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [layout, setLayout] = useState<DagLayoutName>("dagre");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // Layout preference survives reloads (module 20). Read post-mount (SSR
  // renders the default first — no hydration mismatch), same pattern as the
  // workspace's panel toggles.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = browserStorage.readRaw("local", "dt:chat:dag-layout");
    if (saved === "dagre" || saved === "breadthfirst") {
      // One-shot hydration from an external store (localStorage); the
      // cascading-render concern does not apply to a mount-time read.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLayout(saved);
    }
  }, []);
  const changeLayout = useCallback((next: DagLayoutName) => {
    setLayout(next);
    browserStorage.writeRaw("local", "dt:chat:dag-layout", next);
  }, []);
  // Trace search (#79): input feeds a debounced query so per-keystroke Set
  // rebuilds (over up to DAG_NODE_LIMIT nodes) stay off the typing path.
  const [searchInput, setSearchInput] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setSearchQuery(searchInput), 200);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // Gated on `open`: state.messages changes identity on every streaming
  // event, and the panel stays mounted while hidden, so an ungated memo
  // would re-aggregate the whole session per event for nothing. An imported
  // DSL document takes over the view (module 11).
  const fullDag = useMemo(() => {
    if (!open) return null;
    if (imported) return dslToDag(imported.doc);
    return computeSessionDag(
      { messages, selectedBranches },
      null,
      { expandAll: true, truncate: true },
    );
  }, [open, imported, messages, selectedBranches]);

  const dag = useMemo(() => (fullDag ? withCollapsedRounds(fullDag) : null), [fullDag]);

  const labels = useMemo(() => {
    const map = new Map<string, string>();
    if (!dag) return map;
    for (const node of dag.nodes) map.set(node.id, labelFor(node, t));
    return map;
  }, [dag, t]);

  // Search hits over the current (possibly imported) DAG (#79). Derived
  // from the debounced query, not the raw input.
  const searchMatches = useMemo(
    () => (fullDag ? searchDagNodes(fullDag, searchQuery) : new Set<string>()),
    [fullDag, searchQuery],
  );
  const highlightIds = useMemo(() => {
    const visible = new Set<string>();
    if (!dag || !fullDag) return visible;
    for (const matchId of searchMatches) {
      let node = fullDag.byId.get(matchId);
      while (node && !dag.byId.has(node.id)) {
        node = node.parentId ? fullDag.byId.get(node.parentId) : undefined;
      }
      if (node) visible.add(node.id);
    }
    return visible;
  }, [dag, fullDag, searchMatches]);

  const executionSteps = useMemo(() => {
    if (!selectedNode || !fullDag || fullDag.byId.get(selectedNode)?.kind !== "round") return [];
    const children = new Map<string, DagNode[]>();
    for (const node of fullDag.nodes) {
      if (!node.parentId) continue;
      const entries = children.get(node.parentId) ?? [];
      entries.push(node);
      children.set(node.parentId, entries);
    }
    const steps: DagNode[] = [];
    const visit = (parentId: string) => {
      for (const child of children.get(parentId) ?? []) {
        if (child.kind === "round") continue;
        steps.push(child);
        visit(child.id);
      }
    };
    visit(selectedNode);
    return steps;
  }, [fullDag, selectedNode]);

  // Export the full session DSL — all call trees expanded, independent of the
  // panel's expansion state (#33). Whitelisted fields only.
  const handleExportDsl = useCallback(() => {
    const text = serializeSessionDsl({ messages, selectedBranches }, { sessionId });
    const blob = new Blob([text], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `session-dsl-${sessionId || "export"}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }, [messages, selectedBranches, sessionId]);

  // Mermaid codegen for CI/reports — rendered offline by the existing
  // Mermaid.tsx pipeline (securityLevel: "strict").
  const handleExportMermaid = useCallback(() => {
    const doc = buildSessionDslDocument({ messages, selectedBranches }, { sessionId });
    const text = dslToMermaid(doc);
    const blob = new Blob([text], { type: "text/vnd.mermaid" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `session-dsl-${sessionId || "export"}.mmd`;
    link.click();
    URL.revokeObjectURL(url);
  }, [messages, selectedBranches, sessionId]);

  const handleExportThoughtMap = useCallback(() => {
    // `fullDag`, not the rendered `dag`: the export must be independent of
    // the panel's expansion state (#33) — the collapsed view drops every
    // tool/retrieval/subagent node.
    if (!fullDag) return;
    downloadThoughtMapSvg(
      buildThoughtMapSvg(buildThoughtMapInput(fullDag, sessionId)),
      `thought-map-${sessionId || "export"}.svg`,
    );
  }, [fullDag, sessionId]);

  // Import a DSL export and preview it as a DAG (module 11). Rendering-only:
  // the parsed document never touches session state or persistence.
  const handleImportDsl = useCallback(async (file: File) => {
    setImportError(null);
    if (file.size > IMPORT_MAX_BYTES) {
      setImportError(t("File exceeds the 10 MB import limit"));
      return;
    }
    try {
      const doc = parseSessionDsl(await file.text());
      setImported({ doc, filename: file.name });
      setSelectedNode(null);
      setFitToken((v) => v + 1);
    } catch (err) {
      setImportError(t("Invalid DSL file: {{message}}", { message: (err as Error).message }));
    }
  }, [t]);

  const detailRows = useMemo<DetailRow[]>(() => {
    if (!selectedNode || !dag) return [];
    const node = dag.byId.get(selectedNode);
    if (!node) return [];
    const rows: DetailRow[] = [];
    const meta = node.meta;
    if (meta.messageRole) rows.push({ key: "role", value: meta.messageRole });
    if (meta.capability) rows.push({ key: "capability", value: meta.capability });
    if (meta.branchInfo)
      rows.push({
        key: "branch",
        value: t("Branch {{index}} of {{total}}", {
          index: meta.branchInfo.index,
          total: meta.branchInfo.total,
        }),
      });
    if (meta.callKind) rows.push({ key: "callKind", value: meta.callKind, mono: true });
    if (meta.toolName) rows.push({ key: "tool", value: meta.toolName, mono: true });
    if (meta.provider) rows.push({ key: "provider", value: meta.provider, mono: true });
    if (meta.subagentName) rows.push({ key: "subagent", value: meta.subagentName, mono: true });
    if (meta.roundIndex != null)
      rows.push({ key: t("Reasoning"), value: String(meta.roundIndex + 1) });
    if (meta.callState) rows.push({ key: "state", value: meta.callState });
    if (meta.durationMs != null)
      rows.push({ key: "duration", value: `${Math.round(meta.durationMs / 100) / 10}s` });
    if (meta.query) rows.push({ key: "query", value: meta.query, mono: true });
    if (meta.error) rows.push({ key: "error", value: meta.error });
    if (meta.textPreview) rows.push({ key: "preview", value: meta.textPreview });
    return rows;
  }, [selectedNode, dag, t]);

  if (!open || !dag) return null;

  // Live-mode nodes carry the persisted message id; optimistic (negative)
  // and imported-snapshot nodes have none — no control is rendered then.
  const locateTargetId =
    onLocateMessage && selectedNode
      ? (dag.byId.get(selectedNode)?.meta.messageId ?? null)
      : null;
  const locateable = locateTargetId != null && locateTargetId > 0;

  return (
    <section
      className="flex h-full min-h-0 flex-col bg-[var(--card)]"
      aria-label={t("Session DAG")}
    >
      <header className="flex min-h-11 shrink-0 items-center justify-between border-b border-[var(--border)] px-3">
        <p className="min-w-0 truncate text-xs text-[var(--muted-foreground)]">
          {t("{{n}} messages", { n: dag.nodes.filter((n) => n.kind === "user" || n.kind === "assistant").length })}
          <span className="mx-1.5 opacity-50">·</span>
          {t("Conversation and reasoning details")}
        </p>
        <div className="flex items-center gap-1">
          {!imported && (
            <>
              <Tooltip label={t("Import DSL")} side="bottom">
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
                  aria-label={t("Import DSL")}
                >
                  <Upload size={16} />
                </button>
              </Tooltip>
              <Tooltip label={t("Export DSL")} side="bottom">
                <button
                  onClick={handleExportDsl}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
                  aria-label={t("Export DSL")}
                >
                  <Download size={16} />
                </button>
              </Tooltip>
              <Tooltip label={t("Export Mermaid")} side="bottom">
                <button
                  onClick={handleExportMermaid}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
                  aria-label={t("Export Mermaid")}
                >
                  <Code2 size={16} />
                </button>
              </Tooltip>
              <Tooltip label={t("Export thought map (shape only, no text)")} side="bottom">
                <button
                  onClick={handleExportThoughtMap}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
                  aria-label={t("Export thought map (shape only, no text)")}
                >
                  <MapIcon size={16} />
                </button>
              </Tooltip>
            </>
          )}
          <Tooltip label={layout === "dagre" ? t("Layered layout") : t("Tree layout")} side="bottom">
            <button
                onClick={() => changeLayout(layout === "dagre" ? "breadthfirst" : "dagre")}
                className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
                aria-label={t("Switch layout")}
                aria-pressed={layout === "dagre"}
              >
              {layout === "dagre" ? <Network size={16} /> : <ListTree size={16} />}
            </button>
          </Tooltip>
          <Tooltip label={t("Fit view")} side="bottom">
            <button
              onClick={() => setFitToken((v) => v + 1)}
              className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
              aria-label={t("Fit view")}
            >
              <Maximize2 size={16} />
            </button>
          </Tooltip>
        </div>
      </header>

      <input
        ref={fileInputRef}
        type="file"
        accept="application/json,.json"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleImportDsl(file);
          e.target.value = ""; // allow re-importing the same file
        }}
      />

      {imported && (
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--border)] bg-[var(--muted)] px-4 py-1.5 text-xs">
          <span className="truncate text-[var(--muted-foreground)]">
            {t("Imported DSL view: {{file}}", { file: imported.filename })}
          </span>
          <button
            onClick={() => {
              setImported(null);
              setSelectedNode(null);
            }}
            className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--card)] hover:text-[var(--foreground)]"
          >
            <ArrowLeft size={14} />
            {t("Back to session")}
          </button>
        </div>
      )}

      {dag.truncated ? (
        <div className="shrink-0 border-b border-[var(--border)] bg-[var(--muted)]/60 px-4 py-1.5 text-xs text-[var(--muted-foreground)]">
          {t("Trace truncated: {{n}} call nodes beyond the {{limit}}-node budget were hidden", {
            n: dag.truncated.dropped,
            limit: dag.truncated.limit,
          })}
        </div>
      ) : null}

      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-[var(--border)] px-4">
        <Search size={14} className="shrink-0 text-[var(--muted-foreground)]" />
        <input
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder={t("Search trace (tools, queries, text)")}
          className="h-full min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-[var(--muted-foreground)]"
          aria-label={t("Search trace (tools, queries, text)")}
        />
        {searchQuery.trim() ? (
          <span className="shrink-0 text-[11px] text-[var(--muted-foreground)]">
            {t("{{n}} matches", { n: searchMatches.size })}
          </span>
        ) : null}
      </div>

      {importError && (
        <div className="shrink-0 border-b border-[var(--destructive)]/30 bg-[var(--destructive)]/10 px-4 py-1.5 text-xs text-[var(--destructive)]">
          {importError}
        </div>
      )}

      <div className="relative min-h-0 flex-1 bg-[var(--background)]">
        {dag.nodes.length > 1 ? (
          <div className="pointer-events-none absolute left-3 top-3 z-10 flex items-center gap-3 rounded-md bg-[var(--card)]/90 px-2 py-1 text-[10px] text-[var(--muted-foreground)] shadow-sm ring-1 ring-[var(--border)]">
            <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-sm bg-[var(--muted-foreground)]" />{t("Question")}</span>
            <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-sm border-2 border-[var(--primary)]" />{t("Answer")}</span>
            <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-[var(--border)]" />{t("Reasoning")}</span>
          </div>
        ) : null}
        {dag.nodes.length <= 1 ? (
          <div className="flex h-full items-center justify-center px-6 text-center text-sm text-[var(--muted-foreground)]">
            {t("No trace events in this session yet")}
          </div>
        ) : (
          <CytoscapeDag
            dag={dag}
            labels={labels}
            childCounts={NO_HIDDEN_CHILDREN}
            onToggleMessage={setSelectedNode}
            selectedNode={selectedNode}
            onSelectNode={setSelectedNode}
            fitToken={fitToken}
            layout={layout}
            highlightIds={highlightIds}
          />
        )}
      </div>

      {detailRows.length > 0 ? (
        <footer className="max-h-[38%] shrink-0 overflow-y-auto border-t border-[var(--border)] bg-[var(--card)] px-4 py-3">
          <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">{t("Node details")}</div>
          <ActivityDetailGrid rows={detailRows} />
          {executionSteps.length > 0 ? (
            <div className="mt-3 border-t border-[var(--border)] pt-3">
              <div className="mb-2 text-[11px] font-medium text-[var(--muted-foreground)]">
                {t("Execution steps")}
              </div>
              <ol className="space-y-1">
                {executionSteps.map((step, index) => (
                  <li key={step.id} className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-[var(--muted)]/60">
                    <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[var(--muted)] text-[9px] text-[var(--muted-foreground)]">
                      {index + 1}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{labelFor(step, t)}</span>
                    {step.meta.callState ? (
                      <span className="shrink-0 text-[10px] text-[var(--muted-foreground)]">{step.meta.callState}</span>
                    ) : null}
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
          {locateable ? (
            <div className="mt-3 border-t border-[var(--border)] pt-3">
              <button
                onClick={() => onLocateMessage?.(locateTargetId!)}
                className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
              >
                <LocateFixed size={13} />
                {t("Locate in conversation")}
              </button>
            </div>
          ) : null}
        </footer>
      ) : null}
    </section>
  );
}

function labelFor(node: DagNode, t: (key: string, opts?: Record<string, unknown>) => string): string {
  switch (node.kind) {
    case "root":
      return t("Session");
    case "user": {
      const q = node.meta.messageIndex != null ? Math.floor(node.meta.messageIndex / 2) + 1 : 1;
      const preview = node.meta.textPreview?.replace(/\s+/g, " ").trim();
      const summary = preview && preview.length > 26 ? `${preview.slice(0, 26)}…` : preview;
      return summary ? `${t("Question")} ${q}\n${summary}` : `${t("Question")} ${q}`;
    }
    case "assistant": {
      const a = node.meta.messageIndex != null ? Math.floor(node.meta.messageIndex / 2) + 1 : 1;
      const branch = node.meta.branchInfo ? ` ${node.meta.branchInfo.index}/${node.meta.branchInfo.total}` : "";
      const preview = node.meta.textPreview?.replace(/\s+/g, " ").trim();
      const summary = preview && preview.length > 26 ? `${preview.slice(0, 26)}…` : preview;
      return summary ? `${t("Answer")} ${a}${branch}\n${summary}` : `${t("Answer")} ${a}${branch}`;
    }
    case "round":
      return `${t("Reasoning")} ${(node.meta.roundIndex ?? 0) + 1}`;
    case "tool_call":
      return node.meta.toolName ?? t("Tool call");
    case "retrieve":
      return t("Retrieval");
    case "subagent":
      return node.meta.subagentName ?? t("Subagent");
  }
}
