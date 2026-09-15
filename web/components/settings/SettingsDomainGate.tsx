"use client";

import { Lock, SlidersHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  settingsDomainBlockReason,
  type SettingsDomainBlock,
} from "@/features/settings/navigation/settings-nav";
import { useSettingsAccess } from "@/features/settings/navigation/SettingsAccessProvider";

const NOTICE: Record<
  SettingsDomainBlock,
  { icon: typeof Lock; title: string; body: string }
> = {
  "admin-only": {
    icon: Lock,
    title: "Settings unavailable",
    body: "This settings page is managed by an administrator for this installation.",
  },
  "llm-not-applicable": {
    icon: SlidersHorizontal,
    title: "Hidden for this agent backend",
    // Deliberately narrow: the backend reports that model-provider settings do
    // not drive this backend's conversations. It does not say every model on
    // this page is unnecessary, so the copy does not either.
    body: "The agent backend you selected supplies its own models for conversations, so KAGWeb hides the model-provider settings here. Switch backends on the Agent Backend page to configure them.",
  },
};

/**
 * Route-level gate for a settings category.
 *
 * The single settings document used to enforce visibility by filtering the
 * sections it stacked, so a hidden page could never render. Now that each
 * category owns a route, that filter is gone and every category has to ask for
 * itself — without this, opening `/settings/agent-loop` directly would render
 * an admin page for anyone.
 *
 * The backend still rejects the requests, so this is a UI boundary and not the
 * security boundary; it exists so the page is honest about what the account
 * may configure.
 */
export function SettingsDomainGate({
  domain,
  children,
}: {
  domain: string;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const access = useSettingsAccess();

  // Access resolves from an auth probe. Rendering before it lands would flash
  // admin content at a non-admin, and would also mount sections that then
  // fetch — so the gate waits rather than guessing.
  if (!access.resolved) return <div className="h-48" aria-busy="true" />;

  const block = settingsDomainBlockReason(domain, access);
  if (!block) return <>{children}</>;

  const notice = NOTICE[block];
  const Icon = notice.icon;

  return (
    <div className="flex items-center justify-center py-16">
      <div className="flex max-w-md flex-col items-center gap-4 rounded-2xl border border-[var(--border)] bg-[var(--secondary)]/40 px-8 py-10 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--background)] text-[var(--muted-foreground)]">
          <Icon size={20} strokeWidth={1.8} />
        </div>
        <h2 className="text-base font-semibold text-[var(--foreground)]">
          {t(notice.title)}
        </h2>
        <p className="text-sm leading-relaxed text-[var(--muted-foreground)]">
          {t(notice.body)}
        </p>
      </div>
    </div>
  );
}
