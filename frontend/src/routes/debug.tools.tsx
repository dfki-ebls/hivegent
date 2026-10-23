import { createFileRoute } from "@tanstack/react-router";
import { ToolDebugConsole } from "@/components/ToolDebugConsole";

export const Route = createFileRoute("/debug/tools")({
  component: ToolDebugConsole,
});
