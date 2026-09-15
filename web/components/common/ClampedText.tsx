"use client";

import { useCallback, useId, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";

/**
 * Renders text that may be a whole command transcript collapsed to a few
 * lines, with a toggle to reveal the rest.
 *
 * Backends do not always send a tidy label: an ACP agent's approval `title`
 * is routinely a multi-line description of what it wants to run. Left alone
 * that text takes over the card it sits in, so anything longer than the
 * folded height starts folded. The fold is decided from the string itself
 * rather than a measured overflow, which keeps the server-rendered frame
 * identical to the hydrated one.
 */

/** East-Asian Wide / Fullwidth blocks (CJK, Hangul, kana, fullwidth forms). */
const WIDE_RANGES: Array<[number, number]> = [
  [0x1100, 0x115f],
  [0x2e80, 0x303e],
  [0x3041, 0x33ff],
  [0x3400, 0x4dbf],
  [0x4e00, 0x9fff],
  [0xa000, 0xa4cf],
  [0xac00, 0xd7a3],
  [0xf900, 0xfaff],
  [0xfe30, 0xfe6f],
  [0xff00, 0xff60],
  [0xffe0, 0xffe6],
  [0x20000, 0x3fffd],
];

/**
 * Visual width of `text`, counting a CJK glyph as two.
 *
 * A plain character count folds 240 hanzi far later than 240 Latin letters,
 * because they render about twice as wide; counting wide code points double
 * keeps the budget roughly equal across scripts.
 */
function visualWidth(text: string): number {
  let width = 0;
  for (const character of text) {
    const code = character.codePointAt(0) ?? 0;
    width += code >= 0x1100 && WIDE_RANGES.some(([lo, hi]) => code >= lo && code <= hi) ? 2 : 1;
  }
  return width;
}

/**
 * Rough visual-width capacity of one line in these cards. Only used to
 * decide *whether* to fold, so it needs to be in the right ballpark, not
 * exact — the clamp class is what actually bounds the height.
 */
const WIDTH_PER_LINE = 60;

/**
 * Static line-count → utility maps. Both the running clamp and the fold
 * budget derive from the same number, so there is one knob rather than two
 * that can disagree; the classes are spelled out because Tailwind only
 * emits what it can see in the source.
 */
const CLAMP_CLASS: Record<number, string> = {
  1: "line-clamp-1",
  2: "line-clamp-2",
  3: "line-clamp-3",
  4: "line-clamp-4",
  5: "line-clamp-5",
  6: "line-clamp-6",
  7: "line-clamp-7",
  8: "line-clamp-8",
};

export default function ClampedText({
  text,
  className = "",
  clampLines = 6,
  label,
}: {
  text: string;
  /** Applied to the wrapper in both states. */
  className?: string;
  /** Lines shown while folded; also sets the budget that triggers folding. */
  clampLines?: number;
  /** What is being folded, so the toggle's accessible name is unambiguous. */
  label?: string;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const bodyId = useId();
  const toggle = useCallback(() => setExpanded((value) => !value), []);

  // Scanning a 3000-character transcript on every render (three cards on
  // screen) is wasted work; the width only changes when the text does.
  const needsFold = useMemo(
    () => visualWidth(text) > clampLines * WIDTH_PER_LINE,
    [text, clampLines],
  );

  if (!needsFold) {
    return <div className={className}>{text}</div>;
  }

  const action = expanded ? t("Collapse") : t("Expand");
  // Several cards can be on screen at once; "Expand" alone would leave a
  // screen-reader user unable to tell which control belongs to which text.
  const accessibleName = label ? `${action} — ${label}` : action;

  return (
    <div className={className}>
      <div id={bodyId} className={expanded ? undefined : CLAMP_CLASS[clampLines]}>
        {text}
      </div>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={expanded}
        aria-controls={bodyId}
        aria-label={accessibleName}
        className="mt-1 inline-flex items-center gap-0.5 text-[11px] text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
      >
        <ChevronDown
          size={11}
          aria-hidden
          className={`transition-transform ${expanded ? "rotate-180" : ""}`}
        />
        {action}
      </button>
    </div>
  );
}
