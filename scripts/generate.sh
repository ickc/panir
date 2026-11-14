#!/usr/bin/env bash
# Regenerate schema/reified.json and corpus/arbitrary.jsonl from pandoc-types,
# then everything derived from them. Needs GHC and cabal (see pins.env).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=../pins.env
. ./pins.env
cat > haskell/cabal.project.local <<EOT
index-state: $INDEX_STATE
constraints: pandoc-types ==$PANDOC_TYPES_VERSION
EOT
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
(cd haskell && cabal run -v1 panir-schema -- "$out")
mv "$out/reified.json" schema/reified.json
mv "$out/arbitrary.jsonl" corpus/arbitrary.jsonl
python3 tools/derive.py
python3 tools/gen_python.py
ruff format -q python/src/panir/_types.py
python3 tools/gen_rust.py
rustfmt --edition 2024 rust/src/generated.rs
python3 tools/gen_ts.py
python3 tools/gen_julia.py
