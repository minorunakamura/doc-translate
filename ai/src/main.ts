import { readFileSync } from "node:fs";
import { getSupportedThinkingLevels } from "@earendil-works/pi-ai/models";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import { encodeRecord, type JSONLRecord, readRecords } from "./jsonl.js";

function version(url: URL): string {
  const manifest: unknown = JSON.parse(readFileSync(url, "utf8"));
  if (
    typeof manifest !== "object" ||
    manifest === null ||
    !("version" in manifest) ||
    typeof manifest.version !== "string"
  )
    throw new Error("Invalid package version");
  return manifest.version;
}

const versions = {
  helper_version: version(new URL("../package.json", import.meta.url)),
  library_version: version(
    new URL(
      "../package.json",
      import.meta.resolve("@earendil-works/pi-ai/models"),
    ),
  ),
};

function metadata(request: JSONLRecord): JSONLRecord["payload"] {
  const models = builtinModels();
  if (
    request.type === "providers" &&
    Object.keys(request.payload).length === 0
  ) {
    return {
      ...versions,
      providers: models.getProviders().map(({ id, name }) => ({ id, name })),
    };
  }
  if (
    request.type === "models" &&
    Object.keys(request.payload).length === 1 &&
    typeof request.payload.provider === "string"
  ) {
    const provider = request.payload.provider;
    if (!models.getProvider(provider)) throw new Error("unknown_provider");
    return {
      ...versions,
      provider,
      models: models.getModels(provider).map((model) => ({
        id: model.id,
        name: model.name,
        image_input: model.input.includes("image"),
        reasoning: model.reasoning,
        thinking_levels: getSupportedThinkingLevels(model),
      })),
    };
  }
  throw new Error("invalid_request");
}

try {
  for await (const request of readRecords(process.stdin)) {
    try {
      process.stdout.write(
        encodeRecord({
          ...request,
          type: "result",
          payload: metadata(request),
        }),
      );
    } catch (error) {
      const code =
        error instanceof Error && error.message === "unknown_provider"
          ? "unknown_provider"
          : "invalid_request";
      process.stdout.write(
        encodeRecord({ ...request, type: "error", payload: { code } }),
      );
      process.exitCode = 2;
    }
  }
} catch {
  process.stderr.write("Invalid metadata JSONL input\n");
  process.exitCode = 2;
}
