import assert from "node:assert/strict";
import test from "node:test";

import {
  decodeResourceSegment,
  knowledgeBaseFileRoute,
  knowledgeBaseRoute,
} from "../lib/resource-routes";

test("resource identities are encoded as path segments", () => {
  assert.equal(
    knowledgeBaseRoute("calculus / 微积分"),
    "/knowledge-bases/calculus%20%2F%20%E5%BE%AE%E7%A7%AF%E5%88%86",
  );
});

test("knowledge files open the KB detail page rather than raw API streams", () => {
  assert.equal(
    knowledgeBaseFileRoute("test1", "论文详细解读.md"),
    "/knowledge-bases/test1?file=%E8%AE%BA%E6%96%87%E8%AF%A6%E7%BB%86%E8%A7%A3%E8%AF%BB.md",
  );
  assert.equal(knowledgeBaseFileRoute("test1", " docs/readme.md "), "/knowledge-bases/test1?file=docs%2Freadme.md");
  assert.equal(knowledgeBaseFileRoute("test1", " "), "/knowledge-bases/test1");
});

test("dynamic resource parameters decode back to stored identities", () => {
  assert.equal(
    decodeResourceSegment("%E5%9B%BD%E9%99%85%E5%8C%BB%E7%96%97"),
    "国际医疗",
  );
  assert.equal(decodeResourceSegment("calculus%20%2F%20%E5%BE%AE%E7%A7%AF%E5%88%86"), "calculus / 微积分");
  assert.equal(decodeResourceSegment("100%25%20coverage"), "100% coverage");
  assert.equal(decodeResourceSegment(null), null);
});
