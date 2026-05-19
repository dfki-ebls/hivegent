import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface UserTextPartProps {
  text: string;
  messageId: string;
  isEditing: boolean;
  onCancelEdit: () => void;
  onSubmitEdit: (messageId: string, newText: string) => void;
}

export function UserTextPart({
  text,
  messageId,
  isEditing,
  onCancelEdit,
  onSubmitEdit,
}: UserTextPartProps) {
  const [editText, setEditText] = useState(text);

  useEffect(() => {
    setEditText(text);
  }, [text, isEditing]);

  if (isEditing) {
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
              if (editText.trim()) {
                onSubmitEdit(messageId, editText);
              }
            }
          }}
        />
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onCancelEdit}>
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={() => {
              if (editText.trim()) {
                onSubmitEdit(messageId, editText);
              }
            }}
          >
            Submit
          </Button>
        </div>
      </div>
    );
  }

  return <div className="whitespace-pre-wrap">{text}</div>;
}
