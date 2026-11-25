import { CircleAlertIcon, FileText, ImageIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { type AssetEntry } from "@/api/documents";
import { previewText } from "@/lib/utils";
import { type ContentModel, type SidebarTab, type View } from "@/components/documents/dialog/model";
import { SidebarItem } from "@/components/documents/dialog/parts";
import { StatusPanel } from "@/components/common/status-panel";
import { Badge } from "@/components/ui/badge";
import { ItemContent, ItemDescription, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

/** The full-document entry plus the chunks or extracted assets of a document, selecting what the main pane shows. */
export function DocumentSidebar({
  view,
  onSelect,
  sidebarTab,
  onSidebarTabChange,
  contentModel,
  isManagedMode,
  hasAssets,
  assets,
  assetsLoading,
}: {
  view: View;
  onSelect: (view: View) => void;
  sidebarTab: SidebarTab;
  onSidebarTabChange: (tab: SidebarTab) => void;
  contentModel: ContentModel;
  isManagedMode: boolean;
  hasAssets: boolean;
  assets: AssetEntry[] | undefined;
  assetsLoading: boolean;
}) {
  const { t } = useTranslation();

  if (contentModel.error) {
    return (
      <aside className="flex w-56 shrink-0 border-r">
        <StatusPanel icon={CircleAlertIcon} title={contentModel.error} />
      </aside>
    );
  }

  if (isManagedMode && !contentModel.metadata && !hasAssets) return null;

  return (
    <aside className="flex min-h-0 w-56 shrink-0 flex-col gap-2 border-r p-2">
      <SidebarItem active={view.mode === "full-doc"} onClick={() => onSelect({ mode: "full-doc" })}>
        <ItemMedia variant="icon">
          <FileText />
        </ItemMedia>
        <ItemContent>
          <ItemTitle>{t(($) => $.options.chunkPosition.fullDocument)}</ItemTitle>
        </ItemContent>
      </SidebarItem>

      {hasAssets ? (
        <Tabs value={sidebarTab} onValueChange={(value) => onSidebarTabChange(value as SidebarTab)}>
          <TabsList className="w-full">
            <TabsTrigger value="chunks">{t(($) => $.documents.dialog.chunksTab)}</TabsTrigger>
            <TabsTrigger value="assets">{t(($) => $.documents.dialog.assetsTab)}</TabsTrigger>
          </TabsList>
        </Tabs>
      ) : (
        contentModel.chunks.length > 0 && <Separator />
      )}

      <div className="lazy-blocks flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
        {sidebarTab === "chunks" &&
          contentModel.chunks
            .filter((item) => !item.isFullDocument)
            .map((item) => {
              const active = view.mode === "chunk" && item.id === view.id;

              return (
                <SidebarItem
                  key={item.id}
                  active={active}
                  onClick={() => onSelect({ mode: "chunk", id: item.id })}
                >
                  <ItemContent className="min-w-0">
                    <ItemTitle>
                      <Badge variant={active ? "default" : "outline"}>{item.badge}</Badge>
                      {item.detail && (
                        <span className="text-xs font-normal text-muted-foreground">
                          {item.detail}
                        </span>
                      )}
                    </ItemTitle>
                    <ItemDescription className="line-clamp-1 text-xs">
                      {previewText(item.content)}
                    </ItemDescription>
                  </ItemContent>
                </SidebarItem>
              );
            })}

        {sidebarTab === "assets" &&
          (assetsLoading ? (
            <Skeleton className="h-12" />
          ) : (
            assets?.map((asset, index) => (
              <SidebarItem
                key={asset.path}
                active={view.mode === "asset" && view.index === index}
                onClick={() => onSelect({ mode: "asset", index })}
              >
                <ItemMedia variant="icon">
                  <ImageIcon />
                </ItemMedia>
                <ItemContent className="min-w-0">
                  <ItemTitle className="max-w-full">{asset.name}</ItemTitle>
                  <ItemDescription className="line-clamp-1 text-xs">
                    {asset.description || t(($) => $.documents.dialog.noDescription)}
                  </ItemDescription>
                </ItemContent>
              </SidebarItem>
            ))
          ))}
      </div>
    </aside>
  );
}
