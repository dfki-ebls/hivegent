import { render } from "@testing-library/react";
import { expect, it } from "vitest";

import { MarkdownText } from "@/components/chat/markdown/markdown-text";

it("renders fenced code as a code block and no remote image or raw HTML beyond markdown", () => {
  const { container } = render(
    <MarkdownText>
      {
        '```python\nrun()\n```\n\n![chart](https://evil.example/a.png) <img src="https://evil.example/b.png"> <details><summary>more</summary>text</details>'
      }
    </MarkdownText>,
  );
  const block = container.querySelector('[data-slot="code-block"]');

  expect(block?.querySelector('[data-slot="code-block-title"]')?.textContent).toBe("python");
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("details")).toBeNull();
  expect(container.textContent).toContain("chart");
});
