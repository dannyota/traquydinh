import ipaddress
import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx

from .processes import child_environment


class AdminURLPasswordError(ValueError):
    pass


def loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_loopback(url: str) -> None:
    value = urlsplit(url)
    if (
        value.scheme not in {"http", "https"}
        or not loopback(value.hostname or "")
        or value.username
        or value.password
        or value.fragment
    ):
        raise ValueError("a loopback HTTP URL is required")


def database_url(admin: str, name: str) -> str:
    try:
        url = urlsplit(admin)
        params = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True)
        if (
            url.scheme not in {"postgres", "postgresql"}
            or not loopback(url.hostname or "")
            or url.fragment
            or any(k not in {"sslmode", "connect_timeout"} for k, _ in params)
        ):
            raise ValueError()
        return urlunsplit((url.scheme, url.netloc, "/" + name, url.query, ""))
    except ValueError:
        raise ValueError("invalid local PostgreSQL administrator URL") from None


class Stack:
    def __init__(
        self,
        root: Path,
        out: Path,
        embedder: str,
        ports: list[int],
        admin: str,
        corpus: str | None = None,
    ) -> None:
        validate_loopback(embedder)
        database_url(admin, "postgres")
        if urlsplit(admin).password is not None:
            raise AdminURLPasswordError(
                "CORPUS_TEST_ADMIN_URL must not contain a password; use PGPASSWORD or PGPASSFILE"
            )
        if not corpus:
            raise ValueError("a separate corpus reader URL is required")
        database_url(corpus, "{database}")
        admin_user = unquote(urlsplit(admin).username or "")
        corpus_user = unquote(urlsplit(corpus).username or "")
        if not admin_user or not corpus_user or admin_user == corpus_user:
            raise ValueError("a separate corpus reader role is required")
        if len(ports) != 4 or len(set(ports)) != 4 or any(not 0 < p < 65536 for p in ports):
            raise ValueError("four distinct loopback ports are required")
        self.root, self.out, self.embedder, self.ports = root, out, embedder, ports
        self.admin, self.corpus = admin, corpus
        self.env = child_environment(os.environ)
        self.pg_env = child_environment(os.environ, "postgres")
        self.system_env = child_environment(os.environ, "system")
        self.processes: list[subprocess.Popen] = []
        self.created = False
        self.temporary: tempfile.TemporaryDirectory | None = None
        self.name = "trachuan_e2e_" + secrets.token_hex(6)
        self.endpoints: dict[str, tuple[str, str]] = {}

    def command(self, args: list[str], env: dict | None = None, sql: str | None = None) -> None:
        result = subprocess.run(
            args,
            cwd=self.root,
            env=env if env is not None else self.env,
            input=sql,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("local stack command failed: " + Path(args[0]).name)

    def sql(self, url: str, sql: str) -> None:
        self.command(
            ["psql", "-X", "-d", url, "-v", "ON_ERROR_STOP=1", "-v", "db=" + self.name],
            self.pg_env,
            sql,
        )

    def __enter__(self) -> Stack:
        try:
            self.start()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def spawn(self, args: list[str], env: dict) -> subprocess.Popen:
        process = subprocess.Popen(
            args,
            cwd=self.root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.processes.append(process)
        return process

    def wait(self, url: str, process: subprocess.Popen, port: int) -> None:
        with httpx.Client(trust_env=False, timeout=1) as client:
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError("local stack process exited")
                owners = subprocess.run(
                    ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=self.system_env,
                )
                pids = set(owners.stdout.split())
                if owners.returncode not in {0, 1} or (pids and pids != {str(process.pid)}):
                    raise RuntimeError("loopback port is not owned by the child")
                try:
                    if pids == {str(process.pid)} and client.get(url).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
        raise RuntimeError("local stack readiness timed out")

    def start(self) -> None:
        for port in self.ports:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        self.temporary = tempfile.TemporaryDirectory(prefix=".stack-", dir=self.out)
        self.scratch = Path(self.temporary.name)
        for name, target in (
            ("server", "./apps/server/cmd/server"),
            ("fakegoogle", "./apps/server/cmd/fakegoogle"),
            ("agentmatch", "./apps/corpus/cmd/agentmatch"),
        ):
            self.command(["go", "build", "-o", str(self.scratch / name), target])
        clients = self.scratch / "clients.json"
        clients.write_text(
            json.dumps(
                [
                    {
                        "client_id": "agenteval",
                        "client_name": "Agent evaluation",
                        "redirect_uris": ["http://127.0.0.1:12345/callback"],
                    }
                ]
            ),
            encoding="utf-8",
        )
        keyfile = self.scratch / "account_keys"
        keyfile.write_text("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n", encoding="ascii")
        keyfile.chmod(0o600)
        auth = database_url(self.admin, self.name)
        corpus = database_url(self.corpus, "{database}")
        corpus += ("&" if "?" in corpus else "?") + urlencode(
            {"default_transaction_read_only": "on"}
        )
        base_env = self.pg_env | {
            "SERVER_AUTH_DATABASE_URL": auth,
            "SERVER_CORPUS_DATABASE_URL": corpus,
            "SERVER_QUERY_EMBEDDER_URL": self.embedder,
            "SERVER_ACCOUNT_KEYS_FILE": str(keyfile),
            "SERVER_OAUTH_CLIENTS": str(clients),
            "SERVER_SIGNUP_EMAILS": "agent-eval@example.invalid",
            "GOOGLE_CLIENT_ID": "e2e-client",
            "GOOGLE_CLIENT_SECRET": "e2e-secret",
        }
        self.sql(self.admin, 'CREATE DATABASE :"db"\n')
        self.created = True
        first = base_env | {"SERVER_PUBLIC_URL": f"http://localhost:{self.ports[0]}"}
        self.command([str(self.scratch / "server"), "migrate"], first)
        self.sql(
            auth,
            "INSERT INTO corpus_release VALUES "
            "('vn', 'corpus_vn_20261114_5', now(), 'agenteval')\n",
        )
        for name, server_port, google_port in (
            ("normal", self.ports[0], self.ports[2]),
            ("trimmed", self.ports[1], self.ports[3]),
        ):
            server, google = f"http://localhost:{server_port}", f"http://127.0.0.1:{google_port}"
            env = base_env | {
                "SERVER_LISTEN": f"127.0.0.1:{server_port}",
                "SERVER_PUBLIC_URL": server,
                "SERVER_GOOGLE_ISSUER": google,
            }
            if name == "trimmed":
                env["SERVER_COMPACT_RELATED"] = "omit"
            process = self.spawn(
                [
                    str(self.scratch / "fakegoogle"),
                    "-listen",
                    f"127.0.0.1:{google_port}",
                    "-client-id",
                    "e2e-client",
                    "-client-secret",
                    "e2e-secret",
                    "-redirect-uri",
                    server + "/api/v1/auth/google/callback",
                ],
                self.system_env,
            )
            self.wait(google + "/.well-known/openid-configuration", process, google_port)
            process = self.spawn([str(self.scratch / "server"), "serve"], env)
            self.wait(server + "/readyz", process, server_port)
            self.endpoints[name] = (server, google)

    def close(self) -> None:
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.processes.clear()
        try:
            if self.created:
                self.sql(self.admin, 'DROP DATABASE IF EXISTS :"db" WITH (FORCE)\n')
                self.created = False
        finally:
            if self.temporary:
                self.temporary.cleanup()
