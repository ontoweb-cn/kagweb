"use client";

import { Microscope } from "lucide-react";
import { useTranslation } from "react-i18next";

export default function ResearchAgentPage() {
  const { t } = useTranslation();

  return (
    <main className="flex h-full min-h-0 items-center justify-center px-6 py-10">
      <div className="flex flex-col items-center gap-4 text-center">
        <Microscope
          aria-hidden="true"
          className="h-8 w-8 text-[var(--muted-foreground)]"
          strokeWidth={1.5}
        />
        <p className="font-serif text-xl font-medium text-[var(--foreground)]">
          {t("Under development...")}
        </p>
      </div>
    </main>
  );
}
