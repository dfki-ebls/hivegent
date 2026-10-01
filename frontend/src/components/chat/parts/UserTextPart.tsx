import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface UserTextPartProps {
  text: string;
  messageId: string;
  isEditing: boolean;
  onCancelEdit: () => void;
  onSubmitEdit: (messageId: string, newText: string) => void;
}

export function UserTextPart({ isEditing, ...props }: UserTextPartProps) {
  if (isEditing) return <UserTextEditor {...props} />;

  return <div className="whitespace-pre-wrap">{props.text}</div>;
}

// Mounted only while editing, so every edit starts from the current text.
function UserTextEditor({
  text,
  messageId,
  onCancelEdit,
  onSubmitEdit,
}: Omit<UserTextPartProps, "isEditing">) {
  const [editText, setEditText] = useState(text);

  const submit = () => {
    if (editText.trim()) onSubmitEdit(messageId, editText);
  };

  return (
    <div className="space-y-2">
      <Textarea
        value={editText}
        onChange={(e) => setEditText(e.target.value)}
        className="min-h-[80px] resize-y"
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            onCancelEdit();
          } else if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="flex gap-2">
        <Button variant="outline" size="sm" onClick={onCancelEdit}>
          Cancel
        </Button>
        <Button size="sm" onClick={submit}>
          Submit
        </Button>
      </div>
    </div>
  );
}
