import { pathToFileURL } from "node:url";
import { AuthDialog } from "./auth.js";
import { FileCredentialStore } from "./credentials.js";
import { readFileSync, realpathSync } from "node:fs";
import {
  getSupportedThinkingLevels,
  ModelsError,
  type Models,
} from "@earendil-works/pi-ai";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import { encodeRecord, type JSONLRecord, readRecords } from "./jsonl.js";
import {
  generate,
  generationConfig,
  generationError,
  preflight,
  type GenerationConfig,
} from "./generation.js";

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

function metadata(
  request: JSONLRecord,
  models: Models,
): JSONLRecord["payload"] {
  if (
    request.type === "providers" &&
    Object.keys(request.payload).length === 0
  ) {
    return {
      ...versions,
      providers: models.getProviders().map(({ id, name, auth }) => ({
        id,
        name,
        login_methods: [
          ...(auth.apiKey?.login ? ["api_key"] : []),
          ...(auth.oauth ? ["oauth"] : []),
        ],
      })),
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

function send(
  request: JSONLRecord,
  type: string,
  payload: JSONLRecord["payload"],
): void {
  if (!process.stdout.destroyed)
    process.stdout.write(encodeRecord({ ...request, type, payload }));
}

export async function runHelper(
  models: Models,
  store: FileCredentialStore,
): Promise<void> {
  const controller = new AbortController();
  let active: Promise<void> | undefined;
  let dialog: AuthDialog | undefined;
  let requestId: string | undefined;
  let config: GenerationConfig | undefined;
  let generationStarted = false;
  const stop = () => {
    controller.abort();
    process.stdin.destroy();
  };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
  process.stdout.on("error", stop);
  const generation = async (request: JSONLRecord) => {
    let result: JSONLRecord["payload"] = {};
    let code: string | undefined;
    try {
      if (request.type === "preflight") {
        if (config) throw new Error("invalid_request");
        const selected = generationConfig(request.payload, models);
        result = {
          ...versions,
          ...(await preflight(selected, models, controller.signal)),
        };
        config = selected;
        requestId = request.request_id;
      } else {
        if (!config || request.request_id !== requestId || generationStarted)
          throw new Error("invalid_request");
        generationStarted = true;
        result = {
          ...versions,
          ...(await generate(
            request.payload,
            config,
            models,
            controller.signal,
          )),
        };
        if (typeof result.code === "string") code = result.code;
      }
      if (controller.signal.aborted) code ??= "cancelled";
    } catch (error) {
      code = generationError(error, controller.signal);
    } finally {
      try {
        await store.drain();
      } catch (error) {
        code =
          error instanceof ModelsError && error.code === "oauth"
            ? "auth_failed"
            : "storage_failed";
      }
    }
    if (code) {
      process.exitCode = [
        "invalid_request",
        "unknown_provider",
        "unknown_model",
        "unsupported_thinking",
        "unsupported_image",
        "invalid_image",
        "input_limit",
      ].includes(code)
        ? 2
        : 1;
      send(request, "error", { ...result, code });
    } else send(request, "result", result);
  };
  const authenticate = async (request: JSONLRecord) => {
    let payload: JSONLRecord["payload"] = {};
    let code: string | undefined;
    try {
      const { provider, method } = request.payload;
      if (
        typeof provider !== "string" ||
        Object.keys(request.payload).length !==
          (request.type === "login" ? 2 : 1)
      )
        throw new Error("invalid_request");
      const selected = models.getProvider(provider);
      if (!selected) throw new Error("unknown_provider");
      if (request.type === "login") {
        if (
          (method !== "api_key" && method !== "oauth") ||
          !(method === "oauth"
            ? selected.auth.oauth
            : typeof selected.auth.apiKey?.login === "function")
        )
          throw new Error("unsupported_method");
        const interaction = new AuthDialog(controller.signal, (type, data) =>
          send(request, type, data),
        );
        dialog = interaction;
        const deviceId =
          provider === "openai" && method === "oauth"
            ? await store.getDeviceId()
            : undefined;
        await models.login(
          provider,
          method,
          {
            signal: controller.signal,
            prompt: (prompt) => interaction.prompt(prompt),
            notify: (event) => interaction.notify(event),
          },
          deviceId
            ? { getDeviceId: () => deviceId, agentName: "doc-translate" }
            : undefined,
        );
        payload = { operation: "login", provider, method };
      } else {
        await store.delete(provider, { signal: controller.signal });
        payload = { operation: "logout", provider };
      }
      if (controller.signal.aborted) throw new Error("cancelled");
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      code = [
        "invalid_request",
        "unknown_provider",
        "unsupported_method",
      ].includes(message)
        ? message
        : controller.signal.aborted
          ? "cancelled"
          : "auth_failed";
    } finally {
      try {
        await store.drain();
      } catch {
        code = "storage_failed";
      }
    }
    if (code) {
      process.exitCode = [
        "invalid_request",
        "unknown_provider",
        "unsupported_method",
      ].includes(code)
        ? 2
        : 1;
      send(request, "error", { code });
    } else send(request, "result", payload);
  };
  try {
    for await (const request of readRecords(process.stdin)) {
      if (controller.signal.aborted) break;
      if (request.type === "response") {
        if (request.request_id !== requestId || !dialog?.respond(request))
          stop();
        continue;
      }
      if (
        active ||
        generationStarted ||
        (config && request.type !== "generate")
      ) {
        send(request, "error", { code: "invalid_request" });
        process.exitCode = 2;
        stop();
        break;
      }
      if (request.type === "preflight" || request.type === "generate") {
        active = generation(request).finally(() => {
          active = undefined;
        });
      } else if (request.type === "login" || request.type === "logout") {
        requestId = request.request_id;
        active = authenticate(request).finally(() => {
          active = undefined;
        });
      } else {
        try {
          send(request, "result", metadata(request, models));
        } catch (error) {
          send(request, "error", {
            code:
              error instanceof Error && error.message === "unknown_provider"
                ? "unknown_provider"
                : "invalid_request",
          });
          process.exitCode = 2;
        }
      }
    }
    if (active) controller.abort();
  } catch {
    const wasAborted = controller.signal.aborted;
    controller.abort();
    if (!active && !wasAborted) {
      process.stderr.write("Invalid metadata JSONL input\n");
      process.exitCode = 2;
    } else process.exitCode ??= 1;
  } finally {
    await active;
    try {
      await store.drain();
    } catch {
      process.exitCode = 1;
    }
    process.removeListener("SIGINT", stop);
    process.removeListener("SIGTERM", stop);
  }
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href
) {
  const store = new FileCredentialStore();
  await runHelper(builtinModels({ credentials: store }), store);
}
