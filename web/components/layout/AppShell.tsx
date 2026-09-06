"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { usePathname } from "next/navigation";
import TopBanner from "@/components/layout/TopBanner";
import { useDevice } from "@/hooks/useDevice";
import type { ReactNode } from "react";

/* Lets the sidebar dismiss the drawer after a nav click without every layout
   threading a callback down through WorkspaceSidebar/UtilitySidebar. Null on
   desktop and anywhere outside AppShell, so `drawer?.close()` is a no-op there
   rather than a crash. */
const SidebarDrawerContext = createContext<{ close: () => void } | null>(null);

export function useSidebarDrawer() {
  return useContext(SidebarDrawerContext);
}

interface AppShellProps {
  /** The route group's sidebar (workspace or utility). */
  sidebar: ReactNode;
  children: ReactNode;
}

/**
 * The app frame, shared by the (workspace) and (utility) route groups.
 *
 * Two stacked rows:
 *
 *   banner  the TopBanner strip — brand, the three menu groups, account — a
 *           fixed 56px chrome row above everything that scrolls (z-30; the
 *           fixed right-hand panels start below it, at top-14).
 *   body    sidebar and content as siblings in a flex row:
 *
 * Selecting a banner group does not open a dropdown: the group's entries
 * appear as a column at the top of the sidebar. Which group is revealed is
 * derived from the current route (no stored selection), so first load and
 * cross-group navigation both render a consistent banner + sidebar + content.
 *
 *     >= 768px  unchanged from what this app has always rendered.
 *     <  768px  the sidebar leaves the flow entirely and becomes an overlay
 *               drawer behind a scrim; the menu groups live in the drawer
 *               (TopNavList) and the hamburger lives in the banner.
 *
 * The split is expressed in CSS (`max-md:` / `md:`), not in `useDevice()`, so
 * the very first server-rendered paint is already correct on a phone. JS only
 * owns the part that is stateful anyway: whether the drawer is open.
 */
export default function AppShell({ sidebar, children }: AppShellProps) {
  const pathname = usePathname();
  const { isMobile } = useDevice();
  const [drawerOpen, setDrawerOpen] = useState(false);

  const close = useCallback(() => setDrawerOpen(false), []);

  // Any route change hands the screen back to the content. Compared during
  // render rather than in an effect (same pattern as SessionViewerPanel's
  // session reset) so the drawer never paints open over the new route.
  const [trackedPathname, setTrackedPathname] = useState(pathname);
  if (trackedPathname !== pathname) {
    setTrackedPathname(pathname);
    setDrawerOpen(false);
  }

  useEffect(() => {
    if (!drawerOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen]);

  return (
    <SidebarDrawerContext.Provider value={{ close }}>
      {/* dvh, not vh: iOS Safari's 100vh includes the retracted address bar, so
          a vh-sized shell pushes the composer under it. */}
      <div className="flex h-dvh flex-col overflow-hidden">
        <TopBanner
          drawerOpen={drawerOpen}
          onMenuClick={() => setDrawerOpen(true)}
        />

        <div className="flex min-h-0 flex-1 overflow-hidden">
          {drawerOpen ? (
            <div
              onClick={close}
              aria-hidden
              className="fixed inset-0 z-40 bg-black/40 md:hidden"
            />
          ) : null}

          {/* `inert` (not just translate-x) while closed: a drawer parked
              off-screen still holds ~20 focusable nav items, and without this
              Tab walks the user into a sidebar they cannot see. This is the
              half `max-md:` cannot express, hence useDevice(). */}
          <div
            inert={isMobile && !drawerOpen ? true : undefined}
            className={`max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-50 max-md:shadow-xl max-md:transition-transform max-md:duration-200 max-md:ease-out ${
              drawerOpen ? "max-md:translate-x-0" : "max-md:-translate-x-full"
            }`}
          >
            {sidebar}
          </div>

          <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--background)]">
            <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
          </main>
        </div>
      </div>
    </SidebarDrawerContext.Provider>
  );
}
