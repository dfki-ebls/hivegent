import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { formatTarget } from "@/api/documents";
import { errorMessage } from "@/lib/utils";
import { PendingIcon } from "@/components/common/spinner";
import { Button } from "@/components/ui/button";
import { DialogFooter } from "@/components/ui/dialog";
import { Field, FieldDescription, FieldError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useWorkspaces } from "@/hooks/use-workspaces";

/** The edit form of a document, mounted per edit so it starts from *filename* and *initialContent*. */
export function EditPane({
  filename,
  initialContent,
  isNew,
  target,
  onSave,
  onCancel,
  onSaved,
}: {
  filename: string;
  initialContent: string;
  isNew: boolean;
  target?: string;
  onSave?: (filename: string, content: string) => Promise<void>;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const workspaces = useWorkspaces();
  const [editFilename, setEditFilename] = useState(filename);
  const [editContent, setEditContent] = useState(initialContent);

  const save = useMutation({
    mutationFn: async ({ name, content }: { name: string; content: string }) =>
      onSave?.(name, content),
    meta: { inlineError: true },
    onSuccess: onSaved,
  });

  return (
    <>
      <div className="flex min-h-0 flex-1 flex-col gap-2 px-4 pt-2">
        {isNew && (
          <Field>
            <Input
              value={editFilename}
              onChange={(e) => setEditFilename(e.target.value)}
              placeholder={t(($) => $.documents.dialog.filenamePlaceholder)}
              className="text-lg font-semibold"
            />
            {target && (
              <FieldDescription>
                {t(($) => $.documents.dialog.creatingIn, {
                  target: formatTarget(target, workspaces),
                })}
              </FieldDescription>
            )}
          </Field>
        )}
        <Textarea
          value={editContent}
          onChange={(e) => setEditContent(e.target.value)}
          placeholder={t(($) => $.documents.dialog.contentPlaceholder)}
          // A fixed size, as measuring a long document on every keystroke lags.
          className="min-h-0 flex-1 field-sizing-fixed font-mono text-sm"
        />
      </div>
      <DialogFooter className="p-4">
        {save.error && (
          <FieldError className="mr-auto self-center">{errorMessage(save.error)}</FieldError>
        )}
        <Button variant="outline" onClick={onCancel}>
          {t(($) => $.common.actions.cancel)}
        </Button>
        <Button
          onClick={() => save.mutate({ name: editFilename.trim(), content: editContent })}
          disabled={save.isPending || !editFilename.trim()}
        >
          <PendingIcon pending={save.isPending} />
          {t(($) => $.common.actions.save)}
        </Button>
      </DialogFooter>
    </>
  );
}
