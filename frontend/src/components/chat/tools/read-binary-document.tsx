import { FileImage, FileText, Paperclip } from "lucide-react";
import { Tool, ToolContent, ToolHeader } from "@/components/ai-elements/tool";
import { ToolParameters } from "@/components/ToolDisplay";
import { parseJson, type ToolPart } from "@/lib/chat/tool-part";
import { formatFileSize } from "@/lib/utils";

interface BinaryReadResult {
  file_path: string;
  media_type: string;
  size: number;
  pages: number[];
}

function isBinaryReadResult(value: unknown): value is BinaryReadResult {
  if (value == null || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.file_path === "string" &&
    typeof v.media_type === "string" &&
    typeof v.size === "number" &&
    Array.isArray(v.pages)
  );
}

interface ReadBinaryDocumentToolProps {
  part: ToolPart;
  metadata: unknown;
}

export function ReadBinaryDocumentTool({ part, metadata }: ReadBinaryDocumentToolProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const input = parseJson<Record<string, unknown>>(part.input);
  const result = isBinaryReadResult(metadata) ? metadata : null;
  const Icon = result?.media_type === "application/pdf" ? FileText : FileImage;

  return (
    <Tool defaultOpen={false}>
      <ToolHeader title="Read Binary Document" type="tool-read_binary_document" state={state} />
      <ToolContent>
        {input && <ToolParameters params={input} />}
        {result && (
          <div className="flex items-start gap-3 rounded-md border bg-muted/40 p-3 text-sm">
            <Icon className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1 space-y-1">
              <div className="truncate font-medium" title={result.file_path}>
                {result.file_path}
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                <span>{result.media_type}</span>
                <span>{formatFileSize(result.size)}</span>
                {result.pages.length > 0 && <span>pages {result.pages.join(", ")}</span>}
                <span className="flex items-center gap-1">
                  <Paperclip className="size-3" />
                  attached to model
                </span>
              </div>
            </div>
          </div>
        )}
      </ToolContent>
    </Tool>
  );
}
