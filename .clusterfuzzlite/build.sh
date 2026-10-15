#!/bin/bash -eu
# Build every fuzz/fuzz_*.py into a libFuzzer target with Atheris (compile_python_fuzzer runs PyInstaller).
# Runtime dependencies come from uv.lock and are installed with --require-hashes, so nothing unpinned enters.
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file /tmp/requirements.txt
python3 -m pip install --require-hashes --no-deps -r /tmp/requirements.txt

for fuzzer in fuzz/fuzz_*.py; do
  name=$(basename -s .py "$fuzzer")
  compile_python_fuzzer "$fuzzer" --paths=src --paths=fuzz
  cp fuzz/transcript.dict "$OUT/$name.dict"
done
# Seed the JSON fuzzer with the bundled transcripts.
zip -j -q "$OUT/fuzz_transcript_json_seed_corpus.zip" data/transcripts/*.json
