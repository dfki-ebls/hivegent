// Only `lint` is used: the catalogs are typed TypeScript modules under
// `src/i18n/locales`, so key parity is enforced by the type checker instead.
// A plain object, since the CLI comes from the Nix dev shell, not npm.
export default {
  locales: ["en", "de"],
  extract: {
    input: ["src/**/*.{ts,tsx}"],
    ignore: [
      "src/components/ui/**",
      "src/components/ai-elements/**",
      "src/test/**",
      "src/routeTree.gen.ts",
    ],
    output: "src/i18n/locales/{{language}}/{{namespace}}.json",
  },
  lint: {
    ignoredAttributes: [
      "className",
      "data-testid",
      "data-slot",
      "variant",
      "size",
      "side",
      "align",
      "type",
      "rel",
      "target",
      "id",
      "name",
      "value",
      "key",
      "role",
      "lang",
    ],
  },
};
