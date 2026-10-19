import { XIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  Attachment,
  AttachmentAction,
  AttachmentActions,
  AttachmentContent,
  AttachmentMedia,
  AttachmentTitle,
} from "@/components/ui/attachment";
import { InputGroupAddon } from "@/components/ui/input-group";
import type { ComposerFile } from "@/stores/composer-store";

interface AttachedFilesProps {
  files: ComposerFile[];
  onRemove: (id: string) => void;
}

/** The images attached to the draft, each with a button to remove it. */
export function AttachedFiles({ files, onRemove }: AttachedFilesProps) {
  const { t } = useTranslation();

  if (files.length === 0) return null;

  return (
    <InputGroupAddon align="block-start" className="flex-wrap">
      {files.map(({ id, part: file }) => (
        <Attachment key={id} size="sm">
          <AttachmentMedia variant="image">
            <img src={file.url} alt={file.filename ?? t(($) => $.chat.messages.attachedImage)} />
          </AttachmentMedia>
          {file.filename && (
            <AttachmentContent>
              <AttachmentTitle>{file.filename}</AttachmentTitle>
            </AttachmentContent>
          )}
          <AttachmentActions>
            <AttachmentAction
              aria-label={
                file.filename
                  ? t(($) => $.chat.composer.removeAttachment, { name: file.filename })
                  : t(($) => $.chat.composer.removeImage)
              }
              onClick={() => onRemove(id)}
            >
              <XIcon />
            </AttachmentAction>
          </AttachmentActions>
        </Attachment>
      ))}
    </InputGroupAddon>
  );
}
