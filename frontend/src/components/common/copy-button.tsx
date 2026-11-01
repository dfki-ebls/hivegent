import { CheckIcon, CopyIcon } from "lucide-react";
import { type ComponentProps, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";

interface CopyButtonProps extends Omit<ComponentProps<typeof Button>, "value" | "onClick"> {
  value: string;
}

/** Copies `value` and confirms it with a check for two seconds, localized unlike the generated one. */
export function CopyButton({
  value,
  variant = "ghost",
  size = "icon-xs",
  className,
  ...props
}: CopyButtonProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;

    const timeout = setTimeout(() => setCopied(false), 2000);

    return () => clearTimeout(timeout);
  }, [copied]);

  // Outside a secure context the clipboard API is missing, which rejects here.
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      toast.add({ type: "error", title: t(($) => $.common.states.copyFailed) });
    }
  };

  return (
    <>
      <Button
        variant={variant}
        size={size}
        className={cn("hover:bg-foreground/5 dark:hover:bg-foreground/10", className)}
        onClick={() => void copy()}
        {...props}
        aria-label={copied ? t(($) => $.common.states.copied) : t(($) => $.common.actions.copy)}
      >
        {copied ? <CheckIcon /> : <CopyIcon />}
      </Button>
      <span aria-live="polite" className="sr-only">
        {copied ? t(($) => $.common.states.copied) : ""}
      </span>
    </>
  );
}
