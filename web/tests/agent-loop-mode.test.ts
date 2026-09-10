import assert from "node:assert/strict";
import test from "node:test";

import {
  PRIMARY_AUTO,
  PRIMARY_NONE,
  modeFromPrimary,
} from "../lib/agent-loop-mode";

test("a missing primary reads as Automatic", () => {
  assert.equal(modeFromPrimary(null, "claude-local"), PRIMARY_AUTO);
  assert.equal(modeFromPrimary(undefined, "claude-local"), PRIMARY_AUTO);
});

test("the empty-string primary reads as the shell stub, not Automatic", () => {
  // Regression: `primary || "__auto__"` treated the stored "" (the user's
  // "None" choice) as unset, so the picker silently snapped back to Automatic.
  assert.equal(modeFromPrimary("", "claude-local"), PRIMARY_NONE);
});

test("a pinned profile keeps its own id", () => {
  assert.equal(modeFromPrimary("codex-local", "claude-local"), "codex-local");
});

test("a primary that equals the auto-resolved one reads as Automatic", () => {
  assert.equal(modeFromPrimary("claude-local", "claude-local"), PRIMARY_AUTO);
});
