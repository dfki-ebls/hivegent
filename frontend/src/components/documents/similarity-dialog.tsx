import { useTranslation } from "react-i18next";

import { ScrollDialogContent } from "@/components/scroll-dialog-content";
import { SimilarityView } from "@/components/documents/similarity-view";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { basename } from "@/lib/utils";

/** The similarity of the folder case base at *path*. */
export function SimilarityDialog({
  open,
  onOpenChange,
  path,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  path: string;
}) {
  const { t } = useTranslation();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <ScrollDialogContent className="h-[90dvh] gap-0 overflow-hidden p-0 sm:max-w-[min(96rem,calc(100%-4rem))]">
        <DialogHeader className="px-6 pt-6 pb-4">
          <DialogTitle className="truncate pr-8">
            {t(($) => $.documents.similarity.tab)}: {basename(path)}
          </DialogTitle>
        </DialogHeader>
        {path && <SimilarityView path={path} />}
      </ScrollDialogContent>
    </Dialog>
  );
}
