# config/

`settings.yaml` is the canonical reference for every setting the agent reads: the environment variable, the `Settings` field in `src/podcast_agent/config.py`, its default and what it means. It is generated, never edited by hand:

```bash
make config        # regenerate after changing config.py or .env.example
make drift         # fails if it is stale or .env.example misses a variable (CI runs this)
```

Per-environment values live with the deployment, not here: `deploy/helm/podcast-agent/values-{dev,prod}.yaml` and `deploy/gitops/envs/`. Standard OpenTelemetry variables (`OTEL_*`) are documented in `docs/OBSERVABILITY.md`.
