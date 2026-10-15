# Each area's targets live in tools/make/<area>.mk. `make help` lists them all.
SHELL := /bin/bash
.DEFAULT_GOAL := help

include tools/make/help.mk tools/make/dev.mk tools/make/quality.mk tools/make/docker.mk tools/make/observability.mk tools/make/evals.mk tools/make/helm.mk tools/make/guide.mk
