import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChatMessageList } from "@/features/chat/messages";
import { initI18n } from "@/i18n/init";
import type { StreamEvent } from "@/features/chat/model/protocol";

initI18n("en");

// The exact event sequence KAGWeb's approval flow emits mid-turn
// (capability.py _handle_approval_request). While the turn is paused the
// message is still streaming — the card must be interactive in that state,
// not only after the turn persists.
function approvalEvents(): StreamEvent[] {
  const questions = [
    {
      id: "approval",
      header: "Approval",
      prompt: "The agent wants to run 'shell'. Allow it to continue?",
      options: [
        { value: "once", label: "Allow once", description: "Approve this single request" },
        { value: "deny", label: "Deny", description: "Do not run it" },
      ],
      multi_select: false,
    },
  ];
  return [
    {
      type: "tool_call",
      source: "chat",
      stage: "responding",
      content: "",
      metadata: { call_id: "approval-req-1", call_state: "running", args: { questions } },
      turn_id: "t1",
      seq: 1,
    },
    {
      type: "tool_result",
      source: "chat",
      stage: "responding",
      content: "",
      metadata: { call_id: "approval-req-1", tool_metadata: { ask_user: { questions } } },
      turn_id: "t1",
      seq: 2,
    },
  ] as unknown as StreamEvent[];
}

describe("live approval card (turn still streaming)", () => {
  it("renders the card as interactive while isStreaming and submits the decision", async () => {
    const user = userEvent.setup();
    const submit = vi.fn();
    render(
      <ChatMessageList
        messages={[
          {
            id: 2,
            role: "assistant",
            content: "",
            events: approvalEvents(),
            parentMessageId: null,
          },
        ]}
        isStreaming={true}
        onCopyAssistantMessage={vi.fn()}
        onRegenerateMessage={() => undefined}
        onSubmitUserReply={submit}
      />,
    );

    expect(screen.getByText(/Allow it to continue/)).toBeVisible();
    // Two-step interaction: pick the option, then confirm. Option rows are
    // prefixed with a letter (A/B/…), so match by substring.
    await user.click(screen.getByRole("button", { name: /Allow once/ }));
    await user.click(screen.getByRole("button", { name: "Submit" }));
    expect(submit).toHaveBeenCalledTimes(1);
    const arg = submit.mock.calls[0][0] as { answers?: Array<{ text?: string }> };
    expect(JSON.stringify(arg)).toContain("once");
  });

  it("flips the card to resolved once the decision progress arrives", async () => {
    const events = [
      ...approvalEvents(),
      {
        type: "progress",
        source: "chat",
        stage: "responding",
        content: "Approved",
        metadata: {
          ask_user_resolved: true,
          ask_user_tool_call_id: "approval-req-1",
          answers: [{ questionId: "approval", text: "once" }],
        },
        turn_id: "t1",
        seq: 3,
      } as unknown as StreamEvent,
    ];
    render(
      <ChatMessageList
        messages={[
          { id: 2, role: "assistant", content: "", events, parentMessageId: null },
        ]}
        isStreaming={true}
        onCopyAssistantMessage={vi.fn()}
        onRegenerateMessage={() => undefined}
        onSubmitUserReply={vi.fn()}
      />,
    );

    // Resolved: the interactive controls are gone; the decision text is
    // reflected in the card.
    expect(screen.queryByRole("button", { name: /Allow once/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Submit" })).toBeNull();
    expect(screen.getByText("Your answers")).toBeVisible();
  });
});
