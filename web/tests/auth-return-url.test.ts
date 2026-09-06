import assert from "node:assert/strict";
import test from "node:test";

import {
  browserReturnPath,
  inheritLoginHash,
  loginHref,
  normalizeInternalReturnPath,
} from "../shared/auth/return-url";

test("return URLs preserve path, query, and fragment", () => {
  const destination = browserReturnPath({
    pathname: "/notebooks/notes-1",
    search: "?course=course-2",
    hash: "#notes",
  });
  assert.equal(destination, "/notebooks/notes-1?course=course-2#notes");
  assert.equal(
    loginHref(destination),
    "/login?next=%2Fnotebooks%2Fnotes-1%3Fcourse%3Dcourse-2%23notes",
  );
});

test("return URLs reject external and ambiguous navigation", () => {
  for (const unsafe of [
    "https://example.com",
    "//example.com/path",
    "/\\example.com/path",
    "/%2f%2fexample.com/path",
    "/chat%0a/next",
    "javascript:alert(1)",
    "/chat\n/next",
  ]) {
    assert.equal(normalizeInternalReturnPath(unsafe), "/", unsafe);
  }
});

test("login inherits a server-invisible fragment without replacing an explicit one", () => {
  assert.equal(inheritLoginHash("/settings", "#tools"), "/settings#tools");
  assert.equal(
    inheritLoginHash("/settings#appearance", "#tools"),
    "/settings#appearance",
  );
});

// Subpath deployments: `next` stays app-relative (router.replace applies the
// basePath itself) while loginHref — consumed via window.location.href —
// carries the prefix. Both prefixed and unprefixed inputs normalize to the
// same app-relative output.
test("return URLs strip the deployment prefix for the next parameter", () => {
  process.env.NEXT_PUBLIC_BASE_PATH = "/kagweb";
  try {
    assert.equal(normalizeInternalReturnPath("/kagweb"), "/");
    assert.equal(
      normalizeInternalReturnPath("/kagweb/chat?id=1#x"),
      "/chat?id=1#x",
    );
    assert.equal(normalizeInternalReturnPath("/chat"), "/chat");
    // The bare prefix plus query keeps its query after stripping.
    assert.equal(normalizeInternalReturnPath("/kagweb?a=b"), "/?a=b");

    const destination = browserReturnPath({
      pathname: "/kagweb/notebooks/notes-1",
      search: "?course=course-2",
    });
    assert.equal(destination, "/notebooks/notes-1?course=course-2");
    assert.equal(
      loginHref(destination),
      "/kagweb/login?next=%2Fnotebooks%2Fnotes-1%3Fcourse%3Dcourse-2",
    );
  } finally {
    delete process.env.NEXT_PUBLIC_BASE_PATH;
  }
});
