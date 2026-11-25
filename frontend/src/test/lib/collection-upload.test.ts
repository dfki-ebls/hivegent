import JSZip from "jszip";
import { expect, it } from "vitest";

import { buildCollectionZip } from "@/lib/collection-upload";

it("dates each entry at its file's time, clamped to the DOS epoch", async () => {
  const modified = Date.UTC(2020, 4, 17, 12, 30, 2);
  const inner = new JSZip();
  inner.file("inner.md", "# inner", { date: new Date(modified) });
  const archive = new File([await inner.generateAsync({ type: "blob" })], "inner.zip");

  const built = await buildCollectionZip(
    {
      looseFiles: [
        new File(["# a"], "a.md", { lastModified: modified }),
        new File(["# old"], "old.md", { lastModified: 0 }),
      ],
      directories: [],
      zipFiles: [archive],
    },
    "collection",
  );
  const { files } = await JSZip.loadAsync(built);

  expect(files["a.md"]?.date.getTime()).toBe(modified);
  expect(files["inner.md"]?.date.getTime()).toBe(modified);
  expect(files["old.md"]?.date.getTime()).toBe(Date.UTC(1980, 0, 1));
});
