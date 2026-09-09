/**
 * Semantic zoom tiers for the session DAG: zoom level is a *reading intent*,
 * not a geometry scale —
 *
 *   turn    full message cards      leave below 0.8
 *   plaque  badge + one-line takeaway  leave below 0.32, return at 0.9
 *   glyph   one colored dot per node   return at 0.4, then 0.9 for turn
 *
 * Dual thresholds per boundary (hysteresis) prevent flapping when the zoom
 * sits exactly on a cutoff. The bands are relative to the canvas's
 * `MIN_READABLE_ZOOM` (0.3), so all three tiers are reachable.
 */
export type DagZoomTier = "turn" | "plaque" | "glyph";

export function nextZoomTier(prev: DagZoomTier, zoom: number): DagZoomTier {
  if (prev === "turn") {
    return zoom <= 0.32 ? "glyph" : zoom <= 0.8 ? "plaque" : "turn";
  }
  if (prev === "plaque") {
    if (zoom >= 0.9) return "turn";
    return zoom <= 0.32 ? "glyph" : "plaque";
  }
  // glyph
  if (zoom >= 0.9) return "turn";
  return zoom >= 0.4 ? "plaque" : "glyph";
}
