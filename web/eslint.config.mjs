import nextConfig from "eslint-config-next";
import i18nPlugin from "./eslint/i18n-plugin.mjs";

const config = [
  ...nextConfig,
  {
    files: ["app/**/*.{ts,tsx}", "components/**/*.{ts,tsx}"],
    plugins: {
      i18n: i18nPlugin,
    },
    rules: {
      // During migration keep as warning; change to "error" once phase2/3 complete.
      //
      // ``学研助手`` is the product name: the same string in every locale, so
      // translating it would be wrong rather than missing.
      "i18n/no-literal-ui-text": ["warn", { allow: ["学研助手"] }],
    },
  },
  {
    // Internal component-preview page — not linked from any surface (reached
    // by URL only), and its annotations are developer notes in one language by
    // design. There is no user-facing copy here for the i18n rule to enforce.
    files: ["app/**/avatar-preview/**"],
    rules: {
      "i18n/no-literal-ui-text": "off",
    },
  },
  {
    // Vendored upstream source — see `web/vendor/thinking-orbs/index.ts` for
    // provenance and the list of local changes.
    //
    // Both hooks it ships seed their state from `matchMedia` with a
    // synchronous `setState` in the effect body, which the React Compiler rule
    // rejects. Rewriting them would deepen every future re-sync in exchange
    // for one render at mount, so the rule is off here rather than the file
    // being skipped: everything else still gets linted.
    files: ["vendor/**/*.{ts,tsx}"],
    rules: {
      "react-hooks/set-state-in-effect": "off",
    },
  },
  {
    // ``.next-*`` covers every build output, including the throwaway dist dirs
    // a second dev server needs (KAGWEB_NEXT_DIST_DIR, see next.config.js):
    // without it, running one turns `npx eslint .` — a CI gate — red with
    // hundreds of errors from generated code.
    ignores: [
      "node_modules/**",
      ".next/**",
      ".next-*/**",
      "dist/**",
      "out/**",
      "tmp/**",
      "coverage/**",
      "playwright-report/**",
      "test-results/**",
      "contracts/generated/.tmp/**",
    ],
  },
];

export default config;
