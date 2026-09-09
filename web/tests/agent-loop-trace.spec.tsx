/**
 * A CLI-backed chat turn must read like a native one in the activity trace:
 * each tool/Skill call is its own named row, the model's reasoning is visible,
 * and the trace settles when the loop stops working.
 *
 * The fixture is the post-fix wire shape (see its `_comment`); a pre-fix
 * recording would render three identical rows titled "Responding", which is
 * exactly the bug being pinned here.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { StreamEvent } from "@/features/chat/model/protocol";
import { AssistantActivity, CallTracePanel } from "@/features/chat/trace";
import { initI18n } from "@/i18n/init";

import recorded from "./fixtures/agent-loop-round-events.json";

initI18n("en");

const turn = (recorded as { turn: StreamEvent[]; finish: StreamEvent }).turn;
const finish = (recorded as { turn: StreamEvent[]; finish: StreamEvent }).finish;
const answer = turn
  .filter((event) => event.type === "content")
  .map((event) => String(event.content))
  .join("");

describe("agent-loop turn in the activity trace", () => {
  it("names each tool and Skill call instead of titling rows by stage", () => {
    const { container } = render(<CallTracePanel events={turn} />);
    const text = container.textContent ?? "";

    expect(text).toContain("Running command");
    expect(text).toContain("ls -la");
    expect(text).toContain("Finding files");
    expect(text).toContain("**/*.ts");
    expect(text).toContain("Reading skill");
    expect(text).toContain("dataviz");
    // The bug's signature: the stage name standing in for a tool row.
    expect(text).not.toContain("Responding");
  });

  it("shows the model's reasoning as a row while the round is live", () => {
    const { container } = render(
      <AssistantActivity events={turn} isStreaming content={answer} />,
    );

    expect(container.textContent).toContain("Exploring");
    expect(container.textContent).toContain(
      "The skill invocation returned a receipt",
    );
    // The disclosure wrapper is open while the loop is still working.
    expect(container.querySelector(".grid-rows-\\[1fr\\]")).not.toBeNull();
  });

  it("settles once the loop closes its final round", () => {
    const { container } = render(
      <AssistantActivity
        events={[...turn, finish]}
        isStreaming={false}
        content={answer}
      />,
    );

    expect(container.querySelector(".grid-rows-\\[0fr\\]")).not.toBeNull();
    expect(container.querySelector(".grid-rows-\\[1fr\\]")).toBeNull();
  });

  it("keeps the loop's prose in the answer, not in the trace", () => {
    render(<CallTracePanel events={turn} />);

    // The narration is answer text; only the thinking is trace material.
    expect(screen.queryByText(/fire off a few tool calls/)).toBeNull();
  });
});
