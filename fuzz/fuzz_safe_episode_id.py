"""Fuzz the episode-id sanitizer: no input may produce an id that escapes the output folder."""

import sys

import atheris

with atheris.instrument_imports():
    from _invariants import SAFE_ID

    from podcast_agent.transcript import safe_episode_id


def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    raw = fdp.ConsumeUnicodeNoSurrogates(fdp.ConsumeIntInRange(0, 256))
    fallback = fdp.ConsumeUnicodeNoSurrogates(64)
    out = safe_episode_id(raw, fallback)
    assert SAFE_ID.match(out), f"unsafe id {out!r} from {raw!r} / {fallback!r}"


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
