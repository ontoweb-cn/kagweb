import test from "node:test";
import assert from "node:assert/strict";

import {
  buildThoughtMapSvg,
  type ThoughtMapInput,
  type ThoughtMapNode,
} from "../lib/thought-map-export";

const input: ThoughtMapInput = {
  nodes: [
    { id: "u1", level: 0, row: 0, kind: "user" },
    { id: "a1", level: 1, row: 0, kind: "assistant", insightType: "ruleout" },
    { id: "a2", level: 2, row: 0, kind: "assistant", insightType: "decision" },
  ],
  edges: [
    { source: "u1", target: "a1" },
    { source: "a1", target: "a2", dashed: true },
  ],
  title: "会话 unified_test",
};

test("thought map svg carries nodes, edges and the caption", () => {
  const svg = buildThoughtMapSvg(input);
  assert.match(svg, /<svg /);
  assert.match(svg, /<circle/);
  assert.match(svg, /<rect/); // user node
  assert.match(svg, /stroke-dasharray="4 4"/); // dashed drilldown edge
  assert.match(svg, /会话 unified_test — user×1 · ruleout×1 · decision×1/);
});

test("badge colours come from the shared insight palette", () => {
  const svg = buildThoughtMapSvg(input);
  // The same hex the activity-header chip uses, so the two surfaces cannot
  // disagree about what a "ruleout" looks like.
  assert.match(svg, /fill="#ef4444"/);
  assert.match(svg, /fill="#6B5CE7"/);
});

test("thought map svg never contains conversation text", () => {
  /** The one law of the shape export: text cannot leak because the input
      type carries none — but assert the guarantee against realistic leaks. */
  // A caller smuggling text via extra properties: the serializer reads only
  // known fields, so the smuggled text cannot reach the SVG.
  const smuggled = { secret: "password123" } as unknown as ThoughtMapNode;
  const leaky = buildThoughtMapSvg({
    ...input,
    nodes: [...input.nodes, { ...smuggled, id: "x", level: 3, row: 0, kind: "other" }],
  });
  assert.ok(!leaky.includes("password123"));
  assert.ok(!leaky.includes("<surface>"));
});
