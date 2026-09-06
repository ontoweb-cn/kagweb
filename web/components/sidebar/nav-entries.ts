import {
  BookOpen,
  BookText,
  Brain,
  GraduationCap,
  House,
  LayoutGrid,
  Library,
  Microscope,
  PenLine,
  Puzzle,
  Route,
  Settings,
  type LucideIcon,
} from "lucide-react";

import type { Capability } from "@/lib/capability-routes";

/** Which top-banner menu group an entry belongs to (see ``TOP_NAV_GROUPS``). */
export type TopNavGroupId = "learning" | "research" | "skills";

export interface NavEntry {
  href: string;
  label: string;
  icon: LucideIcon;
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
 * (``lib/sidebar-layout.ts``). Adding an entry here places it for everyone,
 * including people who have already arranged their sidebar: it arrives next to
 * the neighbour it follows below rather than at the bottom of their list.
 *
 * The ``group`` tag routes each entry into a top-banner menu group; the
 * rendered order inside a group still follows the learner's saved arrangement.
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
  // Partners and My Agents are temporarily hidden from navigation. Their
  // routes and services remain available for later restoration.
  {
    href: "/co-writer",
    label: "Writing Agent",
    icon: PenLine,
    tooltipKey: "Co-Writer tooltip",
    requires: "llm",
    group: "learning",
  },
  {
    href: "/research-agent",
    label: "Research Agent",
    icon: Microscope,
    tooltipKey: "Research Agent tooltip",
    group: "research",
  },
  {
    href: "/books",
    label: "Book",
    icon: Library,
    tooltipKey: "Book tooltip",
    requires: "llm",
    group: "learning",
  },
  // Courses nav entry temporarily hidden pending further product work.
  // The route and its data are untouched — only this entry point is gone.
  {
    href: "/mastery",
    label: "Mastery Path",
    icon: Route,
    tooltipKey: "Learn through a living mastery map",
    requires: "llm",
    group: "learning",
  },
  {
    href: "/reading",
    label: "Immersive Reading",
    icon: BookText,
    tooltipKey: "Immersive Reading tooltip",
    requires: "llm",
    group: "learning",
  },
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
  {
    // Memory is its own top-level console (pulled out of the Learning Space):
    // a place to inspect and curate the tutor's long-term memory, not a daily
    // workspace. Never gated — memory has no per-user model requirement.
    href: "/memory",
    label: "Memory",
    icon: Brain,
    tooltipKey: "Memory tooltip",
  },
  {
    // Knowledge Center sits just above Settings: it's a console for managing
    // KBs and retrieval engines, not a daily workspace. Never gated — embedding
    // / search are shared admin infrastructure, no per-user model grant needed.
    href: "/knowledge-bases",
    label: "Knowledge Center",
    icon: BookOpen,
    tooltipKey: "Knowledge tooltip",
  },
  { href: "/settings", label: "Settings", icon: Settings },
];

/** Skill-center consoles. Its own array rather than a ``group`` tag on
 *  PRIMARY_NAV: these are not daily workspaces, so they must not leak into the
 *  learner's drag-order list or the sidebar history column. */
export const SKILLS_NAV: NavEntry[] = [
  {
    href: "/space/skills",
    label: "EduHub",
    icon: Puzzle,
  },
];

export interface TopNavGroup {
  id: TopNavGroupId;
  /** i18n key printed on the group button. */
  label: string;
  /** Pictogram shown on the banner button and as the side panel heading. */
  icon: LucideIcon;
  entries: NavEntry[];
}

/** The top-banner menu groups (D1 decision, see
 *  docs/plans/2026-09-04-top-banner-ui-design.md §9). Within-group order still
 *  follows the learner's saved arrangement, resolved at render time in
 *  ``TopBanner``. Selecting a group does not open a dropdown — it reveals the
 *  group's entries as a column in the sidebar. */
export const TOP_NAV_GROUPS: TopNavGroup[] = [
  {
    id: "learning",
    label: "Learning",
    icon: GraduationCap,
    entries: PRIMARY_NAV.filter((entry) => entry.group === "learning"),
  },
  {
    id: "research",
    label: "Research",
    icon: Microscope,
    entries: PRIMARY_NAV.filter((entry) => entry.group === "research"),
  },
  {
    id: "skills",
    label: "Skill Center",
    icon: Puzzle,
    entries: SKILLS_NAV,
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
    return (
      (pathname === "/space" || pathname.startsWith("/space/")) &&
      !pathname.startsWith("/mastery")
    );
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** Entry-level active check shared by the banner buttons and the sidebar
 *  panel. ``/space/skills`` is its own top-banner group (EduHub) and also
 *  prefix-matches Learning Space's ``/space`` — let it light up only the
 *  skills group. */
export function isEntryActive(pathname: string, entry: NavEntry) {
  if (entry.href === "/space" && pathname.startsWith("/space/skills")) {
    return false;
  }
  return isNavActive(pathname, entry.href);
}
