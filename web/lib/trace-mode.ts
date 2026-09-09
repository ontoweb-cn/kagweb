/**
 * Trace disclosure mode: "learner" (default) keeps the trace quiet — no
 * inline thinking streams, no argument grids — while "expert" restores the
 * full-density view. One knob, three affected dimensions only (thinking
 * density, argument density, the toggle itself); panel mutual-exclusion
 * models are deliberately untouched.
 *
 * Storage goes through the shared `browserStorage` boundary (`local` scope,
 * `dt:chat:` key namespace) — direct window.localStorage access is a contract
 * violation (architecture-contracts "shared boundary" test). The first render
 * always reports the constant default so SSR/hydration never mismatches; the
 * real stored value is only read post-mount (same pattern as the DAG layout
 * persistence).
 */
import { browserStorage } from "@/shared/storage";

export type TraceMode = "learner" | "expert";

const STORAGE_KEY = "dt:chat:trace-mode";
const DEFAULT_MODE: TraceMode = "learner";

const listeners = new Set<() => void>();
let current: TraceMode = DEFAULT_MODE;
let initialized = false;

function ensureInitialized(): void {
  if (initialized || typeof window === "undefined") return;
  initialized = true;
  const raw = browserStorage.readRaw("local", STORAGE_KEY);
  if (raw === "learner" || raw === "expert") current = raw;
}

export function getTraceMode(): TraceMode {
  ensureInitialized();
  return current;
}

export function setTraceMode(mode: TraceMode): void {
  current = mode;
  if (typeof window !== "undefined") {
    browserStorage.writeRaw("local", STORAGE_KEY, mode);
  }
  for (const listener of listeners) listener();
}

export function subscribeTraceMode(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
