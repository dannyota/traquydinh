import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

from trachuan_agenteval.oauth import authorize


def test_pkce_login_consent_and_token_exchange_without_network():
    seen = []
    state = ""
    challenge = ""

    def handle(request):
        nonlocal state, challenge
        seen.append(request)
        path = request.url.path
        if path == "/oauth/authorize":
            state = request.url.params["state"]
            challenge = request.url.params["code_challenge"]
            assert request.url.params["client_id"] == "agenteval"
            return httpx.Response(
                303,
                headers={
                    "location": "/oauth/consent/pending",
                    "set-cookie": "__Host-oauth=synthetic; Secure; HttpOnly; Path=/",
                },
            )
        if path == "/api/v1/auth/google/start":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:29081/authorize"})
        if request.url.port == 29081:
            assert "__Host-oauth" not in request.headers.get("cookie", "")
            if request.method == "GET":
                return httpx.Response(200, text='<input type="hidden" name="state" value="google">')
            assert parse_qs(request.content.decode())["email"] == ["agent-eval@example.invalid"]
            return httpx.Response(
                302,
                headers={
                    "location": "http://localhost:29080/api/v1/auth/google/callback?code=google"
                },
            )
        if path == "/api/v1/auth/google/callback":
            assert "__Host-oauth=synthetic" in request.headers["cookie"]
            return httpx.Response(
                302,
                headers={
                    "location": "/oauth/consent/pending",
                    "set-cookie": "__Host-session=synthetic-session; Secure; Path=/",
                },
            )
        if path == "/oauth/consent/pending":
            assert "__Host-session=synthetic-session" in request.headers["cookie"]
            if request.method == "GET":
                return httpx.Response(
                    200, text='<input type="hidden" name="csrf_token" value="form">'
                )
            assert request.headers["origin"] == "http://localhost:29080"
            assert parse_qs(request.content.decode())["csrf_token"] == ["form"]
            return httpx.Response(
                303,
                headers={
                    "location": f"http://127.0.0.1:12345/callback?code=auth&state={state}&iss=http%3A%2F%2Flocalhost%3A29080"
                },
            )
        if path == "/oauth/token":
            import base64
            import hashlib

            data = parse_qs(request.content.decode())
            verifier = data["code_verifier"][0]
            assert (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
                == challenge
            )
            assert data["resource"] == ["http://localhost:29080/mcp"]
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-access",
                    "refresh_token": "synthetic-refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        raise AssertionError(path)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), trust_env=False
        ) as client:
            token = await authorize(client, "http://localhost:29080", "http://127.0.0.1:29081")
            assert await token.access() == "synthetic-access"

    asyncio.run(run())
    assert len(seen) == 8


def test_oauth_rejects_nonlocal_server_before_request():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: pytest.fail("network"))
        ) as c:
            with pytest.raises(ValueError):
                await authorize(c, "https://example.com", "http://127.0.0.1:29081")

    asyncio.run(run())


def test_concurrent_refreshes_rotate_only_once():
    from trachuan_agenteval.oauth import Token

    class Client:
        def __init__(self):
            self.calls = 0

        async def post(self, url, data):
            self.calls += 1
            await asyncio.sleep(0)
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-new-access",
                    "refresh_token": "synthetic-new-refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )

    async def run():
        client = Client()
        token = Token(client, "http://localhost:29080", "synthetic-old", "synthetic-refresh", 0)
        values = await asyncio.gather(token.access(), token.access())
        assert values == ["synthetic-new-access", "synthetic-new-access"]
        assert client.calls == 1

    asyncio.run(run())
