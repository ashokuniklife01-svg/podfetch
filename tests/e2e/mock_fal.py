"""Tiny stand-in for queue.fal.run + rest.fal.ai used by the browser E2E test."""
import json, os, time, uuid
from flask import Flask, request, jsonify, send_file
HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
jobs = {}
uploads = {}
LOG = []

def ok_key():
    return request.headers.get("Authorization") == "Key fake-key:fake-secret"

@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r

@app.post("/storage/upload/initiate")
def initiate():
    if not ok_key(): return jsonify(detail="No user found for Key ID and Secret"), 401
    fid = uuid.uuid4().hex
    base = request.host_url.rstrip("/")
    return jsonify(upload_url=f"{base}/put/{fid}", file_url=f"{base}/files/{fid}.jpg")

@app.put("/put/<fid>")
def put(fid):
    uploads[fid] = request.get_data()
    return "", 200

@app.get("/files/<name>")
def files(name):
    return send_file(os.path.join(HERE, name))

@app.post("/<path:endpoint>")
def submit(endpoint):
    if not ok_key(): return jsonify(detail="No user found for Key ID and Secret"), 401
    body = request.get_json()
    LOG.append({"endpoint": endpoint, "body": body})
    if "BLOCKME" in body.get("prompt", ""):
        return jsonify(detail=[{"loc": ["body", "prompt"], "msg": "flagged", "type": "content_policy_violation"}]), 422
    if "NOMONEY" in body.get("prompt", ""):
        return jsonify(detail="User is locked. Reason: Exhausted balance."), 403
    rid = uuid.uuid4().hex
    owner_app = "/".join(endpoint.split("/")[:2])
    jobs[rid] = {"t": time.time(), "endpoint": endpoint, "body": body}
    base = request.host_url.rstrip("/") + "/" + owner_app + "/requests/" + rid
    return jsonify(request_id=rid, status_url=base + "/status", response_url=base)

@app.get("/<path:owner_app>/requests/<rid>/status")
def status(owner_app, rid):
    j = jobs[rid]; dt = time.time() - j["t"]
    if dt < 1: return jsonify(status="IN_QUEUE", queue_position=0), 202
    if dt < 3: return jsonify(status="IN_PROGRESS", logs=[{"message": "working"}]), 202
    return jsonify(status="COMPLETED")

@app.get("/<path:owner_app>/requests/<rid>")
def result(owner_app, rid):
    j = jobs[rid]; b = j["body"]; base = request.host_url.rstrip("/")
    portrait = b.get("aspect_ratio") == "9:16" or b.get("image_size") in ("portrait_16_9",) or (isinstance(b.get("image_size"), dict) and b["image_size"]["height"] > b["image_size"]["width"]) or "portrait" in b.get("prompt", "")
    tag = "916" if portrait else "169"
    if "image" in j["endpoint"] and "to-video" not in j["endpoint"] or j["endpoint"] in ("fal-ai/nano-banana", "fal-ai/nano-banana-pro", "fal-ai/flux-2-pro", "fal-ai/flux/dev", "fal-ai/flux/schnell"):
        return jsonify(images=[{"url": f"{base}/files/out{tag}.png"}])
    return jsonify(video={"url": f"{base}/files/out{tag}.mp4"})

@app.get("/_log")
def log():
    return jsonify(LOG)

if __name__ == "__main__":
    app.run(port=5056)
