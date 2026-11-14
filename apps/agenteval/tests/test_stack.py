import pytest

from trachuan_agenteval.stack import child_environment, database_url, validate_loopback


@pytest.mark.parametrize(
    "url",
    [
        "postgres://user@example.com/postgres",
        "postgres://user@127.0.0.1/postgres?options=-c+search_path%3Dpublic",
        "postgres://user@127.0.0.1/postgres?host=example.com",
        "http://127.0.0.1/postgres",
    ],
)
def test_admin_url_cannot_escape_loopback(url):
    with pytest.raises(ValueError):
        database_url(url, "auth")


def test_database_url_preserves_only_e2e_options():
    assert database_url("postgres://fixture@127.0.0.1:25433/postgres?sslmode=disable", "auth") == (
        "postgres://fixture@127.0.0.1:25433/auth?sslmode=disable"
    )


def test_child_environment_drops_model_keys_and_inherited_server_settings():
    env = child_environment(
        {
            "PATH": "/bin",
            "ANTHROPIC_API_KEY": "synthetic",
            "OPENAI_API_KEY": "synthetic",
            "SERVER_GOOGLE_ISSUER": "bad",
            "PGPASSWORD": "synthetic-db",
        }
    )
    assert env == {"PATH": "/bin", "GOPROXY": "off"}


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1.evil/", "file:///tmp/x"])
def test_embedder_cannot_escape_loopback(url):
    with pytest.raises(ValueError):
        validate_loopback(url)


def test_stack_commands_use_real_corpus_readonly_and_omit_only_on_trimmed(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from trachuan_agenteval import stack as module

    commands, statements, children = [], [], []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, target):
            assert target[0] == "127.0.0.1"

    monkeypatch.setattr(module.socket, "socket", Socket)
    stack = module.Stack(
        tmp_path,
        tmp_path,
        "http://127.0.0.1:8091",
        [29080, 29082, 29081, 29083],
        "postgres://fixture@127.0.0.1/postgres",
        "postgres://corpus_reader@127.0.0.1/{database}",
    )
    monkeypatch.setattr(stack, "command", lambda args, env=None: commands.append((args, env)))
    monkeypatch.setattr(stack, "sql", lambda url, sql: statements.append((url, sql)))
    monkeypatch.setattr(
        stack, "spawn", lambda args, env: children.append((args, env)) or SimpleNamespace()
    )
    monkeypatch.setattr(stack, "wait", lambda *args: None)
    with stack:
        assert stack.name.startswith("trachuan_e2e_")
        servers = [env for args, env in children if args[-1] == "serve"]
        assert len(servers) == 2
        assert "SERVER_COMPACT_RELATED" not in servers[0]
        assert servers[1]["SERVER_COMPACT_RELATED"] == "omit"
        assert "default_transaction_read_only=on" in servers[0]["SERVER_CORPUS_DATABASE_URL"]
        assert all(
            "ANTHROPIC_API_KEY" not in env and "OPENAI_API_KEY" not in env for _, env in children
        )
        scratch = stack.scratch
    assert not scratch.exists()
    assert "corpus_vn_20261114_5" in statements[1][1]
    assert statements[-1][1] == 'DROP DATABASE IF EXISTS :"db" WITH (FORCE)\n'
    assert all("DROP DATABASE" not in sql or "corpus_vn" not in sql for _, sql in statements)


@pytest.mark.parametrize("corpus", [None, "postgres://fixture@127.0.0.1/corpus"])
def test_real_corpus_requires_separate_reader_role(tmp_path, corpus):
    from trachuan_agenteval.stack import Stack

    with pytest.raises(ValueError, match="separate corpus reader"):
        Stack(
            tmp_path,
            tmp_path,
            "http://127.0.0.1:8091",
            [29080, 29082, 29081, 29083],
            "postgres://fixture@127.0.0.1/postgres",
            corpus,
        )


@pytest.mark.parametrize("password", ["synthetic-password", "", "synthetic%40password"])
def test_admin_password_is_rejected_before_startup_without_exposing_value(tmp_path, password):
    from trachuan_agenteval.stack import Stack

    with pytest.raises(ValueError, match="CORPUS_TEST_ADMIN_URL") as error:
        Stack(
            tmp_path,
            tmp_path,
            "http://127.0.0.1:8091",
            [29080, 29082, 29081, 29083],
            f"postgres://admin:{password}@127.0.0.1/postgres",
            "postgres://reader:synthetic-reader@127.0.0.1/{database}",
        )
    assert "synthetic" not in str(error.value)
