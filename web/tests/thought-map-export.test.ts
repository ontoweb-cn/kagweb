import test from "node:test";
import assert from "node:assert/strict";

import {
  buildThoughtMapSvg,
  type ThoughtMapInput,
  type ThoughtMapNode,
} from "../lib/thought-map-export";
import { INSIGHT_TYPES } from "../lib/turn-insight";

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
  // Asserted against the palette, not a literal: the point is that the export
  // reads the same table the activity-header chip does.
  assert.ok(svg.includes(INSIGHT_TYPES.ruleout.color));
  assert.ok(svg.includes(INSIGHT_TYPES.decision.color));
  // A node without a badge gets the neutral ink, never a badge colour.
  assert.ok(svg.includes("#64748b")); // user
});

test("the canvas is tall enough for the deepest row", () => {
  // Five siblings share one level (rows 0-4): the old count-based height
  // clipped the last one out of the viewBox.
  const stacked = buildThoughtMapSvg({
    nodes: Array.from({ length: 5 }, (_, row) => ({
      id: `n${row}`,
      level: 0,
      row,
      kind: "assistant" as const,
    })),
    edges: [],
  });
  const height = Number(/height="(\d+)"/.exec(stacked)?.[1]);
  const maxCy = Math.max(...[...stacked.matchAll(/cy="(\d+)"/g)].map((m) => Number(m[1])));
  assert.ok(maxCy + 8 <= height, `last circle (${maxCy}) escapes the viewBox (${height})`);
});

test("the time ink follows message order, not the row within a level", () => {
  const chain = buildThoughtMapSvg({
    nodes: [
      { id: "a", level: 0, row: 0, order: 0, kind: "assistant" as const },
      { id: "b", level: 1, row: 0, order: 12, kind: "assistant" as const },
    ],
    edges: [],
  });
  // order 0 → 0.45, order 12 → 1.0: a chain must still fade.
  const opacities = [...chain.matchAll(/<circle[^>]*opacity="([\d.]+)"/g)].map((m) => m[1]);
  assert.notEqual(opacities[0], opacities[1]);
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
