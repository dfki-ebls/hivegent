# Repairs and statements applied to every document this build emits, producers
# and the merged product alike. Each rule is idempotent because merged inputs
# are normalized a second time.

def acknowledge($value):
  if has("license") then .license.acknowledgement = $value else .acknowledgement = $value end;

def contact($creator):
  {name: $creator.name}
  + if $creator.email? then
      {contact: [{name: $creator.name, email: $creator.email}]}
    else
      {url: [$creator.url]}
    end;

# The registry page a purl implies, for the ecosystems that have one. A purl
# naming no ecosystem below yields nothing, since a guessed URL is worse than
# an absent one.
def registry_home:
  ((.purl // "") | capture("^pkg:(?<type>[^/]+)/(?<name>[^@]+)")) as $purl
  | {
      pypi: "https://pypi.org/project/\($purl.name)/",
      npm: "https://www.npmjs.com/package/\($purl.name)",
      golang: "https://pkg.go.dev/\($purl.name)",
      nix: "https://search.nixos.org/packages",
    }[$purl.type];

# Descriptive reference types first, then any remaining reference, so the most
# informative URL wins without a second scan of the list.
def component_home:
  [([.externalReferences[]? | select(.type | IN("website", "vcs", "documentation"))]
    + [.externalReferences[]?])[]
   | .url
   | select(test("^https?://"))][0]
  // registry_home;

def with_creator:
  [.authors[]? | select(.email? or .url?)][0] as $author
  | (component_home // null) as $url
  | ($author // (if $url then {name: (.group // .name), url: $url} else null end)) as $creator
  | if (.manufacturer? or .supplier?) or $creator == null then . else .manufacturer = contact($creator) end;

# Use license data from manifests or detected evidence for BSI's original and
# distribution roles. A LicenseRef records genuinely unknown licenses.
def bsi_licenses($unknown):
  ((.licenses // .evidence.licenses // [])
   | map(if .expression? then {expression: .expression}
         elif .license.id? then {license: {id: .license.id}}
         else {expression: $unknown} end)
   | unique_by(tojson)) as $licenses
  | (if $licenses == [] then [{expression: $unknown}] else $licenses end) as $declared
  | .licenses = [["declared", "concluded"][] as $role | $declared[] | acknowledge($role)];

def components:
  .metadata.component, (.components[]? | recurse(.components[]?));

$profile[0] as $profile
| contact($profile.creator) as $manufacturer
| .metadata.component["bom-ref"] as $self

# bombon repeats the root as one of its own components.
| del(.components[]? | select(.["bom-ref"] == $self))

# npm accepts SCP shorthand for repository URLs, but CycloneDX requires an IRI.
| (.. | objects | select(has("externalReferences")).externalReferences[].url)
  |= sub("^git@(?<host>[^:]+):"; "git+ssh://git@\(.host)/")

# BSI maps the document creator to manufacturer. Authors stay present for
# consumers that use CycloneDX's more general authorship field.
| .metadata.manufacturer = $manufacturer
| .metadata.authors = [$profile.creator]
| .metadata.component.manufacturer = $manufacturer
| .metadata.component.purl //= $profile.product.purl
| .metadata.component.licenses //= [{license: {id: $profile.product.license}}]

# Package metadata supplies the project URL wherever it has no direct creator
# contact. An explicit LicenseRef records genuinely unknown licenses without
# inventing a licence grant.
| components |= (
    with_creator
    | bsi_licenses("LicenseRef-\($profile.product.pname)-unknown")
  )

# Connect every component in the closure to the document root.
| ([.dependencies[]?.dependsOn[]?] | unique) as $needed
| ([.dependencies[]?.ref] - $needed - [$self]) as $orphans
| (.dependencies[]? | select(.ref == $self) | .dependsOn) |= ((. // []) + $orphans | unique)

| .compositions = [
    {
      aggregate: "complete",
      assemblies: [$self],
      dependencies: [.dependencies[]?.ref],
    }
  ]
