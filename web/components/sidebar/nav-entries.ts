import { GraduationCap, House, LayoutGrid, Settings } from "lucide-react";

import type { Capability } from "@/lib/capability-routes";

/** Which top-banner menu group an entry belongs to (see ``TOP_NAV_GROUPS``). */
export type TopNavGroupId = "learning";

export interface NavEntry {
  href: string;
  label: string;
  icon: import("lucide-react").LucideIcon;
  tooltipKey?: string;
  /** Model capability this feature needs; locked when the user lacks it. */
  requires?: Capability;
  /** Top-banner group membership; every PRIMARY_NAV entry carries one. */
  group?: TopNavGroupId;
}

/**
 * The workspace features, in the order they ship in.
 *
 * This is the *default* arrangement, not the rendered one — a learner can
 * reorder these and fold the ones they don't use into "More"
 * (``lib/sidebar-layout.ts``).
 */
export const PRIMARY_NAV: NavEntry[] = [
  {
    href: "/chat",
    label: "Learning Agent",
    icon: House,
    tooltipKey: "Home tooltip",
    requires: "llm",
    group: "learning",
  },
  // Partners are temporarily hidden from navigation. Their routes and
  // services remain available for later restoration.
  {
    href: "/space",
    label: "Learning Space",
    icon: LayoutGrid,
    tooltipKey: "Space tooltip",
    group: "learning",
  },
];

/** Consoles that sit under the chat history. Not arrangeable: Settings has to
 *  stay findable, and a console nobody folds away is one less thing to explain. */
export const SECONDARY_NAV: NavEntry[] = [
  { href: "/settings", label: "Settings", icon: Settings },
];

/** Skill-center consoles. Kept as an extension point for future asset
 *  surfaces; currently empty. */
export const SKILLS_NAV: NavEntry[] = [];

export interface TopNavGroup {
  id: TopNavGroupId;
  /** i18n key printed on the group button. */
  label: string;
  /** Pictogram shown on the banner button and as the side panel heading. */
  icon: import("lucide-react").LucideIcon;
  entries: NavEntry[];
}

/** The top-banner menu groups. Selecting a group reveals the group's entries
 *  as a column in the sidebar. */
export const TOP_NAV_GROUPS: TopNavGroup[] = [
  {
    id: "learning",
    label: "Learning",
    icon: GraduationCap,
    entries: PRIMARY_NAV.filter((entry) => entry.group === "learning"),
  },
];

export const PRIMARY_NAV_HREFS = PRIMARY_NAV.map((entry) => entry.href);

export const NAV_BY_HREF = new Map(
  [...PRIMARY_NAV, ...SECONDARY_NAV, ...SKILLS_NAV].map((entry) => [
    entry.href,
    entry,
  ]),
);

export function isNavActive(pathname: string, href: string) {
  if (href === "/space") {
    return pathname === "/space" || pathname.startsWith("/space/");
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** Entry-level active check shared by the banner buttons and the sidebar
 *  panel. */
export function isEntryActive(pathname: string, entry: NavEntry) {
  return isNavActive(pathname, entry.href);
}
