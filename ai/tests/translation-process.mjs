import "./no-network.mjs";
import { appendFile, readFile } from "node:fs/promises";
import { setTimeout as delay } from "node:timers/promises";
import { createModels, createProvider } from "@earendil-works/pi-ai";
import {
  fauxAssistantMessage,
  fauxProvider,
} from "@earendil-works/pi-ai/providers/faux";
import { createAssistantMessageEventStream } from "@earendil-works/pi-ai/utils/event-stream";
import { FileCredentialStore } from "../dist/credentials.js";
import { runHelper } from "../dist/main.js";

const [directory, answerPath, recordingPath] = process.argv.slice(2);
const store = new FileCredentialStore(directory);
const models = createModels({ credentials: store });
const response = JSON.parse(await readFile(answerPath, "utf8"));
const faux = fauxProvider({
  api: "faux-api",
  models: [
    {
      id: "text",
      input: ["text"],
      ...(response.input_limit_bytes
        ? { inputLimits: { maxRequestBytes: response.input_limit_bytes } }
        : {}),
    },
  ],
});
faux.setResponses([
  async (context, _options, _state, model) => {
    await appendFile(recordingPath, `${JSON.stringify(context)}\n`);
    const input = JSON.parse(context.messages[1].content[0].text);
    const units = JSON.parse(input.text);
    if (response.input_limit_bytes) {
      await _options.onPayload({
        source: units.map((unit) => unit.source_text).join(""),
      });
    }
    if (response.barrier) {
      const deadline = Date.now() + 5000;
      const waitForPeer = async () => {
        if (
          (await readFile(recordingPath, "utf8")).trim().split("\n").length >= 2
        )
          return;
        if (Date.now() > deadline)
          throw new Error("Two concurrent calls required");
        await delay(10);
        return waitForPeer();
      };
      await waitForPeer();
    }
    const calls = (await readFile(recordingPath, "utf8"))
      .trim()
      .split("\n").length;
    const retry =
      response.retry === "always" || (response.retry && calls === 1);
    const answer = response.answer ?? {
      translations: units.map((unit) => ({
        unit_id: unit.unit_id,
        text: `訳:${unit.source_text}`,
        uncertainties: [],
      })),
    };
    const message = fauxAssistantMessage(
      JSON.stringify(answer),
      retry
        ? { stopReason: "error", errorMessage: "429 rate limit exceeded" }
        : response.length_above &&
            units.reduce((size, unit) => size + unit.source_text.length, 0) >
              response.length_above
          ? { stopReason: "length" }
          : response.fail && input.text.includes(response.fail)
            ? {
                stopReason: "error",
                errorMessage: "artificial permanent failure",
              }
            : {},
    );
    message.provider = model.provider;
    message.model = model.id;
    return message;
  },
]);
models.setProvider(faux.provider);
if (response.wait) {
  const stream = (model, context, options) => {
    const events = createAssistantMessageEventStream();
    queueMicrotask(async () => {
      await appendFile(recordingPath, `${JSON.stringify(context)}\n`);
      if (!options.signal.aborted) {
        await new Promise((resolve) =>
          options.signal.addEventListener("abort", resolve, { once: true }),
        );
      }
      await delay(50);
      const message = fauxAssistantMessage("incomplete", {
        stopReason: "aborted",
      });
      message.provider = model.provider;
      message.model = model.id;
      message.api = model.api;
      message.usage = { ...message.usage, input: 17, totalTokens: 17 };
      events.push({ type: "error", reason: "aborted", error: message });
      events.end(message);
    });
    return events;
  };
  models.setProvider(
    createProvider({
      id: "faux",
      models: faux.models,
      auth: faux.provider.auth,
      api: { stream, streamSimple: stream },
    }),
  );
}
await runHelper(models, store);
if (response.wait) await appendFile(`${recordingPath}.drained`, "drained\n");
