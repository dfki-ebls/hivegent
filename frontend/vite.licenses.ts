import { readdirSync, readFileSync } from "node:fs";
import { findPackageJSON } from "node:module";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { isCSSRequest, type Plugin } from "vite";

// The root of a package in node_modules, skipping tool caches such as `.vite` or `.nitro`.
const PACKAGE_DIR = /^.*\/node_modules\/(?:@[^/]+\/)?[^/.][^/]*/;

const STYLESHEET_IMPORTS = /@(?:import|plugin)\s+["']([^"']+)["']/g;

const LICENSE_FILE = /^(licen[cs]e|copying)/i;

/**
 * The packages a stylesheet pulls in through `@import` and `@plugin`, following
 * its relative imports. Tailwind inlines them itself, so they never reach the
 * module graph. Node resolves them, since a package of only CSS exports nothing
 * under the conditions of a script.
 */
function stylesheetPackages(file: string): string[] {
  return [...readFileSync(file, "utf8").matchAll(STYLESHEET_IMPORTS)].flatMap(
    ([, specifier = ""]) => {
      if (!specifier.startsWith(".")) return findPackageJSON(specifier, pathToFileURL(file)) ?? [];

      return specifier.endsWith(".css")
        ? stylesheetPackages(resolve(dirname(file), specifier))
        : [];
    },
  );
}

/** The notice of the package in `dir`, keyed by name and version. */
function packageNotice(dir: string): [string, string] {
  const {
    name,
    version = "0.0.0",
    license,
  } = JSON.parse(readFileSync(join(dir, "package.json"), "utf8")) as {
    name: string;
    version?: string;
    license?: string;
  };
  const file = readdirSync(dir).find((entry) => LICENSE_FILE.test(entry));
  const heading = `## ${name} - ${version}${license ? ` (${license.trim()})` : ""}`;

  return [
    `${name}@${version}`,
    file ? `${heading}\n\n${readFileSync(join(dir, file), "utf8").trim()}` : heading,
  ];
}

/**
 * Notices of the third-party packages a build inlines, written as `fileName` to
 * the output of each environment that inlines any, after an optional `preamble`
 * such as the app's own license. In place of Vite's
 * `build.license`, which lists only the module graph of the main build, this
 * also lists the packages behind emitted assets such as fonts, those Tailwind
 * inlines into a client stylesheet, those of worker bundles, which are built on
 * their own, and those of another environment's output a bundle inlines, as
 * Nitro does with the `ssr` environment's. Packages left external keep their
 * own license files.
 */
export function thirdPartyLicenses(fileName: string, preamble?: string): Plugin {
  // Package roots and output directory per environment, which worker builds share under the name `client`.
  const packages = new Map<string, Set<string>>();
  const outDirs = new Map<string, string>();

  const plugin: Plugin = {
    name: "third-party-licenses",
    config: () => ({ worker: { plugins: () => [plugin] } }),
    generateBundle: {
      // After Vite has emitted the stylesheets.
      order: "post",
      handler(_, bundle) {
        const { name, config } = this.environment;
        const dirs = packages.get(name) ?? new Set<string>();
        packages.set(name, dirs);
        outDirs.set(name, resolve(config.root, config.build.outDir));

        const ids = Object.values(bundle).flatMap((output) =>
          output.type === "chunk"
            ? output.moduleIds
            : output.originalFileNames.map((file) => resolve(config.root, file)),
        );
        // Server builds emit stylesheets only for their manifests.
        const stylesheets =
          config.consumer === "client"
            ? [
                ...new Set(
                  ids.filter(
                    (id) =>
                      isCSSRequest(id) && id.startsWith("/") && !id.includes("/node_modules/"),
                  ),
                ),
              ]
            : [];

        for (const id of [
          ...ids,
          ...stylesheets.flatMap((id) => stylesheetPackages(id.split("?")[0] ?? id)),
        ]) {
          const inlined = [...outDirs].find(
            ([env, outDir]) => env !== name && id.startsWith(`${outDir}/`),
          );
          const dir = PACKAGE_DIR.exec(id)?.[0];

          if (inlined) for (const found of packages.get(inlined[0]) ?? []) dirs.add(found);
          else if (dir) dirs.add(dir);
        }

        // Each worker ends before the build that imports it does.
        if (config.isWorker || dirs.size === 0) return;

        const notices = [...new Map([...dirs].map(packageNotice))].sort(([a], [b]) =>
          a.localeCompare(b),
        );
        const sections = [preamble?.trim(), "# Licenses", ...notices.map(([, notice]) => notice)];

        this.emitFile({
          type: "asset",
          fileName,
          source: `${sections.filter(Boolean).join("\n\n")}\n`,
        });
      },
    },
  };

  return plugin;
}
