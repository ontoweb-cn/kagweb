"use client";

/**
 * 阶段 B-1：数据导入（受理层）面板——引导式 KAG_COMMAND 模板 + 自由编辑 + 提交。
 *
 * 与构建卡片（MemberBuildPanel）语义区分：构建=任意命令；导入=数据接入引导。
 * 共享 submitKagBuild（POST /projects/{id}/build，same-origin + membership）与
 * P0a 任务可观测（受理回显 + 任务列表入口）。公开契约无文件上传端点，数据须
 * 放在 executor 可达位置（git 仓库/路径）；执行依赖远程 executor。
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import Link from "next/link";
import { Database, Hammer } from "lucide-react";

import { isApiError } from "@/shared/api/errors";
import { hasKagCommandPlaceholder } from "../model";
import { submitKagBuild } from "../api";

interface KagImportTemplate {
  key: string;
  label: string;
  description: string;
  command: string;
}

/**
 * 模板预设：基于 KAG fork 的 `kag builder` CLI 真实语法
 * （--project_id/--user_number/--git_url/--commit_id/--entry_script，
 * KAG/kag/bin/commands/builder.py）。`{projectId}` 自动填充，`<...>` 占位待填。
 */
function importTemplates(projectId: string): KagImportTemplate[] {
  return [
    {
      key: "structured",
      label: "Structured data (Git repo)",
      description: "Build entities from tabular data hosted in a repository.",
      command: `kag builder --project_id ${projectId} --git_url <data-repo-url> --commit_id <commit-id>`,
    },
    {
      key: "documents",
      label: "Documents (Git repo + entry script)",
      description: "Ingest unstructured documents through an entry script.",
      command: `kag builder --project_id ${projectId} --git_url <data-repo-url> --commit_id <commit-id> --entry_script <run-import.py>`,
    },
  ];
}

export function KagImportPanel({ projectId }: { projectId: string }) {
  const { t } = useTranslation();
  const [command, setCommand] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [accepted, setAccepted] = useState<{ taskId: string } | null>(null);

  async function runImport() {
    if (!command.trim()) {
      setError(t("Enter a build command first"));
      return;
    }
    // 评审 P2：模板占位符（<…>）未替换即提交会送出字面占位命令，先拦截
    if (hasKagCommandPlaceholder(command)) {
      setError(t("Replace the <…> placeholders in the command before submitting."));
      return;
    }
    setError("");
    setAccepted(null);
    setSubmitting(true);
    try {
      setAccepted(await submitKagBuild(projectId, command.trim()));
      setCommand("");
    } catch (err) {
      setError(
        isApiError(err) && err.message
          ? err.message
          : t("The build submission was rejected"),
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="mb-8 rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-4">
      <h3 className="flex items-center gap-1.5 text-[13.5px] font-semibold tracking-tight text-[var(--foreground)]">
        <Database size={14} aria-hidden />
        {t("Data import")}
      </h3>
      <p className="mt-1.5 text-[11.5px] leading-relaxed text-[var(--muted-foreground)]">
        {t(
          "Submit a KAG_COMMAND data import. This locally accepts the job; execution requires a remote executor, and data must be reachable from it (this panel does not upload files).",
        )}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {importTemplates(projectId).map((tpl) => (
          <button
            key={tpl.key}
            type="button"
            title={tpl.description}
            onClick={() => {
              setCommand(tpl.command);
              setError("");
              setAccepted(null);
            }}
            className="rounded-md border border-[var(--border)]/60 bg-[var(--background)]/40 px-2.5 py-1.5 text-[11.5px] text-[var(--foreground)] transition-colors hover:border-[var(--foreground)]/30 hover:bg-[var(--muted)]/40"
          >
            {t(tpl.label)}
          </button>
        ))}
      </div>
      <div className="mt-3 flex gap-2">
        <input
          value={command}
          disabled={submitting}
          onChange={(e) => {
            setCommand(e.target.value);
            // 评审 P3-2：编辑命令后清除陈旧的成功/错误回显
            setError("");
            setAccepted(null);
          }}
          placeholder="kag builder --project_id …"
          className="min-w-0 flex-1 rounded-md border border-[var(--border)]/60 bg-[var(--background)]/40 px-2.5 py-1.5 font-mono text-[11.5px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
        />
        <button
          type="button"
          onClick={runImport}
          disabled={submitting}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-[var(--primary)] px-2.5 py-1.5 text-[11.5px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          <Hammer size={12} aria-hidden />
          {submitting ? t("Submitting…") : t("Submit import")}
        </button>
      </div>
      {error ? (
        <p className="mt-2 rounded-md bg-red-500/10 px-3 py-2 text-[11.5px] text-red-600 dark:text-red-400">
          {error}
        </p>
      ) : null}
      {accepted ? (
        <p className="mt-2 rounded-md bg-emerald-500/10 px-3 py-2 text-[11.5px] text-emerald-600 dark:text-emerald-400">
          {t("Build accepted: {{taskId}}", { taskId: accepted.taskId })}{" "}
          <Link href="/kag/tasks" className="underline underline-offset-2">
            {t("View in task list")}
          </Link>
        </p>
      ) : null}
    </section>
  );
}
