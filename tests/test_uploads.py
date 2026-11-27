from conftest import ext
from test_engine import upload


def h(c):
    return {"X-CSRF-Token": c.csrf}


def test_upload_checks(client, app):
    r = client.post("/api/uploads", json={"name": "x.exe", "size": 10}, headers=h(client))
    assert r.status_code == 400
    r = client.post("/api/uploads", json={"name": "x.mp4", "size": 0}, headers=h(client))
    assert r.status_code == 400
    r = client.post("/api/uploads", json={"name": "x.mp4", "size": 10 ** 13}, headers=h(client))
    assert r.status_code == 400 and "MB" in r.get_json()["error"]
    mid = client.post("/api/uploads", json={"name": "../../evil.mp4", "size": 10}, headers=h(client)).get_json()["id"]
    m = ext(app)["db"].one("SELECT * FROM media WHERE id=?", (mid,))
    assert m["filename"] == "evil.mp4" and "/media/" in m["path"]
    assert client.put(f"/api/uploads/{mid}/chunk?offset=5", data=b"abc", headers=h(client)).status_code == 409
    assert client.put(f"/api/uploads/{mid}/chunk", data=b"abc", headers=h(client)).status_code == 400
    assert client.put(f"/api/uploads/{mid}/chunk?offset=0", data=b"x" * 11, headers=h(client)).status_code == 400
    assert client.post(f"/api/uploads/{mid}/complete", headers=h(client)).status_code == 409
    assert client.put(f"/api/uploads/{mid}/chunk?offset=0", data=b"x" * 10, headers=h(client)).status_code == 200
    assert client.get(f"/api/uploads/{mid}").get_json()["received"] == 10
    assert client.post(f"/api/uploads/{mid}/complete", headers=h(client)).status_code == 200
    assert client.put(f"/api/uploads/{mid}/chunk?offset=10", data=b"x", headers=h(client)).status_code == 409


def test_public_media_url(client, app):
    data = bytes(range(256)) * 20
    mid = upload(client, data=data)
    m = ext(app)["db"].one("SELECT * FROM media WHERE id=?", (mid,))
    anon = app.test_client()
    r = anon.get(f"/m/{m['token']}/file.mp4")
    assert r.status_code == 200 and r.data == data and r.mimetype == "video/mp4"
    r = anon.get(f"/m/{m['token']}/file.mp4", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.data == data[:10]
    assert anon.get("/m/short/file.mp4").status_code == 404
    assert anon.get("/m/" + "A" * 32 + "/file.mp4").status_code == 404
    client.delete(f"/api/uploads/{mid}", headers=h(client))
    assert anon.get(f"/m/{m['token']}/file.mp4").status_code == 404


def test_bad_image_rejected(client):
    mid = client.post("/api/uploads", json={"name": "a.png", "size": 4}, headers=h(client)).get_json()["id"]
    client.put(f"/api/uploads/{mid}/chunk?offset=0", data=b"nope", headers=h(client))
    r = client.post(f"/api/uploads/{mid}/complete", headers=h(client))
    assert r.status_code == 400


def test_pages_render(client):
    for p in ("/", "/queue", "/channels"):
        r = client.get(p)
        assert r.status_code == 200 and b"UNIK" in r.data
    html = client.get("/").get_data(as_text=True)
    assert 'id="boot"' in html and "Venu Kalyan Clips" in html
    assert client.get("/nope").status_code == 404


def test_jpeg_original_kept_as_is(client, app):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (800, 1000), (1, 2, 3)).save(buf, "JPEG", quality=97)
    data = buf.getvalue()
    mid = client.post("/api/uploads", json={"name": "a.jpg", "size": len(data)}, headers=h(client)).get_json()["id"]
    client.put(f"/api/uploads/{mid}/chunk?offset=0", data=data, headers=h(client))
    assert client.post(f"/api/uploads/{mid}/complete", headers=h(client)).status_code == 200
    m = ext(app)["db"].one("SELECT * FROM media WHERE id=?", (mid,))
    assert m["jpeg_path"] is None and open(m["path"], "rb").read() == data
    r = app.test_client().get(f"/m/{m['token']}/thumb")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
