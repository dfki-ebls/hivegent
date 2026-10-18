# License notices shipped in the image.
{
  stdenv,
  linkFarm,
  writeText,
  spdx-license-list-data,
  version,
  license,
}:
let
  repo = "https://github.com/dfki-ebls/hivegent";
  doc = "usr/share/doc/hivegent";
  installable = "git+${repo}?ref=refs/tags/v${version}#packages.${stdenv.hostPlatform.system}.docker";
in
linkFarm "hivegent-notices-${version}" {
  "${doc}/LICENSE" = ../LICENSE;
  "usr/share/licenses" = "${spdx-license-list-data.text}/text";
  "${doc}/NOTICE" = writeText "NOTICE" ''
    Hivegent ${version} is licensed under the ${license.fullName} in LICENSE.
    Third-party components and their licenses are listed in the SBOM:
      ${repo}/releases/download/v${version}/hivegent-${version}.cdx.json
    License texts are in /usr/share/licenses, the *.dist-info directories, and the web app at /licenses.txt.

    Corresponding source per GPL-3.0 section 6(d) is the repository at tag v${version}, whose lockfiles pin every component:
      ${repo}/tree/v${version}
    LGPL-2.0-only and LGPL-2.1-only libraries are conveyed under GPL-3.0 per section 3 of those licenses.

    List all sources:
      nix derivation show --recursive '${installable}'
    Rebuild from source:
      nix build '${installable}' --option substitute false
  '';
}
