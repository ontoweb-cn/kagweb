"use client";

import { useEffect, useState } from "react";
import { fetchAuthStatus } from "@/lib/auth";

export interface AuthStatusState {
  /** Whether auth is enabled on the backend. */
  enabled: boolean;
  /** Whether the current session is authenticated. */
  authenticated: boolean;
  /** Whether the authenticated user is an admin. */
  isAdmin: boolean;
  /** Stable account id for account-scoped browser state. */
  userId: string | null;
  /** False when the runtime status endpoint could not be reached. */
  statusAvailable: boolean;
  /** True until the first status fetch resolves. */
  loading: boolean;
  /** Whether the composer may offer a per-turn model picker. Absent field on
   *  older backends means "not reported" — keep the picker (back-compat). */
  modelSelectorEnabled: boolean;
}

const INITIAL: AuthStatusState = {
  enabled: false,
  authenticated: false,
  isAdmin: false,
  userId: null,
  statusAvailable: false,
  loading: true,
  modelSelectorEnabled: true,
};

/**
 * Resolve auth state at runtime from the backend (`/api/auth/status`).
 *
 * The frontend bundle is URL- and auth-agnostic (see web/lib/api.ts): the auth
 * toggle is a runtime setting read from `data/user/settings/auth.json`, never
 * baked into the build. Components that need to know whether auth is on — to
 * show the Sign-out / Admin affordances — use this hook instead of a build-time
 * constant, so it works identically on Docker (read-only rootfs), the PyPI
 * `kagweb start` launcher, and source dev.
 */
function loadAuthStatus(): Promise<AuthStatusState> {
  return fetchAuthStatus().then((status) => ({
    enabled: Boolean(status?.enabled),
    authenticated: Boolean(status?.authenticated),
    isAdmin: status?.role === "admin",
    userId:
      typeof status?.user_id === "string" && status.user_id.trim()
        ? status.user_id
        : null,
    statusAvailable: status !== null,
    loading: false,
    // Hide only on an explicit false: an older backend that predates the
    // field must not lose its picker on upgrade.
    modelSelectorEnabled: status?.model_selector_enabled !== false,
  }));
}

export function useAuthStatus(): AuthStatusState {
  const [state, setState] = useState<AuthStatusState>(INITIAL);

  useEffect(() => {
    let alive = true;
    loadAuthStatus().then((next) => {
      if (alive) setState(next);
    });
    return () => {
      alive = false;
    };
  }, []);

  return state;
}
