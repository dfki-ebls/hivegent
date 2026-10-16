import { Brain, Files, type LucideIcon } from "lucide-react";
import type { ComponentType } from "react";
import { useTranslation } from "react-i18next";

import { type DocumentCanvasTab } from "@/lib/types";
import { useDocumentCanvasStore } from "@/stores/document-canvas-store";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ContextDocuments } from "@/components/documents/ContextDocuments";
import { DocumentManager } from "@/components/documents/DocumentManager";

interface TabDef {
  id: DocumentCanvasTab;
  icon: LucideIcon;
  Panel: ComponentType;
}

// Tabs of the document canvas, in display order. A future view (e.g. a graph or
// database explorer) is a single entry here plus its id in DocumentCanvasTabSchema.
const TABS: TabDef[] = [
  { id: "documents", icon: Files, Panel: DocumentManager },
  { id: "context", icon: Brain, Panel: ContextDocuments },
];

export function DocumentCanvas() {
  const { t } = useTranslation();
  const activeTab = useDocumentCanvasStore((state) => state.activeTab);
  const setActiveTab = useDocumentCanvasStore((state) => state.setActiveTab);

  return (
    <Tabs
      value={activeTab}
      onValueChange={(v) => setActiveTab(v as DocumentCanvasTab)}
      className="h-full gap-0"
    >
      <div className="flex h-15 shrink-0 items-center border-b px-4">
        <TabsList className="w-full sm:w-auto">
          {TABS.map(({ id, icon: Icon }) => (
            <TabsTrigger key={id} value={id} className="sm:flex-none">
              <Icon data-icon="inline-start" />
              {t(($) => $.documents.canvas[id])}
            </TabsTrigger>
          ))}
        </TabsList>
      </div>
      {TABS.map(({ id, Panel }) => (
        <TabsContent key={id} value={id} className="min-h-0 overflow-hidden">
          <Panel />
        </TabsContent>
      ))}
    </Tabs>
  );
}
