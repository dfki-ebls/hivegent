# Backend container: the FastAPI service streamed as a layered image. Secure by
# default — runs as the unprivileged `nobody` uid, ships no shell, and carries a
# baked healthcheck. Every runtime path, port, and the image name/tag are
# arguments, so a deployment can rebuild it without editing this file
# (`docker-backend.override { … }`). The stream script prints an image tarball
# to stdout for `docker load` / `skopeo copy`.
{
  lib,
  dockerTools,
  cacert,
  tzdata,
  curlMinimal,
  backend,
  name ? "hivegent-backend",
  tag ? "latest",
  # Listen address inside the container. 0.0.0.0 is the norm here — the proxy is
  # the only thing in front — so the image bakes the bind flag that opts an
  # auth-disabled server out of the loopback-only guard meant for workstations.
  host ? "0.0.0.0",
  port ? 8000,
  # Volume mount point holding the workspace, store, and model caches.
  dataDir ? "/data",
}:
let
  # Docker healthcheck durations are nanoseconds; spell them as seconds.
  seconds = n: n * 1000000000;
in
dockerTools.streamLayeredImage {
  inherit name tag;
  created = "now";
  # fakeNss: /etc/passwd so the service resolves its non-root uid (plus /tmp);
  # cacert: outbound TLS (LLM endpoint, JWKS, web tools); tzdata: timestamps;
  # curlMinimal: the healthcheck below.
  contents = [
    backend
    cacert
    tzdata
    curlMinimal
    dockerTools.fakeNss
  ];
  # The data volume must be owned by the unprivileged runtime user; /tmp backs
  # libreoffice/docling scratch files (mktemp) and must be world-writable.
  fakeRootCommands = ''
    mkdir -p .${dataDir} ./tmp
    chmod 1777 ./tmp
    chown -R 65534:65534 .${dataDir}
  '';
  config = {
    # Drop root: run as the `nobody` uid/gid provided by fakeNss.
    User = "65534:65534";
    Entrypoint = [ (lib.getExe' backend "hivegent") ];
    Cmd = [
      "serve"
      "--host"
      host
      "--port"
      (toString port)
      "--allow-unsafe-auth-disabled-bind"
    ];
    Env = [
      "HOME=${dataDir}"
      "HF_HOME=${dataDir}/huggingface"
      "PYTHONUNBUFFERED=1"
      "HIVEGENT_DATA_DIR=${dataDir}"
      "SSL_CERT_FILE=${cacert}/etc/ssl/certs/ca-bundle.crt"
    ];
    WorkingDir = dataDir;
    ExposedPorts."${toString port}/tcp" = { };
    Volumes.${dataDir} = { };
    Healthcheck = {
      Test = [
        "CMD"
        (lib.getExe curlMinimal)
        "-fsS"
        "http://localhost:${toString port}/api/health"
      ];
      Interval = seconds 30;
      Timeout = seconds 5;
      # Generous: first start loads embedding/document models.
      StartPeriod = seconds 60;
      Retries = 5;
    };
  };
  meta = {
    description = "Hivegent backend service container";
    maintainers = with lib.maintainers; [ mirkolenz ];
    platforms = lib.platforms.linux;
  };
}
