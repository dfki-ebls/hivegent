import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownText } from "@/components/chat/markdown/MarkdownText";

describe("MarkdownText", () => {
  it("renders fenced code as a code block and leaves inline code inline", () => {
    const { container } = render(
      <MarkdownText>{"Call `run()` like this:\n\n```python\nrun()\n```\n"}</MarkdownText>,
    );
    const block = container.querySelector('[data-slot="code-block"]');

    expect(block?.querySelector('[data-slot="code-block-title"]')?.textContent).toBe("python");
    expect(block?.querySelector('[data-slot="code-block-content"]')?.textContent).toBe("run()");
    expect(container.querySelector('[data-streamdown="inline-code"]')?.textContent).toBe("run()");
  });
});
