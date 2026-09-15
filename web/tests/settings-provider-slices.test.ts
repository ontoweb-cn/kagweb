import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (relative: string) =>
  fs.readFileSync(path.resolve(process.cwd(), relative), "utf8");

test("settings layout installs independently memoized provider slices", () => {
  const layout = read("app/(utility)/settings/layout.tsx");
  for (const provider of [
    "UiSettingsProvider",
    "ModelCatalogProvider",
    "SettingsDraftProvider",
  ]) {
    assert.match(layout, new RegExp(`<${provider}>`));
  }

  const ui = read("features/settings/store/UiSettingsProvider.tsx");
  const catalog = read("features/settings/store/ModelCatalogProvider.tsx");
  const draft = read("features/settings/store/SettingsDraftProvider.tsx");
  for (const source of [ui, catalog, draft]) {
    assert.match(source, /useMemo/);
    assert.doesNotMatch(source, /\}, \[source\]\)/);
  }
});

test("appearance consumes only the UI preference slice", () => {
  const appearance = read(
    "features/settings/sections/AppearanceSettingsSection.tsx",
  );
  assert.match(appearance, /useUiSettings\(\)/);
  assert.doesNotMatch(appearance, /useSettings\(\)/);
});

test("settings routes import feature sections, never sibling route modules", () => {
  // Each category page wraps its feature section directly. A route importing
  // another route's page would tie one page's bundle to another's file layout —
  // which is how the old stacked document reused these sections before the split.
  for (const [route, section] of [
    ["models", "ModelsSettingsSection"],
    ["chat", "ChatSettingsSection"],
    ["appearance", "AppearanceSettingsSection"],
  ] as const) {
    const page = read(`app/(utility)/settings/${route}/page.tsx`);
    assert.match(
      page,
      new RegExp(`features/settings/sections/${section}`),
      `${route} should import its feature section`,
    );
    assert.doesNotMatch(page, /from "\.\/.+\/page"|from "\.\.\/.+page"/);
  }
});
