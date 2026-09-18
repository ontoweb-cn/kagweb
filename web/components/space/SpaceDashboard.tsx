"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { useCapabilityFilter } from "@/features/capabilities/useCapabilityCatalog";
import { fetchKagProjects } from "@/features/kag";
import {
  ArrowUpRight,
  Github,
  History,
  Waypoints,
  type LucideIcon,
} from "lucide-react";

import { listSessions } from "@/lib/session-api";

/**
 * Space dashboard — the hub of `/space`.
 *
 * Replaces the old "land directly in a section behind a side list" flow with a
 * single overview the learner enters from. Each tile is a real entry point that
 * shows a live count so the space feels inhabited, then routes into the full
 * section page (which keeps the mini-nav for lateral movement).
 */

type Lang = { zh: string; en: string };

type DashKey = "chat_history" | "kag";

interface DashboardItem {
  key: DashKey;
  href: string;
  icon: LucideIcon;
  title: Lang;
  blurb: Lang;
  /**
   * Unit shown after the live count, e.g. "168 conversations". Omitted
   * together with ``load`` for a tile that has nothing to count.
   */
  unit?: Lang;
  /** Icon-tile accent — full class strings so Tailwind keeps them. */
  tile: string;
  /**
   * Live count for the tile. Optional: a surface with no countable rows (an
   * ephemeral room, say) renders as title + blurb instead of showing a
   * permanently-loading number.
   */
  load?: () => Promise<number>;
  /**
   * When set, a failed ``load`` also removes the tile instead of merely
   * omitting its count. For the KAG console the same request is the
   * visibility gate: 403 (no grant) or an unreachable OpenSPG server means
   * the surface should not be offered at all.
   */
  hideOnLoadError?: boolean;
  /** GitHub handle of the contributor this surface came from. */
  credit?: string;
  /**
   * Turn capability this surface needs, when it is not served by this
   * repository. The tile is withheld unless the backend registry actually
   * holds the name, so a stock install never offers a room whose capability
   * was never installed (#963).
   */
  requiresCapability?: string;
}

interface DashboardGroup {
  label: Lang;
  items: DashboardItem[];
}

const GROUPS: DashboardGroup[] = [
  {
    label: { zh: "对话与资料", en: "Conversations & Materials" },
    items: [
      {
        key: "chat_history",
        href: "/space/chat-history",
        icon: History,
        title: { zh: "聊天历史", en: "Chat History" },
        blurb: {
          zh: "回顾并继续此前的对话。",
          en: "Review and reopen previous conversations.",
        },
        unit: { zh: "段对话", en: "conversations" },
        tile: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
        load: async () => (await listSessions(200, 0, { force: true })).length,
      },
    ],
  },
  {
    label: { zh: "知识图谱", en: "Knowledge Graph" },
    items: [
      {
        key: "kag",
        href: "/kag",
        icon: Waypoints,
        title: { zh: "KAG 项目", en: "KAG Projects" },
        blurb: {
          zh: "OpenSPG 上的知识图谱项目、Schema 与推理任务。",
          en: "Knowledge-graph projects, schema, and reasoning tasks on OpenSPG.",
        },
        unit: { zh: "个项目", en: "projects" },
        tile: "bg-teal-500/10 text-teal-600 dark:text-teal-400",
        // 可见性即权限（设计 §5.4）：读失败 = 未配置或无 grant，整块隐藏
        load: async () => (await fetchKagProjects()).length,
        hideOnLoadError: true,
      },
    ],
  },
];

const ALL_ITEMS = GROUPS.flatMap((g) => g.items);

/**
 * The groups to render, given what the backend can actually serve.
 *
 * `isAvailable` is null while the probe is in flight: gated tiles stay hidden
 * until then, so a surface whose capability was never installed does not flash
 * into view and out again — an ungated tile is never affected. A group left
 * with no tiles is dropped along with its heading, or "More Projects" would
 * render as a title over nothing (#963).
 */
export function visibleGroups(
  groups: DashboardGroup[],
  isAvailable: ((name: string) => boolean) | null,
  hiddenKeys?: ReadonlySet<DashKey>,
): DashboardGroup[] {
  return groups
    .map((group) => ({
      ...group,
      items: group.items.filter(
        (item) =>
          !hiddenKeys?.has(item.key) &&
          (!item.requiresCapability ||
            (isAvailable?.(item.requiresCapability) ?? false)),
      ),
    }))
    .filter((group) => group.items.length > 0);
}

export { GROUPS as DASHBOARD_GROUPS };

export default function SpaceDashboard() {
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const tr = useCallback((l: Lang) => (zh ? l.zh : l.en), [zh]);

  const [counts, setCounts] = useState<Partial<Record<DashKey, number>>>({});
  // Tiles whose load doubles as the access gate (hideOnLoadError) start hidden
  // and appear only on success — the capability-gate no-flash rule — so a
  // rejected fetch (not configured / no grant) never flashes the tile in.
  const [hidden, setHidden] = useState<ReadonlySet<DashKey>>(
    () =>
      new Set(ALL_ITEMS.filter((item) => item.hideOnLoadError).map((i) => i.key)),
  );

  const capabilityAvailable = useCapabilityFilter();
  const groups = useMemo(
    () => visibleGroups(GROUPS, capabilityAvailable, hidden),
    [capabilityAvailable, hidden],
  );

  useEffect(() => {
    let cancelled = false;
    // Each tile loads independently so one slow/failed endpoint never blanks
    // the whole dashboard.
    for (const item of ALL_ITEMS) {
      if (!item.load) continue;
      item
        .load()
        .then((n) => {
          if (cancelled) return;
          setCounts((prev) => ({ ...prev, [item.key]: n }));
          if (item.hideOnLoadError)
            setHidden((prev) => {
              if (!prev.has(item.key)) return prev;
              const next = new Set(prev);
              next.delete(item.key);
              return next;
            });
        })
        .catch(() => {
          /* leave undefined → tile just omits the count */
          if (!cancelled && item.hideOnLoadError)
            setHidden((prev) => new Set(prev).add(item.key));
        });
    }
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <header className="mb-8">
        <h1 className="font-serif text-[24px] font-semibold leading-tight tracking-tight text-[var(--foreground)]">
          {tr({ zh: "空间", en: "Space" })}
        </h1>
        <p className="mt-1.5 max-w-xl text-[13px] leading-relaxed text-[var(--muted-foreground)]">
          {tr({
            zh: "你的对话、智能体、笔记与练习，集中在一处 —— 从这里进入。",
            en: "Your conversations, agents, notebooks, and practice in one place — enter from here.",
          })}
        </p>
      </header>

      <div className="space-y-9">
        {groups.map((group) => (
          <section key={group.label.en}>
            <h2 className="mb-3 px-0.5 font-serif text-[16px] font-semibold tracking-tight text-[var(--foreground)]">
              {tr(group.label)}
            </h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {group.items.map((item) => (
                <DashboardCard
                  key={item.key}
                  item={item}
                  count={counts[item.key]}
                  tr={tr}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function DashboardCard({
  item,
  count,
  tr,
}: {
  item: DashboardItem;
  count: number | undefined;
  tr: (l: Lang) => string;
}) {
  const Icon = item.icon;
  const loaded = count !== undefined;
  const formatted = useMemo(
    () => (loaded ? count.toLocaleString() : ""),
    [loaded, count],
  );

  return (
    <Link
      href={item.href}
      className="group relative flex flex-col rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 transition-all duration-150 hover:-translate-y-0.5 hover:border-[var(--foreground)]/20 hover:shadow-[0_6px_20px_-12px_rgba(0,0,0,0.25)]"
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${item.tile}`}
        >
          <Icon size={18} strokeWidth={1.7} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[14.5px] font-medium leading-tight tracking-tight text-[var(--foreground)]">
            {tr(item.title)}
          </h3>
          {item.unit ? (
            <div className="mt-1 flex items-baseline gap-1.5">
              {loaded ? (
                <>
                  <span className="text-[20px] font-semibold leading-none tabular-nums text-[var(--foreground)]">
                    {formatted}
                  </span>
                  <span className="text-[12px] text-[var(--muted-foreground)]">
                    {tr(item.unit)}
                  </span>
                </>
              ) : (
                <span className="my-[3px] h-3.5 w-12 animate-pulse rounded bg-[var(--muted)]" />
              )}
            </div>
          ) : null}
        </div>
        <ArrowUpRight
          size={16}
          className="shrink-0 text-[var(--muted-foreground)]/40 transition-colors group-hover:text-[var(--foreground)]"
        />
      </div>
      <p className="mt-3 text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
        {tr(item.blurb)}
      </p>
      {item.credit ? (
        <span className="mt-2.5 inline-flex items-center gap-1 self-start text-[11px] leading-none text-[var(--muted-foreground)] opacity-60">
          <Github size={11} strokeWidth={1.8} aria-hidden />
          {item.credit}
        </span>
      ) : null}
    </Link>
  );
}
