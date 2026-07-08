import type { LucideIcon } from "lucide-react";
import { Download, RotateCcw, Scissors, Trash2 } from "lucide-react";

/** Identifiers for document file-operation actions. */
type DocumentActionId = "rechunk" | "reconvert" | "download" | "delete";

/** A document file-operation action definition. */
interface DocumentAction {
  /** Unique key. */
  id: DocumentActionId;
  /** Lucide icon component. */
  icon: LucideIcon;
  /** Human-readable label (used in batch bar with icon). */
  label: string;
  /** Button variant for the batch bar. Inline buttons always use "ghost". */
  variant: "secondary" | "destructive";
  /** When true, the action only appears for files that have an original binary. */
  requiresOriginal: boolean;
}

/**
 * Canonical list of document file-operation actions.
 *
 * Inline file rows render these as icon-only buttons.
 * The batch selection bar renders these as icon + text buttons.
 */
const DOCUMENT_ACTIONS: readonly DocumentAction[] = [
  {
    id: "rechunk",
    icon: Scissors,
    label: "Rechunk",
    variant: "secondary",
    requiresOriginal: false,
  },
  {
    id: "reconvert",
    icon: RotateCcw,
    label: "Reconvert",
    variant: "secondary",
    requiresOriginal: true,
  },
  {
    id: "download",
    icon: Download,
    label: "Download",
    variant: "secondary",
    requiresOriginal: true,
  },
  { id: "delete", icon: Trash2, label: "Delete", variant: "destructive", requiresOriginal: false },
];

export { DOCUMENT_ACTIONS };
export type { DocumentAction, DocumentActionId };
