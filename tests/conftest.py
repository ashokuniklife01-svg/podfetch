import json
import os
import re
import sys

os.environ["UNIKPOST_NO_APP"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from app import create_app  # noqa: E402


class FakeResp:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Records requests; replies from a queue of (matcher, response)."""

    def __init__(self):
        self.calls = []
        self.routes = []

    def add(self, method, url_part, resp):
        self.routes.append((method, url_part, resp))

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for i, (m, part, resp) in enumerate(self.routes):
            if m == method and part in url:
                self.routes.pop(i)
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected {method} {url}")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def put(self, url, **kw):
        return self.request("PUT", url, **kw)


@pytest.fixture
def fake_http():
    return FakeSession()


def make_env(tmp_path, **extra):
    env = {"TESTING": "1", "SECRET_KEY": "test-secret", "APP_PASSWORD": "pass123",
           "DATA_DIR": str(tmp_path), "BASE_URL": "https://post.example.com", "CRON_TOKEN": "crontok"}
    env.update(extra)
    pg = os.environ.get("TEST_DATABASE_URL")
    if pg:
        import psycopg
        with psycopg.connect(pg, autocommit=True) as c:
            c.execute("DROP SCHEMA public CASCADE")
            c.execute("CREATE SCHEMA public")
        env["DATABASE_URL"] = pg
    return env


@pytest.fixture
def app(tmp_path):
    return create_app(make_env(tmp_path, GOOGLE_CLIENT_ID="gid", GOOGLE_CLIENT_SECRET="gsec",
                               META_APP_ID="mid", META_APP_SECRET="msec"))


@pytest.fixture
def bare_app(tmp_path):
    return create_app(make_env(tmp_path))


def csrf_of(client):
    r = client.get("/login")
    m = re.search(r'name="csrf" value="([^"]+)"', r.get_data(as_text=True))
    return m.group(1)


def login(client, password="pass123"):
    tok = csrf_of(client)
    r = client.post("/login", data={"password": password, "csrf": tok})
    return r


@pytest.fixture
def client(app):
    c = app.test_client()
    assert login(c).status_code == 302
    with c.session_transaction() as s:
        c.csrf = s["csrf"]
    return c


def ext(app):
    return app.extensions["unikpost"]


def connect_channel(app, cid, external_id="EXT", token="tok", refresh="ref"):
    e = ext(app)
    from db import iso, utcnow
    from datetime import timedelta
    e["db"].update("channels", cid, {"status": "connected", "external_id": external_id,
                                     "token_enc": e["box"].encrypt(token),
                                     "refresh_enc": e["box"].encrypt(refresh),
                                     "token_expires_at": iso(utcnow() + timedelta(hours=1))})


def channel_id(app, platform, name_part):
    rows = ext(app)["db"].query("SELECT * FROM channels WHERE platform=?", (platform,))
    return next(r["id"] for r in rows if name_part.lower() in r["name"].lower())
