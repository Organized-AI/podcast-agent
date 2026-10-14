from pathlib import Path

from podcast_agent.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def docker_env_file(path: Path) -> dict[str, str]:
    """Parse a file the way `docker run --env-file` does: KEY=VALUE taken literally, # only at line start."""
    env = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value
    return env


def test_env_example_works_with_docker_env_file(monkeypatch):
    env = docker_env_file(ROOT / ".env.example")
    inline = {k: v for k, v in env.items() if "#" in v}
    assert not inline, f"docker --env-file keeps inline comments as part of the value: {inline}"
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    s = Settings()
    assert s.search_provider == "mock" and s.mock_search_failure_rate == 0.0
    assert s.summary_length_mode == "spec" and len(s.today) == 10
    assert s.resolved_provider() == "offline"  # no key in the example
