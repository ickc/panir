// The package as npm would publish it.

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const here = (path: string) => new URL(`../${path}`, import.meta.url);

test("the tarball has the license, the same as the repository's", () => {
  assert.equal(readFileSync(here("LICENSE"), "utf8"), readFileSync(here("../LICENSE"), "utf8"));
  const out = execFileSync("npm", ["pack", "--dry-run", "--json", "--ignore-scripts"], {
    cwd: here("."), shell: process.platform === "win32",
  }).toString();
  const files = (JSON.parse(out) as { files: { path: string }[] }[])[0]!.files.map((f) => f.path);
  for (const f of ["LICENSE", "README.md", "package.json"]) assert.ok(files.includes(f), `${f} in ${files.join(" ")}`);
});

test("engines is what the library needs, not what the tests need", () => {
  // class static blocks (Context): Node 16.11; CI runs the built package
  // there (ci.yml, package)
  const pkg = JSON.parse(readFileSync(here("package.json"), "utf8")) as { engines: { node: string } };
  assert.equal(pkg.engines.node, ">=16.11");
});
