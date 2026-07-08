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
  /** Human-readable label, shown beside the icon in the batch bar. */
  label: string;
  /** Button variant for the batch bar. */
  variant: "secondary" | "destructive";
  /** When true, the action only appears when a selected file has an original binary. */
  requiresOriginal: boolean;
}

/**
 * The bulk document actions rendered by the batch selection bar
 * ({@link BulkActionBar}) as icon + text buttons. Single-document actions live
 * in the document dialog and the tree row's inline delete button.
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
