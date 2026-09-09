/**
 * Semantic zoom tiers for the session DAG: zoom level is a *reading intent*,
 * not a geometry scale —
 *
 *   turn   (zoom ≥ 0.8)    full message cards
 *   plaque (0.35 – 0.8)    badge + one-line takeaway
 *   glyph  (< 0.35)        one colored dot per node
 *
 * Dual thresholds per boundary (hysteresis) prevent flapping when the zoom
 * sits exactly on a cutoff.
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
