import "./no-network.mjs";
import { appendFile, readFile } from "node:fs/promises";
import { isDeepStrictEqual } from "node:util";
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
    let section = input.context.section;
    let binding;
    if (input.context.phase === "collect" && input.context.wire_version === 2) {
      const groups = JSON.parse(
        await readFile(
          answerPath.replace(/\.json$/u, ".bindings.json"),
          "utf8",
        ),
      );
      section = groups.findIndex((group) =>
        isDeepStrictEqual(group.wire, JSON.parse(input.text)),
      );
      if (section < 0) throw new Error("Missing fixture group binding");
      binding = groups[section];
    }
    const answer = structuredClone(
      Array.isArray(response) ? response[section] : response,
    );
    if (binding && Array.isArray(answer?.candidates)) {
      for (const candidate of answer.candidates) {
        if (!Array.isArray(candidate?.evidence)) continue;
        for (const evidence of candidate.evidence) {
          const index = binding.unit_ids.indexOf(evidence?.unit_id);
          // Invalid/global IDs outside this group must remain invalid.
          if (index >= 0) evidence.unit_id = `request-unit-${index + 1}`;
        }
      }
    }
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
