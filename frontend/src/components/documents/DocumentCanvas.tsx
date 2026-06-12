import { Brain, Files } from "lucide-react";

import { useSettingsStore } from "../../stores/settings-store";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";
import { ContextDocuments } from "./ContextDocuments";
import { DocumentManager } from "./DocumentManager";

export function DocumentCanvas() {
  const documentTab = useSettingsStore((state) => state.documentTab);
  const setDocumentTab = useSettingsStore((state) => state.setDocumentTab);

  return (
    <Tabs
      value={documentTab}
      onValueChange={(v) => setDocumentTab(v as "context" | "documents")}
      className="h-full gap-0"
    >
      <div className="shrink-0 border-b px-4 flex items-center h-15">
        <TabsList className="w-full sm:w-auto">
          <TabsTrigger value="context" className="flex-1 sm:flex-none gap-2">
            <Brain className="h-4 w-4" />
            Context
          </TabsTrigger>
          <TabsTrigger value="documents" className="flex-1 sm:flex-none gap-2">
            <Files className="h-4 w-4" />
            Documents
          </TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="context" className="min-h-0 overflow-hidden">
        <ContextDocuments />
      </TabsContent>
      <TabsContent value="documents" className="min-h-0 overflow-hidden">
        <DocumentManager />
      </TabsContent>
    </Tabs>
  );
}
