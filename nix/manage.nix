# `hivegent-manage`, the one host command running the `hivegent` CLI exactly as
# the service runs it, shared by the NixOS module and the container image.
# Commands that refuse while a server runs on the database, `reset`, stop the
# backend around them and start it again if it ran.
# The supervisor is the caller's: shell snippets saying how to run the CLI
# with the service's identity and how to query, stop, and start the backend.
{ lib, writeShellApplication }:
{
  runtimeInputs,
  # Runs the CLI with the service's environment and identity, the arguments follow.
  run,
  # Succeeds while the backend runs.
  running,
  stop,
  start,
  # Checks run first, such as for privileges.
  prelude ? "",
}:
writeShellApplication {
  name = "hivegent-manage";
  inherit runtimeInputs;
  text = ''
    ${prelude}

    case "''${1-}" in
      reset) ;;
      *) exec ${run} "$@" ;;
    esac

    if ${running}; then
      trap ${lib.escapeShellArg start} EXIT
      ${stop}
    fi

    ${run} "$@"
  '';
}
