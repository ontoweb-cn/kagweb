"use client";

import SettingsOverview from "@/components/settings/SettingsOverview";
import { useSettingsAccess } from "@/features/settings/navigation/SettingsAccessProvider";

/**
 * The settings index.
 *
 * Every category is its own route now (see `settings-nav.ts`), so this page is
 * only the landing view: what state the installation is in, and what is
 * waiting to be applied. It no longer stacks the categories — the persistent
 * navigator reaches them directly, and the route decides what is mounted.
 *
 * Waiting for access to resolve keeps a deep link from mounting a section and
 * firing its API request before runtime auth has been determined.
 */
export default function SettingsPage() {
  const access = useSettingsAccess();
  if (!access.resolved) return <div className="h-48" aria-busy="true" />;
  return <SettingsOverview />;
}
