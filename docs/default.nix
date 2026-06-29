{
  lib,
  stdenvNoCC,
  writeShellApplication,
  python3,
  mdbook,
  mdbook-mermaid,
  # Base path the book is hosted under, must start and end with "/". Sets
  # mdbook's `site-url` so absolute references (canonical links, the 404 page)
  # resolve when the book is mounted on a subpath such as "/docs/" behind Caddy
  # or "/<repo>/" on GitHub Pages. In-page navigation is relative and works
  # under any prefix regardless of this value.
  sitePath ? "/",
}:
stdenvNoCC.mkDerivation (finalAttrs: {
  name = "book";
  nativeBuildInputs = [
    mdbook
    mdbook-mermaid
  ];

  # The canonical logo lives at the repo-level `assets/` and is symlinked into
  # each `src/<lang>/assets/` (mdbook only emits assets that sit inside a book's
  # own source tree), so the build source must span both directories for those
  # symlinks to resolve in the sandbox.
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ./.
      ../assets/logo.svg
    ];
  };
  sourceRoot = "${finalAttrs.src.name}/docs";

  MDBOOK_OUTPUT__HTML__SITE_URL = sitePath;

  # One shared `book.toml`; each language is a sibling source tree under `src/`.
  # English uses the defaults, every other language overrides `src`/`language`
  # via the environment so the config stays single-sourced.
  #
  # `mdbook-mermaid install` drops the mermaid runtime (mermaid.min.js,
  # mermaid-init.js) referenced by `book.toml` next to it; the preprocessor is
  # already configured there, so the call is idempotent and only writes the
  # assets both language builds then copy in.
  buildPhase = ''
    runHook preBuild
    mdbook-mermaid install .
    mdbook build -d book/en
    MDBOOK_BOOK__SRC=src/de MDBOOK_BOOK__LANGUAGE=de mdbook build -d book/de
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    mv book $out
    cp assets/redirect.html $out/index.html
    runHook postInstall
  '';

  passthru.serve = writeShellApplication {
    name = "serve";
    runtimeInputs = [ python3 ];
    text = ''
      python -m http.server \
        --bind 127.0.0.1 \
        --directory ${finalAttrs.finalPackage}
    '';
  };
})
