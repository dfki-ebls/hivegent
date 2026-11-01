import type { ComponentProps } from "react";
import { DialogContent } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

/** Dialog content whose header and footer stay put while the body between them scrolls. */
export function ScrollDialogContent({ className, ...props }: ComponentProps<typeof DialogContent>) {
  return <DialogContent className={cn("flex max-h-[90dvh] flex-col", className)} {...props} />;
}

/** The scrolling body of a `ScrollDialogContent`, whose scrollbar sits at the dialog's edge. */
export function DialogBody({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn("-mx-6 flex min-h-0 flex-col gap-4 overflow-y-auto px-6", className)}
      {...props}
    />
  );
}
