import { Pencil, Sparkles, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { type AssetDescriptionChange, type AssetEntry } from "@/api/documents";
import {
  DescriptionBody,
  DescriptionHeading,
  DescriptionPane,
  FileMetaBadges,
} from "@/components/documents/dialog/parts";
import { PendingIcon } from "@/components/common/spinner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

/** The description of an extracted asset with its file metadata, editable when *editable*. */
export function AssetPane({
  asset,
  editable,
  editing,
  draft,
  pendingAction,
  onDraftChange,
  onEditingChange,
  onChange,
}: {
  asset: AssetEntry;
  editable: boolean;
  editing: boolean;
  draft: string;
  pendingAction: AssetDescriptionChange["action"] | null;
  onDraftChange: (draft: string) => void;
  onEditingChange: (editing: boolean) => void;
  onChange: (change: AssetDescriptionChange) => void;
}) {
  const { t } = useTranslation();
  const assetActionPending = pendingAction !== null;

  return (
    <DescriptionPane>
      <DescriptionHeading
        actions={
          editable &&
          !editing && (
            <div className="flex gap-1">
              <Button
                variant="ghost"
                size="xs"
                disabled={assetActionPending}
                // Alt-text uses the auxiliary model, like the import-time describe pipeline.
                onClick={() =>
                  onChange({
                    action: "generate",
                    name: asset.name,
                  })
                }
              >
                <PendingIcon pending={pendingAction === "generate"} icon={Sparkles} />
                {t(($) => $.documents.dialog.generate)}
              </Button>
              <Button
                variant="ghost"
                size="xs"
                disabled={assetActionPending}
                onClick={() => {
                  onDraftChange(asset.description);
                  onEditingChange(true);
                }}
              >
                <Pencil data-icon="inline-start" />
                {t(($) => $.common.actions.edit)}
              </Button>
              {asset.description && (
                <Button
                  variant="ghost"
                  size="xs"
                  disabled={assetActionPending}
                  onClick={() => onChange({ action: "delete", name: asset.name })}
                >
                  <PendingIcon pending={pendingAction === "delete"} icon={Trash2} />
                  {t(($) => $.common.actions.delete)}
                </Button>
              )}
            </div>
          )
        }
      />

      {editing ? (
        <>
          <Textarea
            aria-label={t(($) => $.documents.dialog.descriptionHeading)}
            value={draft}
            onChange={(e) => onDraftChange(e.target.value)}
            className="min-h-36 font-mono text-sm"
          />
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => onEditingChange(false)}>
              {t(($) => $.common.actions.cancel)}
            </Button>
            <Button
              size="sm"
              disabled={assetActionPending}
              onClick={() =>
                onChange({
                  action: "save",
                  name: asset.name,
                  description: draft,
                })
              }
            >
              <PendingIcon pending={pendingAction === "save"} />
              {t(($) => $.common.actions.save)}
            </Button>
          </div>
        </>
      ) : (
        <DescriptionBody markdown={asset.description} />
      )}

      <FileMetaBadges name={asset.name} mediaType={asset.media_type} sizeBytes={asset.size_bytes} />
    </DescriptionPane>
  );
}
