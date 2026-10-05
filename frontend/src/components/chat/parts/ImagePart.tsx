import { useTranslation } from "react-i18next";

interface ImagePartProps {
  url: string;
  filename?: string;
}

/**
 * An image the user attached to a turn.
 *
 * Rendered from the persisted data URI, so a reloaded conversation shows
 * what the model was actually looking at rather than a silent gap.
 */
export function ImagePart({ url, filename }: ImagePartProps) {
  const { t } = useTranslation();

  return (
    <img
      src={url}
      alt={filename ?? t(($) => $.chat.messages.attachedImage)}
      title={filename}
      className="max-h-64 w-auto max-w-full rounded-md border object-contain"
    />
  );
}
