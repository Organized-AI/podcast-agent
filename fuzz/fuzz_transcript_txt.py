"""Fuzz the plain-text transcript parser.

Transcript text is untrusted input (POST /v1/jobs, POST /v1/process, files in a run folder). Any text must
either parse into an episode with a file-safe id or raise TranscriptError, and must do so quickly:
libFuzzer's per-input timeout catches regular expressions that backtrack, as CodeQL once found here.
"""

import sys

import atheris

with atheris.instrument_imports():
    from _invariants import check_episode

    from podcast_agent.transcript import TranscriptError, parse_txt_transcript


def TestOneInput(data: bytes) -> None:
    text = atheris.FuzzedDataProvider(data).ConsumeUnicodeNoSurrogates(len(data))
    try:
        episode = parse_txt_transcript(text, fallback_id="fuzz")
    except TranscriptError:
        return
    check_episode(episode)


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
