// Parses one original off the main thread, loading only the library its kind needs.
import type { WorkerParsed, WorkerReply, WorkerRequest } from "@/lib/original-preview";

const parse: { [K in keyof WorkerParsed]: (blob: Blob) => Promise<WorkerParsed[K]> } = {
  docx: async (blob) => {
    const { default: mammoth } = await import("mammoth");
    const { value } = await mammoth.convertToHtml({ arrayBuffer: await blob.arrayBuffer() });

    return value;
  },
  xlsx: async (blob) => (await import("read-excel-file/web-worker")).default(blob),
};

addEventListener("message", async ({ data }: MessageEvent<WorkerRequest>) => {
  let reply: WorkerReply<WorkerParsed[WorkerRequest["kind"]]>;

  try {
    reply = { value: await parse[data.kind](data.blob) };
  } catch (error) {
    reply = { error: error instanceof Error ? error.message : String(error) };
  }

  postMessage(reply);
});
