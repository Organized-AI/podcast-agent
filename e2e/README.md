# e2e/

End-to-end checks that need running services.

| Check | Run with | What it proves |
|---|---|---|
| [check_telemetry.py](check_telemetry.py) | `make observe` | A real run's trace reached Jaeger with the documented span tree and no content attributes, and its metrics and alert rules reached Prometheus |

CI runs `make observe` in the **Observability stack** job on every pull request. Eval scoring against committed runs lands with the eval phase ([#10](https://github.com/Organized-AI/podcast-agent/issues/10)).
