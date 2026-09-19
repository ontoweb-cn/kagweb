"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronDown,
  ListChecks,
  RefreshCw,
} from "lucide-react";

import { fetchKagTasks } from "../api";
import type { KagTaskRow } from "../model";
import { KagBackLink, KagPageBody, KagPageHeader, KagStateView } from "./KagPageFrame";

/**
 * `/kag/tasks` 推理任务页（M2.4）：Bridge 上报的 kag_solve 摘要列表
 * （附录 A.3）。advisory 数据：bridge 未上报/未配置时为空列表属正常态。
 */

function formatCost(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return "-";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

function formatTime(iso: string): string {
  if (!iso) return "-";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString();
}

function TaskRow({ task }: { task: KagTaskRow }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div className="px-5 py-3.5">
      <button
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-start gap-3 text-left"
      >
        <ChevronDown
          size={14}
          className={`mt-0.5 shrink-0 text-[var(--muted-foreground)] transition-transform ${
            open ? "rotate-180" : ""
          }`}
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {task.kind === "build" ? (
              <span className="shrink-0 rounded-full bg-sky-500/10 px-2 py-0.5 text-[10px] font-medium text-sky-600 dark:text-sky-400">
                {t("Build")}
              </span>
            ) : null}
            <p className="min-w-0 truncate text-[13px] font-medium text-[var(--foreground)]">
              {task.question || t("(no question)")}
            </p>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-[var(--muted-foreground)]">
            <span className="font-mono">{task.namespace || task.projectId}</span>
            {task.sessionId ? (
              <span className="truncate font-mono">{task.sessionId}</span>
            ) : null}
            <span className="tabular-nums">{formatCost(task.costMs)}</span>
            <span className="tabular-nums">{formatTime(task.createdAt)}</span>
          </div>
        </div>
        {task.references.length > 0 ? (
          <span className="shrink-0 rounded-full bg-[var(--muted)]/50 px-2 py-0.5 text-[10.5px] text-[var(--muted-foreground)]">
            {t("{{count}} references", { count: task.references.length })}
          </span>
        ) : null}
      </button>
      {open ? (
        <div className="mt-2.5 space-y-2 border-l-2 border-[var(--border)] pl-4">
          {task.answerDigest ? (
            <p className="whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-[var(--foreground)]/90">
              {task.answerDigest}
            </p>
          ) : (
            <p className="text-[12px] text-[var(--muted-foreground)]">
              {t("No answer digest reported")}
            </p>
          )}
          {task.references.length > 0 ? (
            <div className="space-y-1">
              <p className="text-[11px] font-medium text-[var(--muted-foreground)]">
                {t("References")}
              </p>
              <ul className="space-y-0.5">
                {task.references.map((reference, index) => (
                  <li
                    key={`${task.taskId}-${index}`}
                    className="break-words font-mono text-[11px] leading-relaxed text-[var(--muted-foreground)]"
                  >
                    {reference}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default function KagTasksPage() {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<KagTaskRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const rows = await fetchKagTasks({ limit: 100 });
      setTasks(rows);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setTasks(null);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <KagPageBody>
      <KagBackLink href="/kag" label={t("KAG Projects")} />
      <KagPageHeader
        icon={ListChecks}
        title={t("Reasoning tasks")}
        description={t(
          "kag_solve summaries reported by the KAG bridge, newest first.",
        )}
        action={
          <button
            onClick={() => void load()}
            disabled={loading || refreshing}
            className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--muted-foreground)] transition-colors hover:bg-[var(--card)] hover:text-[var(--foreground)] disabled:opacity-50"
          >
            <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
            {t("Refresh")}
          </button>
        }
      />

      <KagStateView loading={loading} error={error} />

      {!loading && !error && tasks !== null ? (
        <div className="overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--card)] shadow-sm">
          {tasks.length === 0 ? (
            <div className="px-5 py-12 text-center">
              <p className="text-sm font-medium text-[var(--foreground)]">
                {t("No reasoning tasks yet")}
              </p>
              <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                {t(
                  "Ask a knowledge-graph question in chat; kag_solve runs will be reported here.",
                )}
              </p>
            </div>
          ) : (
            <div className="divide-y divide-[var(--border)]">
              {tasks.map((task) => (
                <TaskRow key={task.taskId || `${task.createdAt}-${task.question}`} task={task} />
              ))}
            </div>
          )}
        </div>
      ) : null}
    </KagPageBody>
  );
}
