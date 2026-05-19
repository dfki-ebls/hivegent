import { cjk } from "@streamdown/cjk";
import { code } from "@streamdown/code";
import { createMathPlugin } from "@streamdown/math";
import { mermaid } from "@streamdown/mermaid";
import type { Components } from "streamdown";
import { Citation } from "@/components/Citation";
import { ImageRef } from "@/components/ImageRef";

export const CITATION_ALLOWED_TAGS = { cite: ["filename", "line"], imgref: ["src"] };
export const CITATION_COMPONENTS: Components = { cite: Citation, imgref: ImageRef };

const math = createMathPlugin({ singleDollarTextMath: true });

export const streamdownPlugins = { cjk, code, math, mermaid };
