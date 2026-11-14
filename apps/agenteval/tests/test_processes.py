from pathlib import Path
from types import SimpleNamespace

import pytest

from trachuan_agenteval.scoring import Evidence, Matcher
from trachuan_agenteval.stack import Stack


@pytest.fixture
def parent_credentials(monkeypatch):
    names = [
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "VENDOR_KEY",
        "VENDOR_TOKEN",
        "VENDOR_SECRET",
        "GOOGLE_CLIENT_SECRET",
    ]
    values = {name: "synthetic-parent-" + name for name in names}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PGPASSWORD", "synthetic-postgres")
    monkeypatch.setenv("PGPASSFILE", "/synthetic/pgpass")
    return values


def assert_no_inherited_credentials(env, parent):
    assert env is not None
    for name, value in parent.items():
        assert env.get(name) != value
        if name != "GOOGLE_CLIENT_SECRET":
            assert name not in env
    assert not any(
        name.endswith(("_KEY", "_TOKEN", "_SECRET"))
        for name in env
        if name != "GOOGLE_CLIENT_SECRET"
    )


def test_matcher_subprocess_has_minimal_environment(monkeypatch, parent_credentials):
    from trachuan_agenteval import scoring

    def run(args, **kwargs):
        env = kwargs.get("env")
        assert_no_inherited_credentials(env, parent_credentials)
        assert set(env) <= {"PATH"}
        return SimpleNamespace(returncode=0, stdout='{"matched":0,"expected":1}')

    monkeypatch.setattr(scoring.subprocess, "run", run)
    assert Matcher(Path("/synthetic/agentmatch")).score("dev-001", [], Evidence())["matched"] == 0


def test_stack_child_environments_are_explicit_and_specific(
    tmp_path, monkeypatch, parent_credentials
):
    from trachuan_agenteval import stack as module

    calls = []
    corpus = "postgres://reader:synthetic-corpus-password@127.0.0.1/{database}"

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, *args):
            pass

    class Process:
        pid = 424242

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, **kwargs):
            return 0

    class HTTP:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            return SimpleNamespace(status_code=200)

    def record(args, **kwargs):
        kind = Path(args[0]).name
        env = kwargs.get("env")
        calls.append((kind, args, env))
        assert_no_inherited_credentials(env, parent_credentials)
        assert "synthetic-corpus-password" not in " ".join(args)
        if kind in {"go", "fakegoogle", "lsof"}:
            assert "PGPASSWORD" not in env
            assert "PGPASSFILE" not in env
            assert "SERVER_CORPUS_DATABASE_URL" not in env
            assert "GOOGLE_CLIENT_SECRET" not in env
        if kind in {"fakegoogle", "lsof"}:
            assert set(env) <= {"PATH"}
        if kind == "server":
            assert "synthetic-corpus-password" in env["SERVER_CORPUS_DATABASE_URL"]
            assert env["GOOGLE_CLIENT_SECRET"] == "e2e-secret"
        if kind == "psql":
            assert env["PGPASSWORD"] == "synthetic-postgres"
            assert env["PGPASSFILE"] == "/synthetic/pgpass"
            assert args[args.index("-d") + 1].startswith("postgres://admin@127.0.0.1/")
        return SimpleNamespace(returncode=0, stdout="424242\n")

    monkeypatch.setattr(module.socket, "socket", Socket)
    monkeypatch.setattr(module.httpx, "Client", HTTP)
    monkeypatch.setattr(module.subprocess, "run", record)
    monkeypatch.setattr(
        module.subprocess, "Popen", lambda args, **kwargs: record(args, **kwargs) and Process()
    )
    with Stack(
        tmp_path,
        tmp_path,
        "http://127.0.0.1:8091",
        [29080, 29082, 29081, 29083],
        "postgres://admin@127.0.0.1/postgres",
        corpus,
    ):
        pass
    assert {kind for kind, _, _ in calls} == {"go", "psql", "server", "fakegoogle", "lsof"}
