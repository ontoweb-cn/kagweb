"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import Link from "next/link";
import { useTranslation } from "react-i18next";

import {
  SettingRow,
  SettingSection,
  SettingsPageHeader,
  inputClass,
} from "@/components/settings/shared";
import { useSettings } from "@/features/settings/store/SettingsStore";
import { apiFetch, apiUrl } from "@/lib/api";
import {
  kagDraftToRequest,
  kagSettingsToDraft,
  parseKagSettings,
  type KagSettingsDraft,
} from "@/features/kag/model";

/**
 * `/settings/kag`（admin-only，设计 §6.3 T1 单租户子集）。
 *
 * kag settings 域：OpenSPG server 地址、Bridge 接线与项目绑定。保存经全局
 * 工具栏（Save Draft / Apply），模式沿 AttachmentsSettingsSection；payload
 * 注册为 PUT 请求体，离开页面后仍可由 EXTENSION_ENDPOINTS 落盘。
 */
export default function KagSettingsSection() {
  const { t } = useTranslation();
  const { registerExtension, pendingExtensionPayload, draftRevision } =
    useSettings();
  const [loaded, setLoaded] = useState<KagSettingsDraft | null>(null);
  const [apiKeySet, setApiKeySet] = useState(false);
  const [draft, setDraft] = useState<KagSettingsDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const response = await apiFetch(apiUrl("/api/settings/kag"));
        const data = (await response.json().catch(() => ({}))) as unknown;
        if (!response.ok) {
          const detail =
            data && typeof data === "object" && "detail" in data
              ? String((data as { detail: unknown }).detail)
              : "";
          throw new Error(detail || t("Failed to load KAG settings."));
        }
        if (cancelled) return;
        const settings = parseKagSettings(data);
        setApiKeySet(settings.bridgeApiKeySet);
        const next = kagSettingsToDraft(settings);
        // 恢复本会话内离开页面时的未保存编辑（模式沿 Attachments）
        const pending = pendingExtensionPayload("kag") as
          | Record<string, unknown>
          | undefined;
        if (pending && typeof pending === "object") {
          const pendingDraft: KagSettingsDraft = {
            spgServerUrl: String(pending.spg_server_url ?? ""),
            bridgeCommand: String(pending.bridge_command ?? ""),
            bridgeArgsText: Array.isArray(pending.bridge_args)
              ? pending.bridge_args.map(String).join("\n")
              : "",
            kagProjectDir: String(pending.kag_project_dir ?? ""),
            namespace: String(pending.namespace ?? ""),
            projectId: String(pending.project_id ?? ""),
            bridgeApiKey:
              typeof pending.bridge_api_key === "string" &&
              pending.bridge_api_key !== ""
                ? pending.bridge_api_key
                : null,
          };
          setLoaded(next);
          setDraft(pendingDraft);
        } else {
          setLoaded(next);
          setDraft(next);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [draftRevision, pendingExtensionPayload, t]);

  const dirty = useMemo(() => {
    if (!loaded || !draft) return false;
    return (
      loaded.spgServerUrl !== draft.spgServerUrl ||
      loaded.bridgeCommand !== draft.bridgeCommand ||
      loaded.bridgeArgsText !== draft.bridgeArgsText ||
      loaded.kagProjectDir !== draft.kagProjectDir ||
      loaded.namespace !== draft.namespace ||
      loaded.projectId !== draft.projectId ||
      draft.bridgeApiKey !== null
    );
  }, [draft, loaded]);

  // 经全局工具栏保存（Save Draft / Apply），无本地按钮。
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const save = useCallback(async () => {
    const current = draftRef.current;
    if (!current) return;
    setError(null);
    try {
      const settings = await (async () => {
        const response = await apiFetch(apiUrl("/api/settings/kag"), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(kagDraftToRequest(current)),
        });
        const data = (await response.json().catch(() => ({}))) as unknown;
        if (!response.ok) {
          const detail =
            data && typeof data === "object" && "detail" in data
              ? String((data as { detail: unknown }).detail)
              : "";
          throw new Error(detail || t("Failed to save KAG settings."));
        }
        return parseKagSettings(data);
      })();
      setApiKeySet(settings.bridgeApiKeySet);
      const next = kagSettingsToDraft(settings);
      setLoaded(next);
      setDraft(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [t]);

  useEffect(() => {
    // payload 注册为 PUT 请求体（离开页面后 EXTENSION_ENDPOINTS 仍可落盘）
    registerExtension("kag", {
      dirty,
      save,
      payload: draft ? kagDraftToRequest(draft) : undefined,
    });
    return () => registerExtension("kag", null);
  }, [dirty, draft, save, registerExtension]);

  const setField = <K extends keyof KagSettingsDraft>(
    key: K,
    value: KagSettingsDraft[K],
  ) => setDraft((current) => (current ? { ...current, [key]: value } : current));

  return (
    <div>
      <SettingsPageHeader
        title={t("KAG Integration")}
        description={t(
          "Wiring for the OpenSPG/KAG stack: the management-plane server, the bridge the agent loop calls, and the project conversations are grounded on.",
        )}
      />

      {loading && (
        <div className="flex items-center gap-2 text-[13px] text-[var(--muted-foreground)]">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t("Loading KAG settings...")}
        </div>
      )}

      {!loading && error && (
        <div className="mb-5 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-[13px] text-red-600 dark:text-red-300">
          {error}
        </div>
      )}

      {!loading && draft && (
        <>
          <SettingSection
            title={t("OpenSPG server")}
            description={t(
              "The management plane proxies this server's /public/v1 API. Deploy it on a trusted internal network — those endpoints are unauthenticated by design.",
            )}
          >
            <SettingRow
              title={t("Server URL")}
              description={t(
                "Base URL of the OpenSPG server, e.g. http://127.0.0.1:8887.",
              )}
              control={
                <input
                  className={`${inputClass} w-[300px] max-w-[45vw] font-mono`}
                  value={draft.spgServerUrl}
                  onChange={(event) => setField("spgServerUrl", event.target.value)}
                  placeholder="http://127.0.0.1:8887"
                />
              }
            />
          </SettingSection>

          <SettingSection
            title={t("Bridge")}
            description={t(
              "The kag-bridge MCP server the agent loop discovers via the session workdir's .mcp.json. Both the command and the project directory must be set for chat wiring to activate.",
            )}
          >
            <SettingRow
              title={t("Bridge command")}
              description={t(
                "Executable that starts the bridge, e.g. a venv's python. Args below are appended.",
              )}
              control={
                <input
                  className={`${inputClass} w-[300px] max-w-[45vw] font-mono`}
                  value={draft.bridgeCommand}
                  onChange={(event) =>
                    setField("bridgeCommand", event.target.value)
                  }
                />
              }
            />
            <SettingRow
              title={t("Bridge arguments")}
              description={t("One per line. The default runs the kag_bridge module.")}
              control={
                <textarea
                  className={`${inputClass} min-h-16 w-[300px] max-w-[45vw] resize-y font-mono text-[12.5px] leading-relaxed`}
                  value={draft.bridgeArgsText}
                  onChange={(event) =>
                    setField("bridgeArgsText", event.target.value)
                  }
                />
              }
            />
            <SettingRow
              title={t("Bridge API key")}
              description={
                apiKeySet
                  ? t("A key is stored. Leave blank to keep it, or type a new one.")
                  : t(
                      "Shared secret the bridge presents when reporting tasks back to this server.",
                    )
              }
              control={
                <input
                  className={`${inputClass} w-[280px] max-w-[45vw]`}
                  type="password"
                  autoComplete="off"
                  placeholder={
                    apiKeySet ? t("•••••••• (kept)") : "kag-bridge-key"
                  }
                  value={draft.bridgeApiKey ?? ""}
                  onChange={(event) => setField("bridgeApiKey", event.target.value)}
                />
              }
            />
          </SettingSection>

          <SettingSection
            title={t("Project binding")}
            description={t(
              "The KAG project conversations are grounded on. The directory must contain kag_config.yaml — the bridge treats that file as the only configuration source.",
            )}
          >
            <SettingRow
              title={t("Project directory")}
              description={t(
                "Absolute path to the KAG project directory on this host.",
              )}
              control={
                <input
                  className={`${inputClass} w-[300px] max-w-[45vw] font-mono`}
                  value={draft.kagProjectDir}
                  onChange={(event) =>
                    setField("kagProjectDir", event.target.value)
                  }
                />
              }
            />
            <SettingRow
              title={t("Namespace")}
              description={t("Project namespace used for grounding hints in chat.")}
              control={
                <input
                  className={`${inputClass} w-[240px] max-w-[40vw] font-mono`}
                  value={draft.namespace}
                  onChange={(event) => setField("namespace", event.target.value)}
                />
              }
            />
            <SettingRow
              title={t("Project ID")}
              description={t(
                "Optional. The OpenSPG project id bound to this namespace.",
              )}
              control={
                <input
                  className={`${inputClass} w-[160px] font-mono`}
                  value={draft.projectId}
                  onChange={(event) => setField("projectId", event.target.value)}
                />
              }
            />
          </SettingSection>

          <SettingSection
            title={t("Management plane")}
            description={t(
              "Projects, schema, and reasoning tasks live under /kag once the server URL above is configured.",
            )}
          >
            <SettingRow
              title={t("KAG console")}
              description={t(
                "Browse projects, the read-only schema tree, and reported reasoning tasks.",
              )}
              control={
                <Link
                  href="/kag"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-[13px] text-[var(--foreground)] transition-colors hover:bg-[var(--card)]"
                >
                  <ExternalLink size={13} aria-hidden />
                  {t("Open console")}
                </Link>
              }
            />
          </SettingSection>
        </>
      )}
    </div>
  );
}
