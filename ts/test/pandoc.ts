// pandoc, for the tests that ask it: $PANDOC, or `pandoc` on PATH. Without
// one a test skips itself, but not in CI (where `CI` is set): there it
// fails, so that no test is skipped unseen.

import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";
import type { TestContext } from "node:test";

let found: string | null | undefined;

/** pandoc's path, or null if there is none to run. */
function locate(): string | null {
  if (found !== undefined) return found;
  const candidate = process.env["PANDOC"] || "pandoc";
  try {
    execFileSync(candidate, ["--version"]);
    found = candidate;
  } catch {
    found = null;
  }
  return found;
}

/** pandoc for test `t`; undefined after skipping it (an error in CI). */
export function pandoc(t: TestContext): string | undefined {
  const path = locate();
  if (path !== null) return path;
  assert.ok(!process.env["CI"], "pandoc isn't on PATH (nor in $PANDOC): CI must run this test");
  t.skip("no pandoc on PATH (nor in $PANDOC)");
  return undefined;
}
