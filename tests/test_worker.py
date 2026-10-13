import shutil

from conftest import make_ctx

from podcast_agent.worker import DirQueue, DirStore, run_worker


def test_dir_queue_worker_is_idempotent_and_dead_letters_bad_input(tmp_path, data_dir):
    q = DirQueue(tmp_path / "q", max_attempts=2)
    store = DirStore(tmp_path / "out")
    shutil.copy(data_dir / "transcripts" / "ep003_bootstrapping.json", tmp_path / "q" / "inbox")
    (tmp_path / "q" / "inbox" / "broken.txt").write_text("no timestamps here")
    ctx = make_ctx()

    assert run_worker(ctx, q, store, once=True) == 1
    assert (tmp_path / "q" / "done" / "ep003_bootstrapping.json").exists()
    assert (tmp_path / "q" / "dead-letter" / "broken.txt").exists()
    reports = list((tmp_path / "out").glob("*/report.json"))
    assert len(reports) == 1

    # redelivery of the same transcript: acknowledged without reprocessing
    shutil.copy(data_dir / "transcripts" / "ep003_bootstrapping.json", tmp_path / "q" / "inbox")
    assert run_worker(ctx, q, store, once=True) == 0
    assert not list((tmp_path / "q" / "inbox").iterdir())
