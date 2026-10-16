import type { ComponentProps } from "react";
import { InputGroupTextarea } from "@/components/ui/input-group";
import { cn } from "@/lib/utils";

/**
 * The message field of a chat form, where Enter submits the form and Shift+Enter breaks the line.
 * A key handler passed in runs first and can claim the key with `preventDefault`.
 */
export function ChatTextarea({
  className,
  onKeyDown,
  ...props
}: ComponentProps<typeof InputGroupTextarea>) {
  return (
    <InputGroupTextarea
      className={cn("max-h-48 min-h-16 p-3", className)}
      onKeyDown={(event) => {
        onKeyDown?.(event);

        if (
          !event.defaultPrevented &&
          event.key === "Enter" &&
          !event.shiftKey &&
          !event.nativeEvent.isComposing
        ) {
          event.preventDefault();
          event.currentTarget.form?.requestSubmit();
        }
      }}
      {...props}
    />
  );
}
