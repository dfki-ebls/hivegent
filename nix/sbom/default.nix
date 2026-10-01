# Reusable BSI TR-03183-2 pipeline for CycloneDX documents built with bombon.
{
  lib,
  cyclonedx-cli,
  cyclonedx-spec,
  callPackage,
  jsonschema,
  jq,
  linkFarm,
  runCommand,
  writeText,
}:
{
  pname,
  version,
  creator,
  license,
  components,
  logicalComponents ? true,
}:
assert lib.assertMsg (
  (creator.email or "") != "" || (creator.url or "") != ""
) "sbom: BSI TR-03183-2 needs a reachable creator, so `creator` needs an `email` or a `url`.";
let
  schemas = "${cyclonedx-spec}/schema";
  sbomqs = callPackage ./sbomqs.nix { };
  profileFile = writeText "sbom-profile.json" (
    lib.toJSON {
      inherit creator;
      product = {
        inherit pname version license;
        purl = "pkg:generic/${pname}@${version}";
      };
    }
  );

  prepare =
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

        jq --slurpfile profile ${profileFile} -f ${./normalize.jq} bom.json > "$out"

        jv --assert-format \
          --map "http://cyclonedx.org/schema/=${schemas}" \
          "${schemas}/bom-$(jq -r .specVersion "$out").schema.json" \
          "$out"
      '';

  documents = lib.mapAttrs (path: bom: prepare (baseNameOf path) [ ] "cp ${bom} bom.json") components;
  productFile = "${pname}.cdx.json";

  product = prepare productFile [ cyclonedx-cli ] ''
    cyclonedx merge \
      --hierarchical \
      --name ${lib.escapeShellArg pname} \
      --version ${lib.escapeShellArg version} \
      --output-file bom.json \
      --input-files ${lib.concatStringsSep " " (lib.attrValues documents)}
  '';

  complianceReport =
    runCommand "${pname}-bsi-v2.1-compliance.json"
      {
        nativeBuildInputs = [
          jq
          sbomqs
        ];
      }
      ''
        sbomqs compliance --bsi-v21 --json ${product} > "$out"
        failures=$(jq -r --slurpfile bom ${product} \
          --argjson logicalComponents ${lib.toJSON logicalComponents} \
          -f ${./compliance.jq} "$out")

        if [ -n "$failures" ]; then
          echo "BSI TR-03183-2 v2.1 required fields are incomplete:" >&2
          echo "$failures" >&2
          exit 1
        fi
      '';
in
# The report is an entry of its own, so realizing the farm realizes it and a
# non-compliant document fails the build before anything can read it.
linkFarm "${pname}-sbom" (
  documents
  // {
    ${productFile} = product;
    "reports/bsi-v2.1.json" = complianceReport;
  }
)
