# CycloneDX SBOMs for the artifacts this repo ships, one document per package,
# since a bill of materials describes one product.
{
  lib,
  bombon,
  pkgs,
  cyclonedx-cli,
  cyclonedx-spec,
  jsonschema,
  jq,
  linkFarm,
  runCommand,
  cyclonedx-gomod,
  cyclonedx-python,
  backend,
  frontend,
  bridge,
  smokescreen,
  docs,
}:
let
  # Every document is validated before it leaves the build, against the schema
  # from the specification repo rather than a producer's private copy of it,
  # since a lockfile is what keeps a schema current. Not `cyclonedx validate`:
  # it asserts no `format`, so the URL repaired below passes it, and its SPDX
  # list is older than the identifiers nixpkgs states. `jv` picks the schema by
  # the version the document states and maps the schema's own base URL onto the
  # directory it came from, so its siblings resolve without a network.
  schemas = "${cyclonedx-spec}/schema";

  validated =
    name: nativeBuildInputs: command:
    runCommand name
      {
        nativeBuildInputs = nativeBuildInputs ++ [
          jq
          jsonschema
        ];
      }
      ''
        ${command}

        jv --assert-format \
          --map "http://cyclonedx.org/schema/=${schemas}" \
          "${schemas}/bom-$(jq -r .specVersion "$out").schema.json" \
          "$out"
      '';

  # Two producer defects, repaired for every document rather than per producer.
  # bombon names the package both as `metadata.component` and as a component of
  # itself under the same `bom-ref`, so the merge below would nest each artifact
  # under a copy of itself. And `npm sbom` copies a dependency's package.json
  # `repository.url` verbatim, so the SCP shorthand npm allows there arrives as
  # a URL that is no `iri-reference` and Dependency-Track rejects the document
  # over it; `git+ssh://git@github.com/owner/repo.git` is how npm spells the
  # same remote. Only an external reference is rewritten, since a URL elsewhere
  # should fail the gate rather than be repaired by a rule aimed past it.
  normalize =
    name: bom:
    validated name [ ] ''
      jq '
        .metadata.component["bom-ref"] as $self
        | del(.components[] | select(.["bom-ref"] == $self))
        | (.. | objects | select(has("externalReferences")).externalReferences[].url)
          |= sub("^git@(?<host>[^:]+):"; "git+ssh://git@\(.host)/")
      ' ${bom} > "$out"
    '';

  # An ecosystem that vendors its dependencies into a single derivation needs a
  # tool of its own to describe them; bombon merges whatever a package carries
  # in the `bombonVendoredSbom` passthru, and ships the npm producer as is.
  # The two below it does not: they run over the built package rather than
  # rebuilding it, and write their document to `$sbom`.
  npmSbom = package: bombon.passthruVendoredSbom.npm package { inherit pkgs; };

  withVendoredSbom =
    package: env: command:
    package.overrideAttrs (old: {
      passthru = (old.passthru or { }) // {
        bombonVendoredSbom = runCommand "${package.pname}-bombon-vendored-sbom" env ''
          mkdir -p "$out"
          sbom="$out/${package.pname}.cdx.json"
          ${command}
        '';
      };
    });

  # bombon's Go producer leaves license detection off, since resolving a
  # license means fetching the module and Go serves neither its graph nor its
  # licenses out of a `vendor/` tree. The package's `goModuleCache` is that
  # fetch, offline. Detected licenses land under `evidence`, where a license
  # matched by its text rather than read from a declaration belongs.
  goSbom =
    package:
    withVendoredSbom package
      {
        nativeBuildInputs = [
          cyclonedx-gomod
          package.go
        ];
        GOFLAGS = "-mod=mod";
        GOPROXY = "file://${package.goModuleCache}";
        GOSUMDB = "off";
      }
      ''
        export HOME="$TMPDIR"
        export GOPATH="$TMPDIR/go"

        # `-version` only has to parse: the main module is the one module the
        # cache cannot serve, and bombon takes the component from Nix anyway.
        cyclonedx-gomod bin \
          -licenses \
          -json \
          -noserial \
          -notimestamp \
          -output-version 1.5 \
          -version v0.0.0 \
          -output "$sbom" \
          ${lib.getExe package}
      '';

  # The Python producer bombon does not ship. uv2nix builds each dependency as
  # its own derivation, so the closure already names them, but those derivations
  # carry no `meta`: the licenses and PyPI names exist only in the dist-info the
  # venv installs, which is what `cyclonedx-py` reads.
  #
  # `--pyproject` is what joins the two graphs: bombon remaps a vendored
  # document's `metadata.component` onto the Nix component that carries it, and
  # a venv scan names no root, so without it the Python tree hangs off a
  # component nothing references.
  #
  # `--gather-license-texts` covers the packages whose `License:` field is prose
  # rather than an SPDX identifier ("Apache 2.0 License"), which cyclonedx-py
  # will not guess an identifier from; the `LICENSE` they ship is the
  # authoritative answer. It gathers one for every package though, so `jq` keeps
  # a text only where the component states its license no other way: 9 of them
  # rather than 69, which is 8 licenses gained for 24 KB.
  pythonSbom =
    package:
    withVendoredSbom package
      {
        nativeBuildInputs = [
          cyclonedx-python
          jq
        ];
      }
      ''
        # The interpreter being read inherits the setup hook's PYTHONPATH, which
        # would put cyclonedx-py's own dependencies in the document as the app's.
        env -u PYTHONPATH cyclonedx-py environment \
          --pyproject ${package.pyproject} \
          --output-reproducible \
          --gather-license-texts \
          --of JSON \
          --sv 1.5 \
          -o gathered.cdx.json \
          ${package.venv}/bin/python

        jq '(.components[]? | select(has("licenses")) | .licenses) |=
              (if any(.license.text | not) then map(select(.license.text | not)) else . end)' \
          gathered.cdx.json > "$sbom"
      '';
  boms = lib.mapAttrs (path: normalize (baseNameOf path)) {
    # bombon walks `drvAttrs`, so a store path that reaches the closure only
    # interpolated into the wrapper has no derivation to describe it. Naming them
    # one by one is what makes them describable: joining them into a single path
    # first would hand bombon a `paths` list it does not see derivations in, and
    # everything behind it loses its metadata.
    "components/backend.cdx.json" = bombon.buildBom (pythonSbom backend) {
      extraPaths = backend.runtimeInputs ++ [ backend.tessdata ];
      # A venv symlinks into each dependency's own store path, so every Python
      # package is a runtime reference of the app as well as a component of the
      # dist-info SBOM. bombon deduplicates on the purl, and `pkg:nix/httpx` is
      # not `pkg:pypi/httpx`, so the Nix view is dropped: it carries neither the
      # license nor the name the ecosystem knows the package by.
      excludes = map (package: lib.escapeRegex package.outPath) backend.venvPackages;
    };
    "components/frontend.cdx.json" = bombon.buildBom (npmSbom frontend) { };
    "components/bridge.cdx.json" = bombon.buildBom (npmSbom bridge) { };
    "components/smokescreen.cdx.json" = bombon.buildBom (goSbom smokescreen) { };
    "components/docs.cdx.json" = bombon.buildBom docs { };
  };
in
linkFarm "hivegent-sbom" (
  boms
  // {
    # The deployment as one document, and the one to reach for: a root
    # component with each artifact and its dependencies beneath it. Hierarchical
    # rather than flat so a component stays attributable to the artifact that
    # needs it, and the per-artifact documents stay beside it under
    # `components/`, since a document describes one product and these are the
    # ones to hand to whoever consumes an artifact alone. The first-party
    # packages share one version, and the application is where it is stated.
    # No `--output-version`: the newest specification the tools write is what
    # BSI TR-03183-2 asks of an SBOM under the Cyber Resilience Act.
    "hivegent.cdx.json" = validated "hivegent.cdx.json" [ cyclonedx-cli ] ''
      cyclonedx merge \
        --hierarchical \
        --name hivegent \
        --version ${backend.version} \
        --output-file "$out" \
        --input-files ${lib.concatStringsSep " " (lib.attrValues boms)}
    '';
  }
)
