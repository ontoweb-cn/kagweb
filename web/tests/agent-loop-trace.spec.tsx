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

  it("summarises the settled turn once the trace folds away", () => {
    const settled = render(
      <AssistantActivity
        events={[...turn, finish]}
        isStreaming={false}
        content={answer}
      />,
    );
    expect(settled.container.textContent).toContain("2 rounds · 4 tool calls");
    settled.unmount();

    // While the loop is still working there is nothing to summarise yet.
    const live = render(
      <AssistantActivity events={turn} isStreaming content={answer} />,
    );
    expect(live.container.textContent).not.toContain("rounds ·");
  });

  it("lists one tier-1 line per round while the trace is folded", () => {
    const { container } = render(
      <AssistantActivity
        events={[...turn, finish]}
        isStreaming={false}
        content={answer}
      />,
    );
    const text = container.textContent ?? "";

    expect(text).toContain("Round 1");
    expect(text).toContain("Round 2");
    expect(text).toContain("Running command ls -la");
    // The round's own narration is marked as self-report, never as fact.
    expect(text).toContain("Model self-report");
  });

  it("shows the observation excerpt only when the row has no chip", () => {
    const { container } = render(<CallTracePanel events={turn} />);
    const chipped = container.querySelector('[data-trace-call-id="tool_00"]');
    const chipLess = container.querySelector('[data-trace-call-id="tool_03"]');

    // A chip always wins: the exec row keeps its command preview and never
    // repeats the tool's output.
    expect(chipped?.textContent).toContain("ls -la");
    expect(chipped?.textContent).not.toContain("/d/workspace/kagweb");
    // Without a chip the row falls back to what the tool saw.
    expect(chipLess?.textContent).toContain("3 candidate files under src");
  });
});
