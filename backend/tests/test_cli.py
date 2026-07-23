from contextlib import contextmanager

from compliance import cli


def test_main_resets_generates_then_runs_pipeline(monkeypatch, capsys):
    calls: list = []

    class FakeSession:
        def execute(self, statement) -> None:
            calls.append("execute")

        def commit(self) -> None:
            calls.append("commit")

    @contextmanager
    def fake_session_scope():
        yield FakeSession()

    monkeypatch.setattr(cli, "SessionLocal", lambda: fake_session_scope())
    monkeypatch.setattr(
        cli, "generate_history", lambda session, **kw: calls.append("generate")
    )
    monkeypatch.setattr(
        cli, "run_pipeline", lambda session, as_of=None: (calls.append("pipeline"), 2)[1]
    )

    cli.main()

    # every table cleared, then generate, then pipeline — each followed by a commit
    assert calls.count("execute") == 7
    assert calls.index("generate") < calls.index("pipeline")
    assert calls[-1] == "commit"
    assert capsys.readouterr().out.strip() == "pipeline complete: 2 alert(s) written"
