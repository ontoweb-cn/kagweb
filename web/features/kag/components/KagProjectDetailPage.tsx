"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronRight,
  CircleDot,
  FolderTree,
  Hash,
  MapPin,
  Tag,
  Waypoints,
} from "lucide-react";

import { isApiError } from "@/shared/api/errors";
import { fetchKagProjectDetail, fetchKagProjectSchema } from "../api";
import {
  buildSpgTypeTree,
  ownProperties,
  type KagProjectDetail,
  type SpgTypeNode,
  type SpgTypeRow,
} from "../model";
import { KagBackLink, KagPageBody, KagPageHeader, KagStateView } from "./KagPageFrame";
import { GraphExplorerSection } from "./GraphExplorerSection";
import { MemberBuildPanel } from "./MemberBuildPanel";
import { KagImportPanel } from "./KagImportPanel";
import { SchemaEditPanel } from "./SchemaEditPanel";

/**
 * `/kag/projects/[id]` 详情页（M2.4：Schema 树 + graph labels 概览；
 * M3.5：类型展开区的 Schema 关系编辑（读模型回传 + 服务端 wire 转换）；
 * 图浏览为 M3.4 —— OpenSPG graph 无子图查询端点，经 reason DSL）。
 */

const KIND_LABEL: Record<string, { zh: string; en: string }> = {
  basic: { zh: "基础类型", en: "Basic" },
  index: { zh: "索引", en: "Index" },
  standard: { zh: "标准类型", en: "Standard" },
  entity: { zh: "实体", en: "Entity" },
  concept: { zh: "概念", en: "Concept" },
  event: { zh: "事件", en: "Event" },
  unknown: { zh: "类型", en: "Type" },
};

function TypeBadge({
  kind,
  text,
}: {
  kind: string;
  text: string;
}) {
  const tone =
    kind === "entity"
      ? "bg-sky-500/10 text-sky-600 dark:text-sky-400"
      : kind === "concept"
        ? "bg-violet-500/10 text-violet-600 dark:text-violet-400"
        : kind === "event"
          ? "bg-amber-500/10 text-amber-600 dark:text-amber-400"
          : "bg-[var(--muted)]/50 text-[var(--muted-foreground)]";
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10.5px] font-medium ${tone}`}>
      {text}
    </span>
  );
}

function PropertyList({
  properties,
  showInherited,
}: {
  properties: { name: string; objectType: string; inherited: boolean }[];
  showInherited: boolean;
}) {
  const { t } = useTranslation();
  const rows = showInherited
    ? properties
    : properties.filter((property) => !property.inherited);
  if (rows.length === 0)
    return (
      <p className="px-3 py-2 text-[11.5px] text-[var(--muted-foreground)]">
        {t("No properties")}
      </p>
    );
  return (
    <div className="divide-y divide-[var(--border)]/40">
      {rows.map((property) => (
        <div
          key={property.name}
          className="flex items-center gap-3 px-3 py-1.5 text-[12px]"
        >
          <span className="w-2 shrink-0" aria-hidden>
            {property.inherited ? (
              <CircleDot size={10} className="text-[var(--muted-foreground)]/50" />
            ) : null}
          </span>
          <span className="min-w-0 flex-1 truncate font-mono text-[var(--foreground)]">
            {property.name}
          </span>
          <span className="shrink-0 truncate font-mono text-[11px] text-[var(--muted-foreground)]">
            {property.objectType}
          </span>
        </div>
      ))}
    </div>
  );
}

function TypeNodeRow({
  node,
  depth,
  projectId,
  allTypes,
  onAltered,
}: {
  node: SpgTypeNode;
  depth: number;
  projectId: string;
  allTypes: SpgTypeRow[];
  onAltered: () => void;
}) {
  const { t, i18n } = useTranslation();
  const zh = Boolean(i18n.language?.toLowerCase().startsWith("zh"));
  const [open, setOpen] = useState(depth < 1);
  const [showInherited, setShowInherited] = useState(false);
  const kind = KIND_LABEL[node.kind] ?? KIND_LABEL.unknown;
  const hasChildren = node.children.length > 0;
  const own = ownProperties(node).length;
  // 关系编辑只对业务类型开放（basic/standard 无关系语义）
  const editable = node.kind !== "basic" && node.kind !== "standard";

  return (
    <div>
      <button
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[var(--background)]/40"
        style={{ paddingLeft: `${12 + depth * 16}px` }}
      >
        <ChevronRight
          size={13}
          className={`shrink-0 text-[var(--muted-foreground)] transition-transform ${
            open ? "rotate-90" : ""
          }`}
          aria-hidden
        />
        <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-[var(--foreground)]">
          {node.name}
          {node.nameZh && zh ? (
            <span className="ml-1.5 font-sans text-[11px] text-[var(--muted-foreground)]">
              {node.nameZh}
            </span>
          ) : null}
        </span>
        <TypeBadge kind={node.kind} text={zh ? kind.zh : kind.en} />
        <span className="shrink-0 text-[10.5px] tabular-nums text-[var(--muted-foreground)]">
          {t("{{count}} properties", { count: own })}
        </span>
      </button>
      {open ? (
        <div className="border-t border-[var(--border)]/40 bg-[var(--background)]/30">
          {node.desc ? (
            <p
              className="border-b border-[var(--border)]/40 px-3 py-1.5 text-[11.5px] leading-relaxed text-[var(--muted-foreground)]"
              style={{ paddingLeft: `${24 + depth * 16}px` }}
            >
              {node.desc}
            </p>
          ) : null}
          {node.properties.length > 0 ? (
            <div className="py-1">
              <div
                className="px-3 pb-1"
                style={{ paddingLeft: `${24 + depth * 16}px` }}
              >
                <label className="inline-flex cursor-pointer items-center gap-1.5 text-[10.5px] text-[var(--muted-foreground)]">
                  <input
                    type="checkbox"
                    checked={showInherited}
                    onChange={() => setShowInherited((value) => !value)}
                  />
                  {t("Show inherited properties")}
                </label>
              </div>
              <PropertyList properties={node.properties} showInherited={showInherited} />
            </div>
          ) : null}
          {editable ? (
            <div
              className="px-3 py-2"
              style={{ paddingLeft: `${24 + depth * 16}px` }}
            >
              <SchemaEditPanel
                projectId={projectId}
                typeRow={node}
                allTypes={allTypes}
                onAltered={onAltered}
              />
            </div>
          ) : null}
          {hasChildren
            ? node.children.map((child) => (
                <TypeNodeRow
                  key={child.key}
                  node={child}
                  depth={depth + 1}
                  projectId={projectId}
                  allTypes={allTypes}
                  onAltered={onAltered}
                />
              ))
            : null}
        </div>
      ) : null}
    </div>
  );
}

function DetailTile({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Hash;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] px-4 py-3">
      <div className="flex items-center gap-1.5 text-[11px] font-medium text-[var(--muted-foreground)]">
        <Icon size={12} aria-hidden />
        {label}
      </div>
      <div className="mt-1.5 truncate text-[13px] font-medium text-[var(--foreground)]" title={value}>
        {value || "-"}
      </div>
    </div>
  );
}

export default function KagProjectDetailPage({
  projectId,
}: {
  projectId: string;
}) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<KagProjectDetail | null>(null);
  const [tree, setTree] = useState<SpgTypeNode[] | null>(null);
  const [rows, setRows] = useState<SpgTypeRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setDetail(null);
      setTree(null);
      setRows([]);
      setError(null);
      setNotFound(false);
      try {
        const next = await fetchKagProjectDetail(projectId);
        if (cancelled) return;
        setDetail(next);
        // 详情先渲染；全量 Schema 树独立加载（可能较大，失败不阻塞详情）
        try {
          const schemaRows = await fetchKagProjectSchema(projectId);
          if (!cancelled) {
            setRows(schemaRows);
            setTree(buildSpgTypeTree(schemaRows));
          }
        } catch {
          if (!cancelled) setTree([]);
        }
      } catch (cause) {
        if (cancelled) return;
        if (isApiError(cause) && cause.status === 404) setNotFound(true);
        else
          setError(
            cause instanceof Error
              ? cause.message
              : t("Failed to load the project"),
          );
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [projectId, t]);

  /** M3.5 编辑成功后的重载：只刷 Schema 树（详情 tiles 不变）。 */
  async function reloadSchema() {
    try {
      const schemaRows = await fetchKagProjectSchema(projectId);
      setRows(schemaRows);
      setTree(buildSpgTypeTree(schemaRows));
    } catch {
      // 刷新失败保留现树（下一次进页会重拉）
    }
  }

  if (loading) {
    return (
      <KagPageBody>
        <KagBackLink href="/kag" label={t("KAG Projects")} />
        <KagStateView loading error={null} />
      </KagPageBody>
    );
  }

  if (notFound) {
    return (
      <KagPageBody>
        <KagBackLink href="/kag" label={t("KAG Projects")} />
        <div className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] px-5 py-10 text-center">
          <p className="text-[13.5px] font-medium text-[var(--foreground)]">
            {t("Project not found")}
          </p>
        </div>
      </KagPageBody>
    );
  }

  if (error || !detail) {
    return (
      <KagPageBody>
        <KagBackLink href="/kag" label={t("KAG Projects")} />
        <KagStateView loading={false} error={error ?? t("Failed to load the project")} />
      </KagPageBody>
    );
  }

  const { project } = detail;

  return (
    <KagPageBody>
      <KagBackLink href="/kag" label={t("KAG Projects")} />
      <KagPageHeader
        icon={Waypoints}
        title={project.name}
        description={project.description || project.namespace}
        meta={
          project.tag ? (
            <span className="rounded-full bg-[var(--muted)]/50 px-2.5 py-0.5 text-[11px] font-medium text-[var(--muted-foreground)]">
              {project.tag}
            </span>
          ) : null
        }
      />

      <div className="mb-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <DetailTile icon={Tag} label={t("Namespace")} value={project.namespace} />
        <DetailTile icon={Hash} label={t("Project ID")} value={project.projectId} />
        <DetailTile
          icon={FolderTree}
          label={t("SPG types")}
          value={
            detail.schemaSummary
              ? String(detail.schemaSummary.spgTypeCount)
              : t("Unavailable")
          }
        />
        <DetailTile
          icon={MapPin}
          label={t("Graph labels")}
          value={
            detail.graphLabels ? String(detail.graphLabels.length) : t("Unavailable")
          }
        />
      </div>

      {detail.graphLabels && detail.graphLabels.length > 0 ? (
        <section className="mb-8">
          <h2 className="mb-2 text-[15px] font-semibold tracking-tight text-[var(--foreground)]">
            {t("Graph labels")}
          </h2>
          <p className="mb-3 text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
            {t(
              "Vertex labels present in the graph store. Browsing subgraphs is planned for a later milestone.",
            )}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {detail.graphLabels.map((label) => (
              <span
                key={label}
                className="rounded-full border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1 font-mono text-[11px] text-[var(--foreground)]"
              >
                {label}
              </span>
            ))}
          </div>
        </section>
      ) : null}

      {rows.length > 0 ? (
        <GraphExplorerSection projectId={projectId} types={rows} />
      ) : null}

      <MemberBuildPanel projectId={projectId} />

      <KagImportPanel projectId={projectId} />

      <section>
        <h2 className="mb-2 text-[15px] font-semibold tracking-tight text-[var(--foreground)]">
          {t("Schema")}
        </h2>
        <p className="mb-3 text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
          {t(
            "SPG type tree. Expand a business type to add or remove its relations; deletions submit explicitly and are applied server-side.",
          )}
        </p>
        {tree === null ? (
          <KagStateView loading error={null} />
        ) : tree.length === 0 ? (
          <div className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] px-5 py-8 text-center text-sm text-[var(--muted-foreground)]">
            {t("The schema could not be loaded")}
          </div>
        ) : (
          <div className="divide-y divide-[var(--border)] overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--card)] shadow-sm">
            {tree.map((node) => (
              <TypeNodeRow
                key={node.key}
                node={node}
                depth={0}
                projectId={projectId}
                allTypes={rows}
                onAltered={reloadSchema}
              />
            ))}
          </div>
        )}
      </section>
    </KagPageBody>
  );
}
