"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  FlaskConical,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  SettingRow,
  SettingSection,
  SettingsPageHeader,
  inputClass,
} from "@/components/settings/shared";
import { Toggle } from "@/components/settings/Toggle";
import { useSettings } from "@/features/settings/store/SettingsStore";
import { apiFetch, apiUrl } from "@/lib/api";

type AgentLoopFamily = "cli" | "http";

type PresetInfo = {
  name: string;
  family: AgentLoopFamily;
  description: string;
};

type StoredProfile = {
  id: string;
  name: string;
  preset: string;
  enabled: boolean;
  command: string;
  args: string[];
  env: Record<string, string>;
  url: string;
  turn_path: string;
  headers: Record<string, string>;
  timeout_seconds: number;
  session_workspace: boolean;
  consult_enabled: boolean;
  workdir: string;
  api_key_set?: boolean;
};

type AgentLoopPayload = {
  settings: {
    profiles: StoredProfile[];
    primary: string;
    consult_budget: number;
    allowed_workdir_roots: string[];
  };
  effective: {
    profiles: StoredProfile[];
    primary: string;
    consult_budget: number;
    allowed_workdir_roots: string[];
  };
  auto_primary: string;
  env_overrides: Record<string, boolean>;
  presets: PresetInfo[];
  bounds: { timeout_seconds: [number, number]; consult_budget: [number, number] };
};

type DetectInfo = {
  key: string;
  label: string;
  family: AgentLoopFamily;
  local: boolean;
  available: boolean;
  detail: string;
};

/** Editable form state; ``apiKey`` is tri-state (null = keep stored key). */
type DraftProfile = StoredProfile & {
  argsText: string;
  envText: string;
  headersText: string;
  apiKey: string | null;
};

type Lang = "zh" | "en";

const PRESET_META: Record<
  string,
  { label: { zh: string; en: string }; url?: string }
> = {
  intellect: {
    label: { zh: "Intellect 社区版", en: "Intellect Community" },
    url: "https://gitee.com/ontoweb/intellect-agent",
  },
  "intellect-team": {
    label: { zh: "Intellect 企业版", en: "Intellect Team (enterprise)" },
    url: "https://gitee.com/wustbd/intellect-team",
  },
  hermes: {
    label: { zh: "HERMES", en: "HERMES" },
    url: "https://github.com/nousresearch/hermes-agent",
  },
  agentscope: {
    label: { zh: "AgentScope", en: "AgentScope" },
    url: "https://github.com/agentscope-ai/agentscope",
  },
  "claude-code": { label: { zh: "Claude Code CLI", en: "Claude Code CLI" } },
  codex: { label: { zh: "Codex CLI", en: "Codex CLI" } },
  opencode: { label: { zh: "OpenCode CLI", en: "OpenCode CLI" } },
  "custom-cli": { label: { zh: "自定义 CLI", en: "Custom CLI" } },
  "custom-http": { label: { zh: "自定义 HTTP 服务", en: "Custom HTTP service" } },
};

function presetLabel(name: string, lang: Lang): string {
  return PRESET_META[name]?.label[lang] ?? name;
}

function presetUrl(name: string): string | undefined {
  return PRESET_META[name]?.url;
}

function isLocalProfile(profile: {
  preset: string;
  url?: string;
  presets: PresetInfo[];
}): boolean {
  const preset = profile.presets.find((item) => item.name === profile.preset);
  if (preset) return preset.family === "cli";
  const host = (profile.url || "").replace(/^[a-z]+:\/\//i, "").split(/[/:]/)[0];
  return (
    host === "localhost" || host === "::1" || host === "0.0.0.0" || host.startsWith("127.")
  );
}

function parseKeyValueLines(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf("=");
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    const value = trimmed.slice(eq + 1).trim();
    if (key) out[key] = value;
  }
  return out;
}

function formatKeyValueLines(map: Record<string, string> | undefined): string {
  return Object.entries(map ?? {})
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

function parseArgLines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function toDraft(profile: StoredProfile): DraftProfile {
  return {
    ...profile,
    argsText: (profile.args || []).join("\n"),
    envText: formatKeyValueLines(profile.env),
    headersText: formatKeyValueLines(profile.headers),
    apiKey: null,
  };
}

function draftToRequest(draft: DraftProfile) {
  return {
    id: draft.id,
    name: draft.name,
    preset: draft.preset,
    enabled: draft.enabled,
    command: draft.command,
    args: parseArgLines(draft.argsText),
    env: parseKeyValueLines(draft.envText),
    url: draft.url,
    turn_path: draft.turn_path,
    headers: parseKeyValueLines(draft.headersText),
    api_key: draft.apiKey,
    timeout_seconds: draft.timeout_seconds,
    session_workspace: draft.session_workspace,
    consult_enabled: draft.consult_enabled,
    workdir: draft.workdir,
  };
}

/** FastAPI error bodies: detail is a string (HTTPException) or a 422 array. */
function detailMessage(data: unknown, fallback: string): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : "",
        )
        .filter(Boolean);
      if (parts.length) return parts.join("; ");
    }
    if (detail) return String(detail);
  }
  return fallback;
}

function StatusChip({ ok, text }: { ok: boolean; text: string }) {
  return (
    <span
      title={text}
      className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-medium ${
        ok
          ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
          : "bg-[var(--border)]/40 text-[var(--muted-foreground)]"
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-[var(--muted-foreground)]/50"}`} />
      {text}
    </span>
  );
}

function DetectBadge({ result }: { result?: DetectInfo }) {
  const { t } = useTranslation();
  if (!result) return null;
  return (
    <StatusChip
      ok={result.available}
      text={
        result.available
          ? result.detail || t("Detected")
          : t("Not detected")
      }
    />
  );
}

export default function AgentLoopSettingsPage() {
  const { t, i18n } = useTranslation();
  const lang: Lang = (i18n.language || "en").toLowerCase().startsWith("zh")
    ? "zh"
    : "en";
  const { registerExtension, pendingExtensionPayload, draftRevision } =
    useSettings();
  const [payload, setPayload] = useState<AgentLoopPayload | null>(null);
  const [drafts, setDrafts] = useState<DraftProfile[] | null>(null);
  const [primaryMode, setPrimaryMode] = useState<string>("__auto__");
  const [consultBudget, setConsultBudget] = useState<number>(3);
  const [workdirRoots, setWorkdirRoots] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [detects, setDetects] = useState<Record<string, DetectInfo>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<
    Record<string, { ok: boolean; message: string }>
  >({});

  const familyOf = useCallback(
    (presetName: string): AgentLoopFamily | null =>
      payload?.presets.find((preset) => preset.name === presetName)?.family ??
      null,
    [payload],
  );

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const response = await apiFetch(apiUrl("/api/settings/agent-loop"));
        const data = (await response.json().catch(() => ({}))) as unknown;
        if (!response.ok) {
          throw new Error(
            detailMessage(data, t("Failed to load Agent Loop settings.")),
          );
        }
        if (cancelled) return;
        const next = data as AgentLoopPayload;
        setPayload(next);
        const pending = pendingExtensionPayload("agent-loop") as
          | {
              drafts?: DraftProfile[];
              primaryMode?: string;
              consultBudget?: number;
              workdirRoots?: string[];
            }
          | undefined;
        setDrafts(pending?.drafts ?? next.settings.profiles.map(toDraft));
        const savedPrimary = next.settings.primary;
        setPrimaryMode(
          pending?.primaryMode ??
            (savedPrimary && savedPrimary === next.auto_primary
              ? "__auto__"
              : savedPrimary || "__auto__"),
        );
        setConsultBudget(pending?.consultBudget ?? next.settings.consult_budget);
        setWorkdirRoots(
          pending?.workdirRoots ?? next.settings.allowed_workdir_roots ?? [],
        );
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

  const runDetect = useCallback(async () => {
    setDetecting(true);
    try {
      const response = await apiFetch(apiUrl("/api/settings/agent-loop/detect"));
      const data = (await response.json().catch(() => ({}))) as {
        results?: DetectInfo[];
      };
      const map: Record<string, DetectInfo> = {};
      for (const result of data.results ?? []) map[result.key] = result;
      setDetects(map);
    } catch {
      // Detection is advisory; leave the previous results in place.
    } finally {
      setDetecting(false);
    }
  }, []);

  // Auto-detect on mount, DeepMentor-style: the page shows install status at
  // config time instead of failing at turn time.
  useEffect(() => {
    runDetect();
  }, [runDetect]);

  // The roots editor keeps raw lines so Enter works while typing; blanks and
  // stray whitespace are dropped only when the draft is compared or saved.
  const rootsForSave = useMemo(
    () => parseArgLines(workdirRoots.join("\n")),
    [workdirRoots],
  );

  const dirty = useMemo(() => {
    if (!payload || !drafts) return false;
    if (consultBudget !== payload.settings.consult_budget) return true;
    if (
      JSON.stringify(rootsForSave) !==
      JSON.stringify(payload.settings.allowed_workdir_roots ?? [])
    )
      return true;
    const savedPrimary = payload.settings.primary;
    const savedMode =
      savedPrimary && savedPrimary === payload.auto_primary
        ? "__auto__"
        : savedPrimary || "__auto__";
    if (primaryMode !== savedMode) return true;
    if (payload.settings.profiles.length !== drafts.length) return true;
    return payload.settings.profiles.some((stored, index) => {
      const draft = drafts[index];
      if (!draft) return true;
      return JSON.stringify(toDraft(stored)) !== JSON.stringify(draft);
    });
  }, [consultBudget, drafts, payload, primaryMode, rootsForSave]);

  // Flush through the global Apply (top toolbar) instead of a local button.
  const stateRef = useRef({ drafts, primaryMode, consultBudget, rootsForSave });
  stateRef.current = { drafts, primaryMode, consultBudget, rootsForSave };
  const save = useCallback(async () => {
    const {
      drafts: current,
      primaryMode: mode,
      consultBudget: budget,
      rootsForSave: roots,
    } = stateRef.current;
    if (!current) return;
    setError(null);
    try {
      const response = await apiFetch(apiUrl("/api/settings/agent-loop"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          profiles: current.map(draftToRequest),
          primary:
            mode === "__auto__" ? null : mode === "__none__" ? "" : mode,
          consult_budget: budget,
          allowed_workdir_roots: roots,
        }),
      });
      const data = (await response.json().catch(() => ({}))) as unknown;
      if (!response.ok) {
        throw new Error(
          detailMessage(data, t("Failed to save Agent Loop settings.")),
        );
      }
      const next = data as AgentLoopPayload;
      setPayload(next);
      setTestResults({});
      setDrafts(next.settings.profiles.map(toDraft));
      setPrimaryMode(
        next.settings.primary && next.settings.primary === next.auto_primary
          ? "__auto__"
          : next.settings.primary || "__auto__",
      );
      setConsultBudget(next.settings.consult_budget);
      setWorkdirRoots(next.settings.allowed_workdir_roots ?? []);
      runDetect();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [runDetect, t]);

  useEffect(() => {
    registerExtension("agent-loop", {
      dirty,
      save,
      payload: {
        drafts,
        primaryMode,
        consultBudget: consultBudget,
        workdirRoots,
      },
    });
    return () => registerExtension("agent-loop", null);
  }, [dirty, save, drafts, primaryMode, consultBudget, workdirRoots, registerExtension]);

  const runTest = useCallback(
    async (draft: DraftProfile) => {
      setTestingId(draft.id || draft.name);
      try {
        const response = await apiFetch(apiUrl("/api/settings/agent-loop/test"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(draftToRequest(draft)),
        });
        const data = (await response.json().catch(() => ({}))) as unknown;
        if (!response.ok || !data || typeof data !== "object" || !("ok" in data)) {
          throw new Error(detailMessage(data, t("Test failed.")));
        }
        const result = data as { ok: boolean; message?: string };
        setTestResults((current) => ({
          ...current,
          [draft.id || draft.name]: {
            ok: result.ok,
            message: result.message || "",
          },
        }));
      } catch (err) {
        setTestResults((current) => ({
          ...current,
          [draft.id || draft.name]: {
            ok: false,
            message: err instanceof Error ? err.message : String(err),
          },
        }));
      } finally {
        setTestingId(null);
      }
    },
    [t],
  );

  const update = (id: string, patch: Partial<DraftProfile>) =>
    setDrafts((current) =>
      (current ?? []).map((draft) =>
        draft.id === id ? { ...draft, ...patch } : draft,
      ),
    );

  const addProfile = (preset: PresetInfo) => {
    const tempId = `new-${Date.now().toString(36)}-${Math.random()
      .toString(36)
      .slice(2, 6)}`;
    const draft = toDraft({
      id: tempId,
      name: presetLabel(preset.name, lang),
      preset: preset.name,
      enabled: true,
      command: "",
      args: [],
      env: {},
      url: "",
      turn_path: "",
      headers: {},
      timeout_seconds: 900,
      session_workspace: true,
      consult_enabled: true,
      workdir: "",
    });
    setDrafts((current) => [...(current ?? []), draft]);
    setExpanded(tempId);
    setPickerOpen(false);
  };

  const removeProfile = (id: string) => {
    setDrafts((current) => (current ?? []).filter((draft) => draft.id !== id));
    if (primaryMode === id) setPrimaryMode("__auto__");
  };

  const envPinned = payload?.env_overrides ?? {};
  const enabledDrafts = (drafts ?? []).filter((draft) => draft.enabled);
  const autoPrimaryLabel = useMemo(() => {
    const id = payload?.auto_primary || "";
    if (!id) return null;
    const draft = (drafts ?? []).find((item) => item.id === id);
    return draft ? draft.name : id;
  }, [payload, drafts]);

  const primaryRadio = (value: string, label: string, hint?: string) => (
    <label
      // Rendered from a `.map()` below: React needs the key on the element the
      // callback returns, not on a wrapper.
      key={value}
      className={`flex cursor-pointer items-start gap-2.5 rounded-xl border px-4 py-3 transition-colors ${
        primaryMode === value
          ? "border-emerald-500/60 bg-emerald-500/5"
          : "border-[var(--border)]/60 bg-[var(--card)] hover:border-[var(--ring)]/50"
      }`}
    >
      <input
        type="radio"
        name="agent-loop-primary"
        className="mt-0.5 accent-emerald-600"
        checked={primaryMode === value}
        onChange={() => setPrimaryMode(value)}
      />
      <span className="min-w-0">
        <span className="block text-[13.5px] font-medium text-[var(--foreground)]">
          {label}
        </span>
        {hint && (
          <span className="mt-0.5 block text-[11.5px] leading-relaxed text-[var(--muted-foreground)]">
            {hint}
          </span>
        )}
      </span>
    </label>
  );

  return (
    <div data-tour="tour-agent-loop">
      <SettingsPageHeader
        title={t("Agent Loop")}
        description={t(
          "Configure the agent loops that drive conversations. One enabled loop is the primary and owns every turn; the others can be consulted by it mid-turn. Local CLIs and services are auto-detected.",
        )}
      />

      <p className="mb-7 text-[12px] text-[var(--muted-foreground)]">
        {t("Changes apply to the next turn — no restart needed.")}
      </p>

      {loading && (
        <div className="flex items-center gap-2 text-[13px] text-[var(--muted-foreground)]">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t("Loading Agent Loop settings...")}
        </div>
      )}

      {!loading && error && (
        <div className="mb-6 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-[13px] text-red-600 dark:text-red-300">
          {error}
        </div>
      )}

      {!loading && payload && drafts && (
        <>
          <SettingSection
            title={t("Local detection")}
            description={t(
              "CLI presets are PATH-probed on this machine; configured HTTP services get a short reachability probe. Advisory only — the definitive check is each profile's Test button.",
            )}
          >
            <div className="flex flex-wrap items-center gap-2 py-4">
              {(payload.presets ?? [])
                .filter((preset) => preset.family === "cli" && preset.name !== "custom-cli")
                .map((preset) => {
                  const result = detects[preset.name];
                  return (
                    <span
                      key={preset.name}
                      className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)]/60 px-2.5 py-1 text-[12px]"
                    >
                      <span className="text-[var(--foreground)]">
                        {presetLabel(preset.name, lang)}
                      </span>
                      {detecting && !result ? (
                        <Loader2 className="h-3 w-3 animate-spin text-[var(--muted-foreground)]" />
                      ) : (
                        <DetectBadge result={result} />
                      )}
                    </span>
                  );
                })}
              <button
                type="button"
                onClick={runDetect}
                disabled={detecting}
                className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-2.5 py-1 text-[12px] text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)] disabled:opacity-50"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${detecting ? "animate-spin" : ""}`} />
                {t("Re-detect")}
              </button>
            </div>
          </SettingSection>

          <SettingSection
            title={t("Primary agent loop")}
            description={t(
              "The primary drives every chat turn. \"Automatic\" prefers a local Intellect (community or enterprise), then any local loop, then the first enabled one.",
            )}
          >
            <div className="grid grid-cols-1 gap-3 py-4 md:grid-cols-2">
              {primaryRadio(
                "__auto__",
                t("Automatic"),
                autoPrimaryLabel
                  ? t("Currently selects: {{name}}", { name: autoPrimaryLabel })
                  : t("Currently selects: none (shell stub)"),
              )}
              {primaryRadio(
                "__none__",
                t("None (framework-shell stub)"),
                t("Every turn completes with the framework-shell notice."),
              )}
              {enabledDrafts.map((draft) =>
                primaryRadio(
                  draft.id,
                  `${draft.name} · ${presetLabel(draft.preset, lang)}`,
                  draft.id === payload.effective.primary
                    ? t("Effective now")
                    : undefined,
                ),
              )}
            </div>
            <SettingRow
              title={t("Consult budget (per turn)")}
              description={t(
                "How many times the primary may consult the other enabled loops in one turn. 0 disables consultation.",
              )}
              control={
                <input
                  className={`${inputClass} w-24`}
                  type="number"
                  min={payload.bounds?.consult_budget?.[0] ?? 0}
                  max={payload.bounds?.consult_budget?.[1] ?? 12}
                  value={consultBudget}
                  onChange={(event) => setConsultBudget(Number(event.target.value))}
                />
              }
            />
          </SettingSection>

          {Object.values(envPinned).some(Boolean) && (
            <div className="mb-7 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-[12.5px] leading-relaxed text-amber-700 dark:text-amber-300">
              {t(
                "Process environment variables (KAGWEB_AGENT_LOOP_BACKEND / _COMMAND / _URL, KAG_AGENT_LOOP_API_KEY) currently override the effective primary. The pinned inputs are disabled — the environment wins until the server is restarted without them.",
              )}
            </div>
          )}

          <SettingSection
            title={t("Allowed working directories")}
            description={t(
              "A CLI profile's working directory must sit inside one of these roots — the loop runs with the server's privileges, so an unchecked path would be an arbitrary-directory grant. One per line, resolved against the project root.",
            )}
          >
            <div className="py-4">
              <textarea
                className={`${inputClass} min-h-20 w-full resize-y font-mono text-[12.5px] leading-relaxed`}
                placeholder={"data/user"}
                value={workdirRoots.join("\n")}
                onChange={(event) =>
                  setWorkdirRoots(event.target.value.split("\n"))
                }
              />
            </div>
          </SettingSection>

          <SettingSection
            title={t("Agent loops")}
            description={t(
              "Each profile is one agent loop — local or remote. Enabled, consultable profiles other than the primary are offered to it for mid-turn consultation (the consult_agent tool call in the trace).",
            )}
          >
            <div className="space-y-3 py-4">
              {drafts.map((draft) => {
                const family = familyOf(draft.preset);
                const known = family !== null;
                const detect = detects[draft.id];
                const local = isLocalProfile({
                  preset: draft.preset,
                  url: draft.url,
                  presets: payload.presets ?? [],
                });
                const testResult = testResults[draft.id || draft.name];
                const isPrimary =
                  primaryMode === draft.id ||
                  (primaryMode === "__auto__" && payload.auto_primary === draft.id);
                return (
                  <div
                    key={draft.id}
                    className={`rounded-xl border transition-colors ${
                      draft.enabled
                        ? "border-[var(--border)]/70 bg-[var(--card)]"
                        : "border-[var(--border)]/40 bg-[var(--card)]/50 opacity-70"
                    }`}
                  >
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
                      <button
                        type="button"
                        onClick={() =>
                          setExpanded(expanded === draft.id ? null : draft.id)
                        }
                        className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      >
                        <ChevronDown
                          className={`h-4 w-4 shrink-0 text-[var(--muted-foreground)] transition-transform ${
                            expanded === draft.id ? "" : "-rotate-90"
                          }`}
                        />
                        <span className="truncate text-[13.5px] font-medium text-[var(--foreground)]">
                          {draft.name || draft.preset}
                        </span>
                        <span className="shrink-0 rounded-md bg-[var(--border)]/40 px-1.5 py-0.5 text-[10.5px] font-medium text-[var(--muted-foreground)]">
                          {presetLabel(draft.preset, lang)}
                        </span>
                        <span className="shrink-0 text-[10.5px] text-[var(--muted-foreground)]">
                          {local ? t("Local") : t("Remote")}
                        </span>
                        {isPrimary && (
                          <span className="shrink-0 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10.5px] font-medium text-emerald-600 dark:text-emerald-400">
                            {t("Primary")}
                          </span>
                        )}
                      </button>
                      <DetectBadge result={detect} />
                      <label className="flex shrink-0 items-center gap-1.5 text-[11.5px] text-[var(--muted-foreground)]">
                        {t("Enabled")}
                        <Toggle
                          checked={draft.enabled}
                          onChange={(next) => update(draft.id, { enabled: next })}
                        />
                      </label>
                    </div>

                    {expanded === draft.id && (
                      <div className="border-t border-[var(--border)]/50 px-4 py-3">
                        <SettingRow
                          title={t("Profile name")}
                          description={t(
                            "Shown in the primary picker and in consult prompts.",
                          )}
                          control={
                            <input
                              className={`${inputClass} w-[220px] max-w-[40vw]`}
                              value={draft.name}
                              onChange={(event) =>
                                update(draft.id, { name: event.target.value })
                              }
                            />
                          }
                        />
                        {!known && (
                          <p className="px-1 py-2 text-[12px] text-red-600 dark:text-red-300">
                            {t(
                              "The preset is not known; pick a preset for this profile.",
                            )}
                          </p>
                        )}

                        {family === "http" && (
                          <>
                            <SettingRow
                              title={t("Service URL")}
                              description={t(
                                "Base URL of the agent service, e.g. http://agent-service:8000.",
                              )}
                              control={
                                <input
                                  className={`${inputClass} w-[360px] max-w-[48vw]`}
                                  placeholder={
                                    local ? "http://localhost:8083" : "https://agent.example.com"
                                  }
                                  value={draft.url}
                                  onChange={(event) =>
                                    update(draft.id, { url: event.target.value })
                                  }
                                />
                              }
                            />
                            <SettingRow
                              title={t("Turn endpoint path")}
                              description={t(
                                "Streamed turn endpoint appended to the service URL.",
                              )}
                              control={
                                <input
                                  className={`${inputClass} w-[240px] max-w-[40vw] font-mono`}
                                  placeholder="/agent/turn"
                                  value={draft.turn_path}
                                  onChange={(event) =>
                                    update(draft.id, { turn_path: event.target.value })
                                  }
                                />
                              }
                            />
                            <SettingRow
                              title={t("API key")}
                              description={
                                draft.api_key_set
                                  ? t("A key is stored. Leave blank to keep it, or type a new one.")
                                  : t("Sent as Authorization: Bearer when set.")
                              }
                              control={
                                <input
                                  className={`${inputClass} w-[280px] max-w-[40vw]`}
                                  type="password"
                                  autoComplete="off"
                                  placeholder={
                                    draft.api_key_set
                                      ? t("•••••••• (kept)")
                                      : "sk-..."
                                  }
                                  value={draft.apiKey ?? ""}
                                  onChange={(event) =>
                                    update(draft.id, { apiKey: event.target.value })
                                  }
                                />
                              }
                            />
                            <div className="py-3">
                              <div className="text-[13.5px] font-medium text-[var(--foreground)]">
                                {t("Extra headers")}
                              </div>
                              <p className="mb-2 mt-1 text-[12px] leading-relaxed text-[var(--muted-foreground)]">
                                {t("One per line as KEY=VALUE. Merged after Authorization.")}
                              </p>
                              <textarea
                                className={`${inputClass} min-h-20 resize-y font-mono text-[12.5px] leading-relaxed`}
                                placeholder={"X-Trace-Id=kagweb"}
                                value={draft.headersText}
                                onChange={(event) =>
                                  update(draft.id, { headersText: event.target.value })
                                }
                              />
                            </div>
                          </>
                        )}

                        {family === "cli" && (
                          <>
                            <SettingRow
                              title={t("Command")}
                              description={t(
                                "Leave blank to use the preset's executable (claude / codex / opencode).",
                              )}
                              control={
                                <input
                                  className={`${inputClass} w-[280px] max-w-[40vw] font-mono`}
                                  placeholder={t("preset default")}
                                  value={draft.command}
                                  onChange={(event) =>
                                    update(draft.id, { command: event.target.value })
                                  }
                                />
                              }
                            />
                            <div className="py-3">
                              <div className="text-[13.5px] font-medium text-[var(--foreground)]">
                                {t("Extra arguments")}
                              </div>
                              <p className="mb-2 mt-1 text-[12px] leading-relaxed text-[var(--muted-foreground)]">
                                {t(
                                  "One argument per line, appended to the preset's argv. Use {prompt} to place the user turn.",
                                )}
                              </p>
                              <textarea
                                className={`${inputClass} min-h-20 resize-y font-mono text-[12.5px] leading-relaxed`}
                                placeholder={"--model\nbig-model\n{prompt}"}
                                value={draft.argsText}
                                onChange={(event) =>
                                  update(draft.id, { argsText: event.target.value })
                                }
                              />
                            </div>
                            <div className="py-3">
                              <div className="text-[13.5px] font-medium text-[var(--foreground)]">
                                {t("Environment variables")}
                              </div>
                              <p className="mb-2 mt-1 text-[12px] leading-relaxed text-[var(--muted-foreground)]">
                                {t(
                                  "One per line as KEY=VALUE. This is the ONLY channel for the child's credentials (API keys etc.).",
                                )}
                              </p>
                              <textarea
                                className={`${inputClass} min-h-20 resize-y font-mono text-[12.5px] leading-relaxed`}
                                placeholder={"ANTHROPIC_API_KEY=sk-..."}
                                value={draft.envText}
                                onChange={(event) =>
                                  update(draft.id, { envText: event.target.value })
                                }
                              />
                            </div>
                            <SettingRow
                              title={t("Per-session workspace")}
                              description={t(
                                "Give each chat session its own working directory so files the loop creates stay reachable across turns.",
                              )}
                              control={
                                <Toggle
                                  checked={draft.session_workspace}
                                  onChange={(next) =>
                                    update(draft.id, { session_workspace: next })
                                  }
                                />
                              }
                            />
                            <SettingRow
                              title={t("Working directory")}
                              description={t(
                                "Where the CLI runs. Leave blank for the per-session workspace; a path here must sit inside an allowed root below.",
                              )}
                              control={
                                <input
                                  className={`${inputClass} w-[360px] max-w-[48vw] font-mono`}
                                  placeholder="D:/projects/my-app"
                                  value={draft.workdir}
                                  onChange={(event) =>
                                    update(draft.id, { workdir: event.target.value })
                                  }
                                />
                              }
                            />
                          </>
                        )}

                        <SettingRow
                          title={t("Per-turn timeout (seconds)")}
                          description={t("Between {{min}} and {{max}} seconds.", {
                            min: payload.bounds?.timeout_seconds?.[0] ?? 30,
                            max: payload.bounds?.timeout_seconds?.[1] ?? 86_400,
                          })}
                          control={
                            <input
                              className={`${inputClass} w-28`}
                              type="number"
                              min={payload.bounds?.timeout_seconds?.[0] ?? 30}
                              max={payload.bounds?.timeout_seconds?.[1] ?? 86_400}
                              value={draft.timeout_seconds}
                              onChange={(event) =>
                                update(draft.id, {
                                  timeout_seconds: Number(event.target.value),
                                })
                              }
                            />
                          }
                        />
                        <SettingRow
                          title={t("Available for consultation")}
                          description={t(
                            "Let the primary loop consult this profile mid-turn (consult_agent). Ignored while this profile is the primary.",
                          )}
                          control={
                            <Toggle
                              checked={draft.consult_enabled}
                              onChange={(next) =>
                                update(draft.id, { consult_enabled: next })
                              }
                            />
                          }
                        />

                        <div className="flex flex-wrap items-center gap-3 border-t border-[var(--border)]/50 py-3">
                          <button
                            type="button"
                            onClick={() => runTest(draft)}
                            disabled={testingId === (draft.id || draft.name)}
                            className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-3.5 py-1.5 text-[12.5px] font-medium text-[var(--foreground)] transition-colors hover:border-[var(--ring)] disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            {testingId === (draft.id || draft.name) ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <FlaskConical className="h-3.5 w-3.5" />
                            )}
                            {t("Run check")}
                          </button>
                          <button
                            type="button"
                            onClick={() => removeProfile(draft.id)}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-transparent px-2.5 py-1.5 text-[12.5px] text-[var(--muted-foreground)] transition-colors hover:border-red-500/30 hover:text-red-600 dark:hover:text-red-300"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            {t("Remove")}
                          </button>
                        </div>
                        {testResult && (
                          <div
                            className={`mb-1 rounded-xl border px-3.5 py-2.5 text-[12px] leading-relaxed ${
                              testResult.ok
                                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                                : "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-300"
                            }`}
                          >
                            {testResult.message}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}

              {!pickerOpen ? (
                <button
                  type="button"
                  onClick={() => setPickerOpen(true)}
                  className="flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-[var(--border)] px-4 py-3 text-[13px] text-[var(--muted-foreground)] transition-colors hover:border-[var(--ring)]/60 hover:text-[var(--foreground)]"
                >
                  <Plus className="h-4 w-4" />
                  {t("Add agent loop")}
                </button>
              ) : (
                <div className="rounded-xl border border-[var(--border)]/60 p-4">
                  <div className="mb-3 flex items-center justify-between">
                    <span className="text-[12px] font-semibold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
                      {t("Pick a preset")}
                    </span>
                    <button
                      type="button"
                      onClick={() => setPickerOpen(false)}
                      className="text-[12px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                    >
                      {t("common.cancel")}
                    </button>
                  </div>
                  <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
                    {(payload.presets ?? [])
                      .filter((preset) => preset.name !== "custom-cli" && preset.name !== "custom-http")
                      .map((preset) => {
                        const detect =
                          preset.family === "cli" ? detects[preset.name] : undefined;
                        return (
                          <button
                            key={preset.name}
                            type="button"
                            onClick={() => addProfile(preset)}
                            className="flex items-start gap-2 rounded-xl border border-[var(--border)]/60 px-3.5 py-2.5 text-left transition-colors hover:border-[var(--ring)]/60"
                          >
                            <span className="min-w-0 flex-1">
                              <span className="flex items-center gap-2">
                                <span className="text-[13px] font-medium text-[var(--foreground)]">
                                  {presetLabel(preset.name, lang)}
                                </span>
                                {preset.family === "http" && (
                                  <span className="rounded-md bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-medium text-sky-600 dark:text-sky-400">
                                    HTTP
                                  </span>
                                )}
                              </span>
                              <span className="mt-0.5 block text-[11.5px] leading-relaxed text-[var(--muted-foreground)]">
                                {preset.description}
                              </span>
                            </span>
                            {detect && <DetectBadge result={detect} />}
                          </button>
                        );
                      })}
                    {(payload.presets ?? [])
                      .filter((preset) => preset.name === "custom-cli" || preset.name === "custom-http")
                      .map((preset) => (
                        <button
                          key={preset.name}
                          type="button"
                          onClick={() => addProfile(preset)}
                          className="flex items-center gap-2 rounded-xl border border-dashed border-[var(--border)]/60 px-3.5 py-2.5 text-left transition-colors hover:border-[var(--ring)]/60"
                        >
                          <Plus className="h-3.5 w-3.5 text-[var(--muted-foreground)]" />
                          <span className="text-[13px] text-[var(--muted-foreground)]">
                            {presetLabel(preset.name, lang)}
                          </span>
                        </button>
                      ))}
                  </div>
                  <p className="mt-3 text-[11.5px] leading-relaxed text-[var(--muted-foreground)]">
                    {t(
                      "A preset only fills in defaults: pick Intellect / HERMES / AgentScope for their service contract, or a CLI preset for a terminal agent. The same preset can be added several times (e.g. one local and one remote Hermes).",
                    )}
                  </p>
                </div>
              )}
            </div>
          </SettingSection>

          <SettingSection
            title={t("How consultation works")}
            description={t(
              "Multi-agent collaboration without changing the turn contract:",
            )}
          >
            <p className="py-3 text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
              {t(
                "With consultable loops configured, the primary receives a manifest of them. When it wants a second opinion it ends its reply with a fenced consult block; KAGWeb runs that loop (visible as a consult_agent tool call in the trace), feeds the result back, and the primary produces the final answer — up to the budget per turn. Consult sessions keep their own continuity via <chat>::consult::<profile> session ids.",
              )}
            </p>
          </SettingSection>
        </>
      )}
    </div>
  );
}
