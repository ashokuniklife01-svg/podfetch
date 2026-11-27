import io
import json
import os
from datetime import timedelta

import pytest

from conftest import channel_id, connect_channel, ext
from db import iso, parse_iso, utcnow
from providers import ProviderError


def upload(client, data=b"\x00" * 3000, name="clip.mp4", w=1080, h=1920, dur=40, chunk=1024):
    r = client.post("/api/uploads", json={"name": name, "size": len(data), "type": "video/mp4",
                                          "width": w, "height": h, "duration": dur},
                    headers={"X-CSRF-Token": client.csrf})
    assert r.status_code == 200, r.get_json()
    mid = r.get_json()["id"]
    off = 0
    while off < len(data):
        part = data[off:off + chunk]
        r = client.put(f"/api/uploads/{mid}/chunk?offset={off}", data=part, headers={"X-CSRF-Token": client.csrf})
        assert r.status_code == 200, r.get_json()
        off = r.get_json()["received"]
    r = client.post(f"/api/uploads/{mid}/complete", headers={"X-CSRF-Token": client.csrf})
    assert r.status_code == 200, r.get_json()
    return mid


def post(client, body):
    return client.post("/api/posts", json=body, headers={"X-CSRF-Token": client.csrf})


class FakeYT:
    def __init__(self):
        self.uploads = []
        self.fail = None

    def build_metadata(self, *a, **kw):
        from providers.youtube import YouTubeClient
        return YouTubeClient.build_metadata(*a, **kw)

    def refresh(self, rt):
        return {"access_token": "fresh", "expires_in": 3600}

    def start_upload(self, token, meta, size, mime):
        if self.fail:
            raise self.fail
        self.uploads.append({"token": token, "meta": meta, "size": size})
        return "https://up/session"

    def upload_file(self, url, path, get_token, progress=None):
        assert os.path.exists(path)
        if progress:
            progress(os.path.getsize(path), os.path.getsize(path))
        return {"id": f"VID{len(self.uploads)}", "status": {"privacyStatus": "public"}}


class FakeMeta:
    def __init__(self):
        self.calls = []
        self.status = "FINISHED"

    def __getattr__(self, name):
        def f(*a, **kw):
            self.calls.append((name, a, kw))
            if name == "ig_create_container":
                return f"C{len(self.calls)}"
            if name == "ig_wait":
                return self.status
            if name == "ig_publish":
                return "M1", "https://instagram.com/p/M1"
            return "R1", "https://facebook.com/R1"
        return f


@pytest.fixture
def env(app, client):
    e = ext(app)
    e["engine"]._yt = FakeYT()
    e["engine"]._meta = FakeMeta()
    yt1 = channel_id(app, "youtube", "Business Growth")
    yt2 = channel_id(app, "youtube", "Telugu")
    ig = channel_id(app, "instagram", "venukalyan01")
    fb = channel_id(app, "facebook", "Venu")
    for cid, ext_id in ((yt1, "UC1"), (yt2, "UC2"), (ig, "IG1"), (fb, "P1")):
        connect_channel(app, cid, ext_id)
    return {"e": e, "c": client, "yt1": yt1, "yt2": yt2, "ig": ig, "fb": fb, "db": e["db"], "eng": e["engine"]}


def dests(db, pid):
    return db.query("SELECT * FROM destinations WHERE post_id=? ORDER BY id", (pid,))


def test_short_to_two_youtube_channels_separate_titles(env):
    c, db = env["c"], env["db"]
    mid = upload(c)
    r = post(c, {"type": "video", "media_ids": [mid], "destinations": [
        {"channel_id": env["yt1"], "title": "Growth tips", "body": "A"},
        {"channel_id": env["yt2"], "title": "తెలుగు టిప్స్", "body": "B", "privacy": "unlisted"}]})
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()["id"]
    ds = dests(db, pid)
    assert [d["kind"] for d in ds] == ["short", "short"] and [d["status"] for d in ds] == ["queued", "queued"]
    assert env["eng"].tick(wait=True) == 2
    ups = env["e"]["engine"]._yt.uploads
    assert ups[0]["meta"]["snippet"]["title"] == "Growth tips #Shorts"
    assert ups[1]["meta"]["snippet"]["title"] == "తెలుగు టిప్స్ #Shorts"
    assert ups[1]["meta"]["status"]["privacyStatus"] == "unlisted"
    ds = dests(db, pid)
    assert all(d["status"] == "published" for d in ds)
    assert ds[0]["remote_url"].startswith("https://youtube.com/shorts/")


def test_long_video_override_and_schedule_youtube(env):
    c, db = env["c"], env["db"]
    mid = upload(c, w=1920, h=1080, dur=1200)
    when = utcnow() + timedelta(days=2)
    r = post(c, {"type": "video", "media_ids": [mid], "destinations": [
        {"channel_id": env["yt1"], "title": "Long one", "schedule_at": iso(when)}]})
    pid = r.get_json()["id"]
    d = dests(db, pid)[0]
    assert d["kind"] == "long" and d["mode"] == "native"
    env["eng"].tick(wait=True)
    up = env["eng"]._yt.uploads[0]["meta"]
    assert up["status"]["publishAt"] == iso(when) and up["status"]["privacyStatus"] == "private"
    assert up["snippet"]["title"] == "Long one"
    d = dests(db, pid)[0]
    assert d["status"] == "scheduled" and d["remote_url"].startswith("https://youtu.be/")
    # once the time passes the queue shows it as published
    env["eng"].now = lambda: when + timedelta(minutes=1)
    env["eng"].tick(wait=True)
    assert dests(db, pid)[0]["status"] == "published"


def test_manual_override_to_long(env):
    c, db = env["c"], env["db"]
    mid = upload(c)
    pid = post(c, {"type": "video", "override": "long", "media_ids": [mid],
                   "destinations": [{"channel_id": env["yt1"], "title": "t"}, {"channel_id": env["fb"], "body": "x"}]}).get_json()["id"]
    assert [d["kind"] for d in dests(db, pid)] == ["long", "video"]


def test_facebook_native_schedule_and_reel(env):
    c, db, eng = env["c"], env["db"], env["eng"]
    mid = upload(c, dur=30)
    when = utcnow() + timedelta(hours=5)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [
        {"channel_id": env["fb"], "body": "reel cap", "schedule_at": iso(when)}]}).get_json()["id"]
    d = dests(db, pid)[0]
    assert d["kind"] == "reel" and d["mode"] == "native" and d["status"] == "queued"
    eng.tick(wait=True)
    name, args, kw = eng._meta.calls[0]
    assert name == "fb_reel" and args[0] == "P1" and args[1] == "tok"
    assert args[2].startswith("https://post.example.com/m/") and args[4] == pytest.approx(when.timestamp(), abs=1)
    assert dests(db, pid)[0]["status"] == "scheduled"


def test_facebook_soon_schedule_is_app_held(env):
    c, db, eng = env["c"], env["db"], env["eng"]
    mid = upload(c, w=1920, h=1080, dur=300)
    when = utcnow() + timedelta(minutes=5)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [
        {"channel_id": env["fb"], "title": "T", "body": "B", "schedule_at": iso(when)}]}).get_json()["id"]
    assert dests(db, pid)[0]["status"] == "waiting"
    assert eng.tick(wait=True) == 0
    eng.now = lambda: when + timedelta(seconds=5)
    assert eng.tick(wait=True) == 1
    name, args, kw = eng._meta.calls[0]
    assert name == "fb_video" and args[5] is None       # published immediately at its time
    assert dests(db, pid)[0]["status"] == "published"


def test_instagram_now(env):
    c, db, eng = env["c"], env["db"], env["eng"]
    mid = upload(c)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [{"channel_id": env["ig"], "body": "hi"}]}).get_json()["id"]
    eng.tick(wait=True)
    names = [x[0] for x in eng._meta.calls]
    assert names == ["ig_create_container", "ig_wait", "ig_publish"]
    d = dests(db, pid)[0]
    assert d["status"] == "published" and d["remote_url"] == "https://instagram.com/p/M1"


def test_instagram_processing_then_publish(env):
    c, db, eng = env["c"], env["db"], env["eng"]
    eng._meta.status = "IN_PROGRESS"
    mid = upload(c)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [{"channel_id": env["ig"], "body": "hi"}]}).get_json()["id"]
    eng.tick(wait=True)
    d = dests(db, pid)[0]
    assert d["status"] == "processing" and d["container_id"]
    eng._meta.status = "FINISHED"
    eng.tick(wait=True)
    assert dests(db, pid)[0]["status"] == "published"
    assert [x[0] for x in eng._meta.calls].count("ig_create_container") == 1


def test_instagram_scheduled_far_future(env):
    c, db, eng = env["c"], env["db"], env["eng"]
    mid = upload(c)
    t0 = utcnow()
    when = t0 + timedelta(days=3)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [
        {"channel_id": env["ig"], "body": "later", "schedule_at": iso(when)}]}).get_json()["id"]
    assert dests(db, pid)[0]["status"] == "waiting"
    assert eng.tick(wait=True) == 0                      # too early to even prepare
    eng.now = lambda: when - timedelta(hours=10)         # inside the 20 h prep window
    assert eng.tick(wait=True) == 1
    d = dests(db, pid)[0]
    assert d["status"] == "waiting" and d["container_id"] and "Ready" in d["detail"]
    assert "ig_publish" not in [x[0] for x in eng._meta.calls]
    assert eng.tick(wait=True) == 0
    eng.now = lambda: when + timedelta(seconds=1)
    eng.tick(wait=True)
    assert dests(db, pid)[0]["status"] == "published"


def test_photo_carousel_and_youtube_blocked(env):
    from PIL import Image
    c, db, eng = env["c"], env["db"], env["eng"]
    mids = []
    for i in range(2):
        buf = io.BytesIO()
        Image.new("RGB", (1080, 1350), (i * 50, 10, 10)).save(buf, "PNG")
        r = c.post("/api/uploads", json={"name": f"p{i}.png", "size": len(buf.getvalue()), "type": "image/png"},
                   headers={"X-CSRF-Token": c.csrf})
        mid = r.get_json()["id"]
        c.put(f"/api/uploads/{mid}/chunk?offset=0", data=buf.getvalue(), headers={"X-CSRF-Token": c.csrf})
        j = c.post(f"/api/uploads/{mid}/complete", headers={"X-CSRF-Token": c.csrf}).get_json()
        assert j["width"] == 1080 and j["height"] == 1350
        mids.append(mid)
    m = db.one("SELECT * FROM media WHERE id=?", (mids[0],))
    assert m["jpeg_path"] and os.path.exists(m["jpeg_path"])
    r = post(c, {"type": "photo", "media_ids": mids, "destinations": [{"channel_id": env["yt1"], "title": "x"}]})
    assert r.status_code == 400 and "can't take" in r.get_json()["error"]
    pid = post(c, {"type": "photo", "media_ids": mids, "destinations": [
        {"channel_id": env["ig"], "body": "cap"}, {"channel_id": env["fb"], "body": "cap"}]}).get_json()["id"]
    assert [d["kind"] for d in dests(db, pid)] == ["carousel", "photos"]
    eng.tick(wait=True)
    creates = [x for x in eng._meta.calls if x[0] == "ig_create_container"]
    assert len(creates) == 3 and creates[0][2]["is_carousel_item"] is True
    assert creates[0][1][3].endswith("/ig.jpg")           # IG gets the JPEG copy
    assert any(x[0] == "fb_photos" for x in eng._meta.calls)
    assert {d["status"] for d in dests(db, pid)} == {"published"}


def test_text_post_facebook_only(env):
    c, db, eng = env["c"], env["db"], env["eng"]
    r = post(c, {"type": "text", "destinations": [{"channel_id": env["ig"], "body": "x"}]})
    assert r.status_code == 400
    pid = post(c, {"type": "text", "destinations": [{"channel_id": env["fb"], "body": "Hello", "link": "https://venukalyan.com/ubm"}]}).get_json()["id"]
    eng.tick(wait=True)
    assert eng._meta.calls[0][0] == "fb_feed" and eng._meta.calls[0][1][3] == "https://venukalyan.com/ubm"
    assert dests(db, pid)[0]["status"] == "published"


def test_validation_errors(env):
    c = env["c"]
    mid = upload(c)
    r = post(c, {"type": "video", "media_ids": [mid], "destinations": [{"channel_id": env["yt1"], "title": ""}]})
    assert r.status_code == 400 and r.get_json()["channel_id"] == env["yt1"]
    assert post(c, {"type": "video", "media_ids": [mid], "destinations": []}).status_code == 400
    assert post(c, {"type": "video", "media_ids": [], "destinations": [{"channel_id": env["fb"]}]}).status_code == 400
    assert post(c, {"type": "nope"}).status_code == 400
    unconnected = channel_id(c.application, "youtube", "Clips")
    r = post(c, {"type": "video", "media_ids": [mid], "destinations": [{"channel_id": unconnected, "title": "t"}]})
    assert r.status_code == 400 and "Connect" in r.get_json()["error"]


def test_transient_retry_then_fail(env):
    c, db, eng = env["c"], env["db"], env["eng"]
    eng._yt.fail = ProviderError("busy", transient=True)
    mid = upload(c)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [{"channel_id": env["yt1"], "title": "t"}]}).get_json()["id"]
    eng.tick(wait=True)
    d = dests(db, pid)[0]
    assert d["status"] == "queued" and d["attempts"] == 1 and d["next_attempt_at"]
    assert eng.tick(wait=True) == 0                     # backoff respected
    base = utcnow()
    for i in range(1, 4):
        eng.now = lambda i=i: base + timedelta(hours=i)
        eng.tick(wait=True)
    d = dests(db, pid)[0]
    assert d["status"] == "failed" and d["attempts"] == 4


def test_reauth_marks_channel(env):
    c, db, eng = env["c"], env["db"], env["eng"]
    eng._yt.fail = ProviderError("YouTube access expired. Reconnect this channel.", reauth=True)
    mid = upload(c)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [{"channel_id": env["yt1"], "title": "t"}]}).get_json()["id"]
    eng.tick(wait=True)
    assert dests(db, pid)[0]["status"] == "failed"
    assert db.one("SELECT status FROM channels WHERE id=?", (env["yt1"],))["status"] == "error"


def test_retry_and_cancel_api(env):
    c, db, eng = env["c"], env["db"], env["eng"]
    eng._yt.fail = ProviderError("nope")
    mid = upload(c)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [{"channel_id": env["yt1"], "title": "t"}]}).get_json()["id"]
    eng.tick(wait=True)
    d = dests(db, pid)[0]
    assert d["status"] == "failed" and d["detail"] == "nope"
    eng._yt.fail = None
    h = {"X-CSRF-Token": c.csrf}
    assert c.post(f"/api/destinations/{d['id']}/retry", headers=h).status_code == 200
    eng.tick(wait=True)
    assert dests(db, pid)[0]["status"] == "published"
    assert c.post(f"/api/destinations/{d['id']}/retry", headers=h).status_code == 400
    # cancel a waiting IG post
    pid2 = post(c, {"type": "video", "media_ids": [mid], "destinations": [
        {"channel_id": env["ig"], "schedule_at": iso(utcnow() + timedelta(days=2))}]}).get_json()["id"]
    d2 = dests(db, pid2)[0]
    assert c.post(f"/api/destinations/{d2['id']}/cancel", headers=h).status_code == 200
    assert dests(db, pid2)[0]["status"] == "canceled"
    q = c.get("/api/queue").get_json()
    assert q["posts"][0]["id"] == pid2 and q["posts"][0]["destinations"][0]["status"] == "canceled"
    assert c.post(f"/api/posts/{pid2}/delete", headers=h).status_code == 200
    assert db.one("SELECT * FROM posts WHERE id=?", (pid2,)) is None


def test_media_gone(env):
    c, db, eng = env["c"], env["db"], env["eng"]
    mid = upload(c)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [{"channel_id": env["yt1"], "title": "t"}]}).get_json()["id"]
    os.remove(db.one("SELECT path FROM media WHERE id=?", (mid,))["path"])
    eng.tick(wait=True)
    d = dests(db, pid)[0]
    assert d["status"] == "failed" and "Upload it again" in d["detail"]


def test_disconnected_channel_fails_cleanly(env):
    c, db, eng = env["c"], env["db"], env["eng"]
    mid = upload(c)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [{"channel_id": env["yt1"], "title": "t"}]}).get_json()["id"]
    db.update("channels", env["yt1"], {"status": "disconnected"})
    eng.tick(wait=True)
    assert "Reconnect" in dests(db, pid)[0]["detail"]


def test_claim_lock_prevents_double_run(env):
    eng, db, c = env["eng"], env["db"], env["c"]
    mid = upload(c)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [{"channel_id": env["yt1"], "title": "t"}]}).get_json()["id"]
    did = dests(db, pid)[0]["id"]
    assert eng.claim(did) and not eng.claim(did)
    assert eng.tick(wait=True) == 0
    eng.release(did)
    assert eng.tick(wait=True) == 1


def test_recover_and_cleanup(env, app):
    eng, db, c = env["eng"], env["db"], env["c"]
    mid = upload(c)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [{"channel_id": env["yt1"], "title": "t"}]}).get_json()["id"]
    did = dests(db, pid)[0]["id"]
    db.update("destinations", did, {"status": "uploading", "locked_until": iso(utcnow() + timedelta(hours=1))})
    eng.recover()
    d = dests(db, pid)[0]
    assert d["status"] == "queued" and d["locked_until"] is None
    eng.tick(wait=True)
    path = db.one("SELECT path FROM media WHERE id=?", (mid,))["path"]
    assert eng.cleanup() == 0 and os.path.exists(path)          # still inside retention
    orphan = upload(c)
    eng.now = lambda: utcnow() + timedelta(hours=30)
    assert eng.cleanup() == 2
    assert not os.path.exists(path)
    assert db.one("SELECT status FROM media WHERE id=?", (orphan,))["status"] == "deleted"


def test_token_refresh_persisted(env):
    eng, db = env["eng"], env["db"]
    db.update("channels", env["yt1"], {"token_expires_at": iso(utcnow() - timedelta(minutes=1))})
    ch = db.one("SELECT * FROM channels WHERE id=?", (env["yt1"],))
    assert eng.yt_token_getter(ch)(False) == "fresh"
    ch = db.one("SELECT * FROM channels WHERE id=?", (env["yt1"],))
    assert env["e"]["box"].decrypt(ch["token_enc"]) == "fresh" and parse_iso(ch["token_expires_at"]) > utcnow()


@pytest.mark.skipif(bool(os.environ.get("TEST_DATABASE_URL")), reason="sqlite file check")
def test_tokens_never_plain_in_db(env):
    path = os.path.join(env["c"].application.extensions["unikpost"]["cfg"].DATA_DIR, "unikpost.db")
    raw = open(path, "rb").read()
    for p in ("unikpost.db-wal",):
        wal = os.path.join(os.path.dirname(path), p)
        if os.path.exists(wal):
            raw += open(wal, "rb").read()
    assert b"tok" not in raw.replace(b"token", b"") and b"ref" not in raw.replace(b"refresh", b"").replace(b"REFERENCES", b"")


def test_instagram_login_account_uses_ig_client(env):
    c, db, eng = env["c"], env["db"], env["eng"]
    eng._ig = FakeMeta()
    db.update("channels", env["ig"], {"auth": "iglogin"})
    mid = upload(c)
    pid = post(c, {"type": "video", "media_ids": [mid], "destinations": [{"channel_id": env["ig"], "body": "x"}]}).get_json()["id"]
    eng.tick(wait=True)
    assert [x[0] for x in eng._ig.calls] == ["ig_create_container", "ig_wait", "ig_publish"]
    assert eng._meta.calls == []
    assert dests(db, pid)[0]["status"] == "published"


def test_refresh_ig_tokens(env):
    db, eng = env["db"], env["eng"]

    class R:
        def refresh(self, t):
            assert t == "tok"
            return {"access_token": "new", "expires_in": 5184000}
    eng._ig = R()
    db.update("channels", env["ig"], {"auth": "iglogin", "token_expires_at": iso(utcnow() + timedelta(days=20))})
    assert eng.refresh_ig_tokens() == 1
    ch = db.one("SELECT * FROM channels WHERE id=?", (env["ig"],))
    assert env["e"]["box"].decrypt(ch["token_enc"]) == "new" and parse_iso(ch["token_expires_at"]) > utcnow() + timedelta(days=59)
    assert eng.refresh_ig_tokens() == 0            # fresh token: nothing to do
