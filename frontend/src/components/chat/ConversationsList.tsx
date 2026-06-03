import { CheckIcon, MessageSquare, PencilIcon, SparklesIcon, TrashIcon, XIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { buildAuxLlmConfig } from "@/lib/api";
import { useConversationsStore } from "@/stores/conversations-store";
import { useSettingsStore } from "@/stores/settings-store";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

// --- Utility functions ---

function formatRelativeTime(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSecs = Math.floor(diffMs / 1000);
  const diffMins = Math.floor(diffSecs / 60);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffSecs < 60) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

// --- State display components ---

function LoadingState() {
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      Loading conversations...
    </div>
  );
}

interface ErrorStateProps {
  message: string;
  onRetry: () => void;
}

function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
      <p className="text-sm text-destructive">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-muted-foreground">
      <MessageSquare className="h-8 w-8" />
      <p className="text-sm">No conversations yet</p>
      <p className="text-xs">Start a new chat to begin</p>
    </div>
  );
}

// --- Inline title editor ---

interface TitleEditorProps {
  value: string;
  onChange: (value: string) => void;
  onSave: () => void;
  onCancel: () => void;
}

function TitleEditor({ value, onChange, onSave, onCancel }: TitleEditorProps) {
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") onSave();
    else if (e.key === "Escape") onCancel();
  };

  return (
    <div
      role="toolbar"
      tabIndex={-1}
      className="flex items-center gap-1"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        className="h-6 text-sm"
      />
      <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onSave}>
        <CheckIcon className="h-3 w-3" />
      </Button>
      <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onCancel}>
        <XIcon className="h-3 w-3" />
      </Button>
    </div>
  );
}

// --- Conversation action buttons ---

interface ConversationActionsProps {
  isGenerating: boolean;
  onEdit: () => void;
  onGenerate: () => void;
  onDelete: () => void;
}

function ConversationActions({
  isGenerating,
  onEdit,
  onGenerate,
  onDelete,
}: ConversationActionsProps) {
  return (
    <div
      role="toolbar"
      tabIndex={-1}
      className="absolute right-2 top-2 flex items-center gap-1 rounded bg-background/90 p-0.5"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onEdit} title="Edit title">
        <PencilIcon className="h-3 w-3" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6"
        onClick={onGenerate}
        disabled={isGenerating}
        title="Generate title with AI"
      >
        <SparklesIcon className={`h-3 w-3 ${isGenerating ? "animate-pulse" : ""}`} />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6 text-destructive hover:text-destructive"
        onClick={onDelete}
        title="Delete conversation"
      >
        <TrashIcon className="h-3 w-3" />
      </Button>
    </div>
  );
}

// --- Conversation item ---

interface ConversationItemProps {
  title: string;
  updatedAt: string;
  messageCount: number;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onUpdateTitle: (title: string) => void;
  onGenerateTitle: () => Promise<void>;
}

function ConversationItem({
  title,
  updatedAt,
  messageCount,
  isActive,
  onSelect,
  onDelete,
  onUpdateTitle,
  onGenerateTitle,
}: ConversationItemProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(title);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isHovered, setIsHovered] = useState(false);

  const handleSaveEdit = () => {
    if (editValue.trim() !== title) {
      onUpdateTitle(editValue.trim());
    }
    setIsEditing(false);
  };

  const handleCancelEdit = () => {
    setEditValue(title);
    setIsEditing(false);
  };

  const handleGenerateTitle = async () => {
    setIsGenerating(true);
    try {
      await onGenerateTitle();
    } finally {
      setIsGenerating(false);
    }
  };

  const handleStartEdit = () => {
    setEditValue(title);
    setIsEditing(true);
  };

  return (
    <div
      // Cannot use <button> because the item contains nested action buttons
      // and an inline title editor input.
      // eslint-disable-next-line jsx-a11y/prefer-tag-over-role
      role="button"
      tabIndex={0}
      className={`group relative w-full rounded-lg border p-3 transition-colors cursor-pointer text-left ${
        isActive
          ? "border-primary bg-primary/5"
          : "border-transparent hover:border-border hover:bg-muted/50"
      }`}
      onClick={() => !isEditing && onSelect()}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && !isEditing) {
          e.preventDefault();
          onSelect();
        }
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div className="flex items-start gap-2">
        <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          {isEditing ? (
            <TitleEditor
              value={editValue}
              onChange={setEditValue}
              onSave={handleSaveEdit}
              onCancel={handleCancelEdit}
            />
          ) : (
            <p className="truncate text-sm font-medium">{title || "Untitled"}</p>
          )}
          <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
            <span>{formatRelativeTime(updatedAt)}</span>
            <span>·</span>
            <span>{messageCount} messages</span>
          </div>
        </div>
      </div>

      {isHovered && !isEditing && (
        <ConversationActions
          isGenerating={isGenerating}
          onEdit={handleStartEdit}
          onGenerate={handleGenerateTitle}
          onDelete={onDelete}
        />
      )}
    </div>
  );
}

interface ConversationsListProps {
  currentConversationId?: string;
  onConversationSelect: (id: string) => void;
  onActiveConversationDeleted: () => void;
}

export function ConversationsList({
  currentConversationId,
  onConversationSelect,
  onActiveConversationDeleted,
}: ConversationsListProps) {
  const {
    conversations,
    isLoading,
    error,
    fetchConversations,
    deleteConversation,
    updateTitle,
    generateTitle,
  } = useConversationsStore();

  const { overrides } = useSettingsStore();

  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null);

  useEffect(() => {
    void fetchConversations();
  }, [fetchConversations]);

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    // Deleting the open conversation leaves the chat view bound to an id the
    // server no longer knows; start a fresh draft so its stale content and
    // dangling route don't linger.
    const wasActive = pendingDelete.id === currentConversationId;
    await deleteConversation(pendingDelete.id);
    setPendingDelete(null);
    if (wasActive) onActiveConversationDeleted();
  };

  const handleGenerateTitle = async (id: string) => {
    await generateTitle(id, buildAuxLlmConfig(overrides));
  };

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState message={error} onRetry={fetchConversations} />;
  if (conversations.length === 0) return <EmptyState />;

  return (
    <div className="h-full space-y-2 overflow-y-auto p-2">
      {conversations.map((conversation) => (
        <ConversationItem
          key={conversation.id}
          title={conversation.title}
          updatedAt={conversation.updated_at}
          messageCount={conversation.message_count}
          isActive={conversation.id === currentConversationId}
          onSelect={() => onConversationSelect(conversation.id)}
          onDelete={() =>
            setPendingDelete({ id: conversation.id, title: conversation.title })
          }
          onUpdateTitle={(title) => updateTitle(conversation.id, title)}
          onGenerateTitle={() => handleGenerateTitle(conversation.id)}
        />
      ))}

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete
              {pendingDelete?.title ? ` "${pendingDelete.title}"` : " this conversation"} and all
              its messages. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void confirmDelete()}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
