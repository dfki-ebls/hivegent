import { Brain, Files, type LucideIcon } from "lucide-react";
import type { ComponentType } from "react";

import { type DocumentCanvasTab } from "../../lib/types";
import { useDocumentCanvasStore } from "../../stores/document-canvas-store";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";
import { ContextDocuments } from "./ContextDocuments";
import { DocumentManager } from "./DocumentManager";

interface TabDef {
  id: DocumentCanvasTab;
  label: string;
  icon: LucideIcon;
  Panel: ComponentType;
}

// Tabs of the document canvas, in display order. A future view (e.g. a graph or
// database explorer) is a single entry here plus its id in DocumentCanvasTabSchema.
const TABS: TabDef[] = [
  { id: "documents", label: "Documents", icon: Files, Panel: DocumentManager },
  { id: "context", label: "Context", icon: Brain, Panel: ContextDocuments },
];

export function DocumentCanvas() {
  const activeTab = useDocumentCanvasStore((state) => state.activeTab);
  const setActiveTab = useDocumentCanvasStore((state) => state.setActiveTab);

  return (
    <Tabs
      value={activeTab}
      onValueChange={(v) => setActiveTab(v as DocumentCanvasTab)}
      className="h-full gap-0"
    >
      <div className="shrink-0 border-b px-4 flex items-center h-15">
        <TabsList className="w-full sm:w-auto">
          {TABS.map(({ id, label, icon: Icon }) => (
            <TabsTrigger key={id} value={id} className="flex-1 sm:flex-none gap-2">
              <Icon className="h-4 w-4" />
              {label}
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
