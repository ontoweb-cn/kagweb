import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const pane = readFileSync(
  path.resolve(process.cwd(), "components/reading/ReaderPane.tsx"),
  "utf8",
);
const textView = readFileSync(
  path.resolve(process.cwd(), "components/reading/TextUnitView.tsx"),
  "utf8",
);
const english = readFileSync(
  path.resolve(process.cwd(), "locales/en/app.json"),
  "utf8",
);
const chinese = readFileSync(
  path.resolve(process.cwd(), "locales/zh/app.json"),
  "utf8",
);

test("a scanned PDF gets a text-view toggle, a normal PDF does not", () => {
  // The OCR extractor tag is the server's record that the raw pages carry no
  // text layer while the store holds selectable text — the only case where
  // swapping the page reader for the text reader buys anything.
  assert.match(pane, /extractor\.endsWith\("-ocr"\)/);
  assert.match(pane, /\{scanTextAvailable && \(/);
});

test("switching views never costs the reader their place", () => {
  // The remounting PDF reader lands on its jump prop; the text view opens at
  // the locator the reader is leaving, not unit 1.
  assert.match(pane, /requestJump\(currentLocator\)/);
  assert.match(
    pane,
    /initialLocator=\{\s*material\.has_raw_view \? currentLocator : undefined/,
  );
  // A newly opened material starts in its own native render mode.
  assert.match(pane, /setTextView\(false\)/);
});

test("TextUnitView keeps an explicit initial locator on mount", () => {
  assert.match(
    textView,
    /Math\.min\(Math\.max\(1, initialLocator \?\? 1\)/,
  );
  // The material reset must not run on mount, or it would drag the toggle's
  // start position back to unit 1.
  assert.match(textView, /mountedMaterialRef\.current === materialId/);
});

test("selections over rendered math quote the stored TeX source", () => {
  // KaTeX keeps the original `$...$` source in its MathML annotation; the
  // selection walk must swap formulas back to source or the server's
  // verbatim check drops every selection that crosses one.
  assert.match(textView, /function markdownSelectionQuote\(range: Range\)/);
  assert.match(textView, /querySelector\("annotation"\)/);
  assert.match(textView, /markdownQuote = isWebMarkdown \? markdownSelectionQuote\(range\) : ""/);
});

test("the scan text-view toggle is translated in both locales", () => {
  for (const locale of [english, chinese]) {
    assert.match(locale, /"Show extracted text": "/);
    assert.match(locale, /"Show page images": "/);
  }
});
