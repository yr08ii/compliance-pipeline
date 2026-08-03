from contextlib import contextmanager

import pytest

from compliance import cli


class FakeSession:
    """Records what the CLI did, and reports how much data is already there."""

    def __init__(self, existing: int = 0):
        self.calls: list[str] = []
        self.existing = existing

    def execute(self, statement) -> None:
        self.calls.append("execute")

    def commit(self) -> None:
        self.calls.append("commit")

    def scalar(self, statement) -> int:
        return self.existing


def _patch(monkeypatch, session, argv):
    @contextmanager
    def scope():
        yield session

    monkeypatch.setattr(cli, "SessionLocal", lambda: scope())
    monkeypatch.setattr(
        cli, "generate_history", lambda s, **kw: session.calls.append("generate")
    )
    monkeypatch.setattr(
        cli,
        "run_pipeline_direct",
        lambda s, as_of=None: (session.calls.append("pipeline"), 2)[1],
    )
    monkeypatch.setattr("sys.argv", argv)


def test_seeds_and_runs_on_an_empty_store(monkeypatch, capsys):
    session = FakeSession(existing=0)
    _patch(monkeypatch, session, ["compliance-run"])

    cli.main()

    assert session.calls.count("execute") == 7  # every table cleared
    assert session.calls.index("generate") < session.calls.index("pipeline")
    assert capsys.readouterr().out.strip() == "pipeline complete: 2 alert(s) written"


def test_refuses_to_wipe_a_loaded_store(monkeypatch, capsys):
    """`compliance-run` truncates every table before reseeding. Over real
    ingested data that is destructive and unrecoverable, so it must stop."""
    session = FakeSession(existing=3_744_819)
    _patch(monkeypatch, session, ["compliance-run", "--as-of", "2026-05-01"])

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "refusing to run" in out
    assert "3,744,819" in out
    # It must name the non-destructive alternative, or the user just adds --force.
    assert "compliance-pipeline" in out
    assert "generate" not in session.calls
    assert "execute" not in session.calls


def test_force_overrides_the_guard(monkeypatch):
    session = FakeSession(existing=5_000)
    _patch(monkeypatch, session, ["compliance-run", "--force"])

    cli.main()

    assert "generate" in session.calls


def test_pipeline_command_scores_without_deleting(monkeypatch, capsys):
    """The command a nightly run or a re-score should use: it touches no
    existing rows."""
    session = FakeSession(existing=3_744_819)
    _patch(monkeypatch, session, ["compliance-pipeline", "--as-of", "2026-05-01"])

    cli.pipeline_main()

    assert "execute" not in session.calls, "scoring must not delete anything"
    assert "generate" not in session.calls, "scoring must not fabricate data"
    assert "pipeline" in session.calls
    out = capsys.readouterr().out
    # States which day was scored, since as-of and scored day differ by one.
    assert "2026-04-30" in out
    assert "2026-05-01" in out
