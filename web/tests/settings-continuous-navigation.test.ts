import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import {
  SETTINGS_CATEGORIES,
  SETTINGS_HUB_HREF,
  SETTINGS_ROUTES,
  settingsDomainForRoute,
  settingsHref,
  storagePathFor,
} from "../features/settings/navigation/settings-nav";

const readWebFile = (...parts: string[]) =>
  readFileSync(path.join(process.cwd(), ...parts), "utf8");

const webPath = (...parts: string[]) => path.join(process.cwd(), ...parts);

test("settings navigation: every category and leaf resolves to a real URL", () => {
  assert.equal(settingsHref("overview"), SETTINGS_HUB_HREF);
  assert.equal(settingsHref("models"), "/settings/models");
  assert.equal(settingsHref("agent-loop"), "/settings/agent-loop");
  // A leaf keeps its anchor but moves under the route that owns it.
  assert.equal(settingsHref("llm"), "/settings/models#llm");
  assert.equal(settingsHref("attachments"), "/settings/chat#attachments");
  // An unknown key must not invent a route; it falls back to the index.
  assert.equal(settingsHref("does-not-exist"), SETTINGS_HUB_HREF);
});

test("settings domains: every rendered section has a navigation leaf", () => {
  // The section lists inside the domain pages are the ground truth for what
  // renders; the leaves here are the ground truth for what links. When the
  // two drift, a section becomes unreachable (videogen shipped exactly this
  // way: the page rendered, but no nav row and no resolvable link).
  for (const file of [
    ["features", "settings", "sections", "ModelsSettingsSection.tsx"],
    ["features", "settings", "sections", "ChatSettingsSection.tsx"],
  ] as const) {
    const source = readWebFile(...file);
    const sectionKeys = Array.from(source.matchAll(/key: "([a-z-]+)"/g)).map(
      (match) => match[1],
    );
    assert.ok(
      sectionKeys.length > 0,
      `${file.join("/")} should declare its sections`,
    );
    for (const key of sectionKeys) {
      const href = settingsHref(key);
      assert.ok(
        href.startsWith("/settings/"),
        `section "${key}" in ${file.join("/")} should be a routed leaf, ` +
          `but settingsHref resolves it to ${href}`,
      );
    }
  }
});

test("settings navigation: every category key has a route and a page file", () => {
  const categories = SETTINGS_CATEGORIES.map((category) => category.key);
  const routes = Object.keys(SETTINGS_ROUTES);
  assert.deepEqual(
    [...routes].sort(),
    [...categories].sort(),
    "SETTINGS_ROUTES must cover exactly the category keys",
  );

  for (const route of Object.values(SETTINGS_ROUTES)) {
    const segment = route.slice("/settings/".length);
    assert.ok(
      existsSync(webPath("app", "(utility)", "settings", segment, "page.tsx")),
      `${route} should have a page module`,
    );
  }
});

test("settings navigation: every category page is behind the domain gate", () => {
  for (const [domain, route] of Object.entries(SETTINGS_ROUTES)) {
    const segment = route.slice("/settings/".length);
    const page = readWebFile(
      "app",
      "(utility)",
      "settings",
      segment,
      "page.tsx",
    );
    // The single document enforced category visibility by filtering the
    // sections it stacked. With one route per category that filter is gone, so
    // each page must gate itself or an ordinary user could open an admin page.
    assert.match(
      page,
      new RegExp(`<SettingsDomainGate domain="${domain}">`),
      `${route} should gate on the "${domain}" category`,
    );
  }

  const gate = readWebFile("components", "settings", "SettingsDomainGate.tsx");
  assert.match(gate, /settingsDomainBlockReason\(domain, access\)/);
  assert.match(gate, /if \(!access\.resolved\)/);
  // "Not applicable to this backend" and "an admin owns this" are different
  // facts and must not collapse into one message.
  assert.match(gate, /llm-not-applicable/);
});

test("settings navigation: the domain for a route round-trips", () => {
  for (const [domain, route] of Object.entries(SETTINGS_ROUTES)) {
    assert.equal(settingsDomainForRoute(route), domain);
  }
  assert.equal(settingsDomainForRoute(SETTINGS_HUB_HREF), null);
  assert.equal(settingsDomainForRoute("/chat"), null);
});

test("settings navigation: labels derive hrefs from keys, never hand-written", () => {
  const nav = readWebFile(
    "features",
    "settings",
    "navigation",
    "settings-nav.ts",
  );

  // A hand-written href sitting beside a key is how the knowledge section
  // ended up linking `/settings#document-parsing` at a section rendered as
  // `id="knowledge"`. Keys are the only source now.
  assert.doesNotMatch(nav, /^\s*href:/m);
  assert.match(nav, /export function settingsHref\(key: string\)/);
  assert.match(nav, /LEAF_DOMAIN/);
});

test("settings consumers link through settingsHref", () => {
  for (const file of [
    ["components", "settings", "SettingsNav.tsx"],
    ["components", "settings", "SettingsOverview.tsx"],
    ["components", "settings", "ConnectionsEditor.tsx"],
    ["components", "sidebar", "VersionBadge.tsx"],
  ] as const) {
    const source = readWebFile(...file);
    assert.doesNotMatch(
      source,
      /"\/settings#/,
      `${file.join("/")} should build settings URLs via settingsHref`,
    );
  }

  // The assistant's hand-off path arrives from a backend that still emits the
  // old fragment form, so the prefix check has to stay permissive enough to
  // accept it; the layout redirect is what resolves it.
  const setupSignals = readWebFile("lib", "setup-signals.ts");
  assert.match(setupSignals, /startsWith\("\/settings"\)/);
});

test("settings legacy anchors: the index maps an old fragment to its route", () => {
  const redirect = readWebFile(
    "components",
    "settings",
    "SettingsLegacyAnchorRedirect.tsx",
  );
  assert.match(redirect, /pathname !== SETTINGS_HUB_HREF/);
  assert.match(redirect, /router\.replace\(settingsHref\(key\)\)/);

  const layout = readWebFile("app", "(utility)", "settings", "layout.tsx");
  assert.match(layout, /<SettingsLegacyAnchorRedirect \/>/);
});

test("settings hub: the index no longer stacks every category", () => {
  const page = readWebFile("app", "(utility)", "settings", "page.tsx");
  assert.match(page, /SettingsOverview/);
  assert.doesNotMatch(page, /CategoryScroll/);
  assert.doesNotMatch(page, /activationKeys/);
});

test("settings domains: models and chat keep their in-route sections", () => {
  const models = readWebFile(
    "features",
    "settings",
    "sections",
    "ModelsSettingsSection.tsx",
  );
  const chat = readWebFile(
    "features",
    "settings",
    "sections",
    "ChatSettingsSection.tsx",
  );
  for (const source of [models, chat]) {
    assert.match(source, /dynamic\(/);
    assert.match(source, /CategoryScroll/);
    assert.match(source, /deferSections/);
  }
  // Models still carries the service leaves as anchors on its own route.
  assert.match(models, /key: "llm"/);
});

test("settings storage paths: keyed by section key, not by URL", () => {
  assert.equal(
    storagePathFor("/settings/network", null),
    "data/user/settings/system.json",
  );
  assert.equal(
    storagePathFor("/settings/models", "llm"),
    "data/user/settings/model_catalog.json",
  );
  // A category whose leaves share one file resolves without a scroll position.
  assert.equal(
    storagePathFor("/settings/models", null),
    "data/user/settings/model_catalog.json",
  );
  // Chat's leaves span three files, so a category-level guess would name the
  // wrong one; it answers only for a known leaf.
  assert.equal(storagePathFor("/settings/chat", null), null);
  assert.equal(
    storagePathFor("/settings/chat", "capabilities"),
    "data/user/settings/main.yaml",
  );
  assert.equal(
    storagePathFor("/settings/chat", "attachments"),
    "data/user/settings/system.json",
  );
  assert.equal(
    storagePathFor(SETTINGS_HUB_HREF, "network"),
    "data/user/settings/system.json",
  );
  assert.equal(storagePathFor(SETTINGS_HUB_HREF, "about"), null);
});

test("settings tour: a step with no visible target is stepped over", () => {
  const overlay = readWebFile(
    "components",
    "settings",
    "SettingsTourOverlay.tsx",
  );
  // The navigator hides categories that do not apply to the configured agent
  // backend, so a step can point at a row that is not rendered. Without a
  // skip, the tour paints nothing at all and the user sees it vanish.
  assert.match(overlay, /advanceTour\(\)/);
  assert.match(overlay, /advanceTour\]/);

  const store = readWebFile("features", "settings", "store", "SettingsStore.tsx");
  // Every tour step addresses the settings navigator, which is present on
  // every settings route.
  assert.match(store, /target: "tour-nav-/);
  // The memory step pointed at a category removed with the learner subsystem,
  // so it retried and then silently showed nothing.
  // Match the step definition, not the comment recording why it went.
  assert.doesNotMatch(store, /target: "tour-nav-memory"/);
});
