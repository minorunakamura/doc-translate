import type { AuthInteraction, AuthPrompt } from "@earendil-works/pi-ai";
import type { JSONLRecord } from "./jsonl.js";

export class AuthDialog implements AuthInteraction {
  private sequence = 0;
  private pending: { id: string; resolve: (value: string) => void } | undefined;

  constructor(
    readonly signal: AbortSignal,
    private readonly send: (
      type: string,
      payload: JSONLRecord["payload"],
    ) => void,
  ) {}

  notify(event: Parameters<AuthInteraction["notify"]>[0]): void {
    this.send("notify", { event: JSON.parse(JSON.stringify(event)) });
  }

  prompt(prompt: AuthPrompt): Promise<string> {
    if (this.signal.aborted || prompt.signal?.aborted)
      return Promise.reject(
        new DOMException("Authentication cancelled", "AbortError"),
      );
    const id = String(++this.sequence);
    return new Promise<string>((resolve, reject) => {
      const cleanup = () => {
        this.signal.removeEventListener("abort", cancel);
        prompt.signal?.removeEventListener("abort", cancel);
        this.pending = undefined;
      };
      const cancel = () => {
        cleanup();
        this.send("prompt_cancel", { prompt_id: id });
        reject(new DOMException("Authentication cancelled", "AbortError"));
      };
      this.pending = {
        id,
        resolve: (value) => {
          cleanup();
          resolve(value);
        },
      };
      this.signal.addEventListener("abort", cancel, { once: true });
      prompt.signal?.addEventListener("abort", cancel, { once: true });
      const { signal: _signal, ...data } = prompt;
      this.send("prompt", {
        prompt_id: id,
        prompt: JSON.parse(JSON.stringify(data)),
      });
    });
  }

  respond(record: JSONLRecord): boolean {
    if (
      record.type !== "response" ||
      typeof record.payload.prompt_id !== "string" ||
      typeof record.payload.value !== "string" ||
      Object.keys(record.payload).length !== 2
    )
      return false;
    // A callback may win while the terminal response is already in flight.
    if (record.payload.prompt_id !== this.pending?.id) return true;
    this.pending.resolve(record.payload.value);
    return true;
  }
}
