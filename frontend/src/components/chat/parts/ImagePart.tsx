import { useTranslation } from "react-i18next";
import { Attachment, AttachmentMedia } from "@/components/ui/attachment";

interface ImagePartProps {
  url: string;
  filename?: string;
}

/**
 * An image the user attached to a turn.
 * Rendered from the persisted data URI, so a reloaded conversation shows
 * what the model was actually looking at rather than a silent gap.
 */
export function ImagePart({ url, filename }: ImagePartProps) {
  const { t } = useTranslation();

  return (
    <Attachment orientation="vertical" title={filename}>
      <AttachmentMedia variant="image">
        <img src={url} alt={filename ?? t(($) => $.chat.messages.attachedImage)} />
      </AttachmentMedia>
    </Attachment>
  );
}
