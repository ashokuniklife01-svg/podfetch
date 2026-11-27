from app import create_app
from conftest import csrf_of, login, make_env


def test_requires_login(app):
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 302 and "/login" in r.location
    r = c.get("/api/queue")
    assert r.status_code == 401 and r.get_json()["error"]
    assert c.get("/health").get_json() == {"ok": True}


def test_login_wrong_then_right(app):
    c = app.test_client()
    r = login(c, "nope")
    assert r.status_code == 200 and b"Wrong password" in r.data
    r = login(c)
    assert r.status_code == 302 and r.location == "/"
    assert c.get("/").status_code == 200


def test_login_rate_limited(app):
    c = app.test_client()
    for _ in range(5):
        login(c, "bad")
    r = login(c)   # even the right password is blocked now
    assert r.status_code == 429 and b"Too many tries" in r.data
    # other IP is fine
    tok = csrf_of(c)
    r = c.post("/login", data={"password": "pass123", "csrf": tok}, headers={"X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 302


def test_login_requires_csrf(app):
    c = app.test_client()
    r = c.post("/login", data={"password": "pass123"})
    assert r.status_code == 400


def test_next_redirect_is_local(app):
    c = app.test_client()
    tok = csrf_of(c)
    r = c.post("/login?next=//evil.com", data={"password": "pass123", "csrf": tok})
    assert r.location == "/"
    c2 = app.test_client()
    tok = csrf_of(c2)
    r = c2.post("/login?next=/queue", data={"password": "pass123", "csrf": tok})
    assert r.location == "/queue"


def test_no_password_configured(tmp_path):
    a = create_app(make_env(tmp_path, APP_PASSWORD=""))
    r = a.test_client().get("/login")
    assert r.status_code == 503 and b"APP_PASSWORD" in r.data


def test_post_without_csrf_rejected(client):
    r = client.post("/channels/add", data={"platform": "youtube", "name": "X"})
    assert r.status_code == 400
    r = client.post("/api/posts", json={})
    assert r.status_code == 400 and "Security" in r.get_json()["error"]


def test_logout(client):
    r = client.post("/logout", data={"csrf": client.csrf})
    assert r.status_code == 302
    assert client.get("/").status_code == 302


def test_cron_token(app):
    c = app.test_client()
    assert c.get("/cron/run").status_code == 403
    assert c.get("/cron/run?token=wrong").status_code == 403
    r = c.get("/cron/run", headers={"X-Cron-Token": "crontok"})
    assert r.status_code == 200 and r.get_json()["ok"]


def test_cron_disabled_without_token(tmp_path):
    a = create_app(make_env(tmp_path, CRON_TOKEN=""))
    assert a.test_client().get("/cron/run?token=").status_code == 404


def test_security_headers(app):
    r = app.test_client().get("/login")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"


def test_secret_key_required(tmp_path, monkeypatch):
    import pytest
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    env = make_env(tmp_path, SECRET_KEY="")
    env.pop("TESTING")
    with pytest.raises(RuntimeError):
        create_app(env)
