"""Unit tests with a mocked fal.ai API (no network, no real key needed)."""
import io
import json
import os
import sys
import glob

import jsonschema
import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as appmod  # noqa: E402
from models import MODELS, ASPECTS  # noqa: E402

SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "schemas")


def load_schema(endpoint):
    f = os.path.join(SCHEMA_DIR, endpoint.replace("/", "_") + ".json")
    doc = json.load(open(f))
    comps = doc["components"]["schemas"]
    # input schema = the requestBody of the POST /{endpoint}
    for path, ops in doc["paths"].items():
        post = ops.get("post")
        if post and "requestBody" in post:
            ref = post["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            name = ref.split("/")[-1]
            schema = dict(comps[name])
            schema["components"] = {"schemas": comps}
            return schema
    raise AssertionError("no input schema for " + endpoint)


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class FakeFal:
    """Routes fake HTTP calls. Configure .status_seq / .result / .submit_resp."""

    def __init__(self):
        self.calls = []
        self.submit_resp = None
        self.status_seq = [{"status": "IN_QUEUE", "queue_position": 2},
                           {"status": "IN_PROGRESS", "logs": [{"message": "step 1"}]},
                           {"status": "COMPLETED"}]
        self.result = None
        self.raise_exc = None

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if self.raise_exc:
            raise self.raise_exc
        assert kw["headers"].get("Authorization", "").startswith("Key ") or method == "PUT"
        if method == "POST" and url.startswith("https://rest.fal.ai/storage/upload/initiate"):
            return Resp(200, {"upload_url": "https://storage.example/put?sig=1", "file_url": "https://v3.fal.media/files/x/start.jpg"})
        if method == "PUT":
            return Resp(200, "")
        if method == "POST" and url.startswith("https://queue.fal.run/"):
            if self.submit_resp:
                return self.submit_resp
            ep = url[len("https://queue.fal.run/"):]
            base = "https://queue.fal.run/" + "/".join(ep.split("/")[:2]) + "/requests/req-123"
            return Resp(200, {"request_id": "req-123", "status_url": base + "/status", "response_url": base, "cancel_url": base + "/cancel"})
        if method == "GET" and url.endswith("/status"):
            s = self.status_seq.pop(0) if len(self.status_seq) > 1 else self.status_seq[0]
            return Resp(202 if s["status"] != "COMPLETED" else 200, dict(s, request_id="req-123"))
        if method == "GET" and "/requests/" in url:
            return Resp(200, self.result)
        raise AssertionError("unexpected call %s %s" % (method, url))


@pytest.fixture
def fal(monkeypatch):
    f = FakeFal()
    monkeypatch.setattr(appmod.requests, "request", f)
    monkeypatch.setenv("FAL_KEY", "test-key-id:test-secret")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    appmod._hits.clear()
    return f


@pytest.fixture
def client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def combos(m):
    durs = m.get("durations") or [None]
    auds = [True, False] if m.get("audio") else [False]
    ress = m.get("resolutions") or [None]
    for a in ASPECTS:
        for d in durs:
            for au in auds:
                for r in ress:
                    yield a, d, au, r


def body_for(m, aspect, d, au, r):
    b = {"mode": m["mode"], "model": m["id"], "prompt": "a founder on a podcast set", "aspect": aspect}
    if d is not None:
        b["duration"] = d
    if r is not None:
        b["resolution"] = r
    b["audio"] = au
    if m["mode"] == "i2v":
        b["image_url"] = "https://v3.fal.media/files/x/start.jpg"
    return b


# ------------------------------------------------------------------ catalogue
def test_three_modes_present():
    modes = {m["mode"] for m in MODELS}
    assert modes == {"t2v", "t2i", "i2v"}


@pytest.mark.parametrize("m", MODELS, ids=[m["id"] for m in MODELS])
def test_payloads_match_fal_schema(m):
    schema = load_schema(m["endpoint"])
    for aspect, d, au, r in combos(m):
        o = {"prompt": "a podcast host", "aspect": aspect, "duration": d, "audio": au, "resolution": r,
             "negative": "blurry", "image_url": "https://v3.fal.media/files/x/start.jpg" if m["mode"] == "i2v" else None}
        payload = m["build"](o)
        jsonschema.validate(payload, schema)
        # aspect is honoured either via aspect_ratio, image_size, or (i2v) the pre-cropped image
        if m["mode"] != "i2v" or "aspect_ratio" in payload:
            if "aspect_ratio" in payload:
                assert payload["aspect_ratio"] == aspect
            else:
                sz = payload["image_size"]
                if isinstance(sz, dict):
                    assert (sz["width"] > sz["height"]) == (aspect == "16:9")
                else:
                    assert sz == ("landscape_16_9" if aspect == "16:9" else "portrait_16_9")
        assert m["price"](o) > 0


# ------------------------------------------------------------------ config / no key
def test_config_without_key(client, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    d = client.get("/api/config").get_json()
    assert d["ok"] and d["key_configured"] is False and d["password_required"] is False
    assert len(d["models"]) == len(MODELS)
    assert "test-key" not in json.dumps(d)


def test_generate_without_key_is_friendly(client, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    r = client.post("/api/generate", json=body_for(MODELS[0], "16:9", 8, True, None))
    assert r.status_code == 503
    assert r.get_json()["error"]["code"] == "no_key"
    assert "FAL_KEY" in r.get_json()["error"]["message"]


def test_key_never_in_responses(client, fal):
    for path in ("/api/config", "/health", "/"):
        assert b"test-secret" not in client.get(path).data


# ------------------------------------------------------------------ full flow for every model & aspect
@pytest.mark.parametrize("m", MODELS, ids=[m["id"] for m in MODELS])
@pytest.mark.parametrize("aspect", ASPECTS)
def test_full_flow(client, fal, m, aspect):
    d = m.get("default_duration")
    r_ = m.get("default_resolution")
    fal.result = ({"images": [{"url": "https://v3.fal.media/files/a/out.png"}]} if m["mode"] == "t2i"
                  else {"video": {"url": "https://v3.fal.media/files/a/out.mp4"}})
    r = client.post("/api/generate", json=body_for(m, aspect, d, bool(m.get("audio")), r_))
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["request_id"] == "req-123" and j["estimate"] > 0
    method, url, kw = fal.calls[-1]
    assert url == "https://queue.fal.run/" + m["endpoint"]
    sent = kw["json"]
    jsonschema.validate(sent, load_schema(m["endpoint"]))
    if "aspect_ratio" in sent:
        assert sent["aspect_ratio"] == aspect
    states = [client.get("/api/status/" + j["job"]).get_json() for _ in range(3)]
    assert [s["state"] for s in states] == ["queued", "running", "done"]
    assert states[0]["position"] == 2
    out = states[2]["outputs"][0]
    assert out["type"] == ("image" if m["mode"] == "t2i" else "video")
    assert out["url"].startswith("https://v3.fal.media/")
    # status/result URLs must use owner/app only
    owner_app = "/".join(m["endpoint"].split("/")[:2])
    assert all(("/" + owner_app + "/requests/") in c[1] for c in fal.calls if c[0] == "GET")


def test_upload_flow(client, fal):
    data = {"image": (io.BytesIO(b"\xff\xd8\xff" + b"0" * 1000), "start.jpg", "image/jpeg")}
    r = client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["url"] == "https://v3.fal.media/files/x/start.jpg"
    assert fal.calls[0][2]["json"]["content_type"] == "image/jpeg"
    assert fal.calls[1][0] == "PUT"


def test_upload_rejects_non_image(client, fal):
    data = {"image": (io.BytesIO(b"hello"), "a.txt", "text/plain")}
    r = client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "bad_image"


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize("patch,code", [
    ({"prompt": ""}, "no_prompt"),
    ({"prompt": "x" * 2001}, "long_prompt"),
    ({"prompt": "hi"}, "short_prompt"),
    ({"aspect": "1:1"}, "bad_aspect"),
    ({"model": "nope"}, "bad_model"),
    ({"mode": "t2i"}, "bad_model"),
    ({"duration": 7}, "bad_duration"),
])
def test_validation(client, fal, patch, code):
    b = body_for(MODELS[0], "16:9", 8, True, None)
    b.update(patch)
    r = client.post("/api/generate", json=b)
    assert r.status_code == 400 and r.get_json()["error"]["code"] == code
    assert not any(c[0] == "POST" and "queue" in c[1] for c in fal.calls)


def test_i2v_requires_image(client, fal):
    m = next(x for x in MODELS if x["mode"] == "i2v")
    b = body_for(m, "9:16", m["default_duration"], False, m.get("default_resolution"))
    b.pop("image_url")
    r = client.post("/api/generate", json=b)
    assert r.get_json()["error"]["code"] == "no_image"


# ------------------------------------------------------------------ error mapping
@pytest.mark.parametrize("status,body,code", [
    (401, {"detail": "No user found for Key ID and Secret"}, "bad_key"),
    (403, {"detail": "User is locked. Reason: Exhausted balance. Top up your balance at fal.ai/dashboard/billing."}, "no_credits"),
    (422, {"detail": [{"loc": ["body", "prompt"], "msg": "The content could not be processed because it contained material flagged by a content checker.", "type": "content_policy_violation"}]}, "content_policy"),
    (504, {"detail": [{"msg": "Generation timeout", "type": "generation_timeout"}]}, "timeout"),
    (422, {"detail": [{"loc": ["body", "duration"], "msg": "bad", "type": "enum"}]}, "invalid_input"),
    (500, "Internal Server Error", "provider_down"),
    (429, {"detail": "Too many requests"}, "busy"),
])
def test_submit_errors(client, fal, status, body, code):
    fal.submit_resp = Resp(status, body)
    r = client.post("/api/generate", json=body_for(MODELS[0], "16:9", 8, True, None))
    j = r.get_json()
    assert j["ok"] is False and j["error"]["code"] == code, j
    assert "test-secret" not in r.get_data(as_text=True)


def test_network_timeout(client, fal):
    fal.raise_exc = requests.Timeout()
    r = client.post("/api/generate", json=body_for(MODELS[0], "16:9", 8, True, None))
    assert r.status_code == 504 and r.get_json()["error"]["code"] == "timeout"


def test_failed_job_in_status(client, fal):
    j = client.post("/api/generate", json=body_for(MODELS[0], "16:9", 8, True, None)).get_json()
    fal.status_seq = [{"status": "COMPLETED", "error": "flagged by content checker", "error_type": "content_policy_violation"}]
    s = client.get("/api/status/" + j["job"]).get_json()
    assert s["state"] == "error" and s["error"]["code"] == "content_policy"


def test_result_error(client, fal):
    j = client.post("/api/generate", json=body_for(MODELS[0], "16:9", 8, True, None)).get_json()
    fal.status_seq = [{"status": "COMPLETED"}]
    fal.result = {"detail": [{"msg": "Generation timeout", "type": "generation_timeout"}]}
    orig = fal.__call__

    def patched(method, url, **kw):
        if method == "GET" and "/requests/" in url and not url.endswith("/status"):
            fal.calls.append((method, url, kw))
            return Resp(504, fal.result)
        return orig(method, url, **kw)
    appmod.requests.request = patched
    s = client.get("/api/status/" + j["job"]).get_json()
    assert s["state"] == "error" and s["error"]["code"] == "timeout"


def test_empty_result(client, fal):
    j = client.post("/api/generate", json=body_for(MODELS[0], "16:9", 8, True, None)).get_json()
    fal.status_seq = [{"status": "COMPLETED"}]
    fal.result = {"something": 1}
    s = client.get("/api/status/" + j["job"]).get_json()
    assert s["state"] == "error" and s["error"]["code"] == "no_media"


def test_tampered_token(client, fal):
    j = client.post("/api/generate", json=body_for(MODELS[0], "16:9", 8, True, None)).get_json()
    a, b = j["job"].split(".")
    forged = appmod._b64(json.dumps({"rid": "x", "m": "veo31", "status_url": "https://evil.example/status", "response_url": "https://evil.example"}).encode()) + "." + b
    r = client.get("/api/status/" + forged)
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "bad_job"
    assert not any("evil" in c[1] for c in fal.calls)


# ------------------------------------------------------------------ password & rate limit
def test_password(client, fal, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert client.get("/api/config").get_json()["password_required"] is True
    b = body_for(MODELS[0], "16:9", 8, True, None)
    r = client.post("/api/generate", json=b)
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "password"
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    r = client.post("/api/generate", json=b, headers={"X-App-Password": "s3cret"})
    assert r.status_code == 200
    tok = r.get_json()["job"]
    assert client.get("/api/status/" + tok).status_code == 401
    assert client.get("/api/status/" + tok, headers={"X-App-Password": "s3cret"}).status_code == 200


def test_rate_limit(client, fal):
    b = body_for(MODELS[-1], "16:9", None, False, None)
    codes = [client.post("/api/generate", json=b).status_code for _ in range(14)]
    assert codes[:12] == [200] * 12 and codes[12] == 429


def test_download_allowlist(client, fal):
    r = client.get("/api/download?url=" + requests.utils.quote("https://evil.example/x.mp4"))
    assert r.status_code == 400
    r = client.get("/api/download?url=" + requests.utils.quote("https://storage.googleapis.com/other/x.mp4"))
    assert r.status_code == 400


def test_spa_and_health(client):
    assert client.get("/health").get_json() == {"ok": True}
    assert b"UNIK" in client.get("/").data
    assert client.get("/api/nope").status_code == 404
