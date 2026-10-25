import { setTimeout as delay } from "node:timers/promises";
import { createModels, createProvider } from "@earendil-works/pi-ai";
import { FileCredentialStore } from "../dist/credentials.js";
import { runHelper } from "../dist/main.js";

const [directory, mode = "secret"] = process.argv.slice(2);
const store = new FileCredentialStore(directory);
const models = createModels({ credentials: store });
const login = async (interaction, options) => {
  if (mode === "error") throw new Error("ARTIFICIAL_SECRET_ERROR");
  if (mode === "saving" || mode === "saving-failure") {
    await store.modify("background", async () => {
      interaction.notify({ type: "progress", message: "saving" });
      await delay(150);
      if (mode === "saving-failure") throw new Error("ARTIFICIAL_SECRET_ERROR");
      return { type: "api_key", key: "SAVED_AFTER_CANCEL" };
    });
  } else if (mode === "callback" || mode === "already-cancelled") {
    const controller = new AbortController();
    if (mode === "already-cancelled") controller.abort();
    const pending = interaction.prompt({
      type: "manual_code",
      message: "Manual code",
      signal: controller.signal,
    });
    setTimeout(() => controller.abort(), 30);
    await pending.catch(() => {});
  } else {
    interaction.notify({
      type: "auth_url",
      url: "https://example.invalid/artificial-auth",
    });
    const key = await interaction.prompt({
      type: mode === "select" ? "select" : mode === "text" ? "text" : "secret",
      message: "Artificial key",
      ...(mode === "select" ? { options: [{ id: "one", label: "One" }] } : {}),
    });
    if (key !== (mode === "select" ? "one" : "ARTIFICIAL_KEY"))
      throw new Error(key);
  }
  if (
    options &&
    (!options.getDeviceId?.().match(/^[a-f0-9-]{36}$/) ||
      options.agentName !== "doc-translate")
  )
    throw new Error("Missing OpenAI options");
  if (mode === "drain") {
    void store
      .modify("background", async () => {
        throw new Error("ARTIFICIAL_SECRET_ERROR");
      })
      .catch(() => {});
  }
  return options
    ? {
        type: "oauth",
        access: "ARTIFICIAL_ACCESS",
        refresh: "ARTIFICIAL_REFRESH",
        expires: 1234,
      }
    : { type: "api_key", key: "ARTIFICIAL_KEY" };
};
for (const id of ["faux", "openai"])
  models.setProvider(
    createProvider({
      id,
      name: id,
      models: [],
      api: {
        stream: () => {
          throw new Error("unused");
        },
        streamSimple: () => {
          throw new Error("unused");
        },
      },
      auth: {
        apiKey: { name: "key", login, resolve: async () => undefined },
        oauth: {
          name: "OAuth",
          login: async (interaction, options) => {
            if (id === "openai" && !options)
              throw new Error("Missing OpenAI options");
            await login(interaction, options);
            return {
              type: "oauth",
              access: "ARTIFICIAL_ACCESS",
              refresh: "ARTIFICIAL_REFRESH",
              expires: 1234,
            };
          },
          refresh: async (c) => c,
          toAuth: async () => ({}),
        },
      },
    }),
  );
models.setProvider(
  createProvider({
    id: "ambient",
    name: "Ambient",
    models: [],
    api: {
      stream: () => {
        throw new Error("unused");
      },
      streamSimple: () => {
        throw new Error("unused");
      },
    },
    auth: { apiKey: { name: "ambient", resolve: async () => undefined } },
  }),
);
await runHelper(models, store);
