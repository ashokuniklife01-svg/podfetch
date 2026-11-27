import json
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from conftest import FakeResp
from providers import ProviderError
from providers.meta import MetaClient
from providers.youtube import CHUNK, YouTubeClient


def yt(fake):
    return YouTubeClient("cid", "csec", "https://x/oauth/google/callback", session=fake, sleep=lambda s: None)


def test_yt_auth_url():
    u = urlparse(yt(None).auth_url("st"))
    q = parse_qs(u.query)
    assert q["access_type"] == ["offline"] and q["state"] == ["st"]
    assert "https://www.googleapis.com/auth/youtube.upload" in q["scope"][0]
    assert "https://www.googleapis.com/auth/youtube.readonly" in q["scope"][0]
    assert "consent" in q["prompt"][0]


def test_yt_exchange_and_channel(fake_http):
    fake_http.add("POST", "oauth2.googleapis.com/token", FakeResp(200, {"access_token": "a", "refresh_token": "r", "expires_in": 3600}))
    fake_http.add("GET", "/youtube/v3/channels", FakeResp(200, {"items": [{"id": "UC1", "snippet": {"title": "T", "customUrl": "@t", "thumbnails": {"default": {"url": "u"}}}}]}))
    c = yt(fake_http)
    assert c.exchange_code("code")["refresh_token"] == "r"
    assert c.my_channel("a") == {"id": "UC1", "title": "T", "handle": "@t", "avatar": "u"}
    assert fake_http.calls[1][2]["headers"]["Authorization"] == "Bearer a"


def test_yt_no_channel(fake_http):
    fake_http.add("GET", "/channels", FakeResp(200, {"items": []}))
    with pytest.raises(ProviderError):
        yt(fake_http).my_channel("a")


def test_yt_refresh_invalid_grant(fake_http):
    fake_http.add("POST", "/token", FakeResp(400, {"error": "invalid_grant", "error_description": "revoked"}))
    with pytest.raises(ProviderError) as e:
        yt(fake_http).refresh("r")
    assert e.value.reauth


def test_yt_metadata_publish_at_forces_private():
    m = YouTubeClient.build_metadata("t", "d", "public", "2030-01-01T00:00:00Z")
    assert m["status"]["privacyStatus"] == "private" and m["status"]["publishAt"] == "2030-01-01T00:00:00Z"
    assert YouTubeClient.build_metadata("t", "d", "unlisted")["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}


def test_yt_start_upload(fake_http):
    fake_http.add("POST", "/upload/youtube/v3/videos", FakeResp(200, {}, {"Location": "https://up/session1"}))
    url = yt(fake_http).start_upload("a", {"snippet": {}}, 123, "video/mp4")
    assert url == "https://up/session1"
    kw = fake_http.calls[0][2]
    assert kw["params"]["uploadType"] == "resumable" and kw["headers"]["X-Upload-Content-Length"] == "123"


def test_yt_quota_error(fake_http):
    fake_http.add("POST", "/upload/", FakeResp(403, {"error": {"message": "q", "errors": [{"reason": "quotaExceeded"}]}}))
    with pytest.raises(ProviderError) as e:
        yt(fake_http).start_upload("a", {}, 1)
    assert "daily limit" in e.value.message and not e.value.transient


def _file(tmp_path, size):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\x01" * size)
    return str(p)


def test_yt_chunked_upload(tmp_path, fake_http):
    size = CHUNK + 1000
    path = _file(tmp_path, size)
    fake_http.add("PUT", "https://up/s", FakeResp(308, None, {"Range": f"bytes=0-{CHUNK - 1}"}))
    fake_http.add("PUT", "https://up/s", FakeResp(200, {"id": "VID1", "status": {"privacyStatus": "public"}}))
    seen = []
    out = yt(fake_http).upload_file("https://up/s", path, lambda force: "tok", lambda a, b: seen.append((a, b)))
    assert out["id"] == "VID1"
    h1, h2 = fake_http.calls[0][2]["headers"], fake_http.calls[1][2]["headers"]
    assert h1["Content-Range"] == f"bytes 0-{CHUNK - 1}/{size}"
    assert h2["Content-Range"] == f"bytes {CHUNK}-{size - 1}/{size}"
    assert seen[-1] == (size, size)


def test_yt_upload_refresh_on_401_and_resume(tmp_path, fake_http):
    path = _file(tmp_path, 1000)
    fake_http.add("PUT", "https://up/s", FakeResp(401, {"error": {"message": "expired"}}))
    fake_http.add("PUT", "https://up/s", FakeResp(308, None, {}))           # status query: nothing stored
    fake_http.add("PUT", "https://up/s", FakeResp(200, {"id": "V"}))
    calls = []

    def tok(force):
        calls.append(force)
        return "new" if force else "old"
    assert yt(fake_http).upload_file("https://up/s", path, tok)["id"] == "V"
    assert calls == [False, True]
    assert fake_http.calls[1][2]["headers"]["Content-Range"] == "bytes */1000"
    assert fake_http.calls[2][2]["headers"]["Authorization"] == "Bearer new"


def test_yt_upload_network_error_then_done(tmp_path, fake_http):
    path = _file(tmp_path, 10)
    fake_http.add("PUT", "https://up/s", requests.ConnectionError("boom"))
    fake_http.add("PUT", "https://up/s", FakeResp(201, {"id": "V2"}))      # status query says complete
    assert yt(fake_http).upload_file("https://up/s", path, lambda f: "t")["id"] == "V2"


def test_yt_upload_gives_up(tmp_path, fake_http):
    path = _file(tmp_path, 10)
    for _ in range(3):
        fake_http.add("PUT", "https://up/s", FakeResp(503, {}))
        fake_http.add("PUT", "https://up/s", FakeResp(503, {}))
    with pytest.raises(ProviderError) as e:
        yt(fake_http).upload_file("https://up/s", path, lambda f: "t", max_retries=2)
    assert e.value.transient


# ---------------- Meta ----------------

def meta(fake, **kw):
    return MetaClient("app", "sec", "https://x/oauth/meta/callback", "v23.0", session=fake, sleep=lambda s: None, **kw)


def test_meta_auth_url_scope_and_config():
    q = parse_qs(urlparse(meta(None).auth_url("s")).query)
    assert "instagram_content_publish" in q["scope"][0] and "pages_manage_posts" in q["scope"][0]
    q = parse_qs(urlparse(meta(None, config_id="CFG").auth_url("s")).query)
    assert q["config_id"] == ["CFG"] and "scope" not in q


def test_meta_exchange_long_lived(fake_http):
    fake_http.add("GET", "/oauth/access_token", FakeResp(200, {"access_token": "short"}))
    fake_http.add("GET", "/oauth/access_token", FakeResp(200, {"access_token": "long"}))
    assert meta(fake_http).exchange_code("c") == "long"
    assert fake_http.calls[1][2]["params"]["grant_type"] == "fb_exchange_token"
    assert fake_http.calls[1][2]["params"]["fb_exchange_token"] == "short"


def test_meta_list_accounts_paging(fake_http):
    fake_http.add("GET", "/me/accounts", FakeResp(200, {"data": [
        {"id": "P1", "name": "A", "username": "a", "access_token": "t1",
         "picture": {"data": {"url": "pic"}},
         "instagram_business_account": {"id": "IG1", "username": "ig_a", "profile_picture_url": "igpic"}}],
        "paging": {"next": "https://graph.facebook.com/v23.0/me/accounts?after=X"}}))
    fake_http.add("GET", "after=X", FakeResp(200, {"data": [{"id": "P2", "name": "B", "access_token": "t2"}]}))
    out = meta(fake_http).list_accounts("u")
    assert [p["id"] for p in out] == ["P1", "P2"]
    assert out[0]["ig"] == {"id": "IG1", "username": "ig_a", "name": None, "avatar": "igpic"}
    assert out[1]["ig"] is None and out[0]["avatar"] == "pic"


def test_meta_error_mapping(fake_http):
    fake_http.add("GET", "/me/accounts", FakeResp(400, {"error": {"code": 190, "message": "expired"}}))
    with pytest.raises(ProviderError) as e:
        meta(fake_http).list_accounts("u")
    assert e.value.reauth
    fake_http.add("POST", "/media", FakeResp(500, {"error": {"code": 2, "message": "temp"}}))
    with pytest.raises(ProviderError) as e:
        meta(fake_http).ig_create_container("IG", "t", "reel", "https://m/v.mp4")
    assert e.value.transient
    fake_http.add("POST", "/media", FakeResp(400, {"error": {"code": 9004, "message": "bad", "error_user_msg": "Media URL not reachable"}}))
    with pytest.raises(ProviderError) as e:
        meta(fake_http).ig_create_container("IG", "t", "photo", "https://m/p.jpg")
    assert "Media URL not reachable" in e.value.message and not e.value.transient
    fake_http.add("GET", "/me", requests.Timeout())
    with pytest.raises(ProviderError) as e:
        meta(fake_http).get("me", "t")
    assert e.value.transient


def test_ig_reel_flow(fake_http):
    m = meta(fake_http)
    fake_http.add("POST", "/IG1/media", FakeResp(200, {"id": "C1"}))
    fake_http.add("GET", "/C1", FakeResp(200, {"status_code": "IN_PROGRESS"}))
    fake_http.add("GET", "/C1", FakeResp(200, {"status_code": "FINISHED"}))
    fake_http.add("POST", "/IG1/media_publish", FakeResp(200, {"id": "M1"}))
    fake_http.add("GET", "/M1", FakeResp(200, {"permalink": "https://instagram.com/reel/x"}))
    cid = m.ig_create_container("IG1", "t", "reel", "https://m/v.mp4", "cap", share_to_feed=False)
    data = fake_http.calls[0][2]["data"]
    assert data["media_type"] == "REELS" and data["video_url"] == "https://m/v.mp4" and data["caption"] == "cap"
    assert data["share_to_feed"] == "false"
    assert m.ig_wait(cid, "t", timeout=30) == "FINISHED"
    assert m.ig_publish("IG1", "t", cid) == ("M1", "https://instagram.com/reel/x")
    assert fake_http.calls[3][2]["data"]["creation_id"] == "C1"


def test_ig_container_error(fake_http):
    fake_http.add("GET", "/C1", FakeResp(200, {"status_code": "ERROR", "status": "Error: aspect ratio"}))
    with pytest.raises(ProviderError) as e:
        meta(fake_http).ig_wait("C1", "t")
    assert "aspect ratio" in e.value.message


def test_ig_carousel_params(fake_http):
    fake_http.add("POST", "/IG/media", FakeResp(200, {"id": "K1"}))
    fake_http.add("POST", "/IG/media", FakeResp(200, {"id": "PARENT"}))
    m = meta(fake_http)
    m.ig_create_container("IG", "t", "photo", "https://m/1.jpg", is_carousel_item=True)
    assert fake_http.calls[0][2]["data"]["is_carousel_item"] == "true" and "caption" not in fake_http.calls[0][2]["data"]
    m.ig_create_container("IG", "t", "carousel", children=["K1", "K2"], caption="c")
    d = fake_http.calls[1][2]["data"]
    assert d["media_type"] == "CAROUSEL" and d["children"] == "K1,K2" and d["caption"] == "c"


def test_fb_video_scheduled(fake_http):
    fake_http.add("POST", "graph-video.facebook.com/v23.0/P/videos", FakeResp(200, {"id": "V"}))
    vid, url = meta(fake_http).fb_video("P", "pt", "https://m/v.mp4", "T", "D", publish_ts=1900000000)
    d = fake_http.calls[0][2]["data"]
    assert d["published"] == "false" and d["scheduled_publish_time"] == "1900000000" and d["file_url"] == "https://m/v.mp4"
    assert d["access_token"] == "pt" and url.endswith("/videos/V")


def test_fb_reel_three_phases(fake_http):
    fake_http.add("POST", "/P/video_reels", FakeResp(200, {"video_id": "R1", "upload_url": "x"}))
    fake_http.add("POST", "rupload.facebook.com/video-upload/v23.0/R1", FakeResp(200, {"success": True}))
    fake_http.add("POST", "/P/video_reels", FakeResp(200, {"success": True}))
    vid, url = meta(fake_http).fb_reel("P", "pt", "https://m/v.mp4", "cap")
    assert fake_http.calls[1][2]["headers"]["file_url"] == "https://m/v.mp4"
    assert fake_http.calls[1][2]["headers"]["Authorization"] == "OAuth pt"
    fin = fake_http.calls[2][2]["data"]
    assert fin["upload_phase"] == "finish" and fin["video_state"] == "PUBLISHED" and "scheduled_publish_time" not in fin
    assert url == "https://www.facebook.com/reel/R1"


def test_fb_reel_scheduled(fake_http):
    fake_http.add("POST", "/P/video_reels", FakeResp(200, {"video_id": "R1"}))
    fake_http.add("POST", "rupload", FakeResp(200, {}))
    fake_http.add("POST", "/P/video_reels", FakeResp(200, {}))
    meta(fake_http).fb_reel("P", "pt", "u", "c", publish_ts=1900000000)
    fin = fake_http.calls[2][2]["data"]
    assert fin["video_state"] == "SCHEDULED" and fin["scheduled_publish_time"] == "1900000000"


def test_fb_photos_and_feed(fake_http):
    fake_http.add("POST", "/P/photos", FakeResp(200, {"id": "F1"}))
    fake_http.add("POST", "/P/photos", FakeResp(200, {"id": "F2"}))
    fake_http.add("POST", "/P/feed", FakeResp(200, {"id": "P_9"}))
    rid, url = meta(fake_http).fb_photos("P", "pt", ["u1", "u2"], "msg", publish_ts=1900000000)
    assert fake_http.calls[0][2]["data"]["temporary"] == "true"
    feed = fake_http.calls[2][2]["data"]
    assert json.loads(feed["attached_media[1]"]) == {"media_fbid": "F2"} and feed["published"] == "false"
    assert rid == "P_9"
    fake_http.add("POST", "/P/feed", FakeResp(200, {"id": "P_10"}))
    meta(fake_http).fb_feed("P", "pt", "hello", "https://a.b")
    d = fake_http.calls[3][2]["data"]
    assert d["link"] == "https://a.b" and "published" not in d
    fake_http.add("POST", "/P/photos", FakeResp(200, {"id": "PH", "post_id": "P_11"}))
    assert meta(fake_http).fb_photo("P", "pt", "u", "m")[0] == "P_11"


# ---------------- Instagram Login ----------------
from providers.meta import InstagramLoginClient


def igl(fake):
    return InstagramLoginClient("igapp", "igsec", "https://x/oauth/instagram/callback", session=fake, sleep=lambda s: None)


def test_iglogin_auth_url():
    q = parse_qs(urlparse(igl(None).auth_url("s")).query)
    assert q["scope"] == ["instagram_business_basic,instagram_business_content_publish"]
    assert q["client_id"] == ["igapp"] and q["state"] == ["s"]


def test_iglogin_exchange_me_refresh(fake_http):
    fake_http.add("POST", "api.instagram.com/oauth/access_token", FakeResp(200, {"data": [{"access_token": "IGS", "user_id": 1}]}))
    fake_http.add("GET", "graph.instagram.com/access_token", FakeResp(200, {"access_token": "IGL", "expires_in": 5183944}))
    c = igl(fake_http)
    assert c.exchange_code("abc#_") == {"access_token": "IGL", "expires_in": 5183944}
    assert fake_http.calls[0][2]["data"]["code"] == "abc"
    assert fake_http.calls[1][2]["params"]["grant_type"] == "ig_exchange_token"
    fake_http.add("GET", "graph.instagram.com/v25.0/me", FakeResp(200, {"id": "app-scoped", "user_id": 1784, "username": "ubmbyvenukalayan"}))
    assert c.me("IGL")["id"] == "1784"
    fake_http.add("GET", "refresh_access_token", FakeResp(200, {"access_token": "IGL2", "expires_in": 5184000}))
    assert c.refresh("IGL")["access_token"] == "IGL2"


def test_iglogin_publish_uses_instagram_host(fake_http):
    fake_http.add("POST", "graph.instagram.com/v25.0/1784/media", FakeResp(200, {"id": "C"}))
    assert igl(fake_http).ig_create_container("1784", "t", "reel", "https://m/v.mp4", "cap") == "C"
