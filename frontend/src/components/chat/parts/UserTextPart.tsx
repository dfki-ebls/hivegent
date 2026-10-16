import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { ChatTextarea } from "@/components/chat/composer/ChatTextarea";
import { InputGroup, InputGroupAddon, InputGroupButton } from "@/components/ui/input-group";

interface UserTextPartProps {
  text: string;
  isEditing: boolean;
  onCancelEdit: () => void;
  onSubmitEdit: (newText: string) => void;
}

/** The user's text as a bubble, or the field that edits it. */
export function UserTextPart({ text, isEditing, onCancelEdit, onSubmitEdit }: UserTextPartProps) {
  if (isEditing) return <UserTextEditor text={text} onCancel={onCancelEdit} onSubmit={onSubmitEdit} />;

  return (
    <Bubble align="end" variant="muted">
      <BubbleContent className="whitespace-pre-wrap">{text}</BubbleContent>
    </Bubble>
  );
}

interface UserTextEditorProps {
  text: string;
  onCancel: () => void;
  onSubmit: (newText: string) => void;
}

// Mounted only while editing, so every edit starts from the current text.
function UserTextEditor({ text, onCancel, onSubmit }: UserTextEditorProps) {
  const { t } = useTranslation();
  const [editText, setEditText] = useState(text);
  const blank = !editText.trim();

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!blank) onSubmit(editText);
  };

  return (
    <form onSubmit={submit} className="w-full">
      <InputGroup>
        <ChatTextarea
          value={editText}
          onChange={(e) => setEditText(e.target.value)}
          aria-label={t(($) => $.chat.messages.editLabel)}
          // oxlint-disable-next-line jsx-a11y/no-autofocus -- the field replaces the bubble the user asked to edit
          autoFocus
          onKeyDown={(e) => e.key === "Escape" && onCancel()}
        />
        <InputGroupAddon align="block-end" className="justify-end">
          <InputGroupButton size="sm" onClick={onCancel}>
            {t(($) => $.common.actions.cancel)}
          </InputGroupButton>
          <InputGroupButton type="submit" size="sm" variant="default" disabled={blank}>
            {t(($) => $.chat.messages.submitEdit)}
          </InputGroupButton>
        </InputGroupAddon>
      </InputGroup>
    </form>
  );
}
