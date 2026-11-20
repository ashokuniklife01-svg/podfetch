"""UNIK AI Studio - prompt->video, prompt->image, image->video via fal.ai.

The fal key (FAL_KEY) stays on the server. The browser only talks to this app.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from models import ASPECTS, BY_ID, public_catalogue

log = logging.getLogger("aistudio")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

QUEUE_BASE = os.environ.get("FAL_QUEUE_BASE", "https://queue.fal.run")
REST_BASE = os.environ.get("FAL_REST_BASE", "https://rest.fal.ai")
ALLOWED_QUEUE_HOSTS = {urlparse(QUEUE_BASE).hostname}
MAX_PROMPT = 2000
MAX_UPLOAD = 10 * 1024 * 1024
HTTP_TIMEOUT = 30

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD + 512 * 1024


def fal_key():
    return (os.environ.get("FAL_KEY") or "").strip()


def app_password():
    return os.environ.get("APP_PASSWORD") or ""


def _secret():
    s = os.environ.get("SECRET_KEY") or ("aistudio:" + fal_key() + ":" + app_password())
    return hashlib.sha256(s.encode()).digest()


# ---------------------------------------------------------------- errors
class ApiError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@app.errorhandler(ApiError)
def _api_error(e):
    return jsonify(ok=False, error={"code": e.code, "message": e.message}), e.status


@app.errorhandler(413)
def _too_big(_e):
    return jsonify(ok=False, error={"code": "too_large", "message": "That file is too big. Please use an image under 10 MB."}), 413


@app.errorhandler(404)
def _nf(_e):
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error={"code": "not_found", "message": "Not found."}), 404
    return send_from_directory(app.static_folder, "index.html")


@app.errorhandler(500)
def _ise(_e):
    return jsonify(ok=False, error={"code": "server_error", "message": "Something went wrong on our side. Please try again."}), 500


def _detail_text(body):
    if isinstance(body, dict):
        d = body.get("detail", body.get("error", body.get("message", "")))
        if isinstance(d, list):
            return "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in d)
        return str(d)
    return str(body or "")


def _detail_types(body):
    types = []
    if isinstance(body, dict):
        d = body.get("detail")
        if isinstance(d, list):
            types = [x.get("type", "") for x in d if isinstance(x, dict)]
        if body.get("error_type"):
            types.append(body["error_type"])
    return types


def friendly_fal_error(status, body):
    """Map a fal HTTP error (status + parsed JSON or text) to an ApiError."""
    text = _detail_text(body)
    low = text.lower()
    types = _detail_types(body)
    if status == 401 or "no user found" in low or "invalid key" in low or "authentication" in low:
        return ApiError("bad_key", "The fal.ai key is missing or wrong. Check FAL_KEY in your Render settings.", 502)
    if "balance" in low or "credit" in low or "billing" in low or "locked" in low or status == 402:
        return ApiError("no_credits", "Your fal.ai account is out of credit. Top up at fal.ai/dashboard/billing, then try again.", 402)
    if status == 403:
        return ApiError("forbidden", "fal.ai refused this request (access denied). Check your fal.ai account and key. " + text[:200], 502)
    if "content_policy_violation" in types or "content policy" in low or "unsafe" in low or "nsfw" in low or "moderation" in low:
        return ApiError("content_policy", "The AI refused this prompt or image because of its content rules. Please rephrase and try again.", 422)
    if "generation_timeout" in types or status == 504 or "timeout" in low:
        return ApiError("timeout", "The AI took too long and gave up. Please try again (shorter clips are faster).", 504)
    if "no_media_generated" in types:
        return ApiError("no_media", "The AI didn't produce a result for this prompt. Try rewording it.", 422)
    if status == 429:
        return ApiError("busy", "fal.ai is rate limiting right now. Wait a minute and try again.", 429)
    if status == 422:
        return ApiError("invalid_input", "The AI rejected the input: " + (text[:300] or "invalid settings"), 422)
    if status >= 500:
        return ApiError("provider_down", "fal.ai had a temporary problem. Please try again in a moment.", 502)
    return ApiError("provider_error", "fal.ai error (" + str(status) + "): " + (text[:300] or "unknown"), 502)


def _json_or_text(r):
    try:
        return r.json()
    except ValueError:
        return r.text


def _fal_headers(extra=None):
    key = fal_key()
    if not key:
        raise ApiError("no_key", "The AI service isn't set up yet. Add your fal.ai key as FAL_KEY in Render, then redeploy.", 503)
    h = {"Authorization": "Key " + key, "Accept": "application/json"}
    if extra:
        h.update(extra)
    return h


def _fal(method, url, **kw):
    try:
        r = requests.request(method, url, timeout=kw.pop("timeout", HTTP_TIMEOUT), **kw)
    except requests.Timeout:
        raise ApiError("timeout", "fal.ai didn't answer in time. Please try again.", 504)
    except requests.RequestException as e:
        log.warning("fal network error: %s", e)
        raise ApiError("network", "Couldn't reach fal.ai. Please try again in a moment.", 502)
    return r


# ---------------------------------------------------------------- rate limit
_hits = defaultdict(deque)
_hits_lock = threading.Lock()
LIMITS = {  # bucket: (max events, window seconds)
    "generate": (int(os.environ.get("RATE_GENERATE", "12")), 600),
    "upload": (30, 600),
    "login": (10, 600),
    "status": (240, 60),
    "download": (60, 600),
}


def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else request.remote_addr) or "?"


def rate_limit(bucket):
    limit, window = LIMITS[bucket]
    now = time.time()
    key = (bucket, client_ip())
    with _hits_lock:
        q = _hits[key]
        while q and q[0] < now - window:
            q.popleft()
        if len(q) >= limit:
            wait = int(window - (now - q[0])) + 1
            raise ApiError("rate_limited", "Slow down a little: too many requests. Try again in %d seconds." % wait, 429)
        q.append(now)


# ---------------------------------------------------------------- password
def require_password():
    pw = app_password()
    if not pw:
        return
    given = request.headers.get("X-App-Password", "")
    if not hmac.compare_digest(given.encode(), pw.encode()):
        raise ApiError("password", "Please enter the studio password.", 401)


# ---------------------------------------------------------------- job tokens
def _b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(data):
    raw = json.dumps(data, separators=(",", ":")).encode()
    sig = hmac.new(_secret(), raw, hashlib.sha256).digest()[:16]
    return _b64(raw) + "." + _b64(sig)


def read_token(tok):
    try:
        a, b = tok.split(".", 1)
        raw = _unb64(a)
        if not hmac.compare_digest(_unb64(b), hmac.new(_secret(), raw, hashlib.sha256).digest()[:16]):
            raise ValueError
        data = json.loads(raw)
    except Exception:
        raise ApiError("bad_job", "This job link isn't valid (the server may have been reset).", 400)
    for k in ("status_url", "response_url"):
        if urlparse(data.get(k, "")).hostname not in ALLOWED_QUEUE_HOSTS:
            raise ApiError("bad_job", "This job link isn't valid.", 400)
    return data


# ---------------------------------------------------------------- output parsing
def extract_outputs(result):
    outs = []
    if not isinstance(result, dict):
        return outs
    v = result.get("video")
    if isinstance(v, dict) and v.get("url"):
        outs.append({"type": "video", "url": v["url"]})
    for v in result.get("videos") or []:
        if isinstance(v, dict) and v.get("url"):
            outs.append({"type": "video", "url": v["url"]})
    for im in result.get("images") or []:
        if isinstance(im, dict) and im.get("url"):
            outs.append({"type": "image", "url": im["url"]})
    im = result.get("image")
    if isinstance(im, dict) and im.get("url"):
        outs.append({"type": "image", "url": im["url"]})
    return outs


# ---------------------------------------------------------------- routes
@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/config")
def config():
    return jsonify(ok=True, password_required=bool(app_password()), key_configured=bool(fal_key()),
                   aspects=list(ASPECTS), models=public_catalogue())


@app.post("/api/login")
def login():
    rate_limit("login")
    if not app_password():
        return jsonify(ok=True)
    pw = (request.get_json(silent=True) or {}).get("password", "")
    if not hmac.compare_digest(str(pw).encode(), app_password().encode()):
        raise ApiError("password", "Wrong password. Please try again.", 401)
    return jsonify(ok=True)


ALLOWED_IMG = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@app.post("/api/upload")
def upload():
    require_password()
    rate_limit("upload")
    f = request.files.get("image")
    if not f:
        raise ApiError("no_image", "Please choose an image first.")
    ctype = (f.mimetype or "").lower()
    if ctype not in ALLOWED_IMG:
        raise ApiError("bad_image", "Please use a JPG, PNG or WEBP image.")
    data = f.read()
    if not data:
        raise ApiError("bad_image", "That image file is empty.")
    if len(data) > MAX_UPLOAD:
        raise ApiError("too_large", "That image is too big. Please use one under 10 MB.", 413)
    headers = _fal_headers({"Content-Type": "application/json"})
    name = "upload." + ALLOWED_IMG[ctype]
    r = _fal("POST", REST_BASE + "/storage/upload/initiate?storage_type=gcs",
             json={"file_name": name, "content_type": ctype}, headers=headers)
    if r.status_code >= 400:
        raise friendly_fal_error(r.status_code, _json_or_text(r))
    init = _json_or_text(r)
    if not isinstance(init, dict) or not init.get("upload_url") or not init.get("file_url"):
        raise ApiError("upload_failed", "Image upload to fal.ai failed. Please try again.", 502)
    r2 = _fal("PUT", init["upload_url"], data=data, headers={"Content-Type": ctype}, timeout=120)
    if r2.status_code >= 400:
        log.warning("fal storage PUT failed %s", r2.status_code)
        raise ApiError("upload_failed", "Image upload to fal.ai failed. Please try again.", 502)
    return jsonify(ok=True, url=init["file_url"])


def validate_request(body):
    mode = body.get("mode")
    m = BY_ID.get(body.get("model", ""))
    if not m or m["mode"] != mode:
        raise ApiError("bad_model", "Please pick a model from the list.")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ApiError("no_prompt", "Please type a prompt describing what you want.")
    if len(prompt) < 3:
        raise ApiError("short_prompt", "Please describe it in a few more words.")
    if len(prompt) > MAX_PROMPT:
        raise ApiError("long_prompt", "That prompt is too long (max %d characters)." % MAX_PROMPT)
    aspect = body.get("aspect")
    if aspect not in ASPECTS:
        raise ApiError("bad_aspect", "Pick 16:9 or 9:16.")
    o = {"prompt": prompt, "aspect": aspect, "negative": str(body.get("negative") or "").strip()[:500]}
    if m.get("durations"):
        try:
            d = int(body.get("duration", m["default_duration"]))
        except (TypeError, ValueError):
            d = -1
        if d not in m["durations"]:
            raise ApiError("bad_duration", "That length isn't available for this model.")
        o["duration"] = d
    o["audio"] = bool(body.get("audio")) if m.get("audio") else False
    if m.get("resolutions"):
        res = body.get("resolution") or m["default_resolution"]
        if res not in m["resolutions"]:
            raise ApiError("bad_resolution", "That quality isn't available for this model.")
        o["resolution"] = res
    if mode == "i2v":
        url = str(body.get("image_url") or "")
        ok_prefixes = ("https://", "data:image/") + ((REST_BASE + "/",) if REST_BASE.startswith("http://") else ())
        if not url.startswith(ok_prefixes):
            raise ApiError("no_image", "Please upload a starting image first.")
        o["image_url"] = url
    return m, o


@app.post("/api/generate")
def generate():
    require_password()
    body = request.get_json(silent=True) or {}
    m, o = validate_request(body)
    headers = _fal_headers({"Content-Type": "application/json"})
    rate_limit("generate")
    payload = m["build"](o)
    r = _fal("POST", QUEUE_BASE + "/" + m["endpoint"], json=payload, headers=headers)
    data = _json_or_text(r)
    if r.status_code >= 400:
        log.info("fal submit %s -> %s %s", m["endpoint"], r.status_code, str(data)[:300])
        raise friendly_fal_error(r.status_code, data)
    if not isinstance(data, dict) or not data.get("request_id"):
        raise ApiError("provider_error", "fal.ai gave an unexpected answer. Please try again.", 502)
    rid = data["request_id"]
    base = QUEUE_BASE + "/" + "/".join(m["endpoint"].split("/")[:2]) + "/requests/" + rid
    tok = make_token({
        "rid": rid, "m": m["id"],
        "status_url": data.get("status_url") or base + "/status",
        "response_url": data.get("response_url") or base,
    })
    est = round(m["price"](o), 3)
    log.info("submitted %s %s ip=%s est=$%s", m["id"], rid, client_ip(), est)
    return jsonify(ok=True, job=tok, request_id=rid, estimate=est)


@app.get("/api/status/<path:tok>")
def status(tok):
    require_password()
    rate_limit("status")
    job = read_token(tok)
    headers = _fal_headers()
    r = _fal("GET", job["status_url"], params={"logs": 1}, headers=headers)
    st = _json_or_text(r)
    if r.status_code >= 400 and r.status_code != 202:
        raise friendly_fal_error(r.status_code, st)
    if not isinstance(st, dict):
        raise ApiError("provider_error", "fal.ai gave an unexpected status. Retrying may help.", 502)
    state = st.get("status")
    logs = [l.get("message", "") for l in (st.get("logs") or []) if isinstance(l, dict)][-3:]
    if state == "IN_QUEUE":
        return jsonify(ok=True, state="queued", position=st.get("queue_position"), logs=logs)
    if state == "IN_PROGRESS":
        return jsonify(ok=True, state="running", logs=logs)
    if state != "COMPLETED":
        return jsonify(ok=True, state="running", logs=logs)
    if st.get("error"):
        e = friendly_fal_error(422 if st.get("error_type") else 500,
                               {"detail": st.get("error"), "error_type": st.get("error_type")})
        return jsonify(ok=True, state="error", error={"code": e.code, "message": e.message})
    r2 = _fal("GET", job["response_url"], headers=headers, timeout=60)
    res = _json_or_text(r2)
    if r2.status_code >= 400:
        e = friendly_fal_error(r2.status_code, res)
        return jsonify(ok=True, state="error", error={"code": e.code, "message": e.message})
    outs = extract_outputs(res)
    if not outs:
        return jsonify(ok=True, state="error", error={"code": "no_media", "message": "The AI finished but returned no file. Please try again or reword the prompt."})
    return jsonify(ok=True, state="done", outputs=outs)


DL_HOSTS = ("fal.media", "fal.run", "fal.ai", "storage.googleapis.com")


@app.get("/api/download")
def download():
    # No password: only proxies fal.ai result files (host allow-list) so <a href> downloads work.
    rate_limit("download")
    url = request.args.get("url", "")
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not any(host == h or host.endswith("." + h) for h in DL_HOSTS):
        raise ApiError("bad_url", "Only fal.ai result files can be downloaded here.")
    if host == "storage.googleapis.com" and not u.path.startswith("/falserverless/"):
        raise ApiError("bad_url", "Only fal.ai result files can be downloaded here.")
    name = os.path.basename(u.path) or "unik-ai-file"
    name = "".join(c for c in name if c.isalnum() or c in "._-")[:80] or "unik-ai-file"
    try:
        r = requests.get(url, stream=True, timeout=60)
    except requests.RequestException:
        raise ApiError("network", "Couldn't fetch the file. The link may have expired.", 502)
    if r.status_code != 200:
        raise ApiError("expired", "This file is no longer available (links expire after a while).", 410)
    hdrs = {"Content-Disposition": 'attachment; filename="unik-' + name + '"',
            "Content-Type": r.headers.get("Content-Type", "application/octet-stream")}
    if r.headers.get("Content-Length"):
        hdrs["Content-Length"] = r.headers["Content-Length"]
    return Response(stream_with_context(r.iter_content(65536)), headers=hdrs)


@app.after_request
def _sec_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
