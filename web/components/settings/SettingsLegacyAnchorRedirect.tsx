"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

import {
  SETTINGS_HUB_HREF,
  settingsHref,
} from "@/features/settings/navigation/settings-nav";
/**
 * Send an old fragment-only settings link to the route that now owns it.
 *
 * Settings used to be one document addressed by fragment (`/settings#llm`).
 * Each category is a route now, so those links have to be translated:
 * `/settings#llm` → `/settings/models#llm`.
 *
 * This lives in the settings layout rather than at each producer because every
 * path into a settings URL ends here — bookmarks, the assistant's
 * `setup_credential` hand-off card (which still emits `/settings#llm`), and the
 * sidebar version badge. One interception point covers them all, and it is
 * deliberately transitional: the mapping is "old fragment → new route", not a
 * permanent alias table.
 */
export function SettingsLegacyAnchorRedirect() {
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (pathname !== SETTINGS_HUB_HREF) return;

    const apply = () => {
      const key = window.location.hash.replace(/^#/, "");
      if (!key) return;
      // `settingsHref` resolves a leaf to its category route plus anchor, and
      // a category or unknown key to the route that owns it — an unknown
      // fragment lands on the hub rather than leaving the hash unresolved.
      router.replace(settingsHref(key));
    };

    apply();
    window.addEventListener("hashchange", apply);
    return () => window.removeEventListener("hashchange", apply);
  }, [pathname, router]);

  return null;
}
