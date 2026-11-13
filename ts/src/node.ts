/**
 * Running a filter under pandoc, with Node.js:
 *
 *     #!/usr/bin/env node
 *     import { Str } from "panir";
 *     import { runFilter } from "panir/node";
 *     runFilter({ Str: (s) => Str(s.text.toUpperCase()) });
 *
 *     pandoc --filter ./upper.js input.md
 */

import { Conversion } from "./conversion.ts";
import { applyFilter } from "./walk.ts";
import type { Filter } from "./walk.ts";
import { parse, serialize } from "./index.ts";

/** Read a document from stdin, apply the filter (or filters), write it to stdout. */
export async function runFilter(filter: Filter): Promise<void>;
export async function runFilter(filters: readonly Filter[]): Promise<void>;
export async function runFilter(filter: Filter | readonly Filter[]): Promise<void> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  const doc = parse(Buffer.concat(chunks).toString("utf8"));
  // the output format (argv), and what pandoc says in the environment
  const conversion = Conversion.fromEnvironment(process.argv.slice(2), process.env);
  const filters: readonly Filter[] = Array.isArray(filter) ? filter : [filter];
  const out = serialize(applyFilter(doc, filters, conversion));
  // written when this resolves: a script may end the process right after
  await new Promise<void>((resolve, reject) => {
    process.stdout.write(out, (e) => (e ? reject(e) : resolve()));
  });
}
