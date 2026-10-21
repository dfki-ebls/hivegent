import {
  BookmarkIcon,
  BookmarkPlusIcon,
  BracesIcon,
  FileDiffIcon,
  FileImageIcon,
  FilePenIcon,
  FilePlusIcon,
  FileTextIcon,
  FolderInputIcon,
  FolderTreeIcon,
  GlobeIcon,
  type LucideIcon,
  MessageCircleQuestionIcon,
  MessagesSquareIcon,
  SearchIcon,
  SquareTerminalIcon,
  TableIcon,
  TelescopeIcon,
  TextSearchIcon,
  Trash2Icon,
  WrenchIcon,
} from "lucide-react";
import { isCatalogKey } from "@/i18n";
import type { ToolName } from "@/lib/chat/tool-part";

/** The icon that names what each built-in tool does, at the start of its trace line. */
const TOOL_ICONS: Record<ToolName, LucideIcon> = {
  apply_changes: FileDiffIcon,
  ask_user: MessageCircleQuestionIcon,
  delete_documents: Trash2Icon,
  edit_document: FilePenIcon,
  explore: TelescopeIcon,
  get_conversation: MessagesSquareIcon,
  grep: TextSearchIcon,
  jq: BracesIcon,
  list_conversations: MessagesSquareIcon,
  list_documents: FolderTreeIcon,
  move_documents: FolderInputIcon,
  query_table: TableIcon,
  read_binary_document: FileImageIcon,
  read_document: FileTextIcon,
  read_memory: BookmarkIcon,
  run_python: SquareTerminalIcon,
  search: SearchIcon,
  web_fetch: GlobeIcon,
  web_search: GlobeIcon,
  write_document: FilePlusIcon,
  write_memory: BookmarkPlusIcon,
};

/** The icon of a tool, a wrench for tools without one such as MCP tools. */
export function ToolIcon({ name }: { name: string }) {
  const Icon = isCatalogKey(TOOL_ICONS, name) ? TOOL_ICONS[name] : WrenchIcon;

  return <Icon />;
}
