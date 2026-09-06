import assert from "node:assert/strict";
import test from "node:test";
import {
  parseSessionDsl,
  serializeSessionDsl,
  type DslDocument,
} from "../features/chat/dag/dsl";
import { assistantMsg, ev, userMsg } from "./session-dag-fixtures";

function richEvents() {
  return [
    ev("thinking", { call_id: "r1", call_kind: "agent_loop_round", trace_group: "stage" }, "plan", 1),
    ev("tool_call", { call_id: "t1", trace_group: "tool_call", tool_name: "rag" }, "rag", 2),
    ev("progress", { call_id: "t1", trace_role: "retrieve", query: "fourier" }, "searching", 3),
    ev("tool_result", { call_id: "t1", trace_group: "tool_call" }, "ok", 4),
    ev("tool_call", { call_id: "t2", trace_group: "tool_call", tool_name: "consult_subagent" }, "go", 5),
    ev("progress", { call_id: "t2", subagent_name: "math", consult_index: 1 }, "working", 6),
    ev("tool_result", { call_id: "t2", trace_group: "tool_call" }, "ok", 7),
  ];
}

test("serializes a session into the tree-shaped DSL document", () => {
  const text = serializeSessionDsl({
    messages: [
      { ...userMsg(1, "what is fourier?"), parentMessageId: null },
      { ...assistantMsg(2, richEvents()), parentMessageId: 1, capability: "chat" },
    ],
  });
  const doc = parseSessionDsl(text);

  assert.equal(doc.version, 1);
  assert.equal(doc.generator, "kagweb/session-dsl");
  assert.equal(doc.trace.length, 2);
  assert.equal(doc.trace[0].kind, "user");
  assert.equal(doc.trace[0].node, "msg:1");
  assert.equal(doc.trace[1].kind, "assistant");
  assert.equal(doc.trace[1].capability, "chat");
  assert.ok(doc.trace[1].text_preview === undefined); // assistant has no user text

  // Full call tree (nested): round → [rag tool (retrieve merged), subagent tool → subagent]
  const calls = doc.trace[1].calls ?? [];
  assert.deepEqual(
    calls.map((c) => c.kind),
    ["round"],
  );
  assert.equal(calls[0].round_index, 0);
  const roundChildren = calls[0].calls ?? [];
  assert.deepEqual(
    roundChildren.map((c) => c.kind),
    ["tool_call", "tool_call"],
  );
  assert.equal(roundChildren[0].tool, "rag");
  assert.equal(roundChildren[0].query, "fourier");
  assert.equal(roundChildren[1].tool, "consult_subagent");
  const sub = roundChildren[1].calls ?? [];
  assert.equal(sub.length, 1);
  assert.equal(sub[0].kind, "subagent");
  assert.equal(sub[0].subagent_name, "math");
  assert.equal(sub[0].consult_index, 1);
});

test("export is independent of panel expansion — full tree always", () => {
  const messages = [
    { ...userMsg(1, "hi"), parentMessageId: null },
    { ...assistantMsg(2, richEvents()), parentMessageId: 1 },
  ];
  // The DSL exporter never takes an expansion set; verify calls exist even
  // though no expansion state is provided anywhere.
  const doc = parseSessionDsl(serializeSessionDsl({ messages }));
  const round = doc.trace[1].calls?.[0];
  assert.equal(round?.kind, "round");
  assert.ok((round?.calls ?? []).length >= 2); // tools nested under the round
});

test("stable strips volatile fields and the session block", () => {
  const messages = [
    { ...userMsg(1, "hi"), parentMessageId: null },
    { ...assistantMsg(2, richEvents()), parentMessageId: 1 },
  ];
  const plain = parseSessionDsl(serializeSessionDsl({ messages }));
  const stable = parseSessionDsl(
    serializeSessionDsl({ messages }, { stable: true }),
  );

  assert.ok(plain.session); // non-stable keeps session + timestamps
  assert.equal(stable.session, undefined); // stable drops the whole block

  const volatileFields = (d: DslDocument) => {
    const found: string[] = [];
    const visit = (entry: { duration_ms?: number; error?: string; calls?: unknown[] }) => {
      if (entry.duration_ms != null) found.push("duration_ms");
      if (entry.error != null) found.push("error");
      for (const child of (entry.calls ?? []) as Array<Parameters<typeof visit>[0]>) {
        visit(child);
      }
    };
    for (const t of d.trace) visit(t as Parameters<typeof visit>[0]);
    return found;
  };
  assert.deepEqual(volatileFields(stable), []);
  // non-stable carries durations (timestamps 1..7 span 6ms)
  assert.ok(volatileFields(plain).includes("duration_ms"));
});

test("normalizeIds renumbers positionally (turn:N / turn:N.M)", () => {
  const messages = [
    { ...userMsg(1, "hi"), parentMessageId: null },
    { ...assistantMsg(2, richEvents()), parentMessageId: 1 },
  ];
  const doc = parseSessionDsl(
    serializeSessionDsl({ messages }, { normalizeIds: true }),
  );
  assert.deepEqual(
    doc.trace.map((t) => t.node),
    ["turn:1", "turn:2"],
  );
  const calls = doc.trace[1].calls ?? [];
  assert.deepEqual(
    calls.map((c) => c.node),
    ["turn:2.1"], // round
  );
  const roundChildren = calls[0].calls ?? [];
  assert.deepEqual(
    roundChildren.map((c) => c.node),
    ["turn:2.2", "turn:2.3"], // tools, DFS order
  );
  assert.equal((roundChildren[0].calls ?? []).length, 0); // rag tool has no children
  assert.equal((roundChildren[1].calls ?? [])[0]?.node, "turn:2.4"); // subagent
});

test("includeText:false removes text previews", () => {
  const messages = [
    { ...userMsg(1, "secret question"), parentMessageId: null },
    { ...assistantMsg(2, []), parentMessageId: 1 },
  ];
  const withText = parseSessionDsl(serializeSessionDsl({ messages }));
  const noText = parseSessionDsl(
    serializeSessionDsl({ messages }, { includeText: false }),
  );
  assert.equal(withText.trace[0].text_preview, "secret question");
  assert.equal(noText.trace[0].text_preview, undefined);
});

test("optimistic negative-id messages are filtered out", () => {
  const messages = [
    { ...userMsg(1, "done"), parentMessageId: null },
    { ...assistantMsg(2, richEvents()), parentMessageId: 1 },
    { ...userMsg(-1725999999999, "in flight"), parentMessageId: 2 },
    { ...assistantMsg(-1725999999998, []), parentMessageId: -1725999999999 },
  ];
  const doc = parseSessionDsl(serializeSessionDsl({ messages }));
  assert.deepEqual(
    doc.trace.map((t) => t.node),
    ["msg:1", "msg:2"],
  );
});

test("edit branches are exported recursively under the fork point", () => {
  // Root fork: user 1 → assistant 2 (branch A) vs user 3 → assistant 4 (branch B).
  // selectedBranches absent → latest (B) is the visible path; A becomes a branch.
  const messages = [
    { ...userMsg(1, "original"), parentMessageId: null },
    { ...assistantMsg(2, richEvents()), parentMessageId: 1 },
    { ...userMsg(3, "edited"), parentMessageId: null },
    { ...assistantMsg(4, richEvents()), parentMessageId: 3 },
  ];
  const doc = parseSessionDsl(serializeSessionDsl({ messages }));

  // visible path: 3 → 4
  assert.deepEqual(
    doc.trace.map((t) => t.node),
    ["msg:3", "msg:4"],
  );
  // user 3 sits at the fork: branch info + the non-selected sibling subtree
  assert.deepEqual(doc.trace[0].branch, { index: 2, total: 2 });
  const branches = doc.trace[0].branches ?? [];
  assert.equal(branches.length, 1);
  assert.equal(branches[0].selected, false);
  // the branch trace continues from the sibling user message 1
  assert.deepEqual(
    branches[0].trace.map((t) => t.node),
    ["msg:1", "msg:2"],
  );
  // branch trace carries its own nested call tree
  const branchRound = branches[0].trace[1].calls?.[0];
  assert.equal(branchRound?.kind, "round");
  assert.ok((branchRound?.calls ?? []).length >= 2);
});

test("parseSessionDsl rejects malformed documents", () => {
  assert.throws(() => parseSessionDsl("not json"), /not valid JSON/);
  assert.throws(() => parseSessionDsl('{"version": 99}'), /version/);
  assert.throws(() => parseSessionDsl('{"version": 1}'), /trace/);
  // Structural validation (#75): type-confused fields must fail here, not
  // crash dslToDag inside the panel's render-phase useMemo.
  const doc = (over: Record<string, unknown>) =>
    JSON.stringify({ version: 1, generator: "kagweb/session-dsl", trace: [over] });
  assert.throws(
    () => parseSessionDsl(doc({ node: "t1", kind: "user", calls: "not-an-array" })),
    /calls must be an array/,
  );
  assert.throws(
    () => parseSessionDsl(doc({ node: "t1", kind: "user", calls: [{ node: "c1" }] })),
    /kind must be one of/,
  );
  assert.throws(
    () => parseSessionDsl(doc({ node: "t1", kind: "banana" })),
    /kind must be "user" or "assistant"/,
  );
  assert.throws(
    () => parseSessionDsl(doc({ node: "t1", kind: "user", branches: [{ trace: "x" }] })),
    /trace must be an array/,
  );
  // Duplicate node ids are NOT rejected: non-normalized exports legitimately
  // repeat raw call ids across main/branch traces (#75). dsl-import dedupes
  // its internal keys instead.
  const dupDoc = JSON.stringify({
    version: 1,
    generator: "kagweb/session-dsl",
    trace: [
      { node: "t1", kind: "user" },
      { node: "t1", kind: "assistant" },
    ],
  });
  assert.doesNotThrow(() => parseSessionDsl(dupDoc));
});

test("normalizeIds namespaces branch ids to avoid collisions with the main trace", () => {
  const messages = [
    { ...userMsg(1, "original"), parentMessageId: null },
    { ...assistantMsg(2, richEvents()), parentMessageId: 1 },
    { ...userMsg(3, "edited"), parentMessageId: null },
    { ...assistantMsg(4, richEvents()), parentMessageId: 3 },
  ];
  const doc = parseSessionDsl(
    serializeSessionDsl({ messages }, { normalizeIds: true }),
  );
  // main trace: 3 → 4
  assert.deepEqual(
    doc.trace.map((t) => t.node),
    ["turn:1", "turn:2"],
  );
  // branch (sibling position 1 in siblingIds): b1:turn:N — never collides
  // with the main trace's turn:N namespace.
  const branchTrace = doc.trace[0].branches?.[0]?.trace ?? [];
  assert.deepEqual(
    branchTrace.map((t) => t.node),
    ["b1:turn:1", "b1:turn:2"],
  );
  const branchRound = branchTrace[1].calls?.[0];
  assert.equal(branchRound?.node, "b1:turn:2.1");
});

test("branches expand one level deep — nested forks inside a branch are not re-expanded", () => {
  // Root fork: user 1 (→ assistant 2, later user 5 → assistant 6) vs user 3.
  // Message 5 is itself a fork point (sibling of assistant 2 under parent 1).
  const messages = [
    { ...userMsg(1, "original"), parentMessageId: null },
    { ...assistantMsg(2, richEvents()), parentMessageId: 1 },
    { ...userMsg(5, "second edit"), parentMessageId: 1 },
    { ...assistantMsg(6, richEvents()), parentMessageId: 5 },
    { ...userMsg(3, "edited"), parentMessageId: null },
    { ...assistantMsg(4, richEvents()), parentMessageId: 3 },
  ];
  // Latest root edit (3 → 4) is the visible path; the single branch walks
  // 1 → 5 → 6 (message 1's latest child).
  const doc = parseSessionDsl(serializeSessionDsl({ messages }));
  const branches = doc.trace[0].branches ?? [];
  assert.equal(branches.length, 1);
  const deep = branches[0].trace;
  assert.deepEqual(
    deep.map((t) => t.node),
    ["msg:1", "msg:5", "msg:6"],
  );
  // msg:5 is itself a fork point (vs assistant 2): it reports `branch`
  // metadata but must NOT nest its own `branches` (single-level expansion
  // bound, review D1).
  assert.deepEqual(deep[1].branch, { index: 2, total: 2 });
  assert.equal(deep[1].branches, undefined);
});

test("normalized exports of the same session are byte-identical (snapshot stability)", () => {
  const messages = [
    { ...userMsg(1, "same question"), parentMessageId: null },
    { ...assistantMsg(2, richEvents()), parentMessageId: 1 },
  ];
  const opts = { stable: true, normalizeIds: true } as const;
  const a = serializeSessionDsl({ messages }, opts);
  const b = serializeSessionDsl({ messages }, opts);
  assert.equal(a, b);
});
