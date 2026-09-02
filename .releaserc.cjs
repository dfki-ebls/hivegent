module.exports = {
  branches: [
    { name: "main" },
    { name: "next" },
    { name: "+([0-9])?(.{+([0-9]),x}).x" },
    { name: "dev", prerelease: true },
    { name: "beta", prerelease: true },
    { name: "alpha", prerelease: true },
  ],
  plugins: [
    [
      "@semantic-release/commit-analyzer",
      {
        preset: "conventionalcommits",
        parserOpts: {
          noteKeywords: ["BREAKING CHANGE", "BREAKING-CHANGE"],
        },
      },
    ],
    [
      "@semantic-release/release-notes-generator",
      {
        preset: "conventionalcommits",
        parserOpts: {
          noteKeywords: ["BREAKING CHANGE", "BREAKING-CHANGE", "NOTABLE CHANGE", "NOTABLE-CHANGE"],
        },
      },
    ],
    [
      "@semantic-release/changelog",
      {
        changelogTitle: "# Changelog",
      },
    ],
    [
      "@semantic-release/npm",
      {
        npmPublish: false,
        pkgRoot: "frontend",
      },
    ],
    [
      "@semantic-release/npm",
      {
        npmPublish: false,
        pkgRoot: "bridge",
      },
    ],
    [
      "@cihelper/semanticrelease-plugin-uv",
      {
        uvPublish: false,
        pkgRoot: "backend",
      },
    ],
    [
      // Built during `prepare`, after the manifests are bumped and before
      // anything is published: the document states the version being released,
      // and a build that fails means no release is created at all.  A release
      // that ships these artifacts has to build them regardless -- the SBOM is
      // read off what was built, not produced independently of it.
      "@semantic-release/exec",
      {
        prepareCmd: "nix build .#sbom --out-link sbom",
      },
    ],
    [
      "@semantic-release/github",
      {
        failComment: false,
        successComment: false,
        addReleases: "bottom",
        assets: [
          {
            path: "sbom/hivegent.cdx.json",
            name: "hivegent-${nextRelease.version}.cdx.json",
            label: "Software Bill of Materials (CycloneDX)",
          },
        ],
      },
    ],
    [
      "semantic-release-major-tag",
      {
        customTags: ["v${major}", "v${major}.${minor}"],
      },
    ],
    [
      "@semantic-release/git",
      {
        message: "chore(release): ${nextRelease.version}",
        assets: [
          "CHANGELOG.md",
          "frontend/package.json",
          "frontend/package-lock.json",
          "bridge/package.json",
          "bridge/package-lock.json",
          "backend/pyproject.toml",
          "backend/uv.lock",
        ],
      },
    ],
  ],
};
