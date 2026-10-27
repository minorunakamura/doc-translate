import "./no-network.mjs";
import { appendFile, readFile } from "node:fs/promises";
import { createModels } from "@earendil-works/pi-ai";
import {
  fauxAssistantMessage,
  fauxProvider,
} from "@earendil-works/pi-ai/providers/faux";
import { FileCredentialStore } from "../dist/credentials.js";
import { runHelper } from "../dist/main.js";

const [directory, answerPath, recordingPath] = process.argv.slice(2);
const store = new FileCredentialStore(directory);
const models = createModels({ credentials: store });
const faux = fauxProvider({
  api: "faux-api",
  models: [{ id: "text", input: ["text"] }],
});
faux.setResponses([
  async (context, _options, _state, model) => {
    await appendFile(recordingPath, `${JSON.stringify(context)}\n`);
    const input = JSON.parse(context.messages[1].content[0].text);
    const answers = JSON.parse(await readFile(answerPath, "utf8"));
    const response = answers[input.context.phase];
    const answer = Array.isArray(response)
      ? response[input.context.section]
      : response;
    const calls = (await readFile(recordingPath, "utf8"))
      .trim()
      .split("\n").length;
    const retry = answers.retry && calls === 1;
    const message = fauxAssistantMessage(
      JSON.stringify(answer),
      retry
        ? { stopReason: "error", errorMessage: "429 rate limit exceeded" }
        : {},
    );
    message.provider = model.provider;
    message.model = model.id;
    return message;
  },
]);
models.setProvider(faux.provider);
await runHelper(models, store);
