#!/usr/bin/env python3
"""Personal downloader for public YouTube, Instagram, and Facebook media you have the right to save."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from flask import (
    Flask,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
)
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from extractors import (
    cookies_path,
    detect_link,
    facebook_format_string,
    facebook_info,
    has_cookies,
    http_download,
    instagram_info,
    instagram_video_format,
    social_friendly_error,
    _login_required_message,
    _ydl_opts,
)

APP_ROOT = Path(__file__).resolve().parent
DOWNLOAD_ROOT = Path(os.environ.get("YT_DOWNLOAD_DIR", "/tmp/yt-downloader-jobs"))
DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)

JOB_TTL_SECONDS = 15 * 60
CLEANUP_AFTER_SEND_SECONDS = 60

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
URL_IN_TEXT_RE = re.compile(
    r"(https?://[^\s<>\"']+|"
    r"//(?:www\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com|m\.youtube\.com|music\.youtube\.com)[^\s<>\"']+|"
    r"(?:www\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com|m\.youtube\.com|music\.youtube\.com)/[^\s<>\"']+)",
    re.IGNORECASE,
)

QUALITIES = {
    "1080": {"label": "1080p MP4", "height": 1080, "kind": "video"},
    "720": {"label": "720p MP4", "height": 720, "kind": "video"},
    "360": {"label": "360p MP4", "height": 360, "kind": "video"},
    "mp3": {"label": "MP3 Audio", "height": None, "kind": "audio"},
}

# Default clients first so adaptive 720p/1080p formats stay available.
# Later entries are tried if extraction or download fails.
FALLBACK_CLIENTS = (
    ["tv", "ios", "web_safari"],
    ["tv_embedded", "ios", "tv"],
    ["web", "mweb", "web_safari"],
    ["android", "ios"],
)

YDL_BASE = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "noprogress": True,
    "retries": 10,
    "fragment_retries": 10,
    "extractor_retries": 3,
    "file_access_retries": 3,
    "socket_timeout": 30,
    "retry_sleep_functions": {},
    "cachedir": False,
    "overwrites": True,
    "concurrent_fragment_downloads": 8,
    "http_chunk_size": 10 * 1024 * 1024,
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_ARIA2C = shutil.which("aria2c")


def _is_youtube_host(host: str) -> bool:
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in {
        "youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "youtu.be",
        "gaming.youtube.com",
        "youtubekids.com",
    }:
        return True
    return (
        host.endswith(".youtube.com")
        or host.endswith(".youtu.be")
        or host.endswith(".youtube-nocookie.com")
    )


def _clean_url(raw: str) -> str:
    """Accept any common YouTube link and return https://www.youtube.com/watch?v=ID."""
    text = re.sub(r"\s+", " ", raw or "").strip()
    if not text:
        raise ValueError("Please paste a YouTube link.")

    text = text.replace("&amp;", "&")
    found = URL_IN_TEXT_RE.search(text)
    url = found.group(1) if found else text
    url = url.strip("<>\"'.,);[]")

    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    url = re.sub(r"^http://", "https://", url, flags=re.IGNORECASE)

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not _is_youtube_host(host):
        raise ValueError("That is not a YouTube link. Paste a youtube.com or youtu.be URL.")

    path = unquote(parsed.path or "")
    query = parse_qs(parsed.query, keep_blank_values=True)
    host_short = host[4:] if host.startswith("www.") else host
    video_id = None

    if "v" in query and query["v"] and query["v"][0]:
        video_id = query["v"][0].strip()

    if host_short == "youtu.be" or host_short.endswith(".youtu.be"):
        part = path.strip("/").split("/")[0].split("?")[0]
        if part:
            video_id = video_id or part

    if not video_id:
        m = re.search(r"/(?:shorts|embed|live|v|e|watch)/([A-Za-z0-9_-]{11})", path)
        if m:
            video_id = m.group(1)

    if not video_id and query.get("u"):
        inner = unquote(query["u"][0])
        if inner.startswith("/"):
            inner = "https://www.youtube.com" + inner
        elif not inner.startswith("http"):
            inner = "https://www.youtube.com/" + inner
        inner_q = parse_qs(urlparse(inner).query)
        if inner_q.get("v"):
            video_id = inner_q["v"][0].strip()
        m = re.search(r"/(?:shorts|embed|live|v|e|watch)/([A-Za-z0-9_-]{11})", urlparse(inner).path or "")
        if m:
            video_id = video_id or m.group(1)

    if video_id:
        video_id = video_id.strip()
        if "?" in video_id:
            video_id = video_id.split("?", 1)[0]
        if not VIDEO_ID_RE.fullmatch(video_id):
            video_id = None

    if not video_id:
        if "list" in query or path.rstrip("/").endswith("playlist") or "/playlist" in path:
            raise ValueError("This looks like a playlist, not a single video. Paste a link to one video.")
        if path.startswith("/@") or path.startswith("/channel/") or path.startswith("/c/") or path.startswith("/user/"):
            raise ValueError("That looks like a channel page. Paste a link to a single video.")
        raise ValueError("Couldn't find a video in that link. Paste a youtube.com or youtu.be URL.")

    return f"https://www.youtube.com/watch?v={video_id}"


def _is_permanent_error(exc: BaseException) -> bool:
    lowered = (str(exc) or "").lower()
    needles = (
        "private video",
        "this video is private",
        "video is private",
        "members-only",
        "members only",
        "this video is available to this channel's members",
        "join this channel to get access",
        "members of this channel",
        "premium members",
        "age-restricted",
        "age restricted",
        "sign in to confirm your age",
        "confirm your age",
        "this video has been removed",
        "video has been removed",
        "removed by the uploader",
        "account associated with this video has been terminated",
        "this video is no longer available",
        "video unavailable",
        "this video is unavailable",
        "does not exist",
        "doesn't exist",
        "copyright claim",
        "not a youtube link",
        "couldn't find a video",
        "looks like a playlist",
        "looks like a channel",
    )
    return any(n in lowered for n in needles)


def _is_bot_block(exc: BaseException) -> bool:
    lowered = (str(exc) or "").lower()
    return any(
        n in lowered
        for n in (
            "sign in to confirm you’re not a bot",
            "sign in to confirm you're not a bot",
            "confirm you are not a bot",
            "not a bot",
            "bot check",
            "please login",
        )
    )


def _friendly_error(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()
    # Specific cases first — "sign in" also appears on private and age-restricted videos.
    if "private" in lowered:
        return "This video is private, so it cannot be downloaded."
    if any(
        n in lowered
        for n in (
            "members-only",
            "members only",
            "channel's members",
            "join this channel",
            "premium",
        )
    ):
        return "This video is members- or Premium-only, so it cannot be downloaded."
    if "age" in lowered and ("restrict" in lowered or "confirm" in lowered or "sign in" in lowered):
        return "This video is age-restricted and cannot be downloaded here."
    if any(
        n in lowered
        for n in (
            "has been removed",
            "removed by the uploader",
            "no longer available",
            "account associated with this video has been terminated",
        )
    ):
        return "This video has been removed or the account is no longer available."
    if "unavailable" in lowered or "not available" in lowered or "does not exist" in lowered or "doesn't exist" in lowered:
        return "This video is unavailable. It may have been removed or is blocked in this region."
    if "copyright" in lowered:
        return "This video is blocked due to a copyright claim."
    if _is_bot_block(exc):
        return (
            "YouTube blocked this request (bot check / sign-in required). "
            "This often happens on shared or datacenter IPs. Try again from "
            "your own computer, or later."
        )
    if "unsupported url" in lowered or "no video" in lowered:
        return "Could not find a YouTube video at that link."
    last = text.strip().splitlines()[-1]
    last = re.sub(r"^ERROR:\s*", "", last).strip()
    last = re.sub(r"^\[youtube\]\s*[A-Za-z0-9_-]{11}:\s*", "", last).strip()
    return last or "Something went wrong while talking to YouTube."


def _speed_opts(use_aria2c: bool = True) -> dict:
    opts: dict = {
        "concurrent_fragment_downloads": 8,
        "retries": 10,
        "fragment_retries": 10,
        "http_chunk_size": 10 * 1024 * 1024,
    }
    if use_aria2c and _ARIA2C:
        opts["external_downloader"] = "aria2c"
        opts["external_downloader_args"] = {
            "aria2c": [
                "-x",
                "16",
                "-s",
                "16",
                "-k",
                "1M",
                "--file-allocation=none",
                "--min-split-size=1M",
                "--summary-interval=0",
                "--max-tries=5",
            ]
        }
    return opts


def _ydl(opts: dict, clients: list[str] | None = None) -> YoutubeDL:
    merged = {**YDL_BASE, **_speed_opts(use_aria2c=bool(opts.get("_use_aria2c", True))), **opts}
    merged.pop("_use_aria2c", None)
    if clients:
        merged["extractor_args"] = {"youtube": {"player_client": clients}}
    return YoutubeDL(merged)


def _call_ydl(opts: dict, fn, *, use_aria2c: bool = True):
    """Run a yt-dlp operation, retrying with other player clients if it fails."""
    last_exc: BaseException | None = None
    attempts: list[list[str] | None] = [None, *list(FALLBACK_CLIENTS)]
    opts = {**opts, "_use_aria2c": use_aria2c}
    for clients in attempts:
        try:
            with _ydl(opts, clients=clients) as ydl:
                return fn(ydl)
        except Exception as exc:
            last_exc = exc
            if _is_permanent_error(exc):
                raise
            continue
    assert last_exc is not None
    raise last_exc


def _extract_info(url: str) -> dict:
    """Extract metadata, trying other clients if needed or if high-res formats are missing."""
    best = None
    best_q = -1
    last_exc: BaseException | None = None
    attempts: list[list[str] | None] = [None, ["tv", "ios", "web_safari"], ["tv_embedded", "ios"]]
    for clients in attempts:
        try:
            with _ydl({"skip_download": True, "_use_aria2c": False}, clients=clients) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info:
                continue
            if info.get("_type") == "playlist" and info.get("entries"):
                info = info["entries"][0] or info
            qualities = _available_qualities(info)
            max_q = max(qualities) if qualities else 0
            if max_q > best_q:
                best, best_q = info, max_q
            if max_q >= 1080:
                return info
        except Exception as exc:
            last_exc = exc
            if _is_permanent_error(exc):
                raise
            continue
    if best:
        return best
    if last_exc:
        raise last_exc
    raise RuntimeError("Could not find a YouTube video at that link.")


def _pick_thumbnail(info: dict) -> str | None:
    thumbs = info.get("thumbnails") or []
    if thumbs:
        stills = [
            t
            for t in thumbs
            if t.get("url") and "vtt" not in (t.get("url") or "") and t.get("preference", 0) != -1
        ]
        pool = stills or thumbs
        best = max(pool, key=lambda t: (t.get("height") or 0, t.get("width") or 0))
        return best.get("url")
    return info.get("thumbnail")


def _quality_p(width, height) -> int | None:
    """YouTube 'p' label: 1920x1080 and 1080x1920 (Shorts) both count as 1080p."""
    try:
        w = int(width) if width else 0
        h = int(height) if height else 0
    except (TypeError, ValueError):
        return None
    if w and h:
        return min(w, h)
    return h or w or None


def _available_qualities(info: dict) -> list[int]:
    found: set[int] = set()
    for f in info.get("formats") or []:
        if f.get("vcodec") in (None, "none"):
            continue
        q = _quality_p(f.get("width"), f.get("height"))
        if q and q > 0:
            found.add(q)
    return sorted(found)


def _format_duration(seconds) -> str | None:
    if seconds is None:
        return None
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return None
    if total < 0:
        return None
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _video_format_string(height: int) -> str:
    """Best video at or below the requested 'p' quality, preferring H.264 + AAC.

    Shorts are often 1080x1920, so width<=N is checked as well as height<=N.
    """
    h = int(height)
    return (
        f"bv*[height<={h}][vcodec^=avc1]+ba[acodec^=mp4a]/"
        f"bv*[width<={h}][vcodec^=avc1]+ba[acodec^=mp4a]/"
        f"bv*[height<={h}][vcodec^=avc]+ba[acodec^=mp4a]/"
        f"bv*[width<={h}][vcodec^=avc]+ba[acodec^=mp4a]/"
        f"bv*[height<={h}][vcodec^=avc1]+ba/"
        f"bv*[width<={h}][vcodec^=avc1]+ba/"
        f"bv*[height<={h}]+ba/"
        f"bv*[width<={h}]+ba/"
        f"b[height<={h}][vcodec^=avc1]/"
        f"b[width<={h}][vcodec^=avc1]/"
        f"b[height<={h}]/"
        f"b[width<={h}]/"
        f"bv*+ba/b"
    )


def _ffprobe(path: Path) -> dict:
    import json

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,size,format_name:stream=codec_name,codec_type,width,height,bit_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(result.stdout or "{}")
    except Exception:
        return {}


def _probe_quality(probe: dict) -> int | None:
    for stream in probe.get("streams") or []:
        if stream.get("codec_type") == "video":
            return _quality_p(stream.get("width"), stream.get("height"))
    return None


def _probe_codecs(path: Path) -> tuple[str | None, str | None]:
    probe = _ffprobe(path)
    vcodec = acodec = None
    for stream in probe.get("streams") or []:
        if stream.get("codec_type") == "video":
            vcodec = (stream.get("codec_name") or "").lower()
        elif stream.get("codec_type") == "audio":
            acodec = (stream.get("codec_name") or "").lower()
    return vcodec, acodec


def _needs_transcode(path: Path) -> bool:
    vcodec, acodec = _probe_codecs(path)
    if not vcodec:
        return False
    return vcodec not in {"h264", "avc1"} or (acodec and acodec not in {"aac", "mp4a"})


def _remux_mp4(src: Path, job: dict) -> Path:
    job["status"] = "processing"
    job["message"] = "Packing the file into MP4…"
    dest = src.with_name(src.stem + ".remux.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60 * 10)
    try:
        src.unlink(missing_ok=True)
    except OSError:
        pass
    return dest


def _transcode_mp4(src: Path, job: dict) -> Path:
    job["status"] = "processing"
    job["message"] = "Converting to a widely playable MP4 (H.264 / AAC)…"
    dest = src.with_name(src.stem + ".compat.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60 * 30)
    try:
        src.unlink(missing_ok=True)
    except OSError:
        pass
    return dest


def _finalize_video(path: Path, job: dict) -> Path:
    """Remux to MP4 when possible; only re-encode if the codecs won't play everywhere."""
    if path.suffix.lower() == ".mp4" and not _needs_transcode(path):
        return path
    if not _needs_transcode(path):
        try:
            return _remux_mp4(path, job)
        except Exception:
            pass
    return _transcode_mp4(path, job)


def _find_output(folder: Path, prefer_exts: tuple[str, ...]) -> Path | None:
    skip = {".part", ".ytdl", ".aria2", ".tmp", ".temp"}
    files = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() not in skip and not p.name.endswith(".part")
    ]
    if not files:
        return None
    for ext in prefer_exts:
        hits = [p for p in files if p.suffix.lower() == ext]
        if hits:
            return max(hits, key=lambda p: p.stat().st_size)
    return max(files, key=lambda p: p.stat().st_size)


def _safe_download_name(title: str, suffix: str) -> str:
    """Keep the video title, stripping only characters that break Windows/macOS filenames."""
    base = title or "youtube-video"
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", base)
    base = re.sub(r"\s+", " ", base).strip(" .")
    # Avoid "Title 1080p - 1080p.mp4" when the title already ends with a quality tag.
    trimmed = re.sub(r"[\s.\-_]*((2160|1440|1080|720|480|360|240)p|4[kK])$", "", base).strip(" .")
    base = trimmed or base or "youtube-video"
    reserved = {
        "con", "prn", "aux", "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
    if base.lower() in reserved:
        base = f"video {base}"
    base = base[:120]
    if suffix and suffix[0] not in ".- ":
        suffix = "." + suffix
    return f"{base}{suffix}"


def _progress_hook(job_id: str):
    def hook(d: dict) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                return
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                percent = 0
                if total:
                    percent = max(0, min(99, int(done * 100 / total)))
                job["status"] = "downloading"
                job["progress"] = percent
                speed = d.get("speed")
                if speed:
                    if speed >= 1_000_000:
                        job["message"] = f"Downloading… {percent}% ({speed / 1_000_000:.1f} MB/s)"
                    else:
                        job["message"] = f"Downloading… {percent}% ({speed / 1000:.0f} KB/s)"
                else:
                    job["message"] = f"Downloading… {percent}%" if total else "Downloading…"
            elif status == "finished":
                job["status"] = "processing"
                job["progress"] = 99
                job["message"] = "Finishing file…"

    return hook


def _download_with_fallback(url: str, opts: dict, job_id: str) -> None:
    try:
        _call_ydl(opts, lambda ydl: ydl.download([url]), use_aria2c=True)
        return
    except Exception as exc:
        if _is_permanent_error(exc):
            raise
        # Retry without aria2c if the external downloader was the problem.
        if _ARIA2C:
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job:
                    job["message"] = "Retrying the download…"
            try:
                _call_ydl(opts, lambda ydl: ydl.download([url]), use_aria2c=False)
                return
            except Exception as exc2:
                if _is_permanent_error(exc2):
                    raise
                exc = exc2
        # Last attempt: loosen the format so a missing exact quality still works.
        msg = str(exc).lower()
        if "format is not available" in msg or "no video format" in msg or "requested format" in msg:
            loose = {**opts, "format": "bv*+ba/b"}
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job:
                    job["message"] = "That quality was missing — trying the best available…"
            _call_ydl(loose, lambda ydl: ydl.download([url]), use_aria2c=False)
            return
        raise


def _to_jpeg(path: Path) -> Path:
    if path.suffix.lower() in {".jpg", ".jpeg"} and path.is_file() and path.read_bytes()[:3] == b"\xff\xd8\xff":
        return path
    out = path.with_suffix(".jpg")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-q:v", "2", str(out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except Exception:
        return path
    if out.is_file() and out.stat().st_size > 0:
        return out
    return path


def _download_social(url: str, opts: dict, cookies: Path | None) -> None:
    base = _ydl_opts(cookies, {**_speed_opts(use_aria2c=False), **opts, "noplaylist": False})
    # Prefer built-in downloader for social CDNs; aria2c can trip on signed URLs.
    with YoutubeDL(base) as ydl:
        ydl.download([url])


def _social_info(platform: str, url: str, kind: str) -> dict:
    if platform == "instagram":
        return instagram_info(url, kind)
    return facebook_info(url, kind)


def _run_social_job(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        url = job["url"]
        quality = job["quality"]
        folder = Path(job["folder"])
        platform = job.get("platform") or "instagram"
        kind = job.get("kind") or "post"
        item_index = int(job.get("item_index") or 0)
        title = job.get("title") or f"{platform} media"
        image_url = job.get("image_url")
        video_url = job.get("video_url")

    cookies = cookies_path(platform) if has_cookies(platform) else None

    try:
        if kind == "story" and not cookies:
            raise RuntimeError(_login_required_message(platform, "story"))

        if quality == "jpg":
            if not image_url:
                info = _social_info(platform, url, kind)
                if not info.get("ok"):
                    raise RuntimeError(info.get("error") or "Couldn't load that post.")
                items = info.get("items") or []
                if item_index < 0 or item_index >= len(items):
                    raise RuntimeError("That photo is no longer in the post.")
                image_url = items[item_index].get("image_url") or items[item_index].get("thumbnail")
            if not image_url:
                raise RuntimeError("Couldn't find a photo at that link.")
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job:
                    job["status"] = "downloading"
                    job["progress"] = 20
                    job["message"] = "Downloading photo…"
            referer = "https://www.instagram.com/" if platform == "instagram" else "https://www.facebook.com/"
            path = http_download(image_url, folder / "photo.jpg", {"Referer": referer})
            path = _to_jpeg(path)
            filename = _safe_download_name(title, f" - {item_index + 1}.jpg")
            with _jobs_lock:
                job = _jobs[job_id]
                job["status"] = "ready"
                job["progress"] = 100
                job["message"] = "Ready to download"
                job["path"] = str(path)
                job["filename"] = filename
                job["fallback"] = False
                job["note"] = None
                job["size"] = path.stat().st_size
                job["ready_at"] = time.time()
            return

        # Video or audio
        if not video_url:
            info = _social_info(platform, url, kind)
            if not info.get("ok"):
                raise RuntimeError(info.get("error") or "Couldn't load that link.")
            items = info.get("items") or []
            if items and 0 <= item_index < len(items):
                item = items[item_index]
                video_url = item.get("video_url")
                if item.get("type") == "photo":
                    raise RuntimeError("That item is a photo. Use the JPG download.")
                title = title or item.get("title") or title

        outtmpl = str(folder / "%(id)s.%(ext)s")
        if quality == "mp3":
            opts = {
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "progress_hooks": [_progress_hook(job_id)],
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
            prefer_exts = (".mp3",)
        else:
            if platform == "facebook":
                fmt = facebook_format_string("sd" if quality == "sd" else "hd")
            else:
                fmt = instagram_video_format()
            opts = {
                "format": fmt,
                "merge_output_format": "mp4",
                "outtmpl": outtmpl,
                "progress_hooks": [_progress_hook(job_id)],
                "postprocessors": [
                    {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
                ],
                "postprocessor_args": {"ffmpeg": ["-movflags", "+faststart"]},
            }
            prefer_exts = (".mp4",)

        if item_index and not video_url:
            opts["playlist_items"] = str(item_index + 1)
        elif not video_url:
            opts["playlist_items"] = str(item_index + 1)

        source = video_url or url
        last_exc: Exception | None = None
        try:
            _download_social(source, opts, cookies)
        except Exception as exc:
            last_exc = exc
            # Retry the page URL without a direct CDN link, then with a looser format.
            retry_opts = dict(opts)
            try:
                if source != url:
                    _download_social(url, retry_opts, cookies)
                    last_exc = None
                else:
                    raise exc
            except Exception as exc2:
                last_exc = exc2
                loose = {**retry_opts, "format": "bv*+ba/b" if quality != "mp3" else "bestaudio/best"}
                loose.pop("playlist_items", None)
                try:
                    with _jobs_lock:
                        job = _jobs.get(job_id)
                        if job:
                            job["message"] = "Trying another format…"
                    _download_social(url, loose, cookies)
                    last_exc = None
                except Exception as exc3:
                    last_exc = exc3
        if last_exc:
            raise last_exc

        path = _find_output(folder, prefer_exts)
        if not path:
            raise RuntimeError("Download finished, but no file was produced.")

        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "processing"
            job["message"] = "Checking the file…"

        if quality != "mp3":
            path = _finalize_video(path, job)
            probe = _ffprobe(path)
            actual_q = _probe_quality(probe)
            label = f"{actual_q}p" if actual_q else ("SD" if quality == "sd" else "HD")
            filename = _safe_download_name(title, f" - {label}.mp4")
            note = None
            fallback = False
            if quality == "hd" and actual_q and actual_q < 720:
                fallback = True
                note = f"HD was not available, so {actual_q}p was prepared instead."
        else:
            filename = _safe_download_name(title, ".mp3")
            actual_q = None
            note = None
            fallback = False

        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "ready"
            job["progress"] = 100
            job["message"] = "Ready to download"
            job["path"] = str(path)
            job["filename"] = filename
            job["height"] = actual_q
            job["fallback"] = fallback
            job["note"] = note
            job["size"] = path.stat().st_size
            job["ready_at"] = time.time()

    except Exception as exc:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                msg = social_friendly_error(platform, kind, exc)
                job["status"] = "error"
                job["message"] = msg
                job["error"] = msg


def _run_job(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        if job.get("platform") and job["platform"] != "youtube":
            platform_job = True
        else:
            platform_job = False
            url = job["url"]
            quality = job["quality"]
            folder = Path(job["folder"])
    if platform_job:
        _run_social_job(job_id)
        return

    try:
        q = QUALITIES[quality]
        outtmpl = str(folder / "%(id)s.%(ext)s")
        if q["kind"] == "audio":
            opts = {
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "progress_hooks": [_progress_hook(job_id)],
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
            prefer_exts = (".mp3",)
        else:
            opts = {
                "format": _video_format_string(q["height"]),
                "merge_output_format": "mp4",
                "outtmpl": outtmpl,
                "progress_hooks": [_progress_hook(job_id)],
                "postprocessors": [
                    {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
                ],
                "postprocessor_args": {
                    "ffmpeg": ["-movflags", "+faststart"],
                },
            }
            prefer_exts = (".mp4",)

        _download_with_fallback(url, opts, job_id)

        path = _find_output(folder, prefer_exts)
        if not path:
            raise RuntimeError("Download finished, but no file was produced.")

        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "processing"
            job["message"] = "Checking the file…"

        if q["kind"] == "video":
            path = _finalize_video(path, job)

        probe = _ffprobe(path)
        actual_q = _probe_quality(probe)
        title = job.get("title") or "youtube-video"
        if q["kind"] == "audio":
            filename = _safe_download_name(title, ".mp3")
            fallback = False
            note = None
        else:
            shown = actual_q or q["height"]
            filename = _safe_download_name(title, f" - {shown}p.mp4")
            fallback = bool(actual_q and actual_q < q["height"])
            if fallback:
                note = (
                    f"{q['height']}p is not available for this video, "
                    f"so the best available quality ({actual_q}p) was prepared instead."
                )
            else:
                note = None

        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "ready"
            job["progress"] = 100
            job["message"] = "Ready to download"
            job["path"] = str(path)
            job["filename"] = filename
            job["height"] = actual_q
            job["fallback"] = fallback
            job["note"] = note
            job["size"] = path.stat().st_size
            job["ready_at"] = time.time()

    except Exception as exc:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = "error"
                job["message"] = _friendly_error(exc)
                job["error"] = job["message"]


def _purge_expired_jobs() -> None:
    now = time.time()
    with _jobs_lock:
        expired = [
            job_id
            for job_id, job in _jobs.items()
            if now - job.get("created_at", now) > JOB_TTL_SECONDS
        ]
        for job_id in expired:
            folder = _jobs[job_id].get("folder")
            _jobs.pop(job_id, None)
            if folder and os.path.isdir(folder):
                shutil.rmtree(folder, ignore_errors=True)


def _cleanup_later(job_id: str, delay: int = CLEANUP_AFTER_SEND_SECONDS) -> None:
    def _do() -> None:
        time.sleep(delay)
        with _jobs_lock:
            job = _jobs.pop(job_id, None)
        if job and job.get("folder") and os.path.isdir(job["folder"]):
            shutil.rmtree(job["folder"], ignore_errors=True)

    threading.Thread(target=_do, daemon=True).start()


@app.get("/")
def index():
    return render_template(
        "index.html",
        instagram_cookies=has_cookies("instagram"),
        facebook_cookies=has_cookies("facebook"),
    )


@app.post("/api/info")
def api_info():
    _purge_expired_jobs()
    raw = (request.get_json(silent=True) or {}).get("url") or request.form.get("url", "")
    detected = detect_link(raw)
    if not detected.get("ok"):
        return jsonify({"ok": False, "error": detected.get("error") or "Please paste a link."}), 400

    platform = detected["platform"]
    kind = detected["kind"]
    url = detected["url"]

    if platform == "youtube":
        try:
            url = _clean_url(raw)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        try:
            info = _extract_info(url)
        except DownloadError as exc:
            return jsonify({"ok": False, "error": _friendly_error(exc), "platform": "youtube", "kind": kind}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": _friendly_error(exc), "platform": "youtube", "kind": kind}), 400

        if not info:
            return jsonify({"ok": False, "error": "Could not find a YouTube video at that link.", "platform": "youtube"}), 400

        if info.get("_type") == "playlist" and info.get("entries"):
            info = info["entries"][0] or info

        qualities_list = _available_qualities(info)
        max_h = max(qualities_list) if qualities_list else 0
        qualities = {
            "1080": {
                "available": max_h >= 1080,
                "best": 1080 if max_h >= 1080 else max_h or None,
            },
            "720": {
                "available": max_h >= 720,
                "best": max((h for h in qualities_list if h <= 720), default=None),
            },
            "360": {
                "available": max_h >= 360 or bool(qualities_list),
                "best": max((h for h in qualities_list if h <= 360), default=max_h or None),
            },
            "mp3": {"available": True, "best": None},
        }
        return jsonify(
            {
                "ok": True,
                "platform": "youtube",
                "kind": kind,
                "id": info.get("id"),
                "title": info.get("title") or "YouTube video",
                "thumbnail": _pick_thumbnail(info),
                "duration": info.get("duration"),
                "duration_text": _format_duration(info.get("duration")),
                "uploader": info.get("uploader") or info.get("channel"),
                "webpage_url": info.get("webpage_url") or url,
                "canonical_url": url,
                "heights": qualities_list,
                "max_height": max_h or None,
                "qualities": qualities,
            }
        )

    if platform == "instagram":
        result = instagram_info(url, kind)
    else:
        result = facebook_info(url, kind)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


SOCIAL_QUALITIES = {"mp4", "mp3", "jpg", "hd", "sd"}


@app.post("/api/prepare")
def api_prepare():
    _purge_expired_jobs()
    body = request.get_json(silent=True) or {}
    raw = body.get("url") or ""
    detected = detect_link(raw)
    if not detected.get("ok"):
        return jsonify({"ok": False, "error": detected.get("error") or "Please paste a link."}), 400

    platform = detected["platform"]
    kind = (body.get("kind") or detected["kind"] or "post").lower()
    quality = (body.get("quality") or "").lower().strip()

    if platform == "youtube":
        try:
            url = _clean_url(raw)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if quality not in QUALITIES:
            return jsonify({"ok": False, "error": "Choose 1080p, 720p, 360p, or MP3."}), 400
        title = (body.get("title") or "youtube-video").strip()[:200]
        job_id = uuid.uuid4().hex
        folder = DOWNLOAD_ROOT / job_id
        folder.mkdir(parents=True, exist_ok=True)
        with _jobs_lock:
            _jobs[job_id] = {
                "id": job_id,
                "platform": "youtube",
                "kind": kind,
                "url": url,
                "quality": quality,
                "title": title,
                "folder": str(folder),
                "status": "queued",
                "progress": 0,
                "message": "Starting…",
                "created_at": time.time(),
            }
        threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
        return jsonify({"ok": True, "job_id": job_id})

    if quality not in SOCIAL_QUALITIES:
        return jsonify({"ok": False, "error": "Choose a download format for that item."}), 400
    if kind == "story" and not has_cookies(platform):
        return jsonify({
            "ok": False,
            "needs_login": True,
            "error": _login_required_message(platform, "story"),
        }), 400

    try:
        item_index = int(body.get("item_index") or 0)
    except (TypeError, ValueError):
        item_index = 0
    if item_index < 0:
        item_index = 0

    title = (body.get("title") or f"{platform} media").strip()[:200]
    job_id = uuid.uuid4().hex
    folder = DOWNLOAD_ROOT / job_id
    folder.mkdir(parents=True, exist_ok=True)
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "platform": platform,
            "kind": kind,
            "url": detected["url"],
            "quality": quality,
            "title": title,
            "item_index": item_index,
            "image_url": body.get("image_url") or None,
            "video_url": body.get("video_url") or None,
            "folder": str(folder),
            "status": "queued",
            "progress": 0,
            "message": "Starting…",
            "created_at": time.time(),
        }
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.get("/api/progress/<job_id>")
def api_progress(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "That download expired or was not found."}), 404
        return jsonify(
            {
                "ok": True,
                "status": job["status"],
                "progress": job.get("progress", 0),
                "message": job.get("message") or "",
                "filename": job.get("filename"),
                "fallback": job.get("fallback", False),
                "note": job.get("note"),
                "height": job.get("height"),
                "size": job.get("size"),
                "error": job.get("error"),
            }
        )


@app.get("/api/file/<job_id>")
def api_file(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            abort(404)
        if job.get("status") != "ready" or not job.get("path"):
            abort(409)
        path = Path(job["path"])
        filename = job.get("filename") or path.name

    if not path.is_file():
        abort(404)

    _cleanup_later(job_id)
    return send_file(
        path,
        as_attachment=True,
        download_name=filename,
        mimetype={
            ".mp3": "audio/mpeg",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(path.suffix.lower(), "video/mp4"),
        max_age=0,
        conditional=False,
    )



@app.get("/health")
def health():
    return jsonify({"ok": True})


def _upgrade_yt_dlp() -> bool:
    """Upgrade yt-dlp on startup. Returns True if a newer version was installed."""
    try:
        import importlib.metadata

        old = importlib.metadata.version("yt-dlp")
    except Exception:
        old = None
    try:
        print("Checking for yt-dlp updates…", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
            timeout=90,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("Could not update yt-dlp (offline or pip error). Using the installed version.", flush=True)
            return False
        try:
            import importlib.metadata as md

            new = md.version("yt-dlp")
        except Exception:
            return False
        if old and new and new != old:
            print(f"Updated yt-dlp {old} → {new}. Restarting…", flush=True)
            return True
        print(f"yt-dlp is up to date ({new or old or 'unknown'}).", flush=True)
        return False
    except Exception:
        print("Could not update yt-dlp (offline or pip error). Using the installed version.", flush=True)
        return False


def _check_ffmpeg() -> None:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    print(
        "WARNING: ffmpeg/ffprobe was not found on PATH. "
        "Install ffmpeg, then restart the app. Video merges and MP3 conversion will fail without it.",
        flush=True,
    )


def _should_skip_yt_dlp_update() -> bool:
    """Skip the startup pip upgrade in Docker / Render / when already pinned at build time."""
    if os.environ.get("YT_SKIP_UPDATE") == "1":
        return True
    if os.environ.get("RENDER"):
        return True
    if Path("/.dockerenv").exists():
        return True
    return False


def main() -> None:
    if not _should_skip_yt_dlp_update():
        if _upgrade_yt_dlp():
            env = os.environ.copy()
            env["YT_SKIP_UPDATE"] = "1"
            os.execvpe(sys.executable, [sys.executable, *sys.argv], env)
    _check_ffmpeg()
    if _ARIA2C:
        print(f"Using aria2c for faster downloads ({_ARIA2C}).", flush=True)
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
    print(f"Open http://127.0.0.1:{port} in your browser.", flush=True)
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
