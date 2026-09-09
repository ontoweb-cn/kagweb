/**
 * Models emit XML-ish fragments in answers — path templates like
 * ``trace/<surface>/<date>.jsonl``, protocol tags, pseudo-HTML. Content that
 * looks like HTML turns on the raw-HTML pipeline (`detectHtmlContent`), and
 * rehype-raw then parses those fragments into real DOM nodes; React 19
 * rejects unknown elements and the whole bubble fails to render.
 *
 * `escapeUnknownHtmlTags` wraps unknown tags in backticks before the markdown
 * pipeline sees them, so rehype-raw never receives an unknown element. These
 * cases pin every shape a model has been observed to emit — including an
 * attribute value that contains an angle bracket, which the tag regex's
 * `[^<>]*?` attribute segment could not span.
 */

import { render } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import { describe, expect, it } from "vitest";

import { escapeUnknownHtmlTagsForDisplay } from "@/lib/markdown-display";

/**
 * Render through the same raw-HTML pipeline the rich renderer enables once
 * `detectHtmlContent` fires, and return the visible text.
 */
function renderRawHtml(markdown: string): string {
  const { container, unmount } = render(
    <ReactMarkdown rehypePlugins={[rehypeRaw]}>{markdown}</ReactMarkdown>,
  );
  const text = container.textContent ?? "";
  unmount();
  return text;
}

/** `visible` is the substring a reader must see if the tag survived as text. */
const PSEUDO_TAGS = [
  { content: "trace/<surface>/<date>.jsonl", visible: "<surface>" },
  { content: "<think>internal scratchpad</think>", visible: "<think>" },
  { content: '<artifact type="code">x</artifact>', visible: "<artifact" },
  { content: "<ns:surface>x</ns:surface>", visible: "<ns:surface>" },
  { content: '<surface mode="a<b">x</surface>', visible: "<surface" },
  { content: '<tool_call>{"a":1}</tool_call>', visible: "<tool_call>" },
  { content: "| a | <surface> |\n|---|---|\n| 1 | 2 |", visible: "<surface>" },
  { content: "[<surface>](https://example.com)", visible: "<surface>" },
  { content: "**<surface>**", visible: "<surface>" },
  // An unbalanced quote must not let the allow-listed `<a` swallow the line.
  { content: '<a href="x> <surface>bad</surface>', visible: "<surface>" },
];

describe("unknown pseudo-tags never reach the raw-HTML pipeline", () => {
  for (const { content, visible } of PSEUDO_TAGS) {
    it(`renders ${JSON.stringify(content)} as text, not an element`, () => {
      const escaped = escapeUnknownHtmlTagsForDisplay(content);
      expect(() => renderRawHtml(escaped)).not.toThrow();
      expect(renderRawHtml(escaped)).toContain(visible);
    });
  }

  it("keeps real HTML elements intact", () => {
    const html = "<details><summary>More</summary>Body</details>";
    expect(escapeUnknownHtmlTagsForDisplay(html)).toBe(html);
    expect(renderRawHtml(html)).toContain("More");
  });
});
