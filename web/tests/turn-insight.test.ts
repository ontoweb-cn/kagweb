import test from "node:test";
import assert from "node:assert/strict";

import { INSIGHT_TYPES, insightMetaOf } from "../lib/turn-insight";

test("every insight type has a distinct colour and a translatable label key", () => {
  const entries = Object.entries(INSIGHT_TYPES);
  assert.equal(entries.length, 5);
  assert.equal(new Set(entries.map(([, meta]) => meta.color)).size, 5);
  for (const [type, meta] of entries) {
    assert.ok(meta.labelKey, `${type} has no label key`);
    assert.match(meta.color, /^#[0-9a-f]{6}$/i, `${type} colour is not a hex value`);
  }
});

test("an unknown or missing type falls back to the insight badge", () => {
  assert.equal(insightMetaOf(undefined), INSIGHT_TYPES.insight);
  assert.equal(insightMetaOf(""), INSIGHT_TYPES.insight);
  assert.equal(insightMetaOf("nonsense"), INSIGHT_TYPES.insight);
});

test("a known type resolves to its own meta", () => {
  assert.equal(insightMetaOf("ruleout"), INSIGHT_TYPES.ruleout);
  assert.equal(insightMetaOf("pivot"), INSIGHT_TYPES.pivot);
});
