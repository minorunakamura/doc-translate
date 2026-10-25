import "./no-network.mjs";
import { createModels, createProvider } from "@earendil-works/pi-ai";
import { createAssistantMessageEventStream } from "@earendil-works/pi-ai/utils/event-stream";
import { setTimeout as delay } from "node:timers/promises";
import { streamSimple as completions } from "@earendil-works/pi-ai/api/openai-completions";
import {
  fauxAssistantMessage,
  fauxProvider,
  fauxToolCall,
} from "@earendil-works/pi-ai/providers/faux";
import { FileCredentialStore } from "../dist/credentials.js";
import { runHelper } from "../dist/main.js";

const [directory, mode = "stop"] = process.argv.slice(2);
const store = new FileCredentialStore(directory);
const models = createModels({ credentials: store });
const faux = fauxProvider({
  api: "faux-api",
  models: [
    {
      id: "vision",
      input: ["text", "image"],
      reasoning: true,
      ...(mode.startsWith("image-limit")
        ? {
            inputLimits: {
              images: {
                maxPerMessage: 1,
                maxPerRequest: 1,
                resize: {
                  maxWidth: 1,
                  maxHeight: 1,
                  maxBytes: mode === "image-limit-equal" ? 120 : 100,
                },
              },
            },
          }
        : {}),
    },
    { id: "text", input: ["text"] },
  ],
});
faux.setResponses([
  async (context, options, state, model) => {
    const message = fauxAssistantMessage(
      JSON.stringify({
        context,
        options: {
          reasoning: options.reasoning ?? null,
          maxRetries: options.maxRetries,
          timeoutMs: options.timeoutMs,
          maxTokens: options.maxTokens,
          temperature: options.temperature,
          transport: options.transport,
          thinkingBudgets: options.thinkingBudgets,
        },
        calls: state.callCount,
      }),
      { stopReason: mode.startsWith("image-limit") ? "stop" : mode },
    );
    message.provider = model.provider;
    message.model = model.id;
    return message;
  },
]);
models.setProvider(faux.provider);
if (
  [
    "missing-terminal",
    "wrong-model",
    "error-zero",
    "partial-usage",
    "toolcall",
    "timeout",
    "noncooperative",
    "refresh",
    "refresh-failure",
    "quota",
    "billing",
    "provider-auth",
  ].includes(mode)
) {
  const response = (model, context, options) => {
    const stream = createAssistantMessageEventStream();
    const message = fauxAssistantMessage(
      mode === "toolcall" ? fauxToolCall("never_execute", {}) : "not JSON",
      {
        stopReason: [
          "error-zero",
          "partial-usage",
          "quota",
          "billing",
          "provider-auth",
        ].includes(mode)
          ? "error"
          : "stop",
        errorMessage:
          mode === "quota"
            ? "429 insufficient_quota"
            : mode === "billing"
              ? "billing limit exceeded"
              : mode === "provider-auth"
                ? "401 invalid_api_key"
                : "ARTIFICIAL_SECRET_ERROR",
      },
    );
    message.provider = model.provider;
    message.model = mode === "wrong-model" ? "wrong" : model.id;
    message.api = model.api;
    if (mode === "partial-usage")
      message.usage = { ...message.usage, input: 17, totalTokens: 17 };
    queueMicrotask(async () => {
      if (mode === "noncooperative") return;
      if (mode === "timeout") {
        process.stdout.write(
          JSON.stringify({
            schema_version: 1,
            request_id: "generation",
            type: "fixture-ready",
            payload: {},
          }) + "\n",
        );
        if (!options.signal.aborted)
          await new Promise((resolve) =>
            options.signal.addEventListener("abort", resolve, { once: true }),
          );
        await delay(10);
        message.stopReason = "aborted";
        message.usage = { ...message.usage, input: 17, totalTokens: 17 };
      }
      if (mode !== "missing-terminal")
        stream.push(
          message.stopReason === "error" || message.stopReason === "aborted"
            ? { type: "error", reason: message.stopReason, error: message }
            : { type: "done", reason: "stop", message },
        );
      stream.end(message);
    });
    return stream;
  };
  const auth = mode.startsWith("refresh")
    ? {
        apiKey: {
          name: "Forbidden fallback",
          resolve: async () => ({
            auth: { apiKey: "ARTIFICIAL_FORBIDDEN_FALLBACK" },
            source: "Forbidden fallback",
          }),
        },
        oauth: {
          name: "Artificial OAuth",
          login: async () => {
            throw new Error("unused");
          },
          refresh: async (credential) => {
            process.stdout.write(
              JSON.stringify({
                schema_version: 1,
                request_id: "generation",
                type: "fixture-ready",
                payload: {},
              }) + "\n",
            );
            await delay(180);
            if (mode === "refresh-failure")
              throw new Error("ARTIFICIAL_SECRET_REFRESH");
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
    : faux.provider.auth;
  if (mode.startsWith("refresh"))
    await store.modify("faux", () => ({
      type: "oauth",
      access: "ARTIFICIAL_OLD",
      refresh: "ARTIFICIAL_REFRESH",
      expires: 1,
    }));
  models.setProvider(
    createProvider({
      id: "faux",
      models: faux.models,
      auth,
      api: { stream: response, streamSimple: response },
    }),
  );
}
if (mode === "preflight-timeout") {
  let checks = 0;
  models.setProvider(
    createProvider({
      id: "faux",
      models: faux.models,
      auth: {
        apiKey: {
          name: "Artificial",
          check: async ({ signal }) => {
            checks += 1;
            if (checks > 1) await delay(150, undefined, { signal });
            return { type: "api_key" };
          },
          resolve: async () => ({ auth: {} }),
        },
      },
      api: {
        stream: (model, context, options) =>
          faux.provider.stream(model, context, options),
        streamSimple: (model, context, options) =>
          faux.provider.streamSimple(model, context, options),
      },
    }),
  );
}
if (mode.startsWith("http-")) {
  let sends = 0;
  const response = (model, context, options) =>
    completions(model, context, {
      ...options,
      fetch: async (_url, init) => {
        sends += 1;
        if (sends > 1) throw new Error("Unexpected retry");
        if (mode === "http-retry")
          return new Response(
            JSON.stringify({ error: { message: "ARTIFICIAL_SECRET_HTTP" } }),
            {
              status: 429,
              headers: {
                "content-type": "application/json",
                "retry-after": "0",
              },
            },
          );
        const body = JSON.parse(init.body);
        const answer = JSON.stringify({ body, sends });
        return new Response(
          `data: ${JSON.stringify({ id: "artificial", choices: [{ index: 0, delta: { content: answer }, finish_reason: null }] })}\n\ndata: ${JSON.stringify({ id: "artificial", choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 11, completion_tokens: 3, total_tokens: 14 } })}\n\ndata: [DONE]\n\n`,
          { headers: { "content-type": "text/event-stream" } },
        );
      },
    });
  models.setProvider(
    createProvider({
      id: "faux",
      models: [
        {
          ...faux.models[0],
          api: "openai-completions",
          thinkingLevelMap: { off: "none" },
          baseUrl: "https://example.invalid/v1",
          ...(mode === "http-limit"
            ? { inputLimits: { maxRequestBytes: 32 } }
            : {}),
        },
      ],
      auth: {
        apiKey: {
          name: "Artificial",
          resolve: async () => ({
            auth: { apiKey: "ARTIFICIAL_API_KEY" },
            source: "Artificial",
          }),
        },
      },
      api: { stream: response, streamSimple: response },
    }),
  );
}
await runHelper(models, store);
