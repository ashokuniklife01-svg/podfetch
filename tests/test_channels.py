from app import link_meta_accounts, parse_handle
from conftest import channel_id, connect_channel, ext


def test_seeded_accounts(app):
    db = ext(app)["db"]
    rows = db.query("SELECT platform, name, handle FROM channels ORDER BY sort")
    by = {p: [r for r in rows if r["platform"] == p] for p in ("youtube", "instagram", "facebook")}
    assert [r["name"] for r in by["youtube"]] == ["Venu Kalyan Business Growth Strategist",
                                                  "Telugu Business Talks with Venu Kalyan",
                                                  "Venu Kalyan Clips", "Soujanya Parenting Coach"]
    assert [r["handle"] for r in by["instagram"]] == ["venukalyan01", "venukalyanpodcastshorts",
                                                      "ubmbyvenukalayan", "msbyvenukalyan", "soujanyaparentingcoach"]
    assert [r["handle"] for r in by["facebook"]] == ["venukalyan01", "Soujanyaparentingcoach"]


def test_not_reseeded(app):
    db = ext(app)["db"]
    db.execute("DELETE FROM channels")
    db.init()
    assert db.one("SELECT COUNT(*) AS n FROM channels")["n"] == 0


def test_parse_handle():
    assert parse_handle("instagram", "https://www.instagram.com/venukalyan01/?utm_source=chatgpt.com") == "venukalyan01"
    assert parse_handle("facebook", "facebook.com/Soujanyaparentingcoach/") == "Soujanyaparentingcoach"
    assert parse_handle("facebook", "https://www.facebook.com/profile.php?id=123") == "123"
    assert parse_handle("instagram", "@my.name") == "my.name"
    assert parse_handle("instagram", "") == ""


def test_add_rename_remove(client, app):
    db = ext(app)["db"]
    r = client.post("/channels/add", data={"platform": "instagram", "name": "",
                                           "handle": "https://instagram.com/newpage/", "csrf": client.csrf})
    assert r.status_code == 302
    row = db.one("SELECT * FROM channels WHERE handle='newpage'")
    assert row and row["name"] == "newpage" and row["status"] == "disconnected"
    client.post(f"/channels/{row['id']}/rename", data={"name": "New Page", "handle": "@np2", "csrf": client.csrf})
    row = db.one("SELECT * FROM channels WHERE id=?", (row["id"],))
    assert row["name"] == "New Page" and row["handle"] == "np2"
    # empty rename is refused
    client.post(f"/channels/{row['id']}/rename", data={"name": " ", "csrf": client.csrf})
    assert db.one("SELECT name FROM channels WHERE id=?", (row["id"],))["name"] == "New Page"
    client.post(f"/channels/{row['id']}/remove", data={"csrf": client.csrf})
    assert db.one("SELECT * FROM channels WHERE id=?", (row["id"],)) is None
    assert client.post("/channels/9999/remove", data={"csrf": client.csrf}).status_code == 404
    assert client.post("/channels/add", data={"platform": "tiktok", "name": "x", "csrf": client.csrf}).status_code == 400


def test_remove_cancels_waiting(client, app):
    db = ext(app)["db"]
    cid = channel_id(app, "instagram", "venukalyan01")
    pid = db.insert("posts", {"post_type": "video", "media_ids": "[]", "created_at": "2026-01-01T00:00:00Z"})
    did = db.insert("destinations", {"post_id": pid, "channel_id": cid, "platform": "instagram", "kind": "reel",
                                     "mode": "app", "status": "waiting", "created_at": "x", "updated_at": "x"})
    client.post(f"/channels/{cid}/remove", data={"csrf": client.csrf})
    assert db.one("SELECT status FROM destinations WHERE id=?", (did,))["status"] == "canceled"


def test_disconnect(client, app):
    cid = channel_id(app, "youtube", "Clips")
    connect_channel(app, cid)
    client.post(f"/channels/{cid}/disconnect", data={"csrf": client.csrf})
    row = ext(app)["db"].one("SELECT * FROM channels WHERE id=?", (cid,))
    assert row["status"] == "disconnected" and row["token_enc"] is None and row["refresh_enc"] is None


def test_setup_needed_state(bare_app):
    from conftest import login
    c = bare_app.test_client()
    login(c)
    html = c.get("/channels").get_data(as_text=True)
    assert "Setup needed" in html and "/connect/" not in html.split("Setup</b>")[1].split("dlgEdit")[0].replace("/connect/new", "")
    r = c.get("/connect/1")
    assert r.status_code == 302 and r.location.endswith("/channels")


def test_channels_page_ready(client):
    html = client.get("/channels").get_data(as_text=True)
    assert "Venu Kalyan Clips" in html and "Connect</a>" in html
    assert "https://post.example.com/oauth/google/callback" in html


def test_connect_redirects_to_google(client):
    r = client.get("/connect/1")
    assert r.status_code == 302 and r.location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "access_type=offline" in r.location and "youtube.upload" in r.location
    cid = channel_id(client.application, "facebook", "Venu")
    r = client.get(f"/connect/{cid}")
    assert r.location.startswith("https://www.facebook.com/v25.0/dialog/oauth")


class FakeYT:
    def __init__(self, ch_id="UC1", title="Venu Kalyan Clips"):
        self.ch = {"id": ch_id, "title": title, "handle": "@vkclips", "avatar": "https://yt3.example/a.jpg"}

    def auth_url(self, state):
        return "https://accounts.google.com/o/oauth2/v2/auth?state=" + state

    def exchange_code(self, code):
        assert code == "good"
        return {"access_token": "ya29.A", "refresh_token": "1//R", "expires_in": 3599}

    def my_channel(self, token):
        return self.ch


def _start(client, path):
    r = client.get(path)
    with client.session_transaction() as s:
        return s["oauth"]["state"]


def test_google_callback_binds_row(client, app):
    e = ext(app)
    e["engine"]._yt = FakeYT()
    cid = channel_id(app, "youtube", "Clips")
    state = _start(client, f"/connect/{cid}")
    r = client.get(f"/oauth/google/callback?state={state}&code=good")
    assert r.status_code == 302
    row = e["db"].one("SELECT * FROM channels WHERE id=?", (cid,))
    assert row["status"] == "connected" and row["external_id"] == "UC1"
    assert e["box"].decrypt(row["refresh_enc"]) == "1//R"
    assert "1//R" not in (row["refresh_enc"] or "")
    # replay of same state is refused
    assert client.get(f"/oauth/google/callback?state={state}&code=good").status_code == 400


def test_google_callback_bad_state(client, app):
    ext(app)["engine"]._yt = FakeYT()
    _start(client, "/connect/1")
    assert client.get("/oauth/google/callback?state=wrong&code=good").status_code == 400


def test_google_callback_cancel(client, app):
    ext(app)["engine"]._yt = FakeYT()
    state = _start(client, "/connect/1")
    r = client.get(f"/oauth/google/callback?state={state}&error=access_denied")
    assert r.status_code == 302


def test_google_new_channel(client, app):
    e = ext(app)
    e["engine"]._yt = FakeYT("UC9", "Brand New")
    state = _start(client, "/connect/new/youtube")
    client.get(f"/oauth/google/callback?state={state}&code=good")
    assert e["db"].one("SELECT * FROM channels WHERE external_id='UC9'")["name"] == "Brand New"


PAGES = [
    {"id": "P1", "name": "Venu Kalyan", "username": "venukalyan01", "token": "EAAPAGE1", "avatar": None,
     "ig": {"id": "IG1", "username": "venukalyan01", "name": "VK", "avatar": None}},
    {"id": "P2", "name": "Soujanya Parenting Coach", "username": "Soujanyaparentingcoach", "token": "EAAPAGE2",
     "avatar": None, "ig": {"id": "IG5", "username": "soujanyaparentingcoach"}},
    {"id": "P3", "name": "Other Page", "username": None, "token": "EAAPAGE3", "avatar": None,
     "ig": {"id": "IG9", "username": "someother"}},
]


def test_link_meta_accounts(app):
    e = ext(app)
    res = link_meta_accounts(e["db"], e["box"], PAGES)
    assert res == {"connected": 4, "discovered": 2}
    fb = e["db"].one("SELECT * FROM channels WHERE platform='facebook' AND handle='venukalyan01'")
    assert fb["status"] == "connected" and fb["external_id"] == "P1" and e["box"].decrypt(fb["token_enc"]) == "EAAPAGE1"
    ig = e["db"].one("SELECT * FROM channels WHERE platform='instagram' AND handle='soujanyaparentingcoach'")
    assert ig["external_id"] == "IG5" and e["box"].decrypt(ig["token_enc"]) == "EAAPAGE2"
    disc = e["db"].query("SELECT * FROM discovered ORDER BY platform")
    assert {d["external_id"] for d in disc} == {"P3", "IG9"}
    # second login is idempotent
    assert link_meta_accounts(e["db"], e["box"], PAGES)["connected"] == 4


def test_discovered_add(client, app):
    e = ext(app)
    link_meta_accounts(e["db"], e["box"], PAGES)
    d = e["db"].one("SELECT * FROM discovered WHERE external_id='IG9'")
    client.post(f"/channels/discovered/{d['id']}/add", data={"csrf": client.csrf})
    row = e["db"].one("SELECT * FROM channels WHERE external_id='IG9'")
    assert row["status"] == "connected" and row["platform"] == "instagram"
    assert e["db"].one("SELECT * FROM discovered WHERE external_id='IG9'") is None


class FakeMeta:
    def auth_url(self, state):
        return "https://www.facebook.com/v23.0/dialog/oauth?state=" + state

    def exchange_code(self, code):
        return "EAALONG"

    def list_accounts(self, token):
        assert token == "EAALONG"
        return PAGES


def test_meta_callback(client, app):
    e = ext(app)
    e["engine"]._meta = FakeMeta()
    cid = channel_id(app, "instagram", "ubmby")      # not in the login -> warning flash
    state = _start(client, f"/connect/{cid}")
    r = client.get(f"/oauth/meta/callback?state={state}&code=c")
    assert r.status_code == 302
    html = client.get("/channels").get_data(as_text=True)
    assert "Couldn&#39;t find ubmbyvenukalayan" in html or "find ubmbyvenukalayan" in html
    assert "Also found in your Facebook login" in html


class FakeIG:
    def auth_url(self, state):
        return "https://www.instagram.com/oauth/authorize?state=" + state

    def exchange_code(self, code):
        return {"access_token": "IGTOKEN", "expires_in": 5184000}

    def me(self, token):
        return {"id": "1784", "username": "ubmbyvenukalayan", "name": "UBM", "avatar": None}


def test_instagram_login_flow(tmp_path):
    from app import create_app
    from conftest import login, make_env
    a = create_app(make_env(tmp_path, INSTAGRAM_APP_ID="i", INSTAGRAM_APP_SECRET="s"))
    c = a.test_client()
    login(c)
    e = ext(a)
    e["engine"]._ig = FakeIG()
    cid = channel_id(a, "instagram", "msbyvenu")     # clicked row differs; handle match wins
    r = c.get(f"/connect/{cid}")
    assert r.location.startswith("https://www.instagram.com/oauth/authorize")
    with c.session_transaction() as s:
        state = s["oauth"]["state"]
    assert c.get(f"/oauth/instagram/callback?state={state}&code=x").status_code == 302
    row = e["db"].one("SELECT * FROM channels WHERE handle='ubmbyvenukalayan'")
    assert row["status"] == "connected" and row["auth"] == "iglogin" and row["external_id"] == "1784"
    assert e["box"].decrypt(row["token_enc"]) == "IGTOKEN"
    assert e["db"].one("SELECT status FROM channels WHERE id=?", (cid,))["status"] == "disconnected"
    html = c.get("/channels").get_data(as_text=True)
    assert "Sync" not in html            # no Facebook keys -> no FB sync button
