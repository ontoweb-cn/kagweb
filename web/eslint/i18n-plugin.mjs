/**
 * Minimal ESLint rule set for i18n hygiene.
 *
 * NOTE: We start with "warn" in eslint.config.mjs during migration.
 * After migration, switch to "error" for hard enforcement.
 */

const UI_ATTRS = new Set(["title", "placeholder", "alt", "aria-label"]);

function hasHumanText(s) {
  return /[A-Za-z\u4e00-\u9fff]/.test(s);
}

export default {
  rules: {
    "no-literal-ui-text": {
      meta: {
        type: "problem",
        docs: {
          description:
            "Disallow literal UI text in JSX (use i18n t() instead).",
        },
        schema: [
          {
            type: "object",
            properties: {
              /**
               * Exact strings that are deliberately not UI copy — brand names,
               * file names, and other text that must read the same in every
               * locale. Translating them would be wrong, not missing.
               */
              allow: { type: "array", items: { type: "string" } },
            },
            additionalProperties: false,
          },
        ],
        messages: {
          jsxText: "Avoid literal UI text in JSX. Use t(\"...\") instead.",
          jsxAttr:
            "Avoid literal UI text in JSX attribute '{{name}}'. Use t(\"...\") instead.",
        },
      },
      create(context) {
        const allowed = new Set(context.options[0]?.allow ?? []);
        return {
          JSXText(node) {
            const raw = node.value ?? "";
            const text = raw.replace(/\s+/g, " ").trim();
            if (!text) return;
            // allow single separators
            if (text.length <= 1) return;
            if (!hasHumanText(text)) return;
            if (allowed.has(text)) return;
            context.report({ node, messageId: "jsxText" });
          },
          JSXAttribute(node) {
            const name = node?.name?.name;
            if (typeof name !== "string") return;
            if (!UI_ATTRS.has(name)) return;
            const v = node.value;
            if (!v) return;
            // Only flag literal string values: title="..."
            if (v.type === "Literal" && typeof v.value === "string") {
              if (!hasHumanText(v.value)) return;
              if (allowed.has(v.value.trim())) return;
              context.report({ node: v, messageId: "jsxAttr", data: { name } });
            }
          },
        };
      },
    },
  },
};
