"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { listAgentLoopModels } from "@/lib/agent-loop-models";
import { createSingleFlight } from "@/lib/single-flight";

interface RefreshOptions {
  force?: boolean;
  /** Keep the last usable payload visible while synchronizing in the background. */
  background?: boolean;
}

interface AgentLoopModelsState {
  status: "loading" | "ready" | "error";
  payload: Awaited<ReturnType<typeof listAgentLoopModels>> | null;
}

type Action =
  | { type: "refresh-started"; background: boolean }
  | { type: "refresh-succeeded"; payload: AgentLoopModelsState["payload"] }
  | { type: "refresh-failed" };

function reducer(state: AgentLoopModelsState, action: Action): AgentLoopModelsState {
  switch (action.type) {
    case "refresh-started":
      return state.payload && action.background
        ? state
        : { status: "loading", payload: state.payload };
    case "refresh-succeeded":
      return { status: "ready", payload: action.payload };
    case "refresh-failed":
      return state.payload
        ? { status: "ready", payload: state.payload }
        : { status: "error", payload: null };
  }
}

const INITIAL: AgentLoopModelsState = { status: "loading", payload: null };

/** Owns agent-loop per-turn model loading, independently of the chat page
 *  lifecycle. Refetches when the session changes — the ACP answer carries
 *  that session's current model. `enabled=false` (picker hidden) keeps it
 *  from fetching at all. */
export function useAgentLoopModels(sessionId: string | null, enabled = true) {
  const [state, dispatch] = useReducer(reducer, INITIAL);
  const latestRequestRef = useRef(0);
  const [loadOptions] = useState(() => createSingleFlight(listAgentLoopModels));
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(
    async (options?: RefreshOptions) => {
      if (!enabled) return;
      const requestId = ++latestRequestRef.current;
      dispatch({ type: "refresh-started", background: options?.background ?? false });
      try {
        const payload = await loadOptions({ sessionId, force: options?.force });
        if (!mountedRef.current || requestId !== latestRequestRef.current) return;
        dispatch({ type: "refresh-succeeded", payload });
      } catch {
        if (!mountedRef.current || requestId !== latestRequestRef.current) return;
        dispatch({ type: "refresh-failed" });
      }
    },
    [loadOptions, sessionId, enabled],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return {
    payload: state.payload,
    source: state.payload?.source ?? null,
    options: state.payload?.options ?? [],
    backendLabel: state.payload?.backend_label ?? "",
    loading: state.status === "loading",
    error: state.status === "error",
    refresh,
  };
}
