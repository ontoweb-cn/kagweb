"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { Menu } from "lucide-react";
import { useTranslation } from "react-i18next";
import { AdminLink } from "@/components/auth/AdminLink";
import { LogoutButton } from "@/components/auth/LogoutButton";
import { ProfileLink } from "@/components/auth/ProfileLink";
import { useCapabilityAccess } from "@/components/access/CapabilityAccessContext";
import {
  TOP_NAV_GROUPS,
  isEntryActive,
  type NavEntry,
  type TopNavGroup,
} from "@/components/sidebar/nav-entries";
import { UserAvatar } from "@/components/UserAvatar";
import { fetchAuthStatus, type AuthStatus } from "@/lib/auth";
import { readNavLayout, resolveNavLayout } from "@/lib/sidebar-layout";

/** Within-group order follows the learner's saved arrangement: visible entries
 *  first (in their stored order), folded ones appended after — folding moves a
 *  feature one click away, it must not remove it. Reads localStorage, so this
 *  resolves on the client after mount; the declaration order is the SSR frame
 *  and the fallback. Shared by the banner buttons and the sidebar panel so the
 *  two always agree. */
export function useOrderedGroups(): TopNavGroup[] {
  const [groups, setGroups] = useState(TOP_NAV_GROUPS);
  useEffect(() => {
    const layout = readNavLayout();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setGroups(
      TOP_NAV_GROUPS.map((group) => {
        const resolved = resolveNavLayout(
          group.entries.map((entry) => entry.href),
          layout,
        );
        const byHref = new Map(group.entries.map((e) => [e.href, e]));
        const ordered = [...resolved.visible, ...resolved.collapsed].flatMap(
          (href) => {
            const entry = byHref.get(href);
            return entry ? [entry] : [];
          },
        );
        return ordered.length > 0 ? { ...group, entries: ordered } : group;
      }),
    );
  }, []);
  return groups;
}

/** Close on any outside pointer press or on Escape. Menu panels are plain
 *  absolute divs inside the banner's stacking context — no scrim, no portal. */
function useDismiss(
  open: boolean,
  onClose: () => void,
): RefObject<HTMLDivElement | null> {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        onClose();
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose]);

  return ref;
}

const DROPDOWN_PANEL =
  "absolute top-full z-50 mt-1 min-w-56 rounded-xl border border-[var(--border)] bg-[var(--popover)] p-1.5 shadow-lg";
const MENU_ROW =
  "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] transition-colors";

/** One secondary-nav row: icon + label, locked (capability missing) renders
 *  disabled with the lock tooltip. Shared by the drawer's vertical list and
 *  the sidebar column that replaces the old banner dropdowns. A locked row
 *  keeps its pointer events (hover must still show the tooltip) but the
 *  click is dead for mouse and keyboard alike — aria-disabled alone would
 *  only tell screen-reader users it's disabled while everyone else
 *  navigated into a gated route. */
export function NavMenuRow({
  entry,
  active,
  onNavigate,
}: {
  entry: NavEntry;
  active: boolean;
  onNavigate?: (event: React.MouseEvent) => void;
}) {
  const { t } = useTranslation();
  const { has } = useCapabilityAccess();
  const locked = entry.requires ? !has(entry.requires) : false;
  const tooltip = entry.tooltipKey ? (t(entry.tooltipKey) as string) : undefined;

  return (
    <Link
      role="menuitem"
      href={entry.href}
      onClick={(event) => {
        if (locked) {
          event.preventDefault();
          return;
        }
        onNavigate?.(event);
      }}
      aria-disabled={locked || undefined}
      title={locked ? (t("Locked — contact your administrator to get access.") as string) : tooltip}
      className={`${MENU_ROW} ${
        locked
          ? "cursor-not-allowed opacity-50"
          : active
            ? "bg-[var(--accent)] font-medium text-[var(--foreground)]"
            : "text-[var(--foreground)]/85 hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
      }`}
    >
      <entry.icon size={16} strokeWidth={active ? 1.9 : 1.5} />
      <span>{t(entry.label)}</span>
    </Link>
  );
}

/** Collapsed-rail twin of ``NavMenuRow``: icon-only, the accessible name and
 *  hover tooltip carry the label, same capability gating and dead-click
 *  semantics as the labeled row. */
export function NavMenuIconButton({
  entry,
  active,
}: {
  entry: NavEntry;
  active: boolean;
}) {
  const { t } = useTranslation();
  const { has } = useCapabilityAccess();
  const locked = entry.requires ? !has(entry.requires) : false;
  const label = t(entry.label) as string;
  const tooltip = entry.tooltipKey
    ? (t(entry.tooltipKey) as string)
    : undefined;

  return (
    <Link
      href={entry.href}
      aria-label={label}
      aria-disabled={locked || undefined}
      onClick={(event) => {
        if (locked) event.preventDefault();
      }}
      title={
        locked
          ? (t("Locked — contact your administrator to get access.") as string)
          : (tooltip ?? label)
      }
      className={`flex h-9 w-9 items-center justify-center rounded-xl transition-all duration-150 ${
        locked
          ? "cursor-not-allowed opacity-50"
          : active
            ? "bg-[var(--accent)] text-[var(--foreground)] shadow-sm"
            : "text-[var(--foreground)]/85 hover:bg-[var(--background)]/60 hover:text-[var(--foreground)]"
      }`}
    >
      <entry.icon size={18} strokeWidth={active ? 2 : 1.6} />
    </Link>
  );
}

/** One desktop top-menu group: a labeled link with the group pictogram.
 *  The revealed sidebar column is derived from the route (see
 *  ``SidebarShell``), so this is a pure navigation affordance: it lands on
 *  the group's first usable entry (the learner's arranged order, skipping
 *  capability-locked ones) and lights up exactly while the route is inside
 *  this group. A real link, not a button — that keeps middle-click /
 *  cmd-click "open in new tab" and Next.js prefetch working, and
 *  ``aria-current`` (instead of the old aria-expanded) is the honest
 *  signal, since nothing expands here anymore. */
function NavGroupButton({ group }: { group: TopNavGroup }) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const { has } = useCapabilityAccess();
  const routeActive = group.entries.some((entry) =>
    isEntryActive(pathname, entry),
  );
  // First entry the learner can actually open; if every entry is locked,
  // fall back to the head entry — the gated page explains itself.
  const target =
    group.entries.find((entry) => !entry.requires || has(entry.requires)) ??
    group.entries[0];
  const targetIsCurrent = target ? isEntryActive(pathname, target) : false;

  return (
    <Link
      href={target?.href ?? "#"}
      aria-current={routeActive || undefined}
      onClick={(event) => {
        // Clicking the group you are already on is a no-op, not a re-navigation.
        if (targetIsCurrent) event.preventDefault();
      }}
      className={`flex items-center gap-1.5 rounded-lg px-3 py-2 text-[13.5px] transition-colors ${
        routeActive
          ? "bg-[var(--accent)] font-medium text-[var(--foreground)]"
          : "text-[var(--foreground)]/85 hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
      }`}
    >
      <group.icon
        size={15}
        strokeWidth={routeActive ? 1.9 : 1.5}
        aria-hidden
      />
      {t(group.label)}
    </Link>
  );
}

/** Avatar button opening the account dropdown (Profile / Admin / Sign out).
 *  The three rows render themselves with their own auth gating — they all
 *  return null when auth is off, so single-user mode shows no account area. */
function AccountMenu() {
  const { t } = useTranslation();
  const pathname = usePathname();
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const ref = useDismiss(open, close);

  useEffect(() => {
    fetchAuthStatus().then((next) => {
      // Only surface the account area when auth is on AND the user is signed in.
      if (next?.enabled && next?.authenticated) setStatus(next);
    });
  }, []);

  if (!status?.username) return null;
  const active = pathname.startsWith("/profile");

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("My profile")}
        className={`flex items-center justify-center rounded-full p-0.5 transition-colors ${
          active || open
            ? "ring-2 ring-[var(--primary)]/60"
            : "hover:ring-2 hover:ring-[var(--muted-foreground)]/30"
        }`}
      >
        <UserAvatar
          username={status.username}
          userId={status.user_id}
          avatar={status.avatar}
          role={status.role}
          size={26}
        />
      </button>
      {open ? (
        <div role="menu" className={`${DROPDOWN_PANEL} right-0 min-w-48`}>
          <ProfileLink />
          <AdminLink />
          <LogoutButton />
        </div>
      ) : null}
    </div>
  );
}

/** Vertical rendering of the three groups for the mobile drawer — the drawer
 *  is the only navigation surface below ``md``, so it carries the same groups
 *  as the desktop banner. */
export function TopNavList({
  onNavigate,
}: {
  onNavigate?: (event: React.MouseEvent) => void;
}) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const groups = useOrderedGroups();

  return (
    <nav aria-label={t("Learning")} className="md:hidden">
      {groups.map((group) => (
        <div key={group.id}>
          <div className="flex items-center gap-1.5 px-3 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            <group.icon size={12} strokeWidth={1.8} aria-hidden />
            <span>{t(group.label)}</span>
          </div>
          {group.entries.map((entry) => (
            <NavMenuRow
              key={entry.href}
              entry={entry}
              active={isEntryActive(pathname, entry)}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      ))}
    </nav>
  );
}

/** Top navigation banner shared by the (workspace) and (utility) route
 *  groups: brand on the left, the three menu groups in the center, the
 *  account area on the right. Selecting a group reveals its entries in the
 *  sidebar rather than in a dropdown. Below ``md`` the groups live in the
 *  sidebar drawer instead (``TopNavList``) and the hamburger takes their
 *  place. */
export default function TopBanner({
  drawerOpen,
  onMenuClick,
}: {
  drawerOpen: boolean;
  onMenuClick: () => void;
}) {
  const { t } = useTranslation();
  const groups = useOrderedGroups();

  return (
    <header className="z-30 flex h-[var(--top-banner-h)] shrink-0 items-center justify-between gap-2 border-b border-[var(--border)] bg-[var(--background)] px-3">
      <div className="flex min-w-0 items-center gap-1">
        <button
          type="button"
          onClick={onMenuClick}
          aria-label={t("Open navigation")}
          aria-expanded={drawerOpen}
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)] md:hidden"
        >
          <Menu size={18} strokeWidth={1.7} />
        </button>
        <Link href="/" className="flex items-center gap-1.5">
          <Image
            src="/logo.png?v=naval-20260904"
            alt="学研助手"
            width={26}
            height={26}
            unoptimized
            className="h-[26px] w-[26px] rounded-md"
          />
          <span className="hidden whitespace-nowrap font-serif text-[18px] font-semibold leading-none text-[var(--foreground)] min-[420px]:inline">
            学研助手
          </span>
        </Link>
      </div>

      <nav
        aria-label={t("Learning")}
        className="flex items-center gap-1 max-md:hidden"
      >
        {groups.map((group) => (
          <NavGroupButton key={group.id} group={group} />
        ))}
      </nav>

      <div className="flex items-center gap-1">
        <AccountMenu />
      </div>
    </header>
  );
}
