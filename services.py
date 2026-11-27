"""Posting engine: turns destination rows into platform API calls.

State machine for a destination row:
  waiting    -> app holds it until its time (FB outside native window, IG)
  queued     -> ready to run now (or after next_attempt_at)
  uploading  -> being sent to the platform
  processing -> IG container being processed by Instagram
  scheduled  -> platform holds it and publishes itself (YT publishAt, FB scheduled)
  published  -> live (remote_url set)
  failed     -> error in `detail`; user can retry
  canceled   -> removed by the user before it ran
"""
import json
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from db import iso, parse_iso, utcnow
from providers import ProviderError
from providers.meta import InstagramLoginClient, MetaClient
from providers.youtube import YouTubeClient
from routing import FB_NATIVE_MIN, IG_CONTAINER_LEAD, with_shorts_tag
from secure import redact

log = logging.getLogger("unikpost")

TERMINAL = ("published", "scheduled", "failed", "canceled")
ACTIVE = ("queued", "waiting", "uploading", "processing")
RETRY_DELAYS = [60, 300, 900]           # seconds
IG_CONTAINER_TTL = timedelta(hours=23)


class Engine:
    def __init__(self, db, cfg, box, youtube=None, meta=None, iglogin=None, now=utcnow):
        self.db, self.cfg, self.box = db, cfg, box
        self._yt = youtube
        self._meta = meta
        self._ig = iglogin
        self.now = now
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="post")
        self._stop = threading.Event()
        self._thread = None

    # ---- clients -----------------------------------------------------
    @property
    def youtube(self):
        if self._yt is None:
            self._yt = YouTubeClient(self.cfg.GOOGLE_CLIENT_ID, self.cfg.GOOGLE_CLIENT_SECRET,
                                     f"{self.cfg.BASE_URL}/oauth/google/callback")
        return self._yt

    @property
    def meta(self):
        if self._meta is None:
            self._meta = MetaClient(self.cfg.META_APP_ID, self.cfg.META_APP_SECRET,
                                    f"{self.cfg.BASE_URL}/oauth/meta/callback",
                                    self.cfg.META_GRAPH_VERSION, self.cfg.META_CONFIG_ID)
        return self._meta

    @property
    def iglogin(self):
        if self._ig is None:
            self._ig = InstagramLoginClient(self.cfg.INSTAGRAM_APP_ID, self.cfg.INSTAGRAM_APP_SECRET,
                                            f"{self.cfg.BASE_URL}/oauth/instagram/callback",
                                            self.cfg.META_GRAPH_VERSION)
        return self._ig

    # ---- helpers -----------------------------------------------------
    def media_url(self, m, jpeg=False):
        if jpeg and m.get("jpeg_path"):
            return f"{self.cfg.BASE_URL}/m/{m['token']}/ig.jpg"
        ext = os.path.splitext(m["filename"])[1].lower() or ".bin"
        return f"{self.cfg.BASE_URL}/m/{m['token']}/file{ext}"

    def _media_for(self, post):
        ids = json.loads(post["media_ids"] or "[]")
        out = []
        for mid in ids:
            m = self.db.one("SELECT * FROM media WHERE id=?", (mid,))
            if not m or m["status"] != "ready" or not os.path.exists(m["path"]):
                raise ProviderError("The media file is gone (server restarted or cleaned up). Upload it again.")
            out.append(m)
        return out

    def _set(self, dest_id, **data):
        data["updated_at"] = iso(self.now())
        self.db.update("destinations", dest_id, data)

    def yt_token_getter(self, ch):
        state = {"token": self.box.decrypt(ch["token_enc"]), "exp": parse_iso(ch["token_expires_at"])}
        refresh = self.box.decrypt(ch["refresh_enc"])

        def get(force=False):
            fresh = state["exp"] and state["exp"] > self.now() + timedelta(minutes=5)
            if state["token"] and fresh and not force:
                return state["token"]
            if not refresh:
                raise ProviderError("YouTube access expired. Reconnect this channel.", reauth=True)
            t = self.youtube.refresh(refresh)
            state["token"] = t["access_token"]
            state["exp"] = self.now() + timedelta(seconds=int(t.get("expires_in", 3600)))
            self.db.update("channels", ch["id"], {"token_enc": self.box.encrypt(state["token"]),
                                                  "token_expires_at": iso(state["exp"]),
                                                  "updated_at": iso(self.now())})
            return state["token"]
        return get

    # ---- due selection -----------------------------------------------
    def is_due(self, d, now=None):
        now = now or self.now()
        if d["locked_until"] and parse_iso(d["locked_until"]) > now:
            return False
        st = d["status"]
        sched = parse_iso(d["schedule_at"])
        nxt = parse_iso(d["next_attempt_at"])
        if st != "scheduled" and nxt is not None and nxt > now:
            return False
        if st == "queued":
            return True
        if st == "processing":
            return True
        if st == "waiting":
            if d["platform"] == "instagram":
                if not d["container_id"]:
                    return sched is None or now >= sched - IG_CONTAINER_LEAD
                return sched is None or now >= sched or self._container_stale(d, now)
            return sched is None or now >= sched
        if st == "scheduled":
            return sched is not None and now >= sched      # flip to published
        return False

    def _container_stale(self, d, now):
        made = parse_iso(d["container_at"])
        return made is not None and now - made > IG_CONTAINER_TTL

    def claim(self, dest_id, seconds=7200):
        now = self.now()
        return self.db.execute(
            "UPDATE destinations SET locked_until=? WHERE id=? AND (locked_until IS NULL OR locked_until<?)",
            (iso(now + timedelta(seconds=seconds)), dest_id, iso(now))) == 1

    def release(self, dest_id):
        self.db.execute("UPDATE destinations SET locked_until=NULL WHERE id=?", (dest_id,))

    def due(self):
        rows = self.db.query(
            "SELECT * FROM destinations WHERE status IN ('queued','waiting','processing','scheduled') ORDER BY id")
        now = self.now()
        return [r for r in rows if self.is_due(r, now)]

    # ---- main entry points -------------------------------------------
    def tick(self, wait=False):
        """Run everything that is due. Returns number of jobs started."""
        started, futures = 0, []
        for d in self.due():
            if not self.claim(d["id"]):
                continue
            started += 1
            if wait:
                self.run(d["id"])
            else:
                futures.append(self._pool.submit(self.run, d["id"]))
        return started

    def run(self, dest_id):
        try:
            self.process(dest_id)
        except Exception as e:      # never let a thread die silently
            log.error("destination %s crashed: %s", dest_id, type(e).__name__)
            self._set(dest_id, status="failed", detail="Unexpected error. Tap retry.")
        finally:
            self.release(dest_id)

    def process(self, dest_id):
        d = self.db.one("SELECT * FROM destinations WHERE id=?", (dest_id,))
        if not d or d["status"] in ("published", "failed", "canceled"):
            return
        now = self.now()
        if d["status"] == "scheduled":
            self._set(dest_id, status="published", detail=None)
            return
        ch = self.db.one("SELECT * FROM channels WHERE id=?", (d["channel_id"],)) if d["channel_id"] else None
        if not ch:
            self._set(dest_id, status="failed", detail="This account was removed.")
            return
        if ch["status"] != "connected":
            self._set(dest_id, status="failed", detail="Account isn't connected. Reconnect it, then retry.")
            return
        post = self.db.one("SELECT * FROM posts WHERE id=?", (d["post_id"],))
        try:
            if d["platform"] == "youtube":
                self._youtube(d, ch, post, now)
            elif d["platform"] == "facebook":
                self._facebook(d, ch, post, now)
            elif d["platform"] == "instagram":
                self._instagram(d, ch, post, now)
        except ProviderError as e:
            self._fail(d, ch, e)

    def _fail(self, d, ch, e):
        attempts = (d["attempts"] or 0) + 1
        if e.reauth:
            self.db.update("channels", ch["id"], {"status": "error", "status_detail": "Reconnect needed",
                                                  "updated_at": iso(self.now())})
            self._set(d["id"], status="failed", detail=redact(e.message), attempts=attempts, progress=None)
        elif e.transient and attempts <= len(RETRY_DELAYS):
            nxt = self.now() + timedelta(seconds=RETRY_DELAYS[attempts - 1])
            # IG posts waiting for a container keep their schedule; others re-queue
            status = "queued" if d["status"] in ("queued", "uploading") or d["platform"] != "instagram" else d["status"]
            self._set(d["id"], status=status, detail="Temporary problem, retrying soon", attempts=attempts,
                      next_attempt_at=iso(nxt), progress=None)
        else:
            self._set(d["id"], status="failed", detail=redact(e.message), attempts=attempts, progress=None)

    # ---- YouTube -----------------------------------------------------
    def _youtube(self, d, ch, post, now):
        media = self._media_for(post)[0]
        title, body = d["title"], d["body"] or ""
        if d["kind"] == "short":
            title, body = with_shorts_tag(title, body)
        sched = parse_iso(d["schedule_at"])
        publish_at = iso(sched) if sched and sched > now + timedelta(minutes=1) else None
        meta = self.youtube.build_metadata(title, body, d["privacy"] or "public", publish_at)
        get_token = self.yt_token_getter(ch)
        self._set(d["id"], status="uploading", detail=None, progress=0)
        session_url = self.youtube.start_upload(get_token(False), meta, media["size"], media["mime"])
        last = {"t": 0}

        def progress(sent, total):
            if time.monotonic() - last["t"] > 2 or sent >= total:
                last["t"] = time.monotonic()
                self._set(d["id"], progress=int(sent * 100 / max(total, 1)))

        video = self.youtube.upload_file(session_url, media["path"], get_token, progress)
        vid = video["id"]
        url = f"https://youtube.com/shorts/{vid}" if d["kind"] == "short" else f"https://youtu.be/{vid}"
        privacy = (video.get("status") or {}).get("privacyStatus")
        if publish_at:
            self._set(d["id"], status="scheduled", remote_id=vid, remote_url=url, progress=100,
                      detail="YouTube will publish it at the set time")
        else:
            note = None
            if privacy and privacy != (d["privacy"] or "public"):
                note = f"YouTube set it to {privacy}"
            self._set(d["id"], status="published", remote_id=vid, remote_url=url, progress=100, detail=note)

    # ---- Facebook ----------------------------------------------------
    def _facebook(self, d, ch, post, now):
        token = self.box.decrypt(ch["token_enc"])
        if not token:
            raise ProviderError("Facebook access expired. Reconnect.", reauth=True)
        page = ch["external_id"]
        sched = parse_iso(d["schedule_at"])
        publish_ts = None
        if d["mode"] == "native" and sched:
            if sched - now >= FB_NATIVE_MIN:
                publish_ts = sched.timestamp()
            elif sched > now:
                # delayed past FB's 10-minute minimum: let the app post it at the time
                self._set(d["id"], status="waiting", mode="app")
                return
        self._set(d["id"], status="uploading", detail=None, progress=None)
        kind, body = d["kind"], d["body"] or ""
        if kind == "text":
            rid, url = self.meta.fb_feed(page, token, body, d["link"] or "", publish_ts)
        else:
            media = self._media_for(post)
            if kind == "video":
                rid, url = self.meta.fb_video(page, token, self.media_url(media[0]), d["title"] or "", body, publish_ts)
            elif kind == "reel":
                rid, url = self.meta.fb_reel(page, token, self.media_url(media[0]), body, publish_ts)
            elif kind == "photo":
                rid, url = self.meta.fb_photo(page, token, self._fb_img(media[0]), body, publish_ts)
            elif kind == "photos":
                rid, url = self.meta.fb_photos(page, token, [self._fb_img(m) for m in media], body, publish_ts)
            else:
                raise ProviderError(f"Unsupported Facebook post kind {kind}")
        if publish_ts:
            self._set(d["id"], status="scheduled", remote_id=rid, remote_url=url,
                      detail="Facebook will publish it at the set time")
        else:
            self._set(d["id"], status="published", remote_id=rid, remote_url=url, detail=None)

    # ---- Instagram ---------------------------------------------------
    def _instagram(self, d, ch, post, now):
        token = self.box.decrypt(ch["token_enc"])
        if not token:
            raise ProviderError("Instagram access expired. Reconnect.", reauth=True)
        ig = ch["external_id"]
        api = self.iglogin if ch.get("auth") == "iglogin" else self.meta
        sched = parse_iso(d["schedule_at"])
        container = d["container_id"]
        if container and self._container_stale(d, now):
            container = None
        if not container:
            media = self._media_for(post)
            kind = d["kind"]
            if kind == "reel":
                container = api.ig_create_container(ig, token, "reel", self.media_url(media[0]),
                                                           d["body"] or "", share_to_feed=self._opt(d, "share_to_feed", True))
            elif kind == "photo":
                container = api.ig_create_container(ig, token, "photo", self.media_url(media[0], jpeg=True),
                                                           d["body"] or "")
            elif kind == "carousel":
                kids = []
                for m in media:
                    kid = api.ig_create_container(ig, token, "photo", self.media_url(m, jpeg=True),
                                                        is_carousel_item=True)
                    api.ig_wait(kid, token, timeout=60)
                    kids.append(kid)
                container = api.ig_create_container(ig, token, "carousel", children=kids,
                                                           caption=d["body"] or "")
            else:
                raise ProviderError(f"Unsupported Instagram post kind {kind}")
            self._set(d["id"], container_id=container, container_at=iso(self.now()),
                      status="processing", detail="Instagram is processing the media")
        code = api.ig_wait(container, token, timeout=90 if d["kind"] == "reel" else 30)
        if code not in ("FINISHED", "PUBLISHED"):
            self._set(d["id"], status="processing", detail="Instagram is processing the media")
            return
        if sched and self.now() < sched:
            self._set(d["id"], status="waiting", detail="Ready. Posts at the set time")
            return
        mid, link = api.ig_publish(ig, token, container)
        self._set(d["id"], status="published", remote_id=mid, remote_url=link or "https://www.instagram.com/",
                  detail=None)

    def _fb_img(self, m):
        # Facebook takes PNG/JPEG originals; use the JPEG copy only for very large files
        return self.media_url(m, jpeg=(m["size"] or 0) > 8 * 1024 * 1024)

    @staticmethod
    def _opt(d, key, default=None):
        try:
            return json.loads(d["options"] or "{}").get(key, default)
        except ValueError:
            return default

    # ---- housekeeping ------------------------------------------------
    def recover(self):
        """On boot: unlock rows and resume uploads interrupted by a restart."""
        self.db.execute("UPDATE destinations SET locked_until=NULL")
        self.db.execute("UPDATE destinations SET status='queued', progress=NULL, "
                        "detail='Restarted after a server restart' WHERE status='uploading'")

    def refresh_ig_tokens(self):
        """Instagram Login tokens last 60 days; refresh them once they're 30+ days old."""
        n = 0
        now = self.now()
        for ch in self.db.query("SELECT * FROM channels WHERE auth='iglogin' AND status='connected'"):
            exp = parse_iso(ch["token_expires_at"])
            if exp and exp - now > timedelta(days=30):
                continue
            try:
                t = self.iglogin.refresh(self.box.decrypt(ch["token_enc"]) or "")
            except ProviderError as e:
                if e.reauth or (exp and exp < now):
                    self.db.update("channels", ch["id"], {"status": "error", "status_detail": "Reconnect needed"})
                continue
            self.db.update("channels", ch["id"], {
                "token_enc": self.box.encrypt(t["access_token"]),
                "token_expires_at": iso(now + timedelta(seconds=t["expires_in"])), "updated_at": iso(now)})
            n += 1
        return n

    def cleanup(self):
        """Delete media no longer needed (keeps it MEDIA_RETENTION_HOURS for retries)."""
        now = self.now()
        cutoff = now - timedelta(hours=self.cfg.MEDIA_RETENTION_HOURS)
        removed = 0
        needed = set()
        for p in self.db.query("SELECT p.id, p.media_ids, MAX(d.updated_at) AS last, "
                               "SUM(CASE WHEN d.status IN ('queued','waiting','uploading','processing') THEN 1 ELSE 0 END) AS active "
                               "FROM posts p LEFT JOIN destinations d ON d.post_id=p.id GROUP BY p.id, p.media_ids"):
            last = parse_iso(p["last"]) if p["last"] else None
            if (p["active"] or 0) > 0 or (last and last > cutoff):
                needed.update(json.loads(p["media_ids"] or "[]"))
        for m in self.db.query("SELECT * FROM media WHERE status IN ('uploading','ready')"):
            if m["id"] in needed:
                continue
            created = parse_iso(m["done_at"] or m["created_at"])
            if created and created > cutoff:
                continue
            self.delete_media_files(m)
            self.db.update("media", m["id"], {"status": "deleted"})
            removed += 1
        return removed

    def delete_media_files(self, m):
        folder = os.path.dirname(m["path"])
        if folder and os.path.basename(folder) == m["token"]:
            shutil.rmtree(folder, ignore_errors=True)
        else:
            for p in (m["path"], m.get("jpeg_path")):
                if p and os.path.exists(p):
                    os.remove(p)

    # ---- background loop ---------------------------------------------
    def start(self):
        if self._thread:
            return
        self.recover()

        def loop():
            n = 0
            while not self._stop.is_set():
                try:
                    self.tick()
                    n += 1
                    if n % 40 == 1:
                        self.cleanup()
                        self.refresh_ig_tokens()
                except Exception as e:
                    log.error("worker tick failed: %s", type(e).__name__)
                self._stop.wait(self.cfg.WORKER_INTERVAL)
        self._thread = threading.Thread(target=loop, name="unikpost-worker", daemon=True)
        self._thread.start()
