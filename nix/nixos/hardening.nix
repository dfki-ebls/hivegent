# Shared systemd sandboxing baseline for the Hivegent units (backend, bridge).
# Merge into a unit's `serviceConfig` with `//` and override the few knobs a
# specific service needs (e.g. the backend sets `PrivateDevices = false` for its
# CUDA models). Runtime/identity settings — `Type`, `Restart`, timeouts,
# `DynamicUser`, `StateDirectory`, `ExecStart`, `SocketBindAllow` — stay per-unit.
#
# Deliberately no `MemoryDenyWriteExecute`: both services run interpreters/JITs
# (CPython C extensions, the V8 JIT) that need writable+executable pages.
{
  UMask = "0077";

  CapabilityBoundingSet = "";
  AmbientCapabilities = "";
  NoNewPrivileges = true;

  # Secure default; the backend overrides to `false` for GPU character devices.
  PrivateDevices = true;
  PrivateIPC = true;
  PrivateMounts = true;
  PrivateTmp = true;
  PrivateUsers = true;

  ProtectClock = true;
  ProtectControlGroups = true;
  ProtectHome = true;
  ProtectHostname = true;
  ProtectKernelLogs = true;
  ProtectKernelModules = true;
  ProtectKernelTunables = true;
  ProtectProc = "invisible";
  ProtectSystem = "strict";

  LockPersonality = true;
  RemoveIPC = true;
  RestrictNamespaces = true;
  RestrictRealtime = true;
  RestrictSUIDSGID = true;

  # Only bind the port a unit explicitly allows via `SocketBindAllow`.
  SocketBindDeny = "any";
  RestrictAddressFamilies = [
    "AF_INET"
    "AF_INET6"
    "AF_UNIX"
  ];

  SystemCallArchitectures = "native";
  SystemCallFilter = [
    "@system-service"
    "~@privileged"
    "~@resources"
  ];
  SystemCallErrorNumber = "EPERM";
}
