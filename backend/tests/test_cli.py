from contextlib import contextmanager

from compliance import cli


def test_main_seeds_and_runs_pipeline(monkeypatch, capsys):
    calls = []

    class FakeSession:
        def execute(self, statement) -> None:
            calls.append(("execute", statement))

        def commit(self) -> None:
            calls.append("commit")

    @contextmanager
    def fake_session_scope():
        yield FakeSession()

    def fake_seed(session) -> None:
        calls.append(("seed", session))

    def fake_run_pipeline(session) -> int:
        calls.append(("pipeline", session))
        return 2

    monkeypatch.setattr(cli, "SessionLocal", lambda: fake_session_scope())
    monkeypatch.setattr(cli, "seed", fake_seed)
    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    cli.main()

    assert [kind for kind, *_ in calls[:7]] == ["execute"] * 7
    assert calls[7] == "commit"
    assert calls[8][0] == "seed"
    assert calls[9] == "commit"
    assert calls[10][0] == "pipeline"
    assert calls[11] == "commit"
    assert capsys.readouterr().out.strip() == "pipeline complete: 2 alert(s) written"