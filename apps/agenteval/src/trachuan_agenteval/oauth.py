import asyncio
import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

from .stack import validate_loopback

CLIENT_ID = "agenteval"
REDIRECT = "http://127.0.0.1:12345/callback"


class Form(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "input" and values.get("type") == "hidden" and values.get("name"):
            self.fields[values["name"]] = values.get("value", "")


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class Browser:
    def __init__(self, client: httpx.AsyncClient, allowed: set[str]) -> None:
        self.client, self.allowed = client, allowed
        self.cookies: dict[str, dict[str, str]] = {}

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        host = origin(url)
        if host not in self.allowed:
            raise RuntimeError("OAuth redirect left the local stack")
        headers = kwargs.pop("headers", {})
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.get(host, {}).items())
        response = await self.client.request(
            method, url, headers=headers, follow_redirects=False, **kwargs
        )
        for header in response.headers.get_list("set-cookie"):
            parsed = SimpleCookie()
            parsed.load(header)
            for key, value in parsed.items():
                self.cookies.setdefault(host, {})[key] = value.value
        return response


def location(response: httpx.Response, base: str, status: int) -> str:
    if response.status_code != status or not response.headers.get("location"):
        raise RuntimeError("OAuth redirect failed")
    return urljoin(base, response.headers["location"])


@dataclass
class Token:
    client: httpx.AsyncClient
    server: str
    value: str = field(repr=False)
    refresh: str = field(repr=False)
    expires: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def access(self) -> str:
        async with self.lock:
            if time.monotonic() >= self.expires - 60:
                response = await self.client.post(
                    self.server + "/oauth/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": CLIENT_ID,
                        "resource": self.server + "/mcp",
                        "refresh_token": self.refresh,
                    },
                )
                self.update(response)
            return self.value

    def update(self, response: httpx.Response) -> None:
        if response.status_code != 200:
            raise RuntimeError("OAuth token exchange failed")
        data = response.json()
        if (
            data.get("token_type", "").lower() != "bearer"
            or not data.get("access_token")
            or not data.get("refresh_token")
            or data.get("expires_in", 0) <= 0
        ):
            raise RuntimeError("invalid OAuth token response")
        self.value, self.refresh = data["access_token"], data["refresh_token"]
        self.expires = time.monotonic() + data["expires_in"]


async def authorize(client: httpx.AsyncClient, server: str, google: str) -> Token:
    validate_loopback(server)
    validate_loopback(google)
    browser = Browser(client, {server, google})
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": server + "/mcp",
        "scope": "mcp:read",
    }
    response = await browser.request("GET", server + "/oauth/authorize?" + urlencode(params))
    consent = location(response, server, 303)
    if origin(consent) != server or not urlsplit(consent).path.startswith("/oauth/consent/"):
        raise RuntimeError("invalid consent redirect")
    response = await browser.request(
        "GET",
        server
        + "/api/v1/auth/google/start?"
        + urlencode(
            {
                "next": urlsplit(consent).path,
            }
        ),
    )
    google_start = location(response, server, 302)
    if origin(google_start) != google:
        raise RuntimeError("invalid fake Google redirect")
    response = await browser.request("GET", google_start)
    if response.status_code != 200:
        raise RuntimeError("fake Google form unavailable")
    form = Form(response.text).fields
    form.update(email="agent-eval@example.invalid", email_verified="on")
    response = await browser.request("POST", google + "/authorize", data=form)
    callback = location(response, google, 302)
    if origin(callback) != server or urlsplit(callback).path != "/api/v1/auth/google/callback":
        raise RuntimeError("invalid Google callback")
    response = await browser.request("GET", callback)
    if location(response, server, 302) != consent:
        raise RuntimeError("Google login did not return to consent")
    response = await browser.request("GET", consent)
    form = Form(response.text).fields
    if response.status_code != 200 or not form.get("csrf_token"):
        raise RuntimeError("consent form unavailable")
    form["decision"] = "approve"
    response = await browser.request("POST", consent, data=form, headers={"Origin": server})
    target = urlsplit(location(response, server, 303))
    query = parse_qs(target.query)
    if (
        target._replace(query="", fragment="").geturl() != REDIRECT
        or query.get("state") != [state]
        or query.get("iss") != [server]
        or len(query.get("code", [])) != 1
    ):
        raise RuntimeError("invalid OAuth authorization response")
    response = await client.post(
        server + "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT,
            "resource": server + "/mcp",
            "code": query["code"][0],
            "code_verifier": verifier,
        },
    )
    token = Token(client, server, "", "", 0)
    token.update(response)
    return token
