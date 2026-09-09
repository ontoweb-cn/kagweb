/**
 * One-line previews shared by the inline trace, the session DAG and the DSL
 * export. The cap and the flattening rule used to live in three places with
 * two different behaviours, so the same text could preview differently
 * depending on which surface produced it.
 */
export const PREVIEW_LIMIT = 140;

/** Flatten to a single line and clip, appending an ellipsis when cut. */
export function clipPreview(value: string, limit: number = PREVIEW_LIMIT): string {
  const flat = value.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}
