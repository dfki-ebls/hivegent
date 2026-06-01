import type { Components } from "streamdown";
import { Citation } from "@/components/Citation";
import { ImageRef } from "@/components/ImageRef";
import { STREAMDOWN_PLUGINS } from "@/components/chat/markdown/config";

export const CITATION_ALLOWED_TAGS = { cite: ["filename", "line"], imgref: ["src"] };
export const CITATION_COMPONENTS: Components = { cite: Citation, imgref: ImageRef };

export const streamdownPlugins = STREAMDOWN_PLUGINS;
