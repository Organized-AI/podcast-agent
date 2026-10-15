# ClusterFuzzLite build image for the Atheris fuzzers in fuzz/ (see .github/workflows/fuzzing.yml).
# Base images are pinned by digest; Dependabot proposes updates.
FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:21f6fab17ace4dc76e1c56de91ee95d3d03da021df94b41056ba036f6c6f55e9
COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /usr/local/bin/uv
COPY . $SRC/podcast-agent
WORKDIR $SRC/podcast-agent
COPY .clusterfuzzlite/build.sh $SRC/
