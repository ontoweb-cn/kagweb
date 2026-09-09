import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Tooltip from "@/components/common/Tooltip";

function renderHost() {
  const { container } = render(
    <Tooltip label="会话活动、附件与预览" side="bottom">
      <button type="button">活动</button>
    </Tooltip>,
  );
  const host = container.querySelector(".group\\/tip") as HTMLElement;
  const button = screen.getByRole("button", { name: "活动" });
  return { host, button };
}

describe("common Tooltip (group/tip) focus handling", () => {
  it("shows on keyboard focus only — not on pointer-granted focus", () => {
    // The reported freeze: pressing the trigger focuses it, and a
    // ``group-focus-within`` hint stayed at full opacity after the pointer
    // left. Visibility therefore keys off ``focus-visible``: a mouse click
    // sets :focus but not :focus-visible, so the hint follows the pointer
    // again without any JS touching focus.
    const { host } = renderHost();
    const tip = host.querySelector('[role="tooltip"]');
    expect(tip).not.toBeNull();
    expect(tip?.className).toContain("group-focus-visible/tip:opacity-100");
    expect(tip?.className).not.toContain("group-focus-within/tip:opacity-100");
  });

  it("never steals focus from its trigger", () => {
    // An earlier attempt blurred the focused descendant on pointer leave,
    // which dropped genuine keyboard focus (and the focus ring) whenever the
    // pointer happened to move off the host. The CSS-only version must leave
    // focus alone.
    const { host, button } = renderHost();
    button.focus();
    expect(document.activeElement).toBe(button);

    fireEvent.pointerLeave(host);
    expect(document.activeElement).toBe(button);
  });

  it("still reveals the hint on hover, with the label", () => {
    const { host } = renderHost();
    const tip = host.querySelector('[role="tooltip"]');
    expect(tip).toHaveTextContent("会话活动、附件与预览");
    expect(tip?.className).toContain("group-hover/tip:opacity-100");
  });
});
