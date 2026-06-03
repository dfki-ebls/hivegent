import { createFileRoute } from "@tanstack/react-router";
import { BackendReadyGate } from "../components/BackendReadyGate";
import { ToolDebugConsole } from "../components/ToolDebugConsole";
import { enforceLogin } from "../oidc";

export const Route = createFileRoute("/debug")({
  beforeLoad: enforceLogin,
  component: () => (
    <BackendReadyGate>
      <ToolDebugConsole />
    </BackendReadyGate>
  ),
});
