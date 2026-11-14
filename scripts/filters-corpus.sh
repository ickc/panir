#!/usr/bin/env bash
# Regenerate corpus/filters.jsonl: what pandoc's Lua filters make of a
# document, which each language's filters must make too. For each
# corpus/filters/NAME.lua, one line: {"name", "input", "output", "also"}, the
# input being corpus/filters/NAME.md if there is one, else
# corpus/features.md. "also" lists the other orders that must make the same
# output: ["bottomup"] for a scenario marked "-- stateless", whose
# functions don't depend on each other's calls.
# Needs a pandoc whose pandoc-api-version matches the schema.
set -euo pipefail
cd "$(dirname "$0")/.."
out=corpus/filters.jsonl
: > "$out"
# (in the same order whatever the locale: meta before metacount)
for lua in $(printf '%s\n' corpus/filters/*.lua | LC_ALL=C sort); do
  name=$(basename "$lua" .lua)
  md=corpus/filters/$name.md
  [ -f "$md" ] || md=corpus/features.md
  input=$(pandoc -f markdown -t json "$md")
  output=$(pandoc -f markdown -t json -L "$lua" "$md")
  also='[]'
  if grep -q '^-- stateless' "$lua"; then also='["bottomup"]'; fi
  printf '{"name":"%s","input":%s,"output":%s,"also":%s}\n' "$name" "$input" "$output" "$also" >> "$out"
done
