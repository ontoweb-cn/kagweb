import test from "node:test";
import assert from "node:assert/strict";

import { nextZoomTier } from "../lib/dag-zoom";

test("turn tier leaves only above the work threshold", () => {
  assert.equal(nextZoomTier("turn", 1.0), "turn");
  assert.equal(nextZoomTier("turn", 0.81), "turn");
  assert.equal(nextZoomTier("turn", 0.8), "plaque");
});

test("plaque tier hysteresis: needs 0.9 to return, 0.32 to drop", () => {
  assert.equal(nextZoomTier("plaque", 0.5), "plaque");
  assert.equal(nextZoomTier("plaque", 0.85), "plaque"); // below the 0.9 return line
  assert.equal(nextZoomTier("plaque", 0.9), "turn");
  assert.equal(nextZoomTier("plaque", 0.4), "plaque");
  assert.equal(nextZoomTier("plaque", 0.32), "glyph");
});

test("glyph tier hysteresis: needs 0.4 to climb to plaque, 0.9 to turn", () => {
  assert.equal(nextZoomTier("glyph", 0.1), "glyph");
  assert.equal(nextZoomTier("glyph", 0.35), "glyph"); // below the 0.4 climb line
  assert.equal(nextZoomTier("glyph", 0.4), "plaque");
  assert.equal(nextZoomTier("glyph", 0.95), "turn");
});
