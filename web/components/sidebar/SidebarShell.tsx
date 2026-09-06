"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAppShell } from "@/context/AppShellContext";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useTranslation } from "react-i18next";
import SessionList from "@/components/SessionList";
import { useSidebarDrawer } from "@/components/layout/AppShell";
import {
  NavMenuIconButton,
  NavMenuRow,
  TopNavList,
  useOrderedGroups,
} from "@/components/layout/TopBanner";
import { useDevice } from "@/hooks/useDevice";
import type { SessionSummary } from "@/lib/session-api";
import {
  SECONDARY_NAV,
  isEntryActive,
  isNavActive,
} from "@/components/sidebar/nav-entries";

interface SidebarShellProps {
  sessions?: SessionSummary[];
  activeSessionId?: string | null;
  loadingSessions?: boolean;
  showSessions?: boolean;
  onSelectSession?: (sessionId: string) => void | Promise<void>;
  onRenameSession?: (sessionId: string, title: string) => void | Promise<void>;
  onDeleteSession?: (sessionId: string) => void | Promise<void>;
}

export function SidebarShell({
  sessions = [],
  activeSessionId = null,
  loadingSessions = false,
  showSessions = false,
  onSelectSession,
  onRenameSession,
  onDeleteSession,
}: SidebarShellProps) {
  const pathname = usePathname();
  const { t } = useTranslation();
  const { sidebarCollapsed, setSidebarCollapsed: setCollapsed } = useAppShell();
  const { isMobile } = useDevice();
  const drawer = useSidebarDrawer();

  // The menu group owning the current route — derived, not stored: the panel
  // and the banner highlight can never drift apart, and navigating across
  // route groups (whose layouts each mount their own shell) cannot lose the
  // selection. Ordering comes from the same hook the banner buttons read.
  const orderedGroups = useOrderedGroups();
  const activeGroup =
    orderedGroups.find((group) =>
      group.entries.some((entry) => isEntryActive(pathname, entry)),
    ) ?? null;

  // Inside the mobile drawer the icon-only rail is pointless — the panel is
  // already hidden when you don't want it, so it always opens fully expanded
  // regardless of the persisted desktop preference.
  const collapsed = sidebarCollapsed && !isMobile;

  /** Dismiss the drawer on nav clicks that actually navigate in-place. */
  const closeDrawerOnNav = (event: React.MouseEvent) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1)
      return;
    drawer?.close();
  };

  // Everything the learner has, minus the archived and minus the tutor threads
  // that render nested under the conversation that spawned them.
  //
  // No recents window any more. The region used to cut the home conversations
  // at eight, which was survivable only because the "Chat" heading above them
  // printed the real count; with the conversations listed directly there is
  // nothing on screen to say that older ones exist, and a sidebar that quietly
  // drops your conversation from yesterday is worse than one you scroll.
  const visibleSessions = sessions.filter(
    (session) =>
      !session.preferences?.archived && !session.preferences?.parent_session_id,
  );

  /* ---- Collapsed state ---- */
  if (collapsed) {
    return (
      <aside className="group/sb relative flex h-full w-[60px] shrink-0 flex-col items-center bg-[var(--secondary)] py-3 transition-all duration-200">
        {/* Rail is a desktop-only affordance now: with the primary nav in the
            top banner, the collapsed column carries just the expand toggle and
            the consoles footer. */}
        <button
          onClick={() => setCollapsed(false)}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-[var(--muted-foreground)] transition-colors hover:bg-[var(--background)]/60 hover:text-[var(--foreground)]"
          aria-label={t("Expand sidebar")}
        >
          <PanelLeftOpen size={16} />
        </button>

        {/* The banner's selected group as an icon rail — the collapsed column
            has no room for labels, tooltips carry them. */}
        {activeGroup ? (
          <div className="mt-2 flex w-full flex-col items-center gap-1 px-1.5">
            {activeGroup.entries.map((entry) => (
              <NavMenuIconButton
                key={entry.href}
                entry={entry}
                active={isEntryActive(pathname, entry)}
              />
            ))}
            <div className="my-1 h-px w-7 bg-[var(--border)]/40" />
          </div>
        ) : null}

        <div className="flex-1" />

        {/* Consoles footer */}
        <div className="flex w-full flex-col items-center gap-1 px-1.5">
          <div className="my-1 h-px w-7 bg-[var(--border)]/40" />
          {SECONDARY_NAV.map((item) => {
            const active = isNavActive(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                title={t(item.label) as string}
                className={`relative flex h-9 w-9 items-center justify-center rounded-xl transition-all duration-150 ${
                  active
                    ? "bg-[var(--accent)] text-[var(--foreground)] shadow-sm"
                    : "text-[var(--foreground)]/85 hover:bg-[var(--background)]/60 hover:text-[var(--foreground)]"
                }`}
              >
                <item.icon size={18} strokeWidth={active ? 2 : 1.6} />
              </Link>
            );
          })}
        </div>
      </aside>
    );
  }

  /* ---- Expanded state ---- */
  return (
    <aside className="relative flex h-full w-[220px] shrink-0 flex-col bg-[var(--secondary)] transition-all duration-200">
      {/* Collapse toggle floats in the top-right corner: the brand lives in
          the top banner now, so the column starts straight with its content
          instead of a reserved header band. Desktop-only — in the drawer the
          scrim and the banner hamburger already own "make this go away". */}
      <button
        onClick={() => setCollapsed(true)}
        className="absolute right-2.5 top-2 z-10 rounded-md p-1 text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)] max-md:hidden"
        aria-label={t("Collapse sidebar")}
      >
        <PanelLeftClose size={15} />
      </button>

      {/* The banner's selected group, revealed as a column (desktop only):
          below md the drawer's TopNavList already shows every group, so the
          column would only duplicate it. */}
      {activeGroup ? (
        <section
          aria-label={t(activeGroup.label) as string}
          className="max-md:hidden px-2 pb-2 pt-1"
        >
          <div className="flex items-center gap-1.5 px-3 pb-1 pt-1 text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            <activeGroup.icon size={12} strokeWidth={1.8} aria-hidden />
            <span>{t(activeGroup.label)}</span>
          </div>
          {activeGroup.entries.map((entry) => (
            <NavMenuRow
              key={entry.href}
              entry={entry}
              active={isEntryActive(pathname, entry)}
              onNavigate={closeDrawerOnNav}
            />
          ))}
          <div className="mx-1 mt-2 h-px bg-[var(--border)]/40" />
        </section>
      ) : null}

      {/* The three menu groups, drawer-only: below md the banner center is
          hidden and this drawer is the only navigation surface. */}
      <div className="md:hidden">
        <TopNavList onNavigate={closeDrawerOnNav} />
        <div className="mx-3 my-1 h-px bg-[var(--border)]/40" />
      </div>

      {/* Chat history — its own region below the nav, takes remaining height */}
      {showSessions && onSelectSession && onRenameSession && onDeleteSession ? (
        <section className="mt-3 flex min-h-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 pt-0.5">
            {loadingSessions ? (
              <SessionList
                sessions={[]}
                activeSessionId={activeSessionId}
                loading
                onSelect={onSelectSession}
                onRename={onRenameSession}
                onDelete={onDeleteSession}
                compact
              />
            ) : (
              <SessionList
                sessions={visibleSessions}
                activeSessionId={activeSessionId}
                onSelect={(sessionId) => {
                  drawer?.close();
                  return onSelectSession(sessionId);
                }}
                onRename={onRenameSession}
                onDelete={onDeleteSession}
                compact
              />
            )}
          </div>
        </section>
      ) : null}

      {/* With no session list at all, fill the gap above the footer. */}
      {(!showSessions ||
        !onSelectSession ||
        !onRenameSession ||
        !onDeleteSession) && <div className="flex-1" />}

      {/* Secondary nav + footer */}
      <div className="border-t border-[var(--border)]/40 px-2 py-2">
        {SECONDARY_NAV.map((item) => {
          const active = isNavActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={closeDrawerOnNav}
              className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] transition-colors ${
                active
                  ? "bg-[var(--accent)] font-medium text-[var(--foreground)]"
                  : "text-[var(--foreground)]/85 hover:bg-[var(--background)]/60 hover:text-[var(--foreground)]"
              }`}
            >
              <item.icon size={16} strokeWidth={active ? 1.9 : 1.5} />
              <span>{t(item.label)}</span>
            </Link>
          );
        })}
      </div>
    </aside>
  );
}
