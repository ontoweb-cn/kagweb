"use client";

/**
 * M4 详情页增量：项目成员卡片（M4-B）+ 构建触发面板（M4-A）。
 *
 * members 读=项目访问；写（PUT members）仅 admin——卡片的编辑区只在 admin
 * 下渲染。构建为受理层（本地 executor 缺失时执行会失败，见后端文档）。
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Boxes, Hammer, Save, Users } from "lucide-react";

import { isApiError } from "@/shared/api/errors";
import {
  fetchKagMembers,
  submitKagBuild,
  updateKagMembers,
  type KagProjectMembers,
} from "../api";

export function MemberBuildPanel({ projectId }: { projectId: string }) {
  const { t } = useTranslation();
  const [members, setMembers] = useState<KagProjectMembers | null>(null);
  const [admin, setAdmin] = useState(false);
  const [draft, setDraft] = useState("");
  const [savingMembers, setSavingMembers] = useState(false);
  const [memberError, setMemberError] = useState("");
  const [command, setCommand] = useState("");
  const [building, setBuilding] = useState(false);
  const [buildError, setBuildError] = useState("");

  const loadMembers = useCallback(async () => {
    setMemberError("");
    try {
      const row = await fetchKagMembers(projectId);
      setAdmin(row.canEdit ?? false);
      setMembers(row);
      setDraft(row.members.join(", "));
    } catch (err) {
      setMemberError(
        err instanceof Error ? err.message : t("The project members could not be loaded"),
      );
      setMembers(null);
    }
  }, [projectId, t]);

  useEffect(() => {
    void loadMembers();
  }, [loadMembers]);

  async function saveMembers() {
    setMemberError("");
    const list = draft
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    setSavingMembers(true);
    try {
      await updateKagMembers(projectId, list);
      await loadMembers();
    } catch (err) {
      setMemberError(
        isApiError(err) && err.message
          ? err.message
          : t("The member update was rejected"),
      );
    } finally {
      setSavingMembers(false);
    }
  }

  async function runBuild() {
    if (!command.trim()) {
      setBuildError(t("Enter a build command first"));
      return;
    }
    setBuildError("");
    setBuilding(true);
    try {
      await submitKagBuild(projectId, command.trim());
      setCommand("");
    } catch (err) {
      setBuildError(
        isApiError(err) && err.message
          ? err.message
          : t("The build submission was rejected"),
      );
    } finally {
      setBuilding(false);
    }
  }

  return (
    <div className="mb-8 grid gap-3 lg:grid-cols-2">
      {/* 成员卡片（M4-B） */}
      <section className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-4">
        <h3 className="flex items-center gap-1.5 text-[13.5px] font-semibold tracking-tight text-[var(--foreground)]">
          <Users size={14} aria-hidden />
          {t("Project members")}
        </h3>
        {memberError ? (
          <p className="mt-2 rounded-md bg-red-500/10 px-3 py-2 text-[11.5px] text-red-600 dark:text-red-400">
            {memberError}
          </p>
        ) : members === null ? (
          <p className="mt-2 text-[12px] text-[var(--muted-foreground)]">
            {t("Loading…")}
          </p>
        ) : (
          <>
            <dl className="mt-3 space-y-1 text-[12px]">
              <div className="flex justify-between">
                <dt className="text-[var(--muted-foreground)]">{t("Owner")}</dt>
                <dd className="font-mono text-[var(--foreground)]">
                  {members.owner || "-"}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-[var(--muted-foreground)]">
                  {t("OpenSPG userNo")}
                </dt>
                <dd className="font-mono text-[var(--foreground)]">
                  {members.ownerUserNo || "-"}
                </dd>
              </div>
            </dl>
            {admin ? (
              <div className="mt-3 space-y-2 border-t border-[var(--border)]/40 pt-3">
                <label
                  htmlFor="member-list"
                  className="text-[11px] text-[var(--muted-foreground)]"
                >
                  {t("Member user ids (comma-separated)")}
                </label>
                <textarea
                  id="member-list"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  disabled={savingMembers}
                  rows={2}
                  className="w-full rounded-md border border-[var(--border)]/60 bg-[var(--background)]/40 px-2.5 py-1.5 font-mono text-[11.5px] text-[var(--foreground)] outline-none focus:border-[var(--foreground)]/30 disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={saveMembers}
                  disabled={savingMembers}
                  className="inline-flex items-center gap-1.5 rounded-md bg-[var(--primary)] px-2.5 py-1.5 text-[11.5px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
                >
                  <Save size={12} aria-hidden />
                  {savingMembers ? t("Saving…") : t("Save members")}
                </button>
              </div>
            ) : (
              <p className="mt-3 text-[11px] text-[var(--muted-foreground)]">
                {t("Members: {{n}}", {
                  // 评审：不用 ``count`` 变量（它触发 i18next 复数 key 查找，
                  // 缺 *_other 后缀）——改用非保留变量 ``n`` 纯计数插值。
                  n: members.members.length,
                })}
              </p>
            )}
          </>
        )}
      </section>

      {/* 构建触发（M4-A） */}
      <section className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-4">
        <h3 className="flex items-center gap-1.5 text-[13.5px] font-semibold tracking-tight text-[var(--foreground)]">
          <Boxes size={14} aria-hidden />
          {t("Build pipeline")}
        </h3>
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-[var(--muted-foreground)]">
          {t(
            "Submit a KAG_COMMAND build. This locally accepts the job; the remote executor must be configured for it to actually run.",
          )}
        </p>
        <div className="mt-3 flex gap-2">
          <input
            value={command}
            disabled={building}
            onChange={(e) => setCommand(e.target.value)}
            placeholder="kag builder --project …"
            className="min-w-0 flex-1 rounded-md border border-[var(--border)]/60 bg-[var(--background)]/40 px-2.5 py-1.5 font-mono text-[11.5px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
          />
          <button
            type="button"
            onClick={runBuild}
            disabled={building}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-[var(--primary)] px-2.5 py-1.5 text-[11.5px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            <Hammer size={12} aria-hidden />
            {building ? t("Submitting…") : t("Submit build")}
          </button>
        </div>
        {buildError ? (
          <p className="mt-2 rounded-md bg-red-500/10 px-3 py-2 text-[11.5px] text-red-600 dark:text-red-400">
            {buildError}
          </p>
        ) : null}
      </section>
    </div>
  );
}