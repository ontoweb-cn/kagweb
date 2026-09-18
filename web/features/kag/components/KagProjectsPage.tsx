"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import {
  ListChecks,
  Plus,
  RefreshCw,
  Waypoints,
  X,
} from "lucide-react";

import { useAuthStatus } from "@/hooks/useAuthStatus";
import { isApiError } from "@/shared/api/errors";
import {
  createKagProject,
  fetchKagEmbeddingProfiles,
  fetchKagProjects,
} from "../api";
import {
  EMPTY_PROJECT_CREATE_FORM,
  projectCreateRequest,
  validateProjectCreateForm,
  type KagProject,
  type KagProjectCreateForm,
} from "../model";
import { KagPageBody, KagPageHeader, KagStateView } from "./KagPageFrame";

/**
 * `/kag` 项目列表页（M2.4：列表 + 创建 + 入口；Schema 编辑 M3）。
 *
 * 权限语义（设计 §6.4）：读走 kag_read_allowed（403 = 无权），
 * 创建仅 admin；OpenSPG 上游未配置/不可达时后端 502，页面给出引导。
 */

function statusOf(error: unknown): number | undefined {
  return isApiError(error) ? error.status : undefined;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return String(error);
}

function ProjectRow({ project }: { project: KagProject }) {
  const { t } = useTranslation();
  return (
    <Link
      href={`/kag/projects/${encodeURIComponent(project.projectId)}`}
      className="group flex items-center gap-4 px-5 py-3.5 transition-colors hover:bg-[var(--background)]/50"
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-[13.5px] font-medium text-[var(--foreground)]">
            {project.name}
          </span>
          {project.tag ? (
            <span className="rounded-full bg-[var(--muted)]/50 px-2 py-0.5 text-[10.5px] font-medium text-[var(--muted-foreground)]">
              {project.tag}
            </span>
          ) : null}
        </div>
        <div className="mt-0.5 truncate font-mono text-[11.5px] text-[var(--muted-foreground)]">
          {project.namespace}
        </div>
      </div>
      <span className="shrink-0 text-[11.5px] tabular-nums text-[var(--muted-foreground)]">
        {t("ID {{id}}", { id: project.projectId })}
      </span>
    </Link>
  );
}

function CreateProjectDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (project: KagProject) => void;
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState<KagProjectCreateForm>(EMPTY_PROJECT_CREATE_FORM);
  const [models, setModels] = useState<{ id: string; name: string; model: string }[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [serverError, setServerError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchKagEmbeddingProfiles()
      .then((profiles) => {
        if (!cancelled) setModels(profiles);
      })
      .catch(() => {
        /* 目录不可用：表单仍可提交，服务端会拒绝 */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const update = <K extends keyof KagProjectCreateForm>(
    key: K,
    value: KagProjectCreateForm[K],
  ) => setForm((current) => ({ ...current, [key]: value }));

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting) return;
    const problems = validateProjectCreateForm(form);
    setErrors(problems);
    if (problems.length > 0) return;
    setSubmitting(true);
    setServerError("");
    try {
      const project = await createKagProject(projectCreateRequest(form));
      onCreated(project);
    } catch (error) {
      setServerError(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  const inputClass =
    "mt-1 w-full rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-sm text-[var(--foreground)] outline-none focus:border-[var(--ring)]";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] px-4"
      role="dialog"
      aria-modal="true"
      onClick={submitting ? undefined : onClose}
    >
      <form
        onClick={(event) => event.stopPropagation()}
        onSubmit={submit}
        className="max-h-[85vh] w-full max-w-md overflow-y-auto rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-xl"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-[var(--foreground)]">
            {t("Create project")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            aria-label={t("Close")}
            className="rounded-md p-1 text-[var(--muted-foreground)] hover:bg-[var(--background)] hover:text-[var(--foreground)] disabled:opacity-40"
          >
            <X size={16} />
          </button>
        </div>

        <p className="mb-4 text-[12px] leading-relaxed text-[var(--muted-foreground)]">
          {t(
            "Creates a LOCAL project on the OpenSPG server. The server initializes the default schema and verifies the embedding endpoint, which can take a while on small hosts.",
          )}
        </p>

        <label className="mb-3 block text-xs text-[var(--muted-foreground)]">
          {t("Project name")}
          <input
            type="text"
            value={form.name}
            onChange={(event) => update("name", event.target.value)}
            disabled={submitting}
            autoFocus
            className={inputClass}
          />
        </label>

        <label className="mb-3 block text-xs text-[var(--muted-foreground)]">
          {t("Namespace (Neo4j database name)")}
          <input
            type="text"
            value={form.namespace}
            onChange={(event) => update("namespace", event.target.value)}
            disabled={submitting}
            className={`${inputClass} font-mono`}
          />
        </label>

        <label className="mb-3 block text-xs text-[var(--muted-foreground)]">
          {t("Embedding model")}
          <select
            value={form.embeddingModelId}
            onChange={(event) => update("embeddingModelId", event.target.value)}
            disabled={submitting || models.length === 0}
            className={`${inputClass} cursor-pointer`}
          >
            <option value="">
              {models.length === 0 ? t("No embedding model configured") : t("Select…")}
            </option>
            {models.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name} · {profile.model}
              </option>
            ))}
          </select>
        </label>

        <label className="mb-3 block text-xs text-[var(--muted-foreground)]">
          {t("Vector dimensions (optional, probed if blank)")}
          <input
            type="text"
            inputMode="numeric"
            value={form.vectorDimensions}
            onChange={(event) => update("vectorDimensions", event.target.value)}
            disabled={submitting}
            placeholder="4096"
            className={inputClass}
          />
        </label>

        <label className="mb-4 block text-xs text-[var(--muted-foreground)]">
          {t("Service account (optional)")}
          <input
            type="text"
            value={form.serviceUserNo}
            onChange={(event) => update("serviceUserNo", event.target.value)}
            disabled={submitting}
            className={inputClass}
          />
        </label>

        {errors.length > 0 && (
          <ul className="mb-3 space-y-1 text-xs text-red-500">
            {errors.map((problem) => (
              <li key={problem}>{t(problem)}</li>
            ))}
          </ul>
        )}
        {serverError && (
          <p className="mb-3 break-words text-xs text-red-500">{serverError}</p>
        )}

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded-lg px-3 py-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)] disabled:opacity-40"
          >
            {t("Cancel")}
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-lg bg-[var(--foreground)] px-3 py-1.5 text-sm font-medium text-[var(--background)] hover:opacity-90 disabled:opacity-40"
          >
            {submitting ? t("Creating…") : t("Create")}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function KagProjectsPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const auth = useAuthStatus();
  const [projects, setProjects] = useState<KagProject[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const rows = await fetchKagProjects();
      setProjects(rows);
      setError(null);
    } catch (cause) {
      setError(cause);
      setProjects(null);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const errorStatus = projects === null ? statusOf(error) : undefined;
  // 403 = 读权限被拒（kag_projects grant 显式禁用）；502 = OpenSPG 上游
  // 未配置/不可达（后端代理语义），两者文案分流。
  const denied = errorStatus === 403;
  const unconfigured = errorStatus === 502;
  const showCreateButton = auth.isAdmin && !denied;

  const headerAction = useMemo(
    () => (
      <div className="flex items-center gap-2">
        <button
          onClick={() => void load()}
          disabled={loading || refreshing}
          className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--muted-foreground)] transition-colors hover:bg-[var(--card)] hover:text-[var(--foreground)] disabled:opacity-50"
        >
          <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
          {t("Refresh")}
        </button>
        {showCreateButton ? (
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--foreground)] transition-colors hover:bg-[var(--card)]"
          >
            <Plus size={14} />
            {t("New project")}
          </button>
        ) : null}
      </div>
    ),
    [load, loading, refreshing, showCreateButton, t],
  );

  return (
    <KagPageBody>
      <KagPageHeader
        icon={Waypoints}
        title={t("KAG Projects")}
        description={t(
          "Knowledge-graph projects managed by this KAGWeb instance on the OpenSPG server.",
        )}
        action={headerAction}
        meta={
          <Link
            href="/kag/tasks"
            className="inline-flex items-center gap-1 text-[12px] text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
          >
            <ListChecks size={13} aria-hidden />
            {t("Reasoning tasks")}
          </Link>
        }
      />

      <KagStateView loading={loading} error={null} />

      {!loading && denied ? (
        <div className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] px-5 py-10 text-center">
          <p className="text-[13.5px] font-medium text-[var(--foreground)]">
            {t("No access to the KAG management plane")}
          </p>
          <p className="mx-auto mt-1.5 max-w-md text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
            {t(
              "Your account is not allowed to browse KAG projects. Ask an administrator to grant access.",
            )}
          </p>
        </div>
      ) : !loading && unconfigured ? (
        <div className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] px-5 py-10 text-center">
          <p className="text-[13.5px] font-medium text-[var(--foreground)]">
            {t("OpenSPG server is not reachable")}
          </p>
          <p className="mx-auto mt-1.5 max-w-md break-words text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
            {t(
              "The KAG integration domain is not configured or the OpenSPG server is unreachable.",
            )}
            {auth.isAdmin ? (
              <>
                {" "}
                <Link
                  href="/settings/kag"
                  className="underline underline-offset-2 hover:text-[var(--foreground)]"
                >
                  {t("Open KAG settings")}
                </Link>
              </>
            ) : null}
          </p>
          <p className="mx-auto mt-2 max-w-lg break-words text-[11.5px] leading-relaxed text-[var(--muted-foreground)]/80">
            {errorMessage(error)}
          </p>
        </div>
      ) : !loading && projects !== null ? (
        <div className="overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--card)] shadow-sm">
          {projects.length === 0 ? (
            <div className="px-5 py-12 text-center">
              <p className="text-sm font-medium text-[var(--foreground)]">
                {t("No projects yet")}
              </p>
              <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                {t(
                  "Projects you create on the OpenSPG server will appear here.",
                )}
              </p>
            </div>
          ) : (
            <div className="divide-y divide-[var(--border)]">
              {projects.map((project) => (
                <ProjectRow key={project.projectId} project={project} />
              ))}
            </div>
          )}
        </div>
      ) : null}

      {showCreate ? (
        <CreateProjectDialog
          onClose={() => setShowCreate(false)}
          onCreated={(project) => {
            setShowCreate(false);
            void load();
            if (project.projectId) {
              // 创建成功后跳详情页（新项目含默认 Schema，直接看全貌）
              router.push(`/kag/projects/${encodeURIComponent(project.projectId)}`);
            }
          }}
        />
      ) : null}
    </KagPageBody>
  );
}
