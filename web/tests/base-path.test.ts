import assert from "node:assert/strict";
import test from "node:test";

import {
  getBasePath,
  stripBasePath,
  withBasePath,
} from "../shared/base-path";

function withPrefix(value: string | undefined, fn: () => void) {
  const previous = process.env.NEXT_PUBLIC_BASE_PATH;
  if (value === undefined) delete process.env.NEXT_PUBLIC_BASE_PATH;
  else process.env.NEXT_PUBLIC_BASE_PATH = value;
  try {
    fn();
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_BASE_PATH;
    else process.env.NEXT_PUBLIC_BASE_PATH = previous;
  }
}

test("no prefix configured: everything is a pass-through", () => {
  withPrefix(undefined, () => {
    assert.equal(getBasePath(), "");
    assert.equal(withBasePath("/api/x"), "/api/x");
    assert.equal(stripBasePath("/kagweb/x"), "/kagweb/x");
  });
});

test("prefix normalization: leading slash added, trailing slashes dropped", () => {
  withPrefix("kagweb", () => assert.equal(getBasePath(), "/kagweb"));
  withPrefix("/kagweb///", () => assert.equal(getBasePath(), "/kagweb"));
  withPrefix("/", () => assert.equal(getBasePath(), ""));
});

test("withBasePath applies the prefix and stays idempotent", () => {
  withPrefix("/sub", () => {
    assert.equal(withBasePath("/api/x"), "/sub/api/x");
    assert.equal(withBasePath("/login?next=%2Fchat"), "/sub/login?next=%2Fchat");
    assert.equal(withBasePath("/"), "/sub/");
    // Idempotency is boundary-aware: exact match and segment match are
    // already-prefixed, but a path merely SHARING the string prefix is not.
    assert.equal(withBasePath("/sub"), "/sub");
    assert.equal(withBasePath("/sub/api/x"), "/sub/api/x");
    assert.equal(withBasePath("/sub-x"), "/sub/sub-x");
    // Relative and protocol-absolute inputs pass through untouched.
    assert.equal(withBasePath("api/x"), "api/x");
    assert.equal(withBasePath("https://example.com"), "https://example.com");
  });
});

test("stripBasePath mirrors withBasePath at the segment boundary", () => {
  withPrefix("/sub", () => {
    assert.equal(stripBasePath("/sub"), "/");
    assert.equal(stripBasePath("/sub/"), "/");
    assert.equal(stripBasePath("/sub/chat?id=1"), "/chat?id=1");
    assert.equal(stripBasePath("/sub-x"), "/sub-x");
    assert.equal(stripBasePath("/other"), "/other");
    // Already-relative input is a no-op (middleware may hand over stripped
    // paths), including its edge cases.
    assert.equal(stripBasePath("/chat"), "/chat");
    assert.equal(stripBasePath("/"), "/");
  });
});
