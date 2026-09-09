/**
 * Turn-level epistemic badges: one judge call per multi-round turn classifies
 * how the exploration *moved* — new understanding, a ruled-out path, a
 * decision, a pivot, or an open question. Display-only: colors and labels
 * never enter context.
 *
 * The vocabulary is mirrored by the backend judge
 * (`kagweb/services/session/turns/title_service.py::_INSIGHT_TYPES`); this
 * module is the single frontend source for it — the union derives from the
 * table so a new type needs one edit here.
 */
export interface InsightTypeMeta {
  /** CSS color for the dot / badge border. */
  color: string;
  /** i18n key — resolved with t() at render time. */
  labelKey: string;
}

/** The judge-written badge. Declared here — the single frontend source — so
 *  the message store, the DAG IR and the canvas all carry one shape. */
export interface TurnInsight {
  takeaway: string;
  type: InsightType;
}

/** Ink for a node without a badge: a user message reads lighter than the
 *  assistant/execution spine. Shared by the DAG canvas and the SVG export so
 *  the two can never disagree about what "no badge" looks like. */
export const NEUTRAL_USER_INK = "#64748b";
export const NEUTRAL_NODE_INK = "#0f172a";

export function nodeInkColor(
  insightType: string | undefined,
  kind: "user" | "assistant" | "other",
): string {
  if (insightType) return insightMetaOf(insightType).color;
  return kind === "user" ? NEUTRAL_USER_INK : NEUTRAL_NODE_INK;
}

export const INSIGHT_TYPES = {
  insight: { color: "#0284c7", labelKey: "Insight" },
  ruleout: { color: "#ef4444", labelKey: "Ruled out" },
  decision: { color: "#6B5CE7", labelKey: "Decision" },
  pivot: { color: "#e8890c", labelKey: "Pivot" },
  open: { color: "#d97706", labelKey: "Open question" },
} satisfies Record<string, InsightTypeMeta>;

export type InsightType = keyof typeof INSIGHT_TYPES;

/** Whether an arbitrary string is one of the judge's types. */
export function isInsightType(value: unknown): value is InsightType {
  return typeof value === "string" && Object.hasOwn(INSIGHT_TYPES, value);
}

/**
 * The badge meta for a type, falling back to `insight` for anything unknown.
 *
 * `Object.hasOwn` rather than a bare index: `INSIGHT_TYPES["constructor"]`
 * would otherwise resolve an `Object.prototype` member (truthy, so `??` never
 * fires) and render a badge with no colour and no label.
 */
export function insightMetaOf(type: string | undefined): InsightTypeMeta {
  return isInsightType(type) ? INSIGHT_TYPES[type] : INSIGHT_TYPES.insight;
}
