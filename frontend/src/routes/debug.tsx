import { createFileRoute } from "@tanstack/react-router";
import { ToolDebugConsole } from "../components/ToolDebugConsole";
import { enforceLogin } from "../oidc";

export const Route = createFileRoute("/debug")({
  beforeLoad: enforceLogin,
  component: ToolDebugConsole,
});
