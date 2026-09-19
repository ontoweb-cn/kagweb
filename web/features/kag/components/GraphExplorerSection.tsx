"use client";

/**
 * M3.4 图浏览：reason DSL 查询 + 表格 + cytoscape 画布。
 *
 * 数据源经 /api/kag/projects/{id}/graph/query（/public/v1/reason/run——graph
 * 控制器无子图查询端点，M2 侦察修正）。2 列 id 的 rows 直接映射为边
 * （rowsToGraph）；模板按钮按 Schema 类型生成 DSL（节点类型须 namespace
 * 全名，关系 label 裸名——M3.3 实测契约）。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
import { Network, Play } from "lucide-react";

import { queryKagGraph } from "../api";
import { rowsToGraph, type SpgTypeRow } from "../model";

cytoscape.use(dagre);

export function GraphExplorerSection({
  projectId,
  types,
}: {
  projectId: string;
  types: SpgTypeRow[];
}) {
  const { t } = useTranslation();
  const [dsl, setDsl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{
    header: string[];
    rows: unknown[];
    rowCount: number;
    truncated: boolean;
  } | null>(null);
  const [renderKey, setRenderKey] = useState(0);

  const business = useMemo(
    () => types.filter((row) => row.kind !== "basic" && row.kind !== "standard"),
    [types],
  );
  const graph = useMemo(
    () => (result ? rowsToGraph(result.rows) : { nodes: [], edges: [] }),
    [result],
  );

  async function run() {
    if (!dsl.trim()) {
      setError(t("Enter a DSL query first"));
      return;
    }
    setError("");
    setBusy(true);
    try {
      const res = await queryKagGraph(projectId, { dsl: dsl.trim() });
      if (res.error) {
        setError(res.error.split("\n")[0]);
        setResult(null);
        return;
      }
      setResult({
        header: res.header,
        rows: res.rows,
        rowCount: res.rowCount,
        truncated: res.truncated,
      });
      setRenderKey((k) => k + 1);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("The graph query was rejected"),
      );
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  const firstType = business[0]?.key ?? "Namespace.Type";

  return (
    <section className="mb-8">
      <h2 className="mb-2 text-[15px] font-semibold tracking-tight text-[var(--foreground)]">
        {t("Graph explorer")}
      </h2>
      <p className="mb-3 text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
        {t(
          "Run a read-only reason DSL query. Node types must use the namespace-qualified full name; relation labels are bare. Two-column id results render as a graph. The DSL takes no LIMIT clause - results are capped server-side.",
        )}
      </p>
      <div className="mb-2 flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={() => setDsl(`MATCH (n:${firstType}) RETURN n.id, n.name`)}
          className="rounded-full border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1 font-mono text-[11px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/50"
        >
          {t("Node template")}
        </button>
        <button
          type="button"
          onClick={() =>
            setDsl(
              `MATCH (n:${firstType})-[p:workFor]->(o:${business[1]?.key ?? firstType}) RETURN n.id, o.id`,
            )
          }
          className="rounded-full border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1 font-mono text-[11px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/50"
        >
          {t("Relation template")}
        </button>
      </div>
      <div className="flex gap-2">
        <input
          value={dsl}
          disabled={busy}
          onChange={(e) => setDsl(e.target.value)}
          placeholder="MATCH (n:ns.Type)-[p:rel]->(o:ns.Other) RETURN n.id, o.id"
          className="min-w-0 flex-1 rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-3 py-2 font-mono text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
        />
        <button
          type="button"
          disabled={busy}
          onClick={run}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-[var(--primary)] px-3.5 py-2 text-[12px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          <Play size={12} aria-hidden />
          {busy ? t("Running…") : t("Run query")}
        </button>
      </div>
      {error ? (
        <p className="mt-2 rounded-md bg-red-500/10 px-3 py-2 font-mono text-[11.5px] text-red-600 dark:text-red-400">
          {error}
        </p>
      ) : null}

      {result ? (
        <div className="mt-3 space-y-3">
          {result.truncated ? (
            <p className="text-[11.5px] text-[var(--muted-foreground)]">
              {t("Results were truncated to the first 200 rows")}
            </p>
          ) : null}
          {graph.edges.length > 0 ? (
            <div className="flex items-center gap-1.5 text-[12px] text-[var(--muted-foreground)]">
              <Network size={13} aria-hidden />
              {t("{{nodes}} nodes, {{edges}} edges", {
                nodes: graph.nodes.length,
                edges: graph.edges.length,
              })}
            </div>
          ) : null}
          <GraphCanvas key={renderKey} nodes={graph.nodes} edges={graph.edges} />
          <div className="overflow-x-auto rounded-xl border border-[var(--border)]/60 bg-[var(--card)]">
            <table className="w-full text-left text-[12px]">
              <thead>
                <tr className="border-b border-[var(--border)]/60 text-[11px] text-[var(--muted-foreground)]">
                  {result.header.map((h) => (
                    <th key={h} className="px-3 py-2 font-mono font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.slice(0, 50).map((row, idx) => (
                  <tr
                    key={idx}
                    className="border-b border-[var(--border)]/30 last:border-0"
                  >
                    {Array.isArray(row)
                      ? row.map((cell, i) => (
                          <td
                            key={i}
                            className="px-3 py-1.5 font-mono text-[var(--foreground)]"
                          >
                            {String(cell ?? "")}
                          </td>
                        ))
                      : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-[var(--muted-foreground)]">
            {t("{{count}} row(s) returned", { count: result.rowCount })}
          </p>
        </div>
      ) : null}
    </section>
  );
}

function GraphCanvas({
  nodes,
  edges,
}: {
  nodes: string[];
  edges: { source: string; target: string }[];
}) {
  const { t } = useTranslation();
  if (nodes.length === 0) return null;
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--border)]/60 bg-[var(--card)]">
      <CytoscapeMount nodes={nodes} edges={edges} />
      <p className="border-t border-[var(--border)]/40 px-3 py-1.5 text-[10.5px] text-[var(--muted-foreground)]">
        {t("Drag to pan, scroll to zoom")}
      </p>
    </div>
  );
}

function CytoscapeMount({
  nodes,
  edges,
}: {
  nodes: string[];
  edges: { source: string; target: string }[];
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement | null>(null);
  // 30 个节点以上用摘要代替（画布过密不可读）
  const capped = nodes.slice(0, 30);
  const nodeKey = nodes.join(",");
  const edgeKey = edges.map((e) => `${e.source}>${e.target}`).join(",");
  useEffect(() => {
    if (!ref.current) return;
    const elements = [
      ...capped.map((id) => ({ data: { id, label: id } })),
      ...edges
        .filter((e) => capped.includes(e.source) && capped.includes(e.target))
        .map((e) => ({ data: { source: e.source, target: e.target } })),
    ];
    const cy = cytoscape({
      container: ref.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "font-size": 9,
            color: "var(--foreground)",
            "background-color": "var(--primary)",
            width: 14,
            height: 14,
            "text-valign": "bottom",
            "text-margin-y": 4,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "var(--muted-foreground)",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.6,
          },
        },
      ],
      // dagre 扩展的布局选项（rankDir 等）不在 cytoscape 核心类型里
      layout: {
        name: "dagre",
        rankDir: "LR",
        nodeSep: 24,
        rankSep: 48,
      } as unknown as cytoscape.LayoutOptions,
    });
    return () => cy.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeKey, edgeKey]);
  if (nodes.length > 30) {
    return (
      <div className="px-4 py-6 text-center text-[12px] text-[var(--muted-foreground)]">
        {t("Too many nodes to render ({{count}}); refine the query", {
          count: nodes.length,
        })}
      </div>
    );
  }
  return <div ref={ref} className="h-64 w-full" aria-label={t("Graph canvas")} />;
}
