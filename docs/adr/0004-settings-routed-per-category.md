# ADR-0004: Settings Routed Per Category

## Status

Accepted (2026-09-15)

## Context

ADR-0002 made fragment-addressed sections under `/settings` the canonical
settings URLs. At the time, settings was one scrolling document: eight
top-level sections and eleven nested ones stacked on a single route, with the
"current page" defined only by scroll position. That design accumulated
mechanisms that existed purely to hold the document together — a
ResizeObserver that re-pinned the target anchor whenever late-loading content
shifted the layout, and cancellation of that pinning on the first user scroll
— and it gave the browser no history entries between sections, so Back could
not retrace a settings walk. One navigator metadata field had already drifted
(a hand-written href pointing at an anchor that rendered under a different
id), which is what hand-written URLs beside keys eventually do.

## Decision

- Each settings category is a real route: `/settings/appearance`,
  `/settings/network`, `/settings/agent-loop`, `/settings/models`,
  `/settings/knowledge`, `/settings/chat`, `/settings/about`. The index
  `/settings` is the overview page.
- Leaves inside a category remain in-document anchors on that category's
  route (`/settings/models#llm`). Categories are the unit users think in
  (Models owns one catalog and one editor); leaves inside one share data and
  loading, and a route per leaf would buy URLs at the cost of a remount per
  service switch.
- Section URLs are derived from a single key→route map
  (`web/features/settings/navigation/settings-nav.ts`). Hand-written hrefs
  beside keys are not stored anywhere.
- Visibility enforcement moves with the route: every category page gates
  itself (`SettingsDomainGate`), because the single document used to hide
  sections by filtering what it stacked, and that filter no longer exists.
- Old `/settings#fragment` links are translated once, at the settings layout
  (`SettingsLegacyAnchorRedirect`), not with permanent redirect aliases —
  consistent with ADR-0002's refusal to keep compatibility shims.

This amends ADR-0002 for the settings surface: page routes use product nouns
per category, and the fragment is the address of a section *within* its
category route, not of a section of one global document.

## Consequences

### Positive

- A deep link lands on its page by navigation, not by scroll alignment, so
  the re-pinning machinery is gone from the top level.
- Back and Forward cross category boundaries.
- Route-level performance budgets name the page that grew
  (`web/scripts/route_budgets.mjs`).
- A category that crashes takes down its own route; the settings segment has
  an error boundary (`app/(utility)/settings/error.tsx`) instead of losing
  the whole surface.

### Negative

- Bookmarks to `/settings#llm`-style URLs work only through the legacy
  translation; anything that bypasses the settings layout (none is known)
  would land on the index.
- Within a category, moving between leaf anchors still does not create
  history entries (the in-document scroll tracker and explicit clicks share
  one history entry), so Back crosses categories but not leaf anchors.

## Alternatives Considered

- Keep the single document and repair its defects incrementally: rejected —
  it kept URL semantics, scroll alignment, and visibility in three different
  mechanisms that had already drifted relative to each other.
- One route per leaf (`/settings/models/llm`, …): rejected — nineteen routes
  whose pages share one catalog and one editor; a remount per service switch
  for no URL benefit the navigator does not already provide.
