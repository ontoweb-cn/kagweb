"use client";

import { useSyncExternalStore } from "react";

import {
  getTraceMode,
  setTraceMode,
  subscribeTraceMode,
  type TraceMode,
} from "@/lib/trace-mode";

/**
 * Reactive trace disclosure mode. The server snapshot is the constant default
 * ("learner"), so SSR and the hydration render agree and the stored value
 * syncs in through the subscription — no mount-time setState, no hydration
 * mismatch.
 */
export function useTraceMode(): [TraceMode, (mode: TraceMode) => void] {
  const mode = useSyncExternalStore(
    subscribeTraceMode,
    getTraceMode,
    () => "learner" as TraceMode,
  );
  return [mode, setTraceMode];
}
