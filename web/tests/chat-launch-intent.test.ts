import test from "node:test";
import assert from "node:assert/strict";

import { readChatLaunchIntent } from "../lib/chat-launch-intent";

test("an absent capability stays unspecified, an empty one means plain chat", () => {
  assert.equal(readChatLaunchIntent("?tool=web_search").capability, null);
  assert.equal(readChatLaunchIntent("?capability=").capability, "");
});

test("an empty search has no launch intent", () => {
  assert.deepEqual(readChatLaunchIntent(""), {
    capability: null,
  });
});
