"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";

import { fetchAuthStatus } from "@/lib/auth";
import { apiFetch, apiUrl } from "@/shared/api/client";
import {
  PENDING_SETTINGS_ACCESS,
  settingsAccessFromAuthStatus,
  withLlmSettingsGate,
  type SettingsAccess,
} from "@/features/settings/navigation/settings-access";

const SettingsAccessContext = createContext<SettingsAccess>(
  PENDING_SETTINGS_ACCESS,
);

/**
 * Read the LLM-settings gate from the agent-loop payload.
 *
 * Returns ``null`` when it cannot be known (request failed, or a backend that
 * does not report the field) so the caller leaves the section as it was rather
 * than hiding settings on a transient failure.
 */
async function fetchLlmSettingsGate(): Promise<boolean | null> {
  try {
    const response = await apiFetch(apiUrl("/api/settings/agent-loop"));
    if (!response.ok) return null;
    const payload = (await response.json()) as {
      effective_primary?: { llm_settings_enabled?: unknown };
    };
    const enabled = payload.effective_primary?.llm_settings_enabled;
    return typeof enabled === "boolean" ? enabled : null;
  } catch {
    return null;
  }
}

/** Resolve settings access once for the entire persistent settings document. */
export function SettingsAccessProvider({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const [access, setAccess] = useState<SettingsAccess>(PENDING_SETTINGS_ACCESS);

  useEffect(() => {
    let cancelled = false;
    void fetchAuthStatus()
      .then(async (authStatus) => {
        if (cancelled) return;
        const base = settingsAccessFromAuthStatus(authStatus);
        // The models category is admin-only, so an ordinary user never sees the
        // LLM section regardless; skip the admin-only request for them.
        const gate = base.hideAdminOnly ? null : await fetchLlmSettingsGate();
        if (cancelled) return;
        setAccess(withLlmSettingsGate(base, gate));
      })
      .catch(() => {
        if (!cancelled) setAccess(settingsAccessFromAuthStatus(null));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo(() => access, [access]);
  return (
    <SettingsAccessContext.Provider value={value}>
      {children}
    </SettingsAccessContext.Provider>
  );
}

export function useSettingsAccess(): SettingsAccess {
  return useContext(SettingsAccessContext);
}
