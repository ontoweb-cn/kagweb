"use client";

import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import Button from "@/components/ui/Button";

/**
 * Error boundary for the settings segment.
 *
 * Each category page loads its own heavy section and fetches on mount, so a
 * crash in one category is now contained to that route instead of taking the
 * whole settings surface down with it. This turns what would otherwise be a
 * blank page into a retry.
 *
 * Next renders the nearest `error.tsx` above the failing subtree, so this sits
 * at the settings segment and covers every category page under it.
 */
export default function SettingsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const { t } = useTranslation();

  useEffect(() => {
    // The launcher's console is where self-hosted operators look; a silent
    // boundary would hide the cause of the blank page they are staring at.
    console.error("Settings section failed to render:", error);
  }, [error]);

  return (
    <div className="flex items-center justify-center py-16">
      <div className="flex max-w-md flex-col items-center gap-4 rounded-2xl border border-[var(--border)] bg-[var(--secondary)]/40 px-8 py-10 text-center">
        <h2 className="text-base font-semibold text-[var(--foreground)]">
          {t("This settings page could not be loaded")}
        </h2>
        <p className="text-sm leading-relaxed text-[var(--muted-foreground)]">
          {t(
            "Something went wrong while rendering this section. Other settings pages are unaffected.",
          )}
        </p>
        {error.digest && (
          <p className="font-mono text-[11px] text-[var(--muted-foreground)]/70">
            {error.digest}
          </p>
        )}
        <Button onClick={reset}>{t("Try again")}</Button>
      </div>
    </div>
  );
}
