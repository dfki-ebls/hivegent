import { useBlocker } from "@tanstack/react-router";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface StreamingNavGuardProps {
  isStreaming: boolean;
  onStop: () => void;
}

/**
 * Confirm before leaving a chat that is still streaming, then abort the run.
 *
 * SPA navigation does not abort an in-flight fetch on its own, so without this
 * a run would keep streaming (and holding an inference slot) after the user
 * moved on. `useBlocker` intercepts every in-app navigation while streaming and
 * only stops the run once the user confirms — matching the browser's own
 * abort-on-unload, which `enableBeforeUnload` mirrors with a native prompt for
 * tab close / reload so both paths behave the same.
 */
export function StreamingNavGuard({ isStreaming, onStop }: StreamingNavGuardProps) {
  const { status, proceed, reset } = useBlocker({
    shouldBlockFn: () => isStreaming,
    enableBeforeUnload: isStreaming,
    withResolver: true,
  });

  return (
    <AlertDialog
      open={status === "blocked"}
      onOpenChange={(open) => {
        if (!open) reset?.();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Stop the current response?</AlertDialogTitle>
          <AlertDialogDescription>
            This chat is still generating a response. Leaving now stops it. Any partial
            answer is saved, so you can return and continue this conversation later.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Stay</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => {
              onStop();
              proceed?.();
            }}
          >
            Leave and stop
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
