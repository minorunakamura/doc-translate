import { encodeRecord, readRecords } from "@doc-translate/ai/jsonl";

// Force UTF-8 sequences and LF framing across chunk boundaries at the public codec.
async function* bytes() {
  for await (const chunk of process.stdin) {
    for (const byte of chunk) yield Uint8Array.of(byte);
  }
}

const input = process.argv.includes("--byte-chunks") ? bytes() : process.stdin;
for await (const record of readRecords(input)) {
  process.stdout.write(encodeRecord(record));
}
