import { apiFetch, apiUrl } from "@/lib/api";
import { invalidateClientCache, withClientCache } from "@/lib/client-cache";

/** One model the active agent-loop backend can run this turn. */
export interface BackendModelOption {
  id: string;
  name: string;
  description?: string;
  /** The backend's actual current model for the probed/live session. */
  is_current: boolean;
}

export type AgentLoopModelsSource =
  /** The agent's own advertised selector (ACP handshake / probe). */
  | "acp"
  /** The conversation LLM catalog (self-hosted Intellect HTTP services). */
  | "catalog"
  /** The operator-curated profile vocabulary. */
  | "profile"
  /** The backend consumes no per-turn model — the picker stays hidden. */
  | "none";

export interface AgentLoopModelsResponse {
  per_turn_model: boolean;
  source: AgentLoopModelsSource;
  backend_label: string;
  options: BackendModelOption[];
}

const AGENT_LOOP_MODELS_CACHE_PREFIX = "agent-loop-models:list:";
const DEFAULT_TIMEOUT_MS = 30_000;

function cacheKey(sessionId: string | null) {
  return `${AGENT_LOOP_MODELS_CACHE_PREFIX}${sessionId ?? ""}`;
}

/** List the per-turn model options the active agent-loop backend offers.
 *
 *  Cached per session (the ACP current-model answer is session-scoped).
 *  Editing an agent-loop profile calls `invalidateAgentLoopModelsCache`; pass
 *  `force` to bypass the cache. */
export async function listAgentLoopModels(options?: {
  sessionId?: string | null;
  force?: boolean;
  timeoutMs?: number;
}): Promise<AgentLoopModelsResponse> {
  const sessionId = options?.sessionId ?? null;
  return withClientCache<AgentLoopModelsResponse>(
    cacheKey(sessionId),
    async () => {
      const controller = new AbortController();
      const timeout = setTimeout(
        () => controller.abort(),
        options?.timeoutMs ?? DEFAULT_TIMEOUT_MS,
      );
      try {
        const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
        const response = await apiFetch(apiUrl(`/api/settings/agent-loop/models${query}`), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Failed to load agent-loop models: ${response.status}`);
        }
        const data = (await response.json()) as AgentLoopModelsResponse;
        return {
          per_turn_model: Boolean(data.per_turn_model),
          source: data.source ?? "none",
          backend_label: data.backend_label ?? "",
          options: Array.isArray(data.options) ? data.options : [],
        };
      } finally {
        clearTimeout(timeout);
      }
    },
    { force: options?.force },
  );
}

export function invalidateAgentLoopModelsCache(): void {
  invalidateClientCache(cacheKey(null));
}

/** The backend-native selection payload for the start-turn command. */
export function backendModelSelection(id: string): { backend_model: string } {
  return { backend_model: id };
}

export function isBackendModelSelection(
  selection: unknown,
): selection is { backend_model: string } {
  return (
    typeof selection === "object" &&
    selection !== null &&
    "backend_model" in selection &&
    typeof (selection as { backend_model: unknown }).backend_model === "string"
  );
}
