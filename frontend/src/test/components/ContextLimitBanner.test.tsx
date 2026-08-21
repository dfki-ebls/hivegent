import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ContextLimitBanner } from "@/components/chat/ContextLimitBanner";

describe("ContextLimitBanner", () => {
  it("lets the user choose compaction", () => {
    const onCompact = vi.fn<() => void>();
    render(
      <ContextLimitBanner
        disabled={false}
        onCompact={onCompact}
        onDismiss={vi.fn<() => void>()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Compact and retry" }));

    expect(onCompact).toHaveBeenCalledOnce();
  });
});
