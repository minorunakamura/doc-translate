// Offline public-provider fixture; no test notifications enter the JSONL wire.
import "./no-network.mjs";
import { writeFile } from "node:fs/promises";
import { setTimeout as delay } from "node:timers/promises";
const publicAPI = new URL(
  "../node_modules/@earendil-works/pi-ai/dist/",
  import.meta.url,
);
const { createModels, createProvider } = await import(
  new URL("index.js", publicAPI)
);
const { fauxProvider, fauxAssistantMessage } = await import(
  new URL("providers/faux.js", publicAPI)
);
const { createAssistantMessageEventStream } = await import(
  new URL("utils/event-stream.js", publicAPI)
);
const { FileCredentialStore } = await import("../dist/credentials.js");
const { runHelper } = await import("../dist/main.js");
const [directory, mode] = process.argv.slice(2);
const store = new FileCredentialStore(directory);
const models = createModels({ credentials: store });
const faux = fauxProvider({
  api: "faux-api",
  models: [{ id: "vision", input: ["text", "image"], reasoning: true }],
});
const ready = () => writeFile(`${directory}/ready`, String(process.pid));
let checks = 0;
const stream = (model, _context, options) => {
  const events = createAssistantMessageEventStream();
  queueMicrotask(async () => {
    const message = fauxAssistantMessage(
      '{"id":"unit-1","evidence":"literal source"}',
    );
    Object.assign(message, {
      provider: model.provider,
      model: model.id,
      api: model.api,
    });
    if (mode === "timeout" || mode === "cancel") {
      await ready();
      if (!options.signal.aborted)
        await new Promise((resolve) =>
          options.signal.addEventListener("abort", resolve, { once: true }),
        );
      await delay(10);
      message.stopReason = "aborted";
      message.usage = { ...message.usage, input: 17, totalTokens: 17 };
      events.push({ type: "error", reason: "aborted", error: message });
    } else events.push({ type: "done", reason: "stop", message });
    events.end(message);
  });
  return events;
};
if (mode.startsWith("refresh"))
  await store.modify("faux", () => ({
    type: "oauth",
    access: "ARTIFICIAL_OLD",
    refresh: "ARTIFICIAL_REFRESH",
    expires: 1,
  }));
const auth = mode.startsWith("refresh")
  ? {
      oauth: {
        name: "Artificial OAuth",
        login: async () => {
          throw new Error("unused");
        },
        refresh: async (credential) => {
          await ready();
          await delay(250);
          if (mode === "refresh-failure") throw new Error("ARTIFICIAL_SECRET");
          return {
            ...credential,
            access: "ARTIFICIAL_ROTATED",
            refresh: "ARTIFICIAL_ROTATED_REFRESH",
            expires: Date.now() + 3600000,
          };
        },
        toAuth: async () => ({}),
      },
    }
  : mode === "preflight-timeout"
    ? {
        apiKey: {
          name: "Artificial",
          check: async () => {
            checks++;
            if (checks === 1) {
              await ready();
              await delay(250);
            }
            return { type: "api_key" };
          },
          resolve: async () => ({ auth: {} }),
        },
      }
    : faux.provider.auth;
models.setProvider(
  createProvider({
    id: "faux",
    models: faux.models,
    auth,
    api: { stream, streamSimple: stream },
  }),
);
await runHelper(models, store);
