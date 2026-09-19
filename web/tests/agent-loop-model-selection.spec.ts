import { describe, expect, it } from "vitest";

import {
  isBackendModelSelection,
  type TurnModelSelection,
} from "../features/chat/model/protocol";
import { llmSelectionKey } from "../lib/llm-options";

describe("backend-native model selection", () => {
  it("keys the backend form separately from catalog pairs", () => {
    const backend: TurnModelSelection = { backend_model: "deepseek:deepseek-flash" };
    const catalog: TurnModelSelection = { profile_id: "p1", model_id: "m1" };

    expect(llmSelectionKey(backend)).toBe("backend:deepseek:deepseek-flash");
    expect(llmSelectionKey(catalog)).toBe("p1:m1");
    expect(llmSelectionKey(null)).toBe("");
    expect(llmSelectionKey({ backend_model: "" })).toBe("");
    // The two forms never collide on one key — they cannot be substituted
    // for each other, so one key space must not let them overlap.
    expect(llmSelectionKey(backend)).not.toBe(llmSelectionKey(catalog));
  });

  it("detects the backend-native payload form", () => {
    expect(isBackendModelSelection({ backend_model: "sonnet" })).toBe(true);
    expect(isBackendModelSelection({ profile_id: "p", model_id: "m" })).toBe(false);
    expect(isBackendModelSelection(null)).toBe(false);
    expect(isBackendModelSelection("sonnet" as unknown as TurnModelSelection)).toBe(false);
  });
});
