import test from "node:test";
import assert from "node:assert/strict";

import { buildThoughtMapInput } from "../features/chat/dag/thought-map";
import type { SessionDag } from "../features/chat/dag/model";

/** A minimal DAG: root → user → assistant → (round → tool). */
function dagWithTool(): SessionDag {
  const nodes = [
    { id: "root", kind: "root" as const, parentId: null, seq: 0, meta: { childCount: 1 } },
    { id: "msg:1", kind: "user" as const, parentId: "root", seq: 1, meta: { childCount: 1 } },
    {
      id: "msg:2",
      kind: "assistant" as const,
      parentId: "msg:1",
      seq: 2,
      meta: { childCount: 1, turnInsight: { takeaway: "排除了缓存路径", type: "ruleout" as const } },
    },
    {
      id: "call:r1:round:0",
      kind: "round" as const,
      parentId: "msg:2",
      seq: 3,
      meta: { childCount: 1 },
    },
    {
      id: "call:t1:tool_call:1",
      kind: "tool_call" as const,
      parentId: "call:r1:round:0",
      seq: 4,
      meta: { childCount: 0, toolName: "exec" },
    },
  ];
  return {
    nodes,
    byId: new Map(nodes.map((node) => [node.id, node])),
    edges: [
      { id: "e1", source: "root", target: "msg:1", kind: "conversation" },
      { id: "e2", source: "msg:1", target: "msg:2", kind: "conversation" },
      { id: "e3", source: "msg:2", target: "call:r1:round:0", kind: "drilldown" },
      { id: "e4", source: "call:r1:round:0", target: "call:t1:tool_call:1", kind: "drilldown" },
    ],
    expandable: new Set<string>(),
    truncated: null,
  };
}

test("the projection levels by longest path and keeps the execution layer", () => {
  const input = buildThoughtMapInput(dagWithTool(), "s1");

  // Every node survives — including the tool the collapsed panel view hides.
  assert.deepEqual(
    input.nodes.map((node) => node.id),
    ["root", "msg:1", "msg:2", "call:r1:round:0", "call:t1:tool_call:1"],
  );
  // A chain: one node per level, rows all 0, message order preserved.
  assert.deepEqual(input.nodes.map((node) => node.level), [0, 1, 2, 3, 4]);
  assert.deepEqual(input.nodes.map((node) => node.row), [0, 0, 0, 0, 0]);
  assert.deepEqual(input.nodes.map((node) => node.order), [0, 1, 2, 3, 4]);
  // Drilldown edges are dashed; conversation edges are not.
  assert.deepEqual(
    input.edges.map((edge) => [edge.source, edge.dashed]),
    [
      ["root", false],
      ["msg:1", false],
      ["msg:2", true],
      ["call:r1:round:0", true],
    ],
  );
});

test("the projection carries the badge type and the node kind", () => {
  const input = buildThoughtMapInput(dagWithTool());

  const assistant = input.nodes.find((node) => node.id === "msg:2");
  assert.equal(assistant?.insightType, "ruleout");
  assert.equal(assistant?.kind, "assistant");
  assert.equal(input.nodes.find((node) => node.id === "msg:1")?.kind, "user");
  assert.equal(input.nodes.find((node) => node.id === "call:t1:tool_call:1")?.kind, "other");
});

test("siblings share a level and get distinct rows", () => {
  const dag = dagWithTool();
  dag.edges.push({
    id: "e5",
    source: "call:r1:round:0",
    target: "call:t2:tool_call:2",
    kind: "drilldown",
  });
  dag.nodes.push({
    id: "call:t2:tool_call:2",
    kind: "tool_call",
    parentId: "msg:2",
    seq: 5,
    meta: { childCount: 0, toolName: "read_file" },
  });
  dag.byId.set("call:t2:tool_call:2", dag.nodes[dag.nodes.length - 1]);

  const input = buildThoughtMapInput(dag);
  const t1 = input.nodes.find((node) => node.id === "call:t1:tool_call:1");
  const t2 = input.nodes.find((node) => node.id === "call:t2:tool_call:2");

  assert.equal(t1?.level, t2?.level);
  assert.notEqual(t1?.row, t2?.row);
});
