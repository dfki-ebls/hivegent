{
  buildGo127Module,
  fetchFromGitHub,
  lib,
}:

buildGo127Module (finalAttrs: {
  pname = "sbomqs";
  version = "2.1.2";

  src = fetchFromGitHub {
    owner = "interlynk-io";
    repo = "sbomqs";
    tag = "v${finalAttrs.version}";
    hash = "sha256-S0CcvnvbtHzN7vm5TF8vm0uGEklTjeqcJFcF7/H6l3M=";
  };

  vendorHash = "sha256-EK/2keML/TlKLTppwSKZtkQNfrL/PE+hGKMFSjYDRew=";

  ldflags = [
    "-s"
    "-w"
    "-X sigs.k8s.io/release-utils/version.gitVersion=${finalAttrs.version}"
  ];

  meta = {
    description = "SBOM quality and compliance checker";
    homepage = "https://github.com/interlynk-io/sbomqs";
    license = lib.licenses.asl20;
    mainProgram = "sbomqs";
  };
})
