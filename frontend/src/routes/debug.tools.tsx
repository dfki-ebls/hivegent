import { createFileRoute } from "@tanstack/react-router";
import { ToolDebugConsole } from "@/components/tool-debug-console";

export const Route = createFileRoute("/debug/tools")({
  component: ToolDebugConsole,
});
