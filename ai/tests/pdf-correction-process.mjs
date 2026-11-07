import "./no-network.mjs";
import { appendFile, readFile, writeFile } from "node:fs/promises";
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
  models: [{ id: "vision", input: ["text", "image"] }],
});
faux.setResponses([
  async (context, _options, _state, model) => {
    await writeFile(recordingPath, JSON.stringify(context));
    await appendFile(`${recordingPath}.jsonl`, `${JSON.stringify(context)}\n`);
    const input = JSON.parse(context.messages[1].content[0].text);
    const raw = await readFile(answerPath, "utf8");
    const response = JSON.parse(raw);
    const calls = (await readFile(`${recordingPath}.jsonl`, "utf8"))
      .trim()
      .split("\n").length;
    const message = fauxAssistantMessage(
      response.responses
        ? JSON.stringify(
            response.responses[input.context.correction_feedback ? 1 : 0],
          )
        : raw,
      response.retry && calls === 1
        ? { stopReason: "error", errorMessage: "429 rate limit exceeded" }
        : (response.terminal ?? {}),
    );
    message.provider = model.provider;
    message.model = model.id;
    return message;
  },
]);
models.setProvider(faux.provider);
await runHelper(models, store);
