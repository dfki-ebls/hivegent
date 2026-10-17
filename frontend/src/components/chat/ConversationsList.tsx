import {
  CheckIcon,
  MessagesSquareIcon,
  PencilIcon,
  SparklesIcon,
  TrashIcon,
  XIcon,
} from "lucide-react";
import { type KeyboardEvent, type Ref, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { keyPrefix } from "@/i18n";
import { buildAuxLlmConfig } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useConversationsStore } from "@/stores/conversations-store";
import { useSettingsStore } from "@/stores/settings-store";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState, LoadingState } from "@/components/EmptyState";
import { REVEAL_ON_HOVER } from "@/components/RowButtons";
import { SearchInput } from "@/components/SearchInput";
import { TooltipButton } from "@/components/TooltipButton";
import { Button } from "@/components/ui/button";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import { useDialogTarget } from "@/hooks/use-dialog-target";
import { useFuzzySearch } from "@/hooks/use-fuzzy-search";
import { formatRelativeTime } from "@/i18n/format";

const T_OPTIONS = keyPrefix(($) => $.chat.conversations);

interface TitleEditorProps {
  value: string;
  onChange: (value: string) => void;
  onSave: () => void;
  onCancel: () => void;
}

function TitleEditor({ value, onChange, onSave, onCancel }: TitleEditorProps) {
  const { t } = useTranslation();
  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter") onSave();
    else if (e.key === "Escape") onCancel();
  };

  return (
    <InputGroup>
      <InputGroupInput
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        aria-label={t(($) => $.chat.conversations.titleLabel)}
        // oxlint-disable-next-line jsx-a11y/no-autofocus -- the field replaces the title the user asked to edit
        autoFocus
      />
      <InputGroupAddon align="inline-end">
        <InputGroupButton
          size="icon-xs"
          aria-label={t(($) => $.common.actions.save)}
          onClick={onSave}
        >
          <CheckIcon />
        </InputGroupButton>
        <InputGroupButton
          size="icon-xs"
          aria-label={t(($) => $.common.actions.cancel)}
          onClick={onCancel}
        >
          <XIcon />
        </InputGroupButton>
      </InputGroupAddon>
    </InputGroup>
  );
}

// Centers the open conversation in its scrolling list once its row mounts.
function centerInList(button: HTMLButtonElement | null): void {
  const row = button?.closest<HTMLElement>("[data-slot=item]");
  const list = row?.parentElement?.parentElement;
  if (!row || !list) return;

  const offset = row.getBoundingClientRect().top - list.getBoundingClientRect().top;
  list.scrollTop += offset - (list.clientHeight - row.offsetHeight) / 2;
}

interface ConversationItemProps {
  ref?: Ref<HTMLButtonElement>;
  title: string;
  updatedAt: string;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onUpdateTitle: (title: string) => void;
  onGenerateTitle: () => Promise<void>;
}

/**
 * One conversation, whose title opens it across the whole row.
 * The actions fade in on hover or focus rather than being hidden, which would drop them out of the tab order,
 * and take no clicks while faded, so a click meant for the row is never theirs.
 */
function ConversationItem({
  ref,
  title,
  updatedAt,
  isActive,
  onSelect,
  onDelete,
  onUpdateTitle,
  onGenerateTitle,
}: ConversationItemProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(title);
  const [isGenerating, setIsGenerating] = useState(false);

  const handleSaveEdit = () => {
    if (editValue.trim() !== title) {
      onUpdateTitle(editValue.trim());
    }

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

  return (
    <Item
      render={<li />}
      size="sm"
      variant={isActive ? "muted" : "default"}
      className="group/row relative hover:bg-muted/50"
    >
      <ItemContent className="min-w-0">
        {isEditing ? (
          <TitleEditor
            value={editValue}
            onChange={setEditValue}
            onSave={handleSaveEdit}
            onCancel={() => setIsEditing(false)}
          />
        ) : (
          <ItemTitle className="w-full">
            <button
              ref={ref}
              type="button"
              className="truncate text-left outline-none after:absolute after:inset-0 after:rounded-2xl focus-visible:after:ring-3 focus-visible:after:ring-ring/30"
              aria-current={isActive ? "page" : undefined}
              onClick={onSelect}
            >
              {title || t(($) => $.untitled)}
            </button>
          </ItemTitle>
        )}
        <ItemDescription className="text-xs">{formatRelativeTime(updatedAt)}</ItemDescription>
      </ItemContent>
      {!isEditing && (
        <ItemActions className={cn("relative", REVEAL_ON_HOVER)}>
          <TooltipButton
            label={t(($) => $.editTitle)}
            size="icon-xs"
            onClick={() => {
              setEditValue(title);
              setIsEditing(true);
            }}
          >
            <PencilIcon />
          </TooltipButton>
          <TooltipButton
            label={t(($) => $.generateTitle)}
            size="icon-xs"
            disabled={isGenerating}
            onClick={handleGenerateTitle}
          >
            {isGenerating ? <Spinner /> : <SparklesIcon />}
          </TooltipButton>
          <TooltipButton label={t(($) => $.delete)} size="icon-xs" destructive onClick={onDelete}>
            <TrashIcon />
          </TooltipButton>
        </ItemActions>
      )}
    </Item>
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
  const { t } = useTranslation();
  const conversations = useConversationsStore((state) => state.conversations);
  const isLoading = useConversationsStore((state) => state.isLoading);
  const error = useConversationsStore((state) => state.error);
  const fetchConversations = useConversationsStore((state) => state.fetchConversations);
  const deleteConversation = useConversationsStore((state) => state.deleteConversation);
  const updateTitle = useConversationsStore((state) => state.updateTitle);
  const generateTitle = useConversationsStore((state) => state.generateTitle);
  const overrides = useSettingsStore((state) => state.overrides);

  const deletion = useDialogTarget({ id: "", title: "" });
  const [deleting, setDeleting] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const matches = useFuzzySearch(conversations, searchQuery, "title");

  useEffect(() => {
    void fetchConversations();
  }, [fetchConversations]);

  const confirmDelete = async () => {
    // Deleting the open conversation leaves the chat view bound to an id the
    // server no longer knows; start a fresh draft so its stale content and
    // dangling route don't linger.
    const wasActive = deletion.target.id === currentConversationId;
    setDeleting(true);

    try {
      await deleteConversation(deletion.target.id);
    } finally {
      setDeleting(false);
      deletion.setOpen(false);
    }

    if (wasActive) onActiveConversationDeleted();
  };

  const handleGenerateTitle = async (id: string) => {
    await generateTitle(id, buildAuxLlmConfig(overrides));
  };

  // A refetch keeps the cached list on screen,
  // so it neither flickers nor loses its scroll position.
  if (isLoading && conversations.length === 0) {
    return <LoadingState className="h-full" />;
  }

  if (error) {
    return (
      <EmptyState title={t(($) => $.common.states.error)} description={error} destructive>
        <Button variant="outline" size="sm" onClick={() => void fetchConversations()}>
          {t(($) => $.common.actions.retry)}
        </Button>
      </EmptyState>
    );
  }

  if (conversations.length === 0) {
    return (
      <EmptyState
        icon={<MessagesSquareIcon />}
        title={t(($) => $.chat.conversations.emptyTitle)}
        description={t(($) => $.chat.conversations.emptyDescription)}
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="p-2 pb-0">
        <SearchInput
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder={t(($) => $.chat.conversations.search)}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {matches.length === 0 ? (
          <EmptyState
            icon={<MessagesSquareIcon />}
            title={t(($) => $.chat.conversations.noMatchesTitle)}
            description={t(($) => $.chat.conversations.noMatchesDescription)}
          />
        ) : (
          <ul className="flex flex-col gap-2.5">
            {matches.map((conversation) => (
              <ConversationItem
                key={conversation.id}
                ref={conversation.id === currentConversationId ? centerInList : undefined}
                title={conversation.title}
                updatedAt={conversation.updated_at}
                isActive={conversation.id === currentConversationId}
                onSelect={() => onConversationSelect(conversation.id)}
                onDelete={() => deletion.show({ id: conversation.id, title: conversation.title })}
                onUpdateTitle={(title) => updateTitle(conversation.id, title)}
                onGenerateTitle={() => handleGenerateTitle(conversation.id)}
              />
            ))}
          </ul>
        )}
      </div>

      <ConfirmDialog
        open={deletion.open}
        onOpenChange={deletion.setOpen}
        title={t(($) => $.chat.conversations.deleteTitle)}
        description={
          deletion.target.title
            ? t(($) => $.chat.conversations.deleteNamed, { title: deletion.target.title })
            : t(($) => $.chat.conversations.deleteUnnamed)
        }
        confirmLabel={t(($) => $.common.actions.delete)}
        busy={deleting}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}
