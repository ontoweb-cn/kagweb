import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { config as proxyConfig } from "../proxy";

// Unit tests for the pure middleware routing policy (web/lib/proxy-policy.ts).
// The policy is deliberately decoupled from `next/server`, so it can be
// exercised here without booting the Next runtime. proxy.ts itself is a thin
// adapter that maps these decisions onto NextResponse.

import {
  classifyToken,
  isAuthExempt,
  isBackendPath,
  isRetiredPagePath,
} from "../lib/proxy-policy";

function makeToken(payload: Record<string, unknown>): string {
  const encode = (value: unknown) =>
    Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "HS256" })}.${encode(payload)}.signature`;
}

test("isBackendPath matches /api and /ws paths only", () => {
  assert.equal(isBackendPath("/api/sessions"), true);
  assert.equal(isBackendPath("/ws/chat"), true);
  assert.equal(isBackendPath("/chat"), false);
  assert.equal(isBackendPath("/apidocs"), false); // no trailing slash → not backend
  assert.equal(isBackendPath("/logo.png"), false);
});

test("matcher lets backend paths through so they rewrite to the API server", () => {
  const matches = (url: string) =>
    unstable_doesMiddlewareMatch({ config: proxyConfig, url });

  assert.equal(matches("http://localhost/api/sessions"), true);
  assert.equal(matches("http://localhost/ws/chat"), true);
  assert.equal(matches("http://localhost/chat"), true);
  assert.equal(matches("http://localhost/_next/static/x.js"), false);
});

test("backend proxy allows long-running agent requests", () => {
  const nextConfig = require(path.resolve(process.cwd(), "next.config.js")) as {
    experimental?: { proxyTimeout?: number };
  };
  assert.ok(
    (nextConfig.experimental?.proxyTimeout ?? 0) >= 30 * 60 * 1000,
    "proxyTimeout must accommodate long PageIndex and Co-Writer turns",
  );
});


test("retired pages cannot fall through to colliding dynamic routes", () => {
  assert.equal(isRetiredPagePath("/partners/groups"), true);
  assert.equal(isRetiredPagePath("/partners/groups/new"), false);
  assert.equal(isRetiredPagePath("/partners/groups/group-1"), false);
  assert.equal(isRetiredPagePath("/partners/group-1"), false);
});

test("the old codex callback path is retired with a 404", () => {
  const source = readFileSync(path.resolve(process.cwd(), "lib/proxy-policy.ts"), "utf8");
  // The backend route is gone; the path must land in the retired set, not
  // rewrite to a ghost endpoint.
  assert.match(source, /RETIRED_PAGE_PATHS = new Set\(\[.*"\/auth\/callback"/s);
  assert.doesNotMatch(source, /openai-codex/);
});

test("isAuthExempt allows public static assets through the auth gate (issue #599)", () => {
  // The Next image optimizer re-fetches these over a cookie-less loopback; if
  // the gate blocked them the sidebar logo/banner would render broken.
  assert.equal(isAuthExempt("/logo.png"), true);
  assert.equal(isAuthExempt("/banner.png"), true);
  assert.equal(isAuthExempt("/logo_black.png"), true);
  assert.equal(isAuthExempt("/apple-touch-icon.png"), true);
  assert.equal(isAuthExempt("/provider-icons/openai.svg"), true);
});

test("isAuthExempt allows auth pages and Next internals", () => {
  assert.equal(isAuthExempt("/login"), true);
  assert.equal(isAuthExempt("/register"), true);
  assert.equal(isAuthExempt("/_next/data/build/chat.json"), true);
  assert.equal(isAuthExempt("/favicon-32x32.png"), true);
});

test("isAuthExempt does NOT exempt protected app routes", () => {
  assert.equal(isAuthExempt("/chat"), false);
  assert.equal(isAuthExempt("/dashboard"), false);
  assert.equal(isAuthExempt("/space/agents"), false);
});

test("classifyToken reports missing for absent or empty cookie", () => {
  const now = 1_000_000_000_000;
  assert.equal(classifyToken(undefined, now), "missing");
  assert.equal(classifyToken("", now), "missing");
});

test("classifyToken reports malformed for non-JWT shapes", () => {
  const now = 1_000_000_000_000;
  assert.equal(classifyToken("a.b", now), "malformed"); // 2 segments
  assert.equal(classifyToken("a.b.c.d", now), "malformed"); // 4 segments
  // Valid 3-segment shape but the payload is not JSON → malformed.
  const notJson = `h.${Buffer.from("not-json").toString("base64url")}.s`;
  assert.equal(classifyToken(notJson, now), "malformed");
});

test("classifyToken honors expiry and accepts unexpired / expiry-less tokens", () => {
  const now = 1_000_000_000_000; // ms
  const nowSec = now / 1000;
  assert.equal(classifyToken(makeToken({ exp: nowSec + 3600 }), now), "valid");
  assert.equal(classifyToken(makeToken({ exp: nowSec - 1 }), now), "expired");
  assert.equal(classifyToken(makeToken({}), now), "valid"); // no exp claim
});
