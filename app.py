"""UNIK Post: one upload, many channels."""
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

from config import Config
from db import Database, iso, parse_iso, utcnow
from providers import ProviderError
from routing import (LIMITS, ValidationError, detect_shape, plan_schedule, route_post,
                     validate_destination)
from secure import TokenBox, redact
from services import ACTIVE, Engine

log = logging.getLogger("unikpost")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

PLATFORMS = ("youtube", "instagram", "facebook")
PLATFORM_NAMES = {"youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
CHUNK_MAX = 16 * 1024 * 1024
IG_IMAGE_MAX = 8 * 1024 * 1024


class RateLimiter:
    """In-memory sliding-window limiter (single instance app)."""

    def __init__(self, limit, window, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def blocked(self, key):
        with self.lock:
            q = self.hits[key]
            now = self.clock()
            while q and now - q[0] > self.window:
                q.popleft()
            return len(q) >= self.limit

    def hit(self, key):
        with self.lock:
            self.hits[key].append(self.clock())

    def reset(self, key):
        with self.lock:
            self.hits.pop(key, None)


def parse_handle(platform, raw):
    """Accept '@name', 'name' or a profile URL; return the bare handle."""
    s = (raw or "").strip()
    if not s:
        return ""
    if "://" in s or s.startswith(("www.", "instagram.com", "facebook.com", "youtube.com")):
        u = urlparse(s if "://" in s else "https://" + s)
        parts = [p for p in u.path.split("/") if p]
        if parts and parts[0] in ("profile.php",):
            q = dict(x.split("=", 1) for x in u.query.split("&") if "=" in x)
            return q.get("id", "")
        s = parts[0] if parts else ""
    s = s.lstrip("@").strip()
    return re.sub(r"[^A-Za-z0-9._\-]", "", s)[:100]


def create_app(env=None, db=None, engine=None, start_worker=None):
    cfg = Config(env)
    app = Flask(__name__)
    testing = bool(env is not None and env.get("TESTING"))
    if not cfg.SECRET_KEY:
        if testing or os.environ.get("FLASK_DEBUG"):
            cfg.SECRET_KEY = "dev-only-secret"
        else:
            raise RuntimeError("SECRET_KEY must be set")
    app.config.update(
        SECRET_KEY=cfg.SECRET_KEY, TESTING=testing,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.BASE_URL.startswith("https://"),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=CHUNK_MAX + 1024 * 1024,
    )
    box = TokenBox(cfg.SECRET_KEY)
    if db is None:
        db = Database(cfg.DATABASE_URL, os.path.join(cfg.DATA_DIR, "unikpost.db"))
    db.init()
    media_dir = os.path.join(cfg.DATA_DIR, "media")
    os.makedirs(media_dir, exist_ok=True)
    engine = engine or Engine(db, cfg, box)
    app.extensions["unikpost"] = {"cfg": cfg, "db": db, "box": box, "engine": engine}
    login_limiter = RateLimiter(5, 15 * 60)
    global_limiter = RateLimiter(50, 15 * 60)
    if start_worker is None:
        start_worker = cfg.WORKER_ENABLED and not testing
    if start_worker:
        engine.start()

    # ---- helpers -----------------------------------------------------
    def client_ip():
        fwd = request.headers.get("X-Forwarded-For", "")
        return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "?"

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    def check_csrf():
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf")
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Security check failed. Refresh the page.")

    def wants_json():
        return request.path.startswith("/api/")

    def login_required(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not session.get("auth"):
                if wants_json():
                    return jsonify(error="Please sign in again"), 401
                return redirect(url_for("login", next=request.path))
            if request.method in ("POST", "PUT", "DELETE", "PATCH"):
                check_csrf()
            return fn(*a, **kw)
        return wrapper

    def channels(platform=None):
        if platform:
            return db.query("SELECT * FROM channels WHERE platform=? ORDER BY sort, id", (platform,))
        return db.query("SELECT * FROM channels ORDER BY sort, id")

    def public_channel(c):
        return {"id": c["id"], "platform": c["platform"], "name": c["name"], "handle": c["handle"],
                "remote_name": c["remote_name"], "avatar": c["avatar_url"], "status": c["status"],
                "detail": c["status_detail"]}

    def platform_ready(p):
        if p == "youtube":
            return cfg.google_ready
        if p == "instagram":
            return cfg.meta_ready or cfg.ig_login_ready
        return cfg.meta_ready

    app.jinja_env.globals.update(csrf_token=csrf_token, PLATFORM_NAMES=PLATFORM_NAMES)

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob: https:; media-src 'self' blob:; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
            "form-action 'self' https://accounts.google.com https://www.facebook.com")
        if request.path.startswith(("/api/", "/oauth/", "/login")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(400)
    def bad_request(e):
        msg = getattr(e, "description", "Bad request")
        if wants_json():
            return jsonify(error=msg), 400
        return render_template("message.html", title="Something's off", message=msg), 400

    @app.errorhandler(404)
    def not_found(e):
        if wants_json():
            return jsonify(error="Not found"), 404
        return render_template("message.html", title="Not found", message="That page doesn't exist."), 404

    @app.errorhandler(413)
    def too_big(e):
        return jsonify(error="Chunk too large"), 413

    # ---- auth --------------------------------------------------------
    @app.get("/health")
    def health():
        return jsonify(ok=True)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not cfg.APP_PASSWORD:
            return render_template("message.html", title="Setup needed",
                                   message="Set APP_PASSWORD on the server to sign in."), 503
        error = None
        if request.method == "POST":
            check_csrf()
            ip = client_ip()
            if login_limiter.blocked(ip) or global_limiter.blocked("all"):
                error = "Too many tries. Wait 15 minutes."
            else:
                pw = request.form.get("password", "")
                if hmac.compare_digest(pw.encode(), cfg.APP_PASSWORD.encode()):
                    login_limiter.reset(ip)
                    nxt = request.args.get("next") or "/"
                    if not nxt.startswith("/") or nxt.startswith("//"):
                        nxt = "/"
                    session.clear()
                    session.permanent = True
                    session["auth"] = True
                    csrf_token()
                    return redirect(nxt)
                login_limiter.hit(ip)
                global_limiter.hit("all")
                error = "Wrong password"
        return render_template("login.html", error=error), (429 if error and "Too many" in error else 200)

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---- pages -------------------------------------------------------
    @app.get("/")
    @login_required
    def compose():
        data = {"channels": [public_channel(c) for c in channels()],
                "limits": LIMITS,
                "maxUploadMb": cfg.MAX_UPLOAD_MB}
        return render_template("compose.html", data=data, active="compose")

    @app.get("/queue")
    @login_required
    def queue():
        return render_template("queue.html", active="queue")

    @app.get("/channels")
    @login_required
    def channels_page():
        groups = {p: channels(p) for p in PLATFORMS}
        disc = db.query("SELECT id, platform, name, handle, avatar_url FROM discovered ORDER BY platform, name")
        base = cfg.BASE_URL or request.host_url.rstrip("/")
        setup = {
            "google": cfg.google_ready, "meta": cfg.meta_ready, "persistent": cfg.persistent_db,
            "base": cfg.BASE_URL, "google_cb": f"{base}/oauth/google/callback",
            "meta_cb": f"{base}/oauth/meta/callback", "cron": bool(cfg.CRON_TOKEN),
            "ig": cfg.ig_login_ready, "ig_cb": f"{base}/oauth/instagram/callback",
        }
        return render_template("channels.html", groups=groups, discovered=disc, setup=setup,
                               ready={p: platform_ready(p) for p in PLATFORMS},
                               meta_ready=cfg.meta_ready, active="channels")

    # ---- channel CRUD ------------------------------------------------
    @app.post("/channels/add")
    @login_required
    def channel_add():
        p = request.form.get("platform")
        if p not in PLATFORMS:
            abort(400, "Pick a platform")
        handle = parse_handle(p, request.form.get("handle"))
        name = (request.form.get("name") or "").strip()[:120] or handle
        if not name:
            flash("Add a name or handle", "error")
            return redirect(url_for("channels_page"))
        now = iso(utcnow())
        mx = db.one("SELECT MAX(sort) AS m FROM channels")["m"] or 0
        db.insert("channels", {"platform": p, "name": name, "handle": handle or None,
                               "status": "disconnected", "sort": mx + 1,
                               "created_at": now, "updated_at": now})
        flash(f"Added {name}", "ok")
        return redirect(url_for("channels_page"))

    @app.post("/channels/<int:cid>/rename")
    @login_required
    def channel_rename(cid):
        c = db.one("SELECT * FROM channels WHERE id=?", (cid,)) or abort(404)
        name = (request.form.get("name") or "").strip()[:120]
        if not name:
            flash("Name can't be empty", "error")
            return redirect(url_for("channels_page"))
        data = {"name": name, "updated_at": iso(utcnow())}
        if "handle" in request.form and c["platform"] != "youtube":
            data["handle"] = parse_handle(c["platform"], request.form.get("handle")) or None
        db.update("channels", cid, data)
        flash("Saved", "ok")
        return redirect(url_for("channels_page"))

    @app.post("/channels/<int:cid>/remove")
    @login_required
    def channel_remove(cid):
        c = db.one("SELECT * FROM channels WHERE id=?", (cid,)) or abort(404)
        if c["platform"] == "youtube" and c["refresh_enc"] and cfg.google_ready:
            try:
                engine.youtube.revoke(box.decrypt(c["refresh_enc"]))
            except Exception:
                pass
        db.execute("UPDATE destinations SET status='canceled', detail='Account removed' "
                   "WHERE channel_id=? AND status IN ('queued','waiting')", (cid,))
        db.execute("DELETE FROM channels WHERE id=?", (cid,))
        flash(f"Removed {c['name']}", "ok")
        return redirect(url_for("channels_page"))

    @app.post("/channels/<int:cid>/disconnect")
    @login_required
    def channel_disconnect(cid):
        db.one("SELECT id FROM channels WHERE id=?", (cid,)) or abort(404)
        db.update("channels", cid, {"token_enc": None, "refresh_enc": None, "token_expires_at": None,
                                    "status": "disconnected", "status_detail": None,
                                    "updated_at": iso(utcnow())})
        flash("Disconnected", "ok")
        return redirect(url_for("channels_page"))

    @app.post("/channels/discovered/<int:did>/add")
    @login_required
    def discovered_add(did):
        d = db.one("SELECT * FROM discovered WHERE id=?", (did,)) or abort(404)
        now = iso(utcnow())
        mx = db.one("SELECT MAX(sort) AS m FROM channels")["m"] or 0
        db.insert("channels", {"platform": d["platform"], "name": d["name"] or d["handle"],
                               "handle": d["handle"], "external_id": d["external_id"],
                               "remote_name": d["name"], "avatar_url": d["avatar_url"],
                               "token_enc": d["token_enc"], "status": "connected", "sort": mx + 1, "auth": "facebook",
                               "created_at": now, "updated_at": now})
        db.execute("DELETE FROM discovered WHERE id=?", (did,))
        flash(f"Added {d['name'] or d['handle']}", "ok")
        return redirect(url_for("channels_page"))

    # ---- OAuth -------------------------------------------------------
    @app.get("/connect/<int:cid>")
    @login_required
    def connect(cid):
        c = db.one("SELECT * FROM channels WHERE id=?", (cid,)) or abort(404)
        return _start_oauth(c["platform"], cid)

    @app.get("/connect/new/<platform>")
    @login_required
    def connect_new(platform):
        if platform not in PLATFORMS:
            abort(404)
        return _start_oauth(platform, None)

    def _start_oauth(platform, cid):
        if not platform_ready(platform):
            flash("Setup needed: add the app keys on the server first.", "error")
            return redirect(url_for("channels_page"))
        state = secrets.token_urlsafe(24)
        flow = platform
        if platform == "instagram":
            flow = "iglogin" if cfg.ig_login_ready else "facebook"
        session["oauth"] = {"state": state, "platform": flow, "cid": cid, "t": time.time()}
        if flow == "youtube":
            return redirect(engine.youtube.auth_url(state))
        if flow == "iglogin":
            return redirect(engine.iglogin.auth_url(state))
        if not cfg.meta_ready:
            flash("Setup needed: add the Facebook app keys on the server first.", "error")
            return redirect(url_for("channels_page"))
        return redirect(engine.meta.auth_url(state))

    def _check_state(platform):
        st = session.pop("oauth", None)
        got = request.args.get("state", "")
        allowed = {"youtube": ("youtube",), "iglogin": ("iglogin",), "meta": ("instagram", "facebook")}[platform]
        if (not st or st.get("platform") not in allowed
                or not hmac.compare_digest(got, st.get("state", "")) or time.time() - st.get("t", 0) > 900):
            abort(400, "Sign-in expired. Try Connect again.")
        if request.args.get("error"):
            flash("Sign-in was canceled", "error")
            return None
        if not request.args.get("code"):
            abort(400, "Missing sign-in code")
        return st

    @app.get("/oauth/google/callback")
    @login_required
    def google_callback():
        st = _check_state("youtube")
        if st is None:
            return redirect(url_for("channels_page"))
        try:
            tok = engine.youtube.exchange_code(request.args["code"])
            ch = engine.youtube.my_channel(tok["access_token"])
        except ProviderError as e:
            flash(e.message, "error")
            return redirect(url_for("channels_page"))
        now = utcnow()
        clash = db.one("SELECT * FROM channels WHERE platform='youtube' AND external_id=?", (ch["id"],))
        cid = st.get("cid")
        if clash and clash["id"] != cid:
            cid = clash["id"]          # same channel already listed: refresh that row instead
        data = {"external_id": ch["id"], "remote_name": ch["title"], "avatar_url": ch["avatar"], "auth": "google",
                "handle": ch.get("handle"), "token_enc": box.encrypt(tok["access_token"]),
                "token_expires_at": iso(now + timedelta(seconds=int(tok.get("expires_in", 3600)))),
                "status": "connected", "status_detail": None, "updated_at": iso(now)}
        if tok.get("refresh_token"):
            data["refresh_enc"] = box.encrypt(tok["refresh_token"])
        if cid:
            db.update("channels", cid, data)
        else:
            mx = db.one("SELECT MAX(sort) AS m FROM channels")["m"] or 0
            data.update(platform="youtube", name=ch["title"], sort=mx + 1, created_at=iso(now))
            db.insert("channels", data)
        flash(f"Connected {ch['title']}", "ok")
        return redirect(url_for("channels_page"))

    @app.get("/oauth/meta/callback")
    @login_required
    def meta_callback():
        st = _check_state("meta")
        if st is None:
            return redirect(url_for("channels_page"))
        try:
            user_token = engine.meta.exchange_code(request.args["code"])
            pages = engine.meta.list_accounts(user_token)
        except ProviderError as e:
            flash(e.message, "error")
            return redirect(url_for("channels_page"))
        result = link_meta_accounts(db, box, pages)
        msg = f"Connected {result['connected']} account" + ("s" if result["connected"] != 1 else "")
        if result["discovered"]:
            msg += f" · {result['discovered']} more found below"
        flash(msg, "ok" if result["connected"] else "error")
        cid = st.get("cid")
        if cid:
            row = db.one("SELECT * FROM channels WHERE id=?", (cid,))
            if row and row["status"] != "connected":
                flash(f"Couldn't find {row['name']} in that Facebook login. "
                      "Make sure you're a Page admin and Instagram is a Business/Creator account linked to it.", "error")
        return redirect(url_for("channels_page"))

    @app.get("/oauth/instagram/callback")
    @login_required
    def instagram_callback():
        st = _check_state("iglogin")
        if st is None:
            return redirect(url_for("channels_page"))
        try:
            tok = engine.iglogin.exchange_code(request.args["code"])
            acct = engine.iglogin.me(tok["access_token"])
        except ProviderError as e:
            flash(e.message, "error")
            return redirect(url_for("channels_page"))
        now = utcnow()
        rows = channels("instagram")
        same = [r for r in rows if r["external_id"] == acct["id"]
                or (_norm(r["handle"]) and _norm(r["handle"]) == _norm(acct["username"]))]
        cid = same[0]["id"] if same else st.get("cid")
        data = {"external_id": acct["id"], "remote_name": acct["username"], "avatar_url": acct["avatar"],
                "handle": acct["username"], "auth": "iglogin", "token_enc": box.encrypt(tok["access_token"]),
                "refresh_enc": None, "token_expires_at": iso(now + timedelta(seconds=tok["expires_in"])),
                "status": "connected", "status_detail": None, "updated_at": iso(now)}
        if cid:
            db.update("channels", cid, data)
        else:
            mx = db.one("SELECT MAX(sort) AS m FROM channels")["m"] or 0
            data.update(platform="instagram", name=acct["username"] or "Instagram", sort=mx + 1, created_at=iso(now))
            db.insert("channels", data)
        flash(f"Connected @{acct['username']}", "ok")
        return redirect(url_for("channels_page"))

    # ---- uploads -----------------------------------------------------
    def media_row(mid):
        return db.one("SELECT * FROM media WHERE id=?", (mid,)) or abort(404)

    @app.post("/api/uploads")
    @login_required
    def upload_init():
        j = request.get_json(silent=True) or {}
        name = os.path.basename(str(j.get("name") or "file"))[:200]
        ext = os.path.splitext(name)[1].lower()
        size = int(j.get("size") or 0)
        if ext in VIDEO_EXT:
            kind = "video"
        elif ext in IMAGE_EXT:
            kind = "image"
        else:
            return jsonify(error="Use a video (MP4/MOV) or image (JPG/PNG)"), 400
        if size <= 0:
            return jsonify(error="Empty file"), 400
        if size > cfg.MAX_UPLOAD_MB * 1024 * 1024:
            return jsonify(error=f"File is over {cfg.MAX_UPLOAD_MB} MB"), 400
        token = secrets.token_urlsafe(24)
        folder = os.path.join(media_dir, token)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "original" + ext)
        open(path, "wb").close()
        mime = str(j.get("type") or "")[:100] or ("video/mp4" if kind == "video" else "image/jpeg")

        def num(k):
            try:
                v = float(j.get(k))
                return v if v > 0 else None
            except (TypeError, ValueError):
                return None
        mid = db.insert("media", {"token": token, "kind": kind, "filename": name, "path": path,
                                  "mime": mime, "size": size, "received": 0,
                                  "width": int(num("width") or 0) or None,
                                  "height": int(num("height") or 0) or None,
                                  "duration": num("duration"), "status": "uploading",
                                  "created_at": iso(utcnow())})
        return jsonify(id=mid, chunk=8 * 1024 * 1024, received=0)

    @app.get("/api/uploads/<int:mid>")
    @login_required
    def upload_status(mid):
        m = media_row(mid)
        return jsonify(id=mid, received=m["received"], size=m["size"], status=m["status"])

    @app.put("/api/uploads/<int:mid>/chunk")
    @login_required
    def upload_chunk(mid):
        m = media_row(mid)
        if m["status"] != "uploading":
            return jsonify(error="Upload already finished"), 409
        offset = request.args.get("offset", type=int)
        if offset is None or offset < 0:
            return jsonify(error="Missing offset"), 400
        if offset != m["received"]:
            return jsonify(error="Offset mismatch", received=m["received"]), 409
        written = 0
        with open(m["path"], "r+b") as f:
            f.seek(offset)
            while True:
                buf = request.stream.read(1024 * 1024)
                if not buf:
                    break
                written += len(buf)
                if offset + written > m["size"]:
                    f.truncate(offset)
                    return jsonify(error="More data than expected"), 400
                f.write(buf)
        db.update("media", mid, {"received": offset + written})
        return jsonify(received=offset + written)

    @app.post("/api/uploads/<int:mid>/complete")
    @login_required
    def upload_complete(mid):
        m = media_row(mid)
        if m["status"] == "ready":
            return jsonify(id=mid, status="ready")
        if m["received"] != m["size"] or os.path.getsize(m["path"]) != m["size"]:
            return jsonify(error="Upload incomplete", received=m["received"]), 409
        data = {"status": "ready", "done_at": iso(utcnow())}
        if m["kind"] == "image":
            try:
                data.update(prepare_image(m["path"]))
            except Exception:
                return jsonify(error="That image couldn't be read"), 400
        db.update("media", mid, data)
        m = media_row(mid)
        return jsonify(id=mid, status="ready", width=m["width"], height=m["height"],
                       shape=detect_shape(m["width"], m["height"], m["duration"]) if m["kind"] == "video" else None)

    @app.delete("/api/uploads/<int:mid>")
    @login_required
    def upload_delete(mid):
        m = media_row(mid)
        used = db.query("SELECT id, media_ids FROM posts")
        if any(mid in json.loads(p["media_ids"] or "[]") for p in used):
            return jsonify(ok=True)          # part of a post: cleanup handles it later
        engine.delete_media_files(m)
        db.update("media", mid, {"status": "deleted"})
        return jsonify(ok=True)

    @app.get("/m/<token>/<name>")
    def media_public(token, name):
        """Tokenized public URL: Meta's servers fetch media from here."""
        if not re.fullmatch(r"[A-Za-z0-9_\-]{20,64}", token):
            abort(404)
        m = db.one("SELECT * FROM media WHERE token=?", (token,))
        if not m or m["status"] != "ready":
            abort(404)
        if name == "ig.jpg" and m["jpeg_path"]:
            path = m["jpeg_path"]
        elif name == "thumb":
            path = os.path.join(os.path.dirname(m["path"]), "thumb.jpg")
        else:
            path = m["path"]
        if not os.path.exists(path):
            abort(404)
        mime = "image/jpeg" if path.endswith((".jpg", ".jpeg")) else m["mime"]
        resp = send_file(path, mimetype=mime, conditional=True, max_age=3600)
        resp.headers["X-Robots-Tag"] = "noindex"
        return resp

    # ---- posts -------------------------------------------------------
    @app.post("/api/posts")
    @login_required
    def create_post():
        j = request.get_json(silent=True) or {}
        post_type = j.get("type")
        if post_type not in ("video", "photo", "text"):
            return jsonify(error="Pick a post type"), 400
        override = j.get("override") if j.get("override") in ("short", "long") else None
        media_ids = [int(x) for x in (j.get("media_ids") or []) if str(x).isdigit()]
        media = []
        for mid in media_ids:
            m = db.one("SELECT * FROM media WHERE id=?", (mid,))
            if not m or m["status"] != "ready":
                return jsonify(error="Wait for the upload to finish"), 400
            media.append(m)
        if post_type == "video" and (len(media) != 1 or media[0]["kind"] != "video"):
            return jsonify(error="Add one video"), 400
        if post_type == "photo":
            if not media or any(m["kind"] != "image" for m in media):
                return jsonify(error="Add photos"), 400
            if len(media) > LIMITS["carousel_max"]:
                return jsonify(error="Up to 10 photos"), 400
        if post_type == "text":
            media = []
            media_ids = []
        dests = j.get("destinations") or []
        if not dests:
            return jsonify(error="Pick at least one account"), 400
        seen, rows, now = set(), [], utcnow()
        for d in dests:
            try:
                cid = int(d.get("channel_id"))
            except (TypeError, ValueError):
                return jsonify(error="Bad account"), 400
            if cid in seen:
                continue
            seen.add(cid)
            ch = db.one("SELECT * FROM channels WHERE id=?", (cid,))
            if not ch:
                return jsonify(error="An account was removed. Refresh the page."), 400
            if ch["status"] != "connected":
                return jsonify(error=f"Connect {ch['name']} first", channel_id=cid), 400
            kind = route_post(post_type, ch["platform"], media, override)
            try:
                clean = validate_destination(post_type, ch["platform"], kind, d, now)
            except ValidationError as e:
                return jsonify(error=f"{ch['name']}: {e}", channel_id=cid), 400
            mode, status = plan_schedule(ch["platform"], kind, clean["schedule_at"], now)
            rows.append({"channel_id": cid, "channel_name": ch["name"], "platform": ch["platform"],
                         "kind": kind, "title": clean["title"], "body": clean["body"],
                         "link": clean["link"], "privacy": clean["privacy"],
                         "options": json.dumps({"share_to_feed": clean["share_to_feed"]}),
                         "schedule_at": iso(clean["schedule_at"]), "mode": mode, "status": status,
                         "attempts": 0, "created_at": iso(now), "updated_at": iso(now)})
        label = (rows[0]["title"] or rows[0]["body"] or (media[0]["filename"] if media else "Post"))[:80]
        pid = db.insert("posts", {"post_type": post_type, "media_ids": json.dumps(media_ids),
                                  "override": override, "label": label, "created_at": iso(now)})
        for r in rows:
            r["post_id"] = pid
            db.insert("destinations", r)
        if start_worker:
            threading.Thread(target=engine.tick, daemon=True).start()
        return jsonify(id=pid, destinations=len(rows))

    @app.get("/api/queue")
    @login_required
    def api_queue():
        posts = db.query("SELECT * FROM posts ORDER BY id DESC LIMIT 50")
        out = []
        if posts:
            ids = [p["id"] for p in posts]
            marks = ",".join("?" for _ in ids)
            dests = db.query(f"SELECT * FROM destinations WHERE post_id IN ({marks}) ORDER BY id", ids)
            by = defaultdict(list)
            for d in dests:
                by[d["post_id"]].append({
                    "id": d["id"], "channel": d["channel_name"], "platform": d["platform"],
                    "kind": d["kind"], "status": d["status"], "detail": d["detail"],
                    "progress": d["progress"], "url": d["remote_url"], "schedule_at": d["schedule_at"],
                    "mode": d["mode"], "title": d["title"] or d["body"] or ""})
            for p in posts:
                thumb = None
                mids = json.loads(p["media_ids"] or "[]")
                if mids:
                    m = db.one("SELECT kind, token, filename, status FROM media WHERE id=?", (mids[0],))
                    if m and m["status"] == "ready" and m["kind"] == "image":
                        thumb = f"/m/{m['token']}/thumb"
                out.append({"id": p["id"], "type": p["post_type"], "label": p["label"],
                            "created_at": p["created_at"], "thumb": thumb,
                            "count": len(mids), "destinations": by[p["id"]]})
        active = any(d["status"] in ACTIVE for p in out for d in p["destinations"])
        return jsonify(posts=out, active=active)

    @app.post("/api/destinations/<int:did>/retry")
    @login_required
    def retry(did):
        d = db.one("SELECT * FROM destinations WHERE id=?", (did,)) or abort(404)
        if d["status"] not in ("failed", "canceled"):
            return jsonify(error="Only failed posts can be retried"), 400
        sched = parse_iso(d["schedule_at"])
        now = utcnow()
        if sched and sched > now + timedelta(minutes=1):
            mode, status = plan_schedule(d["platform"], d["kind"], sched, now)
        else:
            mode, status = "now", "queued"
        db.update("destinations", did, {"status": status, "mode": mode, "detail": None, "attempts": 0,
                                        "next_attempt_at": None, "container_id": None,
                                        "container_at": None, "progress": None, "updated_at": iso(now)})
        if start_worker:
            threading.Thread(target=engine.tick, daemon=True).start()
        return jsonify(ok=True)

    @app.post("/api/destinations/<int:did>/cancel")
    @login_required
    def cancel(did):
        d = db.one("SELECT * FROM destinations WHERE id=?", (did,)) or abort(404)
        if d["status"] not in ("queued", "waiting"):
            return jsonify(error="It's already being sent or is held by the platform"), 400
        n = db.execute("UPDATE destinations SET status='canceled', detail=NULL, updated_at=? "
                       "WHERE id=? AND status IN ('queued','waiting') AND locked_until IS NULL",
                       (iso(utcnow()), did))
        if not n:
            return jsonify(error="It's being sent right now"), 409
        return jsonify(ok=True)

    @app.post("/api/posts/<int:pid>/delete")
    @login_required
    def delete_post(pid):
        db.one("SELECT id FROM posts WHERE id=?", (pid,)) or abort(404)
        busy = db.one("SELECT COUNT(*) AS n FROM destinations WHERE post_id=? AND "
                      "(status IN ('uploading','processing') OR locked_until IS NOT NULL)", (pid,))["n"]
        if busy:
            return jsonify(error="Still sending. Try again in a moment."), 409
        db.execute("DELETE FROM destinations WHERE post_id=?", (pid,))
        db.execute("DELETE FROM posts WHERE id=?", (pid,))
        return jsonify(ok=True)

    # ---- cron --------------------------------------------------------
    @app.route("/cron/run", methods=["GET", "POST"])
    def cron_run():
        if not cfg.CRON_TOKEN:
            abort(404)
        sent = request.headers.get("X-Cron-Token") or request.args.get("token", "")
        if not hmac.compare_digest(sent.encode(), cfg.CRON_TOKEN.encode()):
            abort(403)
        started = engine.tick()
        removed = engine.cleanup()
        refreshed = engine.refresh_ig_tokens()
        return jsonify(ok=True, started=started, cleaned=removed, refreshed=refreshed)

    return app


def prepare_image(path):
    """Read size; make a JPEG copy for Instagram if the original isn't JPEG."""
    from PIL import Image, ImageOps
    out = {}
    with Image.open(path) as src:
        fmt = (src.format or "").upper()
        im = ImageOps.exif_transpose(src)
        out["width"], out["height"] = im.size
        big = os.path.getsize(path) > IG_IMAGE_MAX
        if fmt != "JPEG" or big:
            # Instagram only takes JPEG (max 8 MB). Make a high-quality copy; the original stays untouched.
            jp = os.path.join(os.path.dirname(path), "ig.jpg")
            rgb = im.convert("RGB")
            if big and rgb.width > 2160:
                rgb = rgb.resize((2160, round(rgb.height * 2160 / rgb.width)), Image.LANCZOS)
            rgb.save(jp, "JPEG", quality=95 if not big else 90, subsampling=0, optimize=True)
            out["jpeg_path"] = jp
    thumb = os.path.join(os.path.dirname(path), "thumb.jpg")
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((240, 240))
        im.save(thumb, "JPEG", quality=80)
    return out


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def link_meta_accounts(db, box, pages):
    """Match Pages / IG accounts from a Facebook login to channel rows.

    Unmatched accounts are saved to `discovered` so they can be added in one tap.
    """
    now = iso(utcnow())
    rows = db.query("SELECT * FROM channels WHERE platform IN ('facebook','instagram')")
    fb_rows = [r for r in rows if r["platform"] == "facebook"]
    ig_rows = [r for r in rows if r["platform"] == "instagram"]
    connected, found = 0, []

    def match(cands, ext_id, keys):
        keys = {_norm(k) for k in keys if k}
        for r in cands:
            if r["external_id"] and r["external_id"] == ext_id:
                return r
        for r in cands:
            if not r["external_id"] and (_norm(r["handle"]) in keys or _norm(r["name"]) in keys):
                return r
        return None

    for p in pages:
        tok = box.encrypt(p["token"]) if p.get("token") else None
        r = match(fb_rows, p["id"], [p.get("username"), p["id"], p.get("name")])
        if r:
            db.update("channels", r["id"], {"external_id": p["id"], "remote_name": p.get("name"), "auth": "facebook",
                                            "avatar_url": p.get("avatar"), "token_enc": tok,
                                            "status": "connected", "status_detail": None, "updated_at": now})
            r["external_id"] = p["id"]
            connected += 1
        else:
            found.append(("facebook", p["id"], p.get("name"), p.get("username"), p.get("avatar"), tok))
        ig = p.get("ig")
        if ig:
            r = match(ig_rows, ig["id"], [ig.get("username")])
            if r:
                db.update("channels", r["id"], {"external_id": ig["id"], "remote_name": ig.get("username"), "auth": "facebook",
                                                "avatar_url": ig.get("avatar"), "token_enc": tok,
                                                "status": "connected", "status_detail": None, "updated_at": now})
                r["external_id"] = ig["id"]
                connected += 1
            else:
                found.append(("instagram", ig["id"], ig.get("username"), ig.get("username"), ig.get("avatar"), tok))
    db.execute("DELETE FROM discovered")
    for plat, ext, name, handle, avatar, tok in found:
        db.insert("discovered", {"platform": plat, "external_id": ext, "name": name, "handle": handle,
                                 "avatar_url": avatar, "token_enc": tok, "created_at": now})
    return {"connected": connected, "discovered": len(found)}


app = None
if os.environ.get("UNIKPOST_NO_APP") != "1":
    try:
        app = create_app()
    except RuntimeError as e:     # e.g. SECRET_KEY missing during import in tooling
        log.error("%s", e)
