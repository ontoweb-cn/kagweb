/**
 * Turn-level epistemic badges: one judge call per multi-round turn classifies
 * how the exploration *moved* — new understanding, a ruled-out path, a
 * decision, a pivot, or an open question. Display-only: colors and labels
 * never enter context.
 */
export type InsightType = "insight" | "ruleout" | "decision" | "pivot" | "open";

export interface InsightTypeMeta {
  /** CSS color for the dot / badge border. */
  color: string;
  /** i18n key — resolved with t() at render time. */
  labelKey: string;
}

export const INSIGHT_TYPES: Record<InsightType, InsightTypeMeta> = {
  insight: { color: "#0284c7", labelKey: "Insight" },
  ruleout: { color: "#ef4444", labelKey: "Ruled out" },
  decision: { color: "#6B5CE7", labelKey: "Decision" },
  pivot: { color: "#e8890c", labelKey: "Pivot" },
  open: { color: "#d97706", labelKey: "Open question" },
};

export function insightMetaOf(type: string | undefined): InsightTypeMeta {
  return INSIGHT_TYPES[(type ?? "insight") as InsightType] ?? INSIGHT_TYPES.insight;
}
