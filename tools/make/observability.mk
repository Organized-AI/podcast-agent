##@ Observability
OBS            := docker compose -f deploy/compose/docker-compose.yml
OBSERVE_PROVIDER ?= offline
OTELCOL_IMAGE  := otel/opentelemetry-collector-contrib:0.161.0@sha256:fd328de2552466ad78385e1b1289c3f2402b1c45f265b252aab1955b42845ac1
PROM_IMAGE     := prom/prometheus:v3.14.0@sha256:5ce7540c3c00ef4ab0c9d2c995c6a5b9c421f44b4a115d97a2c7af3b1c21cbb0

observe: ## Start Collector, Jaeger, MLflow, Prometheus, Grafana; run every transcript with telemetry; check it arrived
	$(OBS) --profile observability up -d --wait otel-collector jaeger mlflow prometheus grafana
	OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 $(OBS) run --rm -e LLM_PROVIDER=$(OBSERVE_PROVIDER) agent \
		run data/transcripts --out output/observe --quiet
	$(OBS) run --rm --no-deps -v "$$PWD/e2e:/app/e2e:ro" --entrypoint python agent e2e/check_telemetry.py output/observe
	@echo "Jaeger 127.0.0.1:16686 · MLflow 127.0.0.1:5050 · Prometheus 127.0.0.1:9090 · Grafana 127.0.0.1:3000"

observe-outage: ## After `make observe`: stop MLflow, check a run still reaches Jaeger, restart it, check the queued spans arrive
	$(OBS) --profile observability stop mlflow
	OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 $(OBS) run --rm -e LLM_PROVIDER=offline agent \
		run data/transcripts/ep002_ai_healthcare.json --out output/observe-outage --quiet
	@echo "--- MLflow is down: Jaeger and Prometheus must still have the run"
	$(OBS) run --rm --no-deps -v "$$PWD/e2e:/app/e2e:ro" --entrypoint python agent e2e/check_telemetry.py output/observe-outage \
		--mlflow "" --timeout 60
	$(OBS) --profile observability up -d --wait mlflow
	@echo "--- MLflow is back: the Collector's retry must deliver every span it queued"
	$(OBS) run --rm --no-deps -v "$$PWD/e2e:/app/e2e:ro" --entrypoint python agent e2e/check_telemetry.py output/observe-outage \
		--timeout 180

observe-down: ## Stop the observability stack and drop its data
	$(OBS) --profile observability down -v

observability-check: ## Validate the Collector config and Prometheus config, and unit-test the alert rules
	docker run --rm -v "$$PWD/deploy/observability/otel-collector.yaml:/c.yaml:ro" $(OTELCOL_IMAGE) validate --config=/c.yaml
	docker run --rm -v "$$PWD/deploy/observability/prometheus:/p:ro" --entrypoint promtool $(PROM_IMAGE) check rules /p/alerts.yml
	docker run --rm -v "$$PWD/deploy/observability/prometheus:/etc/prometheus:ro" --entrypoint promtool $(PROM_IMAGE) check config /etc/prometheus/prometheus.yml
	docker run --rm -v "$$PWD/deploy/observability/prometheus:/p:ro" -w /p --entrypoint promtool $(PROM_IMAGE) test rules alerts_test.yml
	python3 -c "import json; json.load(open('deploy/observability/grafana/dashboards/podcast-agent.json'))"
.PHONY: observe observe-outage observe-down observability-check
