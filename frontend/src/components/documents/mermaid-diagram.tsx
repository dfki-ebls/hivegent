import { Streamdown } from "streamdown";

import { STREAMDOWN_PLUGINS, rehypePlugins } from "@/components/chat/markdown/config";

const PLUGINS = { mermaid: STREAMDOWN_PLUGINS.mermaid };
const REHYPE_PLUGINS = rehypePlugins();

/** A Mermaid diagram rendered the way the chat renders one, a default export to load it lazily. */
export default function MermaidDiagram({ source }: { source: string }) {
  return (
    <Streamdown plugins={PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
      {["```mermaid", source, "```"].join("\n")}
    </Streamdown>
  );
}
