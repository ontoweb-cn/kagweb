import test from "node:test";
import assert from "node:assert/strict";

import { INSIGHT_TYPES, insightMetaOf, isInsightType } from "../lib/turn-insight";

test("every insight type carries a colour and a distinct translatable label key", () => {
  for (const [type, meta] of Object.entries(INSIGHT_TYPES)) {
    assert.ok(meta.color, `${type} has no colour`);
    assert.ok(meta.labelKey, `${type} has no label key`);
  }
  const labelKeys = Object.values(INSIGHT_TYPES).map((meta) => meta.labelKey);
  assert.equal(new Set(labelKeys).size, labelKeys.length);
});

test("an unknown or missing type falls back to the insight badge", () => {
  for (const value of [undefined, "", "nonsense"]) {
    assert.equal(insightMetaOf(value), INSIGHT_TYPES.insight);
  }
});

test("prototype keys cannot escape the fallback", () => {
  // `INSIGHT_TYPES["constructor"]` is truthy, so a bare index would render a
  // badge with no colour and no label instead of falling back.
  for (const key of ["constructor", "toString", "valueOf", "__proto__", "hasOwnProperty"]) {
    assert.equal(isInsightType(key), false, key);
    assert.equal(insightMetaOf(key), INSIGHT_TYPES.insight, key);
  }
});

test("a known type resolves to its own meta", () => {
  for (const type of Object.keys(INSIGHT_TYPES) as Array<keyof typeof INSIGHT_TYPES>) {
    assert.equal(isInsightType(type), true);
    assert.equal(insightMetaOf(type), INSIGHT_TYPES[type]);
  }
});
